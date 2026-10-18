"""生成一张全透明贴图，用来让某个子网格「注册但不可见」。

为什么需要：把一层从 .asset 里删掉 meshsettings **不等于不渲染**。实测报错

  [pdxassetutil.cpp:897] Failed to create material with shader
  portrait_attachment_alpha_to_coverage (in gfx/FX/pdxmesh.shader)
  for mesh [moe_head_mesh]

—— shader 名是写在 .mesh 二进制里的，引擎照样会为每个材质槽建材质；
meshsettings 的作用是提供 shader_file（和贴图）。缺了 meshsettings，
引擎就去默认的 gfx/FX/pdxmesh.shader 找 portrait_* shader，找不到就报错。

所以正确的「不可见」做法是**照常注册**，但给它：
  shader = portrait_hair_alpha    （PS_hair 里有 clip(Diffuse.a - 1e-5)）
  texture_diffuse = 全透明贴图    （alpha 全 0 → 每个片元都被 clip 丢掉）

这样引擎能正常建材质（无报错），而该层一个像素都画不出来。

用法:
  python tools/textures/make_invisible_texture.py
"""
import io
import struct
import sys
from pathlib import Path

# stdout 转 utf-8。**用 reconfigure 不用 TextIOWrapper** —— 后者每次都新建一个
# 包装对象，多个模块各包一层时外层被 GC 会关掉共享的 buffer，于是后面任何
# print 都抛 `ValueError: I/O operation on closed file`（动画其实已经写完了，
# 只是收尾的 print 炸掉，看起来像整条失败）。reconfigure 原地改编码，天然幂等。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import moe_paths as MP    # noqa: E402  资源路径解析（子 mod 感知）
# 单体目录已退役（2026-08-29）：产物按子 mod 直写 mod/<mod_id>/。
# MP.BASE 解析到第一个存在的模型包；**定位具体文件用 MP.find()**，
# 它会遍历全部子 mod（flo 在 gfl2 包、lau/per/eld 在 endfield 包）。
MOE = MP.BASE

SIZE = 4
NAME = "moe_invisible.dds"
# properties / normal 也要给，否则采样成白图（见 build_moe_properties_maps.py 注释）。
# 不过这层根本不画，随便给中性值即可 —— 但仍要是**合法 DDS**，否则报找不到贴图。
NEUTRAL_PROPS = (0, 128, 0, 255)      # R=metal 0, G=smooth 中, B=0
NEUTRAL_NORMAL = (128, 128, 255, 255)  # 切空间朝外


def write_dds_bgra(path, size, rgba):
    """写一张未压缩 A8R8G8B8 的 DDS（mip 0 单层）。

    DDS 头 124 字节 + 4 字节 magic。pixelformat 用带 alpha 的未压缩 32 位，
    这样 alpha=0 能被引擎正确读到（BC1 只有 1 bit alpha，BC3 需要压缩器）。
    """
    r, g, b, a = rgba
    # DDS 是 BGRA 排列
    px = bytes([b, g, r, a]) * (size * size)

    DDSD_CAPS = 0x1
    DDSD_HEIGHT = 0x2
    DDSD_WIDTH = 0x4
    DDSD_PITCH = 0x8
    DDSD_PIXELFORMAT = 0x1000
    DDPF_ALPHAPIXELS = 0x1
    DDPF_RGB = 0x40
    DDSCAPS_TEXTURE = 0x1000

    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PITCH | DDSD_PIXELFORMAT
    pitch = size * 4

    header = struct.pack(
        "<I"          # dwSize
        "I"           # dwFlags
        "II"          # height, width
        "I"           # pitchOrLinearSize
        "I"           # depth
        "I"           # mipMapCount
        "11I"         # reserved
        # pixelformat
        "I I I I I I I I"
        # caps
        "I I I I I",
        124, flags, size, size, pitch, 0, 1,
        *([0] * 11),
        32,                       # pf size
        DDPF_ALPHAPIXELS | DDPF_RGB,
        0,                        # fourcc
        32,                       # rgb bit count
        0x00FF0000,               # R mask
        0x0000FF00,               # G mask
        0x000000FF,               # B mask
        0xFF000000,               # A mask
        DDSCAPS_TEXTURE, 0, 0, 0, 0,
    )
    assert len(header) == 124, len(header)
    path.write_bytes(b"DDS " + header + px)
    return len(px) + 128


def main():
    made = []
    for stem, rgba, desc in (
        ("moe_invisible", (0, 0, 0, 0), "全透明（alpha=0，被 clip 丢弃）"),
        ("moe_invisible_properties", NEUTRAL_PROPS, "中性 properties"),
        ("moe_invisible_normal", NEUTRAL_NORMAL, "中性法线"),
    ):
        p = MP.dest(f"{stem}.dds")
        n = write_dds_bgra(p, SIZE, rgba)
        made.append((p, n, desc))
        print(f"写出 {p.name:<34} {n:>5} 字节  {desc}")

    # 回读校验：确认 PIL 能解、alpha 真的是 0
    try:
        from PIL import Image
        im = Image.open(MP.find("moe_invisible.dds")).convert("RGBA")
        px = im.load()
        alphas = {px[x, y][3] for y in range(im.height) for x in range(im.width)}
        print()
        print(f"回读校验 moe_invisible.dds: {im.width}x{im.height} "
              f"alpha 取值集合 {alphas}")
        if alphas == {0}:
            print("  → alpha 全 0，PS_hair 的 clip(a-1e-5) 会丢弃所有片元 ✓")
        else:
            print("  !! alpha 不全为 0，检查 DDS 写法")
    except Exception as exc:                      # noqa: BLE001
        print(f"!! 回读失败: {exc}")


if __name__ == "__main__":
    main()
