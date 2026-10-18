"""把 mod 里的 PNG 贴图转成 CK3 能加载的 DDS。

为什么必须转：CK3 的资源管线只认 .dds。PNG 放在正确目录、.asset 里正确引用，
引擎照样报 `Failed to find texture 'x.png'` —— 它连尝试加载都不做。
原版贴图是 DXT5 + 11 级 mipmap（`female_head_diffuse.dds` 实测）。

关于压缩：Pillow 逐级生成 BC3，本脚本保留完整 mipmap 链。
只有每级实际像素的 alpha 都严格为 255 时，才复用相同色块写成 BC1；
解码后的 RGBA 不变。透明、染色遮罩、法线和材质参数的 alpha 均保留。

用法:
    python tools/textures/convert_textures_dds.py [目标目录]

默认处理各模型子 mod 的 gfx/models/portraits/moe/（moe_paths 解析）。
转换后同时更新 .asset 里的引用（.png → .dds）。
"""
import io
import os
import re
import struct
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

sys.path[:0] = [str(Path(__file__).resolve().parents[2] / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import moe_material_contract as MC   # noqa: E402

import moe_paths as MP        # 资源目录的单点定义

ROOT = Path(__file__).resolve().parents[2]
# 资源根。PNG 分散在 flo/tex、lau/tex、shared/tex 下，
# 所以下面用 rglob 递归扫，不是 glob。
DEFAULT_DIR = MP.BASE

# DDS 头常量
DDSD_CAPS = 0x1
DDSD_HEIGHT = 0x2
DDSD_WIDTH = 0x4
DDSD_PITCH = 0x8
DDSD_LINEARSIZE = 0x80000
DDSD_PIXELFORMAT = 0x1000
DDSD_MIPMAPCOUNT = 0x20000
DDPF_ALPHAPIXELS = 0x1
DDPF_FOURCC = 0x4
DDPF_RGB = 0x40
DDSCAPS_TEXTURE = 0x1000
DDSCAPS_COMPLEX = 0x8
DDSCAPS_MIPMAP = 0x400

# 单张贴图的像素上限。芙洛伦的头发图是 16MB PNG（4096²），
# 未压缩 BGRA + mip 链会膨胀到约 87MB，加载慢且显存吃紧。
# 超过就先降采样。
MAX_EDGE = 2048

# 是否用块压缩。先编码 DXT5(BC3)，严格不透明的完整 mip 链可无损选 DXT1。
# 关掉只在怀疑压缩伪影时用（DXT5 对法线的 RGB 有块伪影，
# 但原版自己也用 DXT5 存法线，所以不是问题）。
COMPRESS = True

MESHSETTINGS_RE = re.compile(r"meshsettings\s*=\s*\{.*?\}", re.S)
SHADER_RE = re.compile(r'\bshader\s*=\s*"([^"]+)"')
DIFFUSE_RE = re.compile(r'\btexture_diffuse\s*=\s*"([^"]+)"')


def colormask_only_diffuses(targets):
    """Diffuse maps left on color-mask slots must keep their source alpha."""
    opacity, colormask = set(), set()
    for target in targets:
        for asset in target.rglob("*.asset"):
            text = asset.read_text(encoding="utf-8-sig", errors="replace")
            for block in MESHSETTINGS_RE.findall(text):
                shader = SHADER_RE.search(block)
                diffuse = DIFFUSE_RE.search(block)
                if not shader or not diffuse:
                    continue
                name = Path(diffuse.group(1).replace("\\", "/")).stem.casefold()
                if shader.group(1) in MC.ALPHA_IS_OPACITY:
                    opacity.add(name)
                if shader.group(1) in MC.ALPHA_IS_COLORMASK:
                    colormask.add(name)
    return colormask - opacity


def build_mip_chain(img):
    """生成 mip 链，直到 1x1。返回 [Image, ...]，第 0 张是原图。"""
    chain = [img]
    w, h = img.size
    while w > 1 or h > 1:
        w = max(1, w // 2)
        h = max(1, h // 2)
        chain.append(chain[-1].resize((w, h), Image.LANCZOS))
    return chain


def binarize_alpha(img, cutout=MC.BINARIZE_ALPHA_CUTOUT):
    """把 alpha 压成 0 / 255 两档。返回 (新图, 被改动的像素数)。

    低于 cutout 的归 0，其余归 255。cutout 的语义随图而变：
    衣物取 16（只有真镂空保留透明），表情/贴花图集取中点 128（偏低的
    半透明真正消失）—— 见 moe_material_contract.binary_alpha_cutout()。
    """
    img = img.convert("RGBA")
    a = img.split()[3]
    new_a = a.point(lambda v: 0 if v < cutout else 255)
    # 改动数 = 两侧都要算。**只统计「被拉到 255」那一侧是错的**：cutout=16 时
    # 归零的只有 alpha 1~15 的脏边（量小，漏了看不出来），但 cutout=128 时
    # 被归零的是那一大批糊脸的半透明像素，正是我们要改的主体，漏算会让日志
    # 报出一个远小于实际的数字，掩盖「规则到底有没有生效」。
    hist = a.histogram()
    changed = (sum(n for v, n in enumerate(hist) if 0 < v < cutout)
               + sum(n for v, n in enumerate(hist) if cutout <= v < 255))
    r, g, b, _ = img.split()
    return Image.merge("RGBA", (r, g, b, new_a)), changed


def opaque_bc3_mip_to_bc1(payload, width, height):
    """复用一个新编码 BC3 mip 的色块；实际像素不全透明度 255 时返回 None。

    不重压 RGB。BC3 总用四色；BC1 的端点顺序决定三色/四色模式，因此
    c0<c1 时交换端点并交换 0/1、2/3 选择器，c0==c1 时统一选择同一颜色。
    4×4 块边缘的填充像素不是纹理像素，不能把它们的 alpha=0 算作透明。
    """
    if (not isinstance(width, int) or not isinstance(height, int) or
            width < 1 or height < 1):
        raise ValueError("BC3 mip dimensions must be positive integers")
    columns = (width + 3) // 4
    count = columns * ((height + 3) // 4)
    if len(payload) != count * 16:
        raise ValueError("BC3 payload does not match mip dimensions")
    blocks = np.frombuffer(payload, dtype=np.uint8).reshape(count, 16)
    a0, a1 = blocks[:, 0].astype(np.uint16), blocks[:, 1].astype(np.uint16)
    palette = np.empty((count, 8), dtype=np.uint16)
    palette[:, 0], palette[:, 1] = a0, a1
    for slot in range(2, 8):
        low = (((6 - slot) * a0 + (slot - 1) * a1) // 5
               if slot <= 5 else (0 if slot == 6 else 255))
        palette[:, slot] = np.where(
            a0 > a1, ((8 - slot) * a0 + (slot - 1) * a1) // 7, low)
    indices = np.zeros(count, dtype=np.uint64)
    for byte in range(6):
        indices |= blocks[:, byte + 2].astype(np.uint64) << (8 * byte)
    rows = np.arange(count)
    block_x, block_y = rows % columns, rows // columns
    for pixel in range(16):
        valid = ((block_x * 4 + pixel % 4 < width) &
                 (block_y * 4 + pixel // 4 < height))
        active = rows[valid]
        selector = ((indices[valid] >> (3 * pixel)) & 7).astype(np.intp)
        if np.any(palette[active, selector] != 255):
            return None

    color = blocks[:, 8:16].copy()
    c0 = np.frombuffer(color[:, :2].copy().tobytes(), dtype="<u2")
    c1 = np.frombuffer(color[:, 2:4].copy().tobytes(), dtype="<u2")
    selectors = np.frombuffer(color[:, 4:8].copy().tobytes(), dtype="<u4").copy()
    swap = c0 < c1
    color[swap, :2], color[swap, 2:4] = blocks[swap, 10:12], blocks[swap, 8:10]
    selectors[swap] ^= np.uint32(0x55555555)
    selectors[c0 == c1] = 0
    color[:, 4:8] = selectors.view(np.uint8).reshape(-1, 4)
    return color.tobytes()


def write_dds_dxt5(path, img, binary_alpha=False,
                   binary_cutout=MC.BINARIZE_ALPHA_CUTOUT, *, optimize_opaque=True):
    """写完整 BC3/BC1 mip 链，返回实际 FourCC；原函数名保留调用兼容。

    默认只有全部 mip 实际像素严格不透明时才无损写 BC1。色块来自同一次
    BC3 编码，不把 PNG 另压 BC1 当作等价。optimize_opaque=False 逐字节
    保留原 BC3 写出行为，供冻结对照使用。

    为什么要压缩：未压缩 32bpp + mip 链让 mod 涨到 342 MB（其中贴图 225 MB），
    游戏启动加载明显变慢。原版贴图就是 DXT5 + 11 级 mipmap
    （`female_head_diffuse.dds` 实测）。

    做法：Pillow 12 能写 DXT5 但**不生成 mip 链**，所以逐级自己压、
    把各级的压缩块**首尾拼接**，再套一个声明了 mipMapCount 的头。
    DXT5 的每级数据是独立的 4×4 块流，拼接是合法的。

    小于 4×4 的 mip 级：DXT5 的块是固定 4×4，2×2 和 1×1 仍占一整块
    （16 字节）。Pillow 会自己补，所以照常调用即可。
    """
    img = img.convert("RGBA")
    chain = build_mip_chain(img)
    if binary_alpha:
        chain = [binarize_alpha(m, binary_cutout)[0] for m in chain]
    w, h = img.size

    blobs = []
    for m in chain:
        buf = io.BytesIO()
        m.save(buf, format="DDS", pixel_format="DXT5")
        blobs.append(buf.getvalue()[128:])   # 去掉每级自带的 128 字节头

    fourcc = b"DXT5"
    if optimize_opaque:
        compact = []
        for mip, blob in zip(chain, blobs):
            converted = opaque_bc3_mip_to_bc1(blob, *mip.size)
            if converted is None:
                break
            compact.append(converted)
        if len(compact) == len(blobs):
            blobs, fourcc = compact, b"DXT1"

    flags = (DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_LINEARSIZE
             | DDSD_PIXELFORMAT | DDSD_MIPMAPCOUNT)
    caps = DDSCAPS_TEXTURE | DDSCAPS_COMPLEX | DDSCAPS_MIPMAP

    hdr = bytearray()
    hdr += b"DDS "
    hdr += struct.pack("<I", 124)
    hdr += struct.pack("<I", flags)
    hdr += struct.pack("<I", h)
    hdr += struct.pack("<I", w)
    # 压缩格式这里填的是**第 0 级的字节数**（linearSize），不是 pitch
    hdr += struct.pack("<I", len(blobs[0]))
    hdr += struct.pack("<I", 0)
    hdr += struct.pack("<I", len(chain))
    hdr += b"\0" * (11 * 4)
    hdr += struct.pack("<I", 32)
    hdr += struct.pack("<I", DDPF_FOURCC)
    hdr += fourcc
    hdr += struct.pack("<I", 0)      # RGBBitCount（压缩格式为 0）
    hdr += struct.pack("<I", 0) * 1  # R mask
    hdr += struct.pack("<I", 0)      # G mask
    hdr += struct.pack("<I", 0)      # B mask
    hdr += struct.pack("<I", 0)      # A mask
    hdr += struct.pack("<I", caps)
    hdr += struct.pack("<I", 0)
    hdr += struct.pack("<I", 0)
    hdr += struct.pack("<I", 0)
    hdr += struct.pack("<I", 0)
    assert len(hdr) == 128, len(hdr)

    with path.open("wb") as f:
        f.write(hdr)
        for b in blobs:
            f.write(b)
    return fourcc.decode("ascii")


def write_dds_bgra(path, img, binary_alpha=False,
                   binary_cutout=MC.BINARIZE_ALPHA_CUTOUT):
    """写未压缩 32bpp BGRA DDS，含完整 mip 链。

    binary_alpha=True 时，**每一级 mip 都重新二值化**。只在第 0 级二值化是
    不够的：LANCZOS 缩小会把 0/255 的边界重新插值成中间值，远景切到低级 mip
    时帽檐又会变成筛子 —— 而肖像镜头恰好经常取到中间 mip。
    """
    img = img.convert("RGBA")
    chain = build_mip_chain(img)
    if binary_alpha:
        chain = [binarize_alpha(m, binary_cutout)[0] for m in chain]
    w, h = img.size

    flags = (DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PITCH
             | DDSD_PIXELFORMAT | DDSD_MIPMAPCOUNT)
    caps = DDSCAPS_TEXTURE | DDSCAPS_COMPLEX | DDSCAPS_MIPMAP

    hdr = bytearray()
    hdr += b"DDS "
    hdr += struct.pack("<I", 124)              # dwSize
    hdr += struct.pack("<I", flags)
    hdr += struct.pack("<I", h)
    hdr += struct.pack("<I", w)
    hdr += struct.pack("<I", w * 4)            # pitch = 每行字节数
    hdr += struct.pack("<I", 0)                # depth
    hdr += struct.pack("<I", len(chain))       # mipMapCount
    hdr += b"\0" * (11 * 4)                    # dwReserved1[11]
    # DDS_PIXELFORMAT: 32 字节
    hdr += struct.pack("<I", 32)               # pf size
    hdr += struct.pack("<I", DDPF_RGB | DDPF_ALPHAPIXELS)
    hdr += struct.pack("<I", 0)                # fourCC = 0（未压缩）
    hdr += struct.pack("<I", 32)               # RGBBitCount
    hdr += struct.pack("<I", 0x00FF0000)       # R mask
    hdr += struct.pack("<I", 0x0000FF00)       # G mask
    hdr += struct.pack("<I", 0x000000FF)       # B mask
    hdr += struct.pack("<I", 0xFF000000)       # A mask
    hdr += struct.pack("<I", caps)
    hdr += struct.pack("<I", 0)                # caps2
    hdr += struct.pack("<I", 0)                # caps3
    hdr += struct.pack("<I", 0)                # caps4
    hdr += struct.pack("<I", 0)                # reserved2
    assert len(hdr) == 128, len(hdr)

    with path.open("wb") as f:
        f.write(hdr)
        for m in chain:
            # DDS 的 BGRA 是字节序 B,G,R,A
            r, g, b, a = m.convert("RGBA").split()
            f.write(Image.merge("RGBA", (b, g, r, a)).tobytes())


def materialize_existing_opaque_dds(targets):
    """Compact legacy BC3 files with no source PNG beside them.

    This uses the same conservative per-mip alpha test as the proof pipeline;
    non-opaque files remain BC3 and all opaque color blocks are preserved.
    """
    compacted = []
    seen = set()
    for target in targets:
        for dds in sorted(target.rglob("*.dds")):
            dds = dds.resolve()
            if dds in seen:
                continue
            seen.add(dds)
            data = dds.read_bytes()
            if len(data) < 128 or data[:4] != b"DDS " or data[84:88] != b"DXT5":
                continue
            height = struct.unpack_from("<I", data, 12)[0]
            width = struct.unpack_from("<I", data, 16)[0]
            mip_count = struct.unpack_from("<I", data, 28)[0] or 1
            offset = 128
            chunks = []
            for level in range(mip_count):
                w, h = max(1, width >> level), max(1, height >> level)
                length = ((w + 3) // 4) * ((h + 3) // 4) * 16
                payload = data[offset:offset + length]
                if len(payload) != length:
                    chunks = []
                    break
                converted = opaque_bc3_mip_to_bc1(payload, w, h)
                if converted is None:
                    chunks = []
                    break
                chunks.append(converted)
                offset += length
            if not chunks:
                continue
            header = bytearray(data[:128])
            header[84:88] = b"DXT1"
            struct.pack_into("<I", header, 20, len(chunks[0]))
            candidate = bytes(header) + b"".join(chunks)
            if candidate == data:
                continue
            tmp = dds.with_suffix(dds.suffix + ".tmp")
            tmp.write_bytes(candidate)
            tmp.replace(dds)
            compacted.append((dds, len(data), len(candidate)))
    return compacted


def main():
    # **必须收全部参数**：调用方（build_moe_mod.py）按 O.GAMES 逐个游戏包传目录，
    # 只读 sys.argv[1] 会让第二个及以后的游戏包**整包贴图从不转换**。
    # 2026-09-11 撞到：GAMES 是 {gfl2, endfield}，gfl2 在前，于是 endfield 三人
    # （洛茜/佩里缇亚/艾尔黛拉）一直在用 08-26 的旧 DDS —— 表情图集二值化改了
    # 也不生效，而构建 rc=0、日志里那 474 行转换记录看着一切正常。
    targets = [Path(a) for a in sys.argv[1:]] or [DEFAULT_DIR]
    missing = [t for t in targets if not t.is_dir()]
    if missing:
        raise SystemExit(f"目录不存在: {missing}")

    # **必须 rglob**：PNG 在 flo/tex 等子目录里，glob 只扫一层，
    # 扫不到会「0 个 PNG」静默通过，看起来像已经转完了。
    pngs = []
    for t in targets:
        pngs.extend(sorted(t.rglob("*.png")))
    if not pngs:
        print(f"No PNG files to convert: {targets}")
        legacy_compacted = materialize_existing_opaque_dds(targets)
        if legacy_compacted:
            print(f"  legacy BC3 -> BC1: {len(legacy_compacted)} files")
        return

    print(f"目标目录 {', '.join(str(t) for t in targets)}")
    color_mask_only = colormask_only_diffuses(targets)
    # 每张贴图互相独立，按进程并行（2026-10-01：92 人几千张串行要半小时以上）。
    # 用进程不用线程：PIL 缩放与 numpy 之外的 mip/压缩循环是纯 Python，持 GIL。
    # 结果按输入顺序收回，日志与 converted 顺序和串行版一致。
    jobs = max(1, int(os.environ.get("MOE_DDS_JOBS", os.cpu_count() or 4)))
    converted = []
    with ProcessPoolExecutor(jobs) as pool:
        for src, dst, line in pool.map(_convert_one, pngs,
                                       [color_mask_only] * len(pngs), chunksize=4):
            converted.append((src, dst))
            print(line, flush=True)
    _update_asset_refs(targets, converted)

    # 删掉 PNG，避免 mod 体积翻倍
    for png in pngs:
        png.unlink()
    print(f"  已删除 {len(pngs)} 个 PNG 源文件")
    legacy_compacted = materialize_existing_opaque_dds(targets)
    if legacy_compacted:
        print(f"  legacy BC3 -> BC1: {len(legacy_compacted)} files")
    print("\n[DDS CONVERSION DONE]")


def _convert_one(png, color_mask_only):
    """转一张 PNG，返回 (源名, 目标名, 日志行)。在子进程里跑，不能打印。"""
    if True:
        with Image.open(png) as im:
            im.load()
            w, h = im.size
            note = ""
            if max(w, h) > MAX_EDGE:
                scale = MAX_EDGE / max(w, h)
                nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
                im = im.resize((nw, nh), Image.LANCZOS)
                note = f"  (降采样 {w}x{h} → {nw}x{nh})"
            # 衣服 diffuse 的中间 alpha 在 CK3 里会变成能看穿的筛子
            # （帽檐透出大地图），二值化。properties/normal 的 A 有别的
            # 语义，wants_binary_alpha 已把它们排除。
            # Shared opacity/color-mask sources were split immediately before
            # the alpha check. The original remains a palette mask and must
            # retain its continuous alpha, even if its filename also matches
            # a global cutout keyword.
            binary = (MC.wants_binary_alpha(png.name)
                      and png.stem.casefold() not in color_mask_only)
            # 阈值按图区分：衣物 16（只有真镂空保留透明），表情/贴花图集 128
            # （偏低的半透明归零真正消失）。两者语义相反，套错比不改更糟 ——
            # 详见 moe_material_contract.BINARIZE_ALPHA_MIDPOINT_KEYWORDS。
            cutout = MC.binary_alpha_cutout(png.name)
            if binary:
                _, nfix = binarize_alpha(im.convert("RGBA"), cutout)
                note += f"  (alpha 二值化 {nfix:,} 像素, 阈值 {cutout})"
            # 虹膜/眼白的 alpha 要整张刷 0 —— 那是原版给的「不要染色」开关
            # （`ColorMaskStrength == 0` 特例）。不刷的话 vPaletteColorEyes
            # 会把虹膜原色**乘掉**，两个角色的眼睛都变成同一种橙红。
            # 详细证据链见 moe_material_contract.NO_PALETTE_ALPHA_KEYWORDS。
            no_pal = MC.wants_no_palette_alpha(png.name)
            if no_pal:
                im = im.convert("RGBA")
                _a = np.asarray(im).copy()
                _before = float(_a[..., 3].mean())
                _a[..., 3] = 0
                im = Image.fromarray(_a, "RGBA")
                note += (f"  (**alpha {_before:.0f}→0**，不接受 palette 染色)")
            dds = png.with_suffix(".dds")
            # 保留原 BC3 色块和完整 mip 链，仅严格不透明的整张图自动选 BC1。
            if COMPRESS:
                fmt = write_dds_dxt5(dds, im, binary_alpha=binary,
                                     binary_cutout=cutout)
                note += f"  ({fmt})"
            else:
                write_dds_bgra(dds, im, binary_alpha=binary,
                               binary_cutout=cutout)
        return (png.name, dds.name,
                f"  {png.name:<42} → {dds.name}  "
                f"{dds.stat().st_size:>12,} bytes{note}")


def _update_asset_refs(targets, converted):
    # 更新 .asset 里的引用。
    #
    # **不能用裸文件名精确匹配** —— .asset 里写的是带子目录的相对路径
    # （"flo/tex/xxx.png"），拿 '"xxx.png"' 去匹配会失配，替换 0 处却
    # **静默成功**：PNG 引用留在 asset 里而 PNG 文件已被下面删掉，
    # 引擎报 Failed to find texture。
    #
    # 改成匹配「路径尾部」：把 /xxx.png" 换成 /xxx.dds"，同时兼容
    # 仍是裸名的旧格式。
    # `targets` can contain multiple model-package roots (gfl2 + endfield).
    # The old implementation searched only `MP.BASE`, which is intentionally
    # the first existing root and therefore skipped the endfield `.asset`
    # files whenever the converter was called with both packages.  Keep the
    # conversion and reference rewrite scoped to the roots supplied by the
    # caller, while de-duplicating nested targets for safe direct invocation.
    assets = sorted({asset for target in targets for asset in target.rglob("*.asset")})
    for asset in assets:
        txt = asset.read_text(encoding="utf-8-sig")
        n = 0
        for src, dst in converted:
            for old_ref, new_ref in ((f'/{src}"', f'/{dst}"'),   # 带子目录
                                     (f'"{src}"', f'"{dst}"')):  # 裸名（兼容）
                if old_ref in txt:
                    txt = txt.replace(old_ref, new_ref)
                    n += 1
                    break
        asset.write_text(txt, encoding="utf-8-sig")
        print(f"\n  {asset.name}: 更新 {n} 处贴图引用")
        # 反向自检：转换过的 PNG 不能还有引用残留（文件马上要被删）
        leftover = [src for src, _ in converted
                    if f'/{src}"' in txt or f'"{src}"' in txt]
        if leftover:
            raise SystemExit(
                f"[dds] **{asset.name} 里仍有 {len(leftover)} 处 PNG 引用未替换**"
                f"（{leftover[:3]}）—— PNG 即将被删，引擎会报"
                f" Failed to find texture")


if __name__ == "__main__":
    main()
