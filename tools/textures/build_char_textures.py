# -*- coding: utf-8 -*-
"""任一角色的贴图落位 + properties / normal 生成（按 moe_chars 登记表驱动）。

取代两份写死的脚本：
  · `build_moe_properties_maps.main()` —— 源目录写死芙洛伦，RMO 配对表按文件名手抄；
  · `build_endfield_textures.py` —— 终末地三角色的目录名手抄在 CHARS 里。
两者的**常量与写图函数**都还在 `build_moe_properties_maps` 里（虹膜粗糙度下限、
RRxG 法线布局、二次元粗糙度区间都是实机定的），这里只做「按角色找源、按命名规则
配对、按部位挑常量档」这一层。

## 源图怎么找
只处理该角色 report（建网格产物）`parts[].submeshes` 里引用到的 diffuse ——
mesh 没引用的图不进 mod。文件在 `moe_chars.src_dir(ck)` 下**递归、大小写不敏感**地找
（少前 2 的 RMO/法线名常带日期后缀且大小写与 diffuse 不一致：
`c_florencessr01_slg_cloth1_rmo_05_08_2025.png` 对 `c_FlorenceSSR01_slg_cloth1_da.png`）。

## properties / normal 的配对规则
diffuse stem 去掉尾部 `_d` / `_da` / `_dt` / `_D` 得 base，然后：
  少前 2   base + `_rmo*`（R=rough G=metal）      base + `_n` / `_n_*`
  终末地   base + `_P`（B=rough）                  base + `_N`
两种都找不到 → 按部位关键词取常量档 + 中性法线。

## 共用贴图
`moe_paths.shared_game_of()` 认出的图（gfl2 的 extra.png、endfield 的 T_actor_common_*）
落该游戏模型包的 `models/shared_tex/`，多个角色重复引用只写一份；若某角色的
`<ck>_tex/` 里还留着旧版副本会被删掉 —— 引擎按基名建全局平表，重名报 Duplicate texture。

用法:
  python tools/textures/build_char_textures.py --ck nag
  python tools/textures/build_char_textures.py --ck eld --out-dir work/eld_tex   # 只看效果
  python tools/textures/build_char_textures.py --all                             # 所有有 report 的角色
"""
import argparse
import contextlib
import io
import json
import os
import re
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim", "textures")]   # 底座在 lib/；同目录的 build_moe_properties_maps
import build_moe_properties_maps as B    # noqa: E402  常量 + 写图函数
import moe_chars as X                    # noqa: E402
import moe_paths as MP                   # noqa: E402

# 部位常量档 (roughness, metalness, spec)。少前 2 与终末地两份表并成一份：
# 数值来源见 build_moe_properties_maps.FLAT_PROFILES / build_endfield_textures.FLAT_BY_PART
# 的注释（虹膜粗糙度 ≥ 0.32 是实机定的硬下限）。
FLAT_BY_PART = [
    ("iris",    ["iris", "_eye_d", "_eye.", "_eye_", "eyes"], (0.38, 0.0, 0.55)),
    ("emotion", ["eyeblend", "emotion", "extra", "eyehl", "_hl"], (0.80, 0.0, 0.10)),
    ("face",    ["face"], (0.70, 0.0, 0.25)),
    ("hair",    ["hair"], (0.65, 0.0, 0.30)),
    ("body",    ["body", "skin"], (0.60, 0.0, 0.30)),
    ("fur",     ["fur"], (0.65, 0.0, 0.28)),
    ("cloth",   ["cloth", "cth", "up_d", "down_d", "wl_d", "weapon", "gun", "qiang"], (0.60, 0.0, 0.30)),
]
DEFAULT_FLAT = ("默认", (0.65, 0.0, 0.28))
_DIFF_SUFFIX = re.compile(r"_(d|da|dt)$", re.I)
_DUP_SUFFIX = re.compile(r"\.\d{3}$")


def _strip_dup(name):
    return _DUP_SUFFIX.sub("", str(name))


def part_of(stem):
    low = stem.lower()
    for name, keys, prof in FLAT_BY_PART:
        if any(k in low for k in keys):
            return name, prof
    return DEFAULT_FLAT


class SrcIndex:
    """源目录下全部图片，按小写文件名索引。"""
    EXT = (".png", ".tga", ".jpg", ".jpeg", ".bmp", ".dds")

    def __init__(self, root: Path):
        self.by_low = {}
        self.all = {}
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in self.EXT and "spa" not in {q.lower() for q in p.parts}:
                self.by_low.setdefault(p.name.lower(), p)
                self.all.setdefault(p.name.lower(), []).append(p)

    def get(self, name):
        return self.by_low.get(str(name).lower())

    def by_digest(self, name, digest):
        """同名多份（终末地源包 textures/ 与 other tex/ 各有一张同名不同内容的图）时，
        按导入时写进别名的 sha256 前缀挑**导入实际用的那份**。

        只按文件名 setdefault 取第一份，会把 other tex/ 里 alpha 语义不同的图写进
        别名 —— 2026-09-27 第二批 58 张贴图因此 alpha 全错（虹膜整片透明、衣片 69% 镂空）。"""
        import hashlib
        low = str(name).lower()
        for p in self.all.get(low, ()):
            if hashlib.sha256(p.read_bytes()).hexdigest().startswith(digest):
                return p
        return None

    def starting(self, prefix_low):
        return sorted((n, p) for n, p in self.by_low.items() if n.startswith(prefix_low))


def find_props_source(idx: SrcIndex, base: str):
    """返回 ("rmo"|"P", path) 或 None。"""
    bl = base.lower()
    for n, p in idx.starting(bl + "_rmo"):
        return "rmo", p
    p = idx.get(f"{base}_P.png")
    if p:
        return "P", p
    return None


def find_normal_source(idx: SrcIndex, base: str):
    bl = base.lower()
    p = idx.get(f"{base}_N.png") or idx.get(f"{base}_n.png")
    if p:
        return p
    for n, p in idx.starting(bl + "_n_"):
        return p
    return None


def props_from_rmo(stem, rmo_path):
    a = B.load01(rmo_path)
    rough = B.ANIME_ROUGH_MIN + a[..., 0] * (B.ANIME_ROUGH_MAX - B.ANIME_ROUGH_MIN)
    metal = a[..., 1] * B.ANIME_METAL_SCALE
    return B.write_properties(stem, rough, metal, B.spec_from_roughness(rough), rough.shape)


def props_from_P(stem, p_path):
    a = B.load01(p_path)
    rough = B.ANIME_ROUGH_MIN + a[..., 2] * (B.ANIME_ROUGH_MAX - B.ANIME_ROUGH_MIN)
    metal = np.zeros_like(rough)
    return B.write_properties(stem, rough, metal, B.spec_from_roughness(rough), rough.shape)


def props_flat(stem, prof, size):
    h, w = size
    r, m, s = prof
    return B.write_properties(stem, np.full((h, w), r, np.float32), np.full((h, w), m, np.float32),
                              np.full((h, w), s, np.float32), (h, w))


def _dest(stem_with_ext, out):
    return (out / stem_with_ext) if out else MP.dest(stem_with_ext)


class _DestShim:
    """给 --out-dir 用：build_moe_properties_maps 的写图函数内部调 `MP.dest()`，
    把 B.MP 换成这个壳就能把 properties/normal 也导进临时目录（2026-09-06 第一次
    试跑时它们全写进了真实部署目录，留下一堆 .png 残留）。"""
    def __init__(self, out): self.out = out
    def dest(self, name): return self.out / name


# MMD 的 toon 阶梯图 / 球面贴图不是 diffuse，只有 no_render 层会引用到（那些层用
# 全透明工具贴图）。找不到时跳过，不算缺失。
AUX_TEX = re.compile(r"(toon|spa|sph|shine)", re.I)


def build_one(ck, out=None, quiet=False, shared_owner=None):
    c = X.BY_CK[ck]
    rep_path = X.report_path(ck)
    if not rep_path.is_file():
        raise SystemExit(f"**{ck} 没有建网格 report** {rep_path} —— 先跑 build_florence_poc.py --model {c['slug']}")
    rep = json.loads(rep_path.read_text(encoding="utf-8"))
    # 剥 Blender 的重复 datablock 后缀 `x.png.001`（源里同一张图被引用两次时 report 原样记下）：
    # 不剥则 diffuse 查不到 → 那一层渲成白图（洁尔佩塔头发 2026-09-06 第一次回归就撞上）。
    needed = sorted({_strip_dup(submesh["diffuse"])
                     for part in rep["parts"].values()
                     for submesh in part.get("submeshes", []) if submesh.get("diffuse")})
    src = X.src_dir(ck)
    idx = SrcIndex(src)
    _rev_alias = {v: k for k, v in (c.get("tex_alias") or {}).items()}   # 别名 → 源文件名（手工皮肤别名）
    _rev_alias.update(rep.get("texture_aliases") or {})                 # 自动隔离别名 → 源文件名
    _real_mp = B.MP
    if out:
        out.mkdir(parents=True, exist_ok=True)
        B.MP = _DestShim(out)
    made, missing, flat_used, srcd, skipped = [], [], [], [], []
    for diff in needed:
        stem = Path(diff).stem
        sp = idx.get(diff) or idx.get(f"{stem}.png")
        # 自动隔离别名的**权威副本**就是导入时拷进 work/<slug>/tex_alias/ 的那张（内容与 mesh 引用一致）
        _alias_copy = X.work_dir(ck) / "tex_alias" / diff
        if sp is None and _alias_copy.is_file():
            sp = _alias_copy
        if sp is None and diff in _rev_alias:   # 皮肤别名（moe_chars.tex_alias）：mesh 里是别名，源盘上是原名
            _m = re.search(r"_([0-9a-f]{10})$", stem)
            sp = (idx.by_digest(_rev_alias[diff], _m.group(1)) if _m else None) \
                or idx.get(_rev_alias[diff]) or idx.get(Path(_rev_alias[diff]).stem + ".png")
        if sp is None:
            if AUX_TEX.search(stem) and diff.lower().endswith(".bmp"):
                skipped.append(diff)
            else:
                missing.append(diff)
            continue
        # 并行时共用图只由指定的那个角色写，别的角色只登记（见 main）
        if shared_owner is not None and MP.shared_game_of(diff) and shared_owner.get(diff, ck) != ck:
            made.append(f"{stem}{sp.suffix.lower() if sp.suffix.lower() in ('.png', '.dds') else '.png'}")
            continue
        # 共用图只写一份；角色目录里若残留旧副本就删（重名 = Duplicate texture）
        if not out and MP.shared_game_of(diff):
            for stale in (MP.tex_dir(ck) / diff, MP.tex_dir(ck) / f"{stem}.dds"):
                if stale.is_file():
                    stale.unlink()
            for suf in ("_properties", "_normal"):
                for ext in (".png", ".dds"):
                    q = MP.tex_dir(ck) / f"{stem}{suf}{ext}"
                    if q.is_file():
                        q.unlink()
        # 非 png/dds 源（鸣潮 cheek_00.tga 等）落位时转成 png：MP.dest 只按 dds/png 路由贴图，
        # 其它扩展名落进包根（还落错了包），DDS 转换也只扫 *.png —— asset 写 .dds 引用、文件却不存在（2026-10-09，24 人）。
        _ext = sp.suffix.lower() if sp.suffix.lower() in (".png", ".dds") else ".png"
        dst = _dest(f"{stem}{_ext}", out)
        if not dst.is_file() or dst.stat().st_mtime < sp.stat().st_mtime:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if _ext == sp.suffix.lower():
                shutil.copy2(sp, dst)
            else:
                from PIL import Image
                with Image.open(sp) as _im:
                    _im.convert("RGBA").save(dst)
        made.append(dst.name)
        # 别名副本的 stem 是 moe_<ck>_<原名>_<digest>；properties/normal 要按源包里的原名配对
        base = _DIFF_SUFFIX.sub("", Path(_rev_alias[diff]).stem if diff in _rev_alias else sp.stem)
        ps = find_props_source(idx, base)
        if ps:
            (props_from_rmo if ps[0] == "rmo" else props_from_P)(stem, ps[1])
            srcd.append((stem, ps[0]))
        else:
            # 常量档写 64×64 即可（采样是常量，尺寸只影响体积）：芙洛伦旧产物就是 64×64，
            # 第一次回归时按 diffuse 全尺寸写出 2048² 的常量图，白占 4 MB/张。
            pname, prof = part_of(sp.stem)
            props_flat(stem, prof, (64, 64))
            flat_used.append((stem, pname, prof))
        nn = find_normal_source(idx, base)
        if nn:
            B.write_normal_rrxg(stem, nn)
        else:
            B.write_flat_normal(stem)
    B.MP = _real_mp
    if not quiet:
        print(f"\n=== {ck} {c['name_cn']}  源 {src.name[:30]}  输出 {out or '部署目录'}")
        print(f"  diffuse {len(made)}/{len(needed)}  properties/normal 用源图 {len(srcd)} 张，常量档 {len(flat_used)} 张")
        for stem, pname, prof in flat_used:
            print(f"      {stem:46} {pname:8} rough={prof[0]} spec={prof[2]}")
    if skipped and not quiet:
        print(f"  跳过 MMD 辅助图 {skipped}")
    if missing:
        print(f"  !! {ck} 找不到贴图 {missing}（在 {src} 下递归找过）")
    return made, missing


def _build_captured(ck, out, owner):
    """子进程里跑 build_one，把它的打印收成一段文本交回主进程按序输出。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _, missing = build_one(ck, out, shared_owner=owner)
    return buf.getvalue(), missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ck", choices=list(X.BY_CK))
    ap.add_argument("--all", action="store_true", help="所有就绪角色（moe_chars.active()）")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--only", default=None, help="配合 --all：逗号分隔的角色子集（测试并行用）")
    a = ap.parse_args()
    if not a.ck and not a.all:
        ap.error("给 --ck 或 --all")
    out = Path(a.out_dir) if a.out_dir else None
    # --all 只做**就绪**角色：未就绪的（动画没齐）不进 .asset，贴图写进 mod 也没人引用。
    cks = [a.ck] if a.ck else [c["ck"] for c in X.active()]
    if a.only:
        cks = [ck for ck in cks if ck in set(a.only.split(","))]
    # 角色之间互相独立，按进程并行（2026-10-01：92 人串行十几分钟）。唯一的共享写入是
    # 共用贴图（shared_tex/，多人引用同一张）：先按登记表顺序给每张共用图指定一个「写入者」，
    # 其余角色只登记不写，免得两个进程同时写同一个文件。输出按角色收齐后顺序打印。
    owner = {}
    for ck in cks:
        rep_path = X.report_path(ck)
        if not rep_path.is_file():
            continue
        rep = json.loads(rep_path.read_text(encoding="utf-8"))
        for part in rep["parts"].values():
            for sm in part.get("submeshes", []):
                if sm.get("diffuse"):
                    d = _strip_dup(sm["diffuse"])
                    if MP.shared_game_of(d):
                        owner.setdefault(d, ck)
    jobs = max(1, int(os.environ.get("MOE_BUILD_JOBS", os.cpu_count() or 4)))
    bad = 0
    with ProcessPoolExecutor(min(jobs, len(cks))) as pool:
        for log, missing in pool.map(_build_captured, cks, [out] * len(cks), [owner] * len(cks)):
            print(log, end="", flush=True)
            bad += len(missing)
    if bad:
        raise SystemExit(f"**{bad} 张贴图缺失**")
    print("[CHAR TEXTURES DONE]")


if __name__ == "__main__":
    main()
