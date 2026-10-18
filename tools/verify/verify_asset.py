# -*- coding: utf-8 -*-
"""进游戏前的两条硬判据（都是本地渲图看不见、只会在日志里报的问题）。

1. **每个 state 引用的 animation id 必须在该 entity 自己的动画集里**
   缺名 → `Unknown animation state` → 僵在上一姿势。
   注意 entity 与动画集的对应关系要经 pdxmesh 走一遍：
       entity.pdxmesh → pdxmesh.import.name → 动画集
2. **每条 animation 指向的 .anim 必须存在，骨数与自己那件 mesh 相等，
   且点名的骨都在那件 mesh 里**
   骨数不等 → 该 mesh 上**每一条**动画被拒播，人物僵住
   （见 [[ck3-bone-count-mismatch-kills-entity]]）。
   比对必须**按 entity**，不能按动画集：一个集合里 head/torso 两类动画
   都在，绑的是两件不同 mesh；拿整集去比 reference_skeleton（=torso），
   head 的脸部 fx 骨会被误报成「mesh 里没有」。
"""
import collections
import io
import pathlib
import re
import subprocess
import sys

# 从任何 cwd 跑都能找到 tools/ 下的模块（原先写死 "tools"，只有在仓库根
# 目录跑才对；这个脚本现在住在 tools/verify/ 下，更要按自身位置推）。
_TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_TOOLS / "lib"), str(_TOOLS / "anim")]
import pdx_parse as PP                                       # noqa: E402

import moe_paths as MP                                        # noqa: E402

# 每份模型包都有独立且不同名的 .asset。无参数时逐份拉起本脚本，避免全局
# BASE 在校验中途切换，也让每份结果有清晰边界。
if "--asset" not in sys.argv:
    assets = MP.assets()
    if not assets:
        sys.exit("一份 .asset 都没有 —— 先跑 python tools/build_all.py --no-sync")
    failed = 0
    for i, asset in enumerate(assets, 1):
        print(f"\n{'=' * 66}\n[{i}/{len(assets)}] {asset}\n{'=' * 66}")
        r = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve()),
                            "--asset", str(asset)])
        failed += r.returncode != 0
    sys.exit(1 if failed else 0)

_ai = sys.argv.index("--asset")
if _ai + 1 >= len(sys.argv):
    sys.exit("--asset 后缺路径")
ASSET = pathlib.Path(sys.argv[_ai + 1]).resolve()
BASE = ASSET.parent
txt = io.open(ASSET, encoding="utf-8-sig").read()


def blocks(kind):
    out = []
    for m in re.finditer(kind + r"\s*=\s*\{", txt):
        i, d = m.end(), 1
        while i < len(txt) and d:
            d += (txt[i] == "{") - (txt[i] == "}")
            i += 1
        out.append(txt[m.end():i - 1])
    return out


sets = {}
for b in blocks("skeletal_animation_set"):
    nm = re.search(r'name\s*=\s*"([^"]+)"', b).group(1)
    ref = re.search(r'reference_skeleton\s*=\s*"([^"]+)"', b)
    sets[nm] = {
        "ref": ref.group(1) if ref else None,
        "anims": dict(re.findall(r'id\s*=\s*"([^"]+)"\s+type\s*=\s*"([^"]+)"', b)),
    }

meshes = {}
for b in blocks("pdxmesh"):
    nm = re.search(r'name\s*=\s*"([^"]+)"', b).group(1)
    f = re.search(r'file\s*=\s*"([^"]+)"', b)
    st = re.search(r'type\s*=\s*skeletal_animation_set\s+name\s*=\s*"([^"]+)"', b)
    meshes[nm] = {"file": f.group(1) if f else None,
                  "set": st.group(1) if st else None}

print(f"动画集 {len(sets)}  pdxmesh {len(meshes)}")

bad, warn, ents = [], [], []
# ---- 判据 1
for b in blocks("entity"):
    nm = re.search(r'name\s*=\s*"([^"]+)"', b)
    pm = re.search(r'pdxmesh\s*=\s*"([^"]+)"', b)
    if not nm or not pm:
        continue
    ent, mesh = nm.group(1), pm.group(1)
    sn = meshes.get(mesh, {}).get("set")
    have = set((sets.get(sn) or {}).get("anims") or {})
    used = set(re.findall(r'state\s*=\s*\{[^}]*animation\s*=\s*"([^"]+)"', b))
    miss = sorted(used - have)
    print(f"  {ent:26} mesh={mesh:22} set={sn:16} "
          f"state 引用 {len(used):4} 缺 {len(miss)}")
    if miss:
        bad.append(f"{ent} 缺 animation id: {miss[:4]}")
    ents.append((ent, mesh, sn, used - set(miss)))


def bones_of_mesh(mesh_name):
    f = meshes.get(mesh_name, {}).get("file")
    out = set()
    if f and (BASE / f).is_file():
        for pp, _n, _k, _v in PP.walk(PP.parse((BASE / f).read_bytes())):
            if "/skeleton/" in pp:
                out.add(pp.split("/skeleton/")[1].split("[")[0])
    return out


def bones_of_anim(p):
    j, out = None, set()
    for pp, name, _k, v in PP.walk(PP.parse(p.read_bytes())):
        if pp == "/info" and name == "j":
            j = v[0]
        elif pp.startswith("/info/") and pp.count("/") == 2:
            out.add(pp.split("/info/", 1)[1].split("[")[0])
    return j, out


# ---- 判据 2：按 entity 逐一比对（不是按动画集）
for ent, mesh, sn, used in ents:
    mb = bones_of_mesh(mesh)
    anims = (sets.get(sn) or {}).get("anims") or {}
    miss_f, wrong, orphan = [], collections.Counter(), collections.Counter()
    for aid in sorted(used):
        ap = BASE / anims[aid]
        if not ap.is_file():
            miss_f.append(anims[aid])
            continue
        j, ab = bones_of_anim(ap)
        wrong[j] += 1
        for b in ab - mb:
            orphan[b] += 1
    print(f"  {ent:26} mesh骨={len(mb):4} 动画 {len(used):4} "
          f"缺文件 {len(miss_f)}  anim 骨数 {dict(wrong)}  "
          f"**mesh 里不存在的骨 {len(orphan)}**")
    if miss_f:
        bad.append(f"{ent} 缺 .anim 文件 {len(miss_f)} 个: {miss_f[:3]}")
    # **骨数必须相等**，这是引擎明说的，不是我推的：
    #   [E][pdx_anim.cpp]: Animation "moe_head_stayback_loop" does not have
    #   the same amount of joints as the mesh it is trying to animate.
    #   ( Animation 189, Mesh 198 )
    # 后果：该 mesh 上**每一条**动画都被拒播
    #   （芙洛伦 torso 7380 次 + head 6864 次 PlayAnimation failed），
    #   人物僵在原地（见 [[ck3-bone-count-mismatch-kills-entity]]）。
    # 我曾把这条放宽成「动画骨是 mesh 骨的子集」，理由是「芙洛伦 198/189
    # 实机正常」—— 那个前提本身是错的，她当时就是坏的，我只是没读到日志。
    # 别再放宽。
    if wrong and any(k != len(mb) for k in wrong):
        bad.append(f"{ent} 骨数不等：mesh {len(mb)} vs anim {dict(wrong)}"
                   f" → 该 mesh 上所有动画都会被拒播")
    if orphan:
        bad.append(f"{ent} 动画点名了 {mesh} 里没有的骨 {len(orphan)} 根: "
                   f"{sorted(orphan)[:6]}")

# ---- 判据 2b：动画集里**没被任何 entity 引用**的 .anim 也要存在
for sn, s in sets.items():
    ref_mesh = meshes.get(s["ref"] or "", {}).get("file")
    miss_f = [r for r in s["anims"].values() if not (BASE / r).is_file()]
    print(f"  {sn:16} ref={s['ref']:22} anim {len(s['anims']):4} "
          f"缺文件 {len(miss_f)}")
    if miss_f:
        bad.append(f"{sn} 缺 .anim 文件 {len(miss_f)} 个: {miss_f[:3]}")

# ---- 判据 3：贴图引用必须是**裸基名**，且基名全局唯一
#
# 引擎对 `gfx/models` 建按基名索引的平表（启动时 debug.log:
# `ThreadedInitTextureLookup found 21203 files`），查表键是基名。任何带 `/`
# 的引用都查不到，报 `Failed to find texture 'per_tex/x.dds'`。
#
# **这个缺陷画面上看不出来**：diffuse 由 .mesh 内嵌的裸名走平表加载、照常
# 命中，只有 .asset 里 meshsettings 覆盖的 specular/normal 静默失效。表现是
# 「贴图正常但没高光、法线不对」，本地渲图与游戏截图都正常。我曾把这 63 条
# 报错当基线噪声放过一次。判据放在这里，让它不能再静默回归。
print()
_refs = sorted(set(re.findall(r'texture_[a-z]+ = "([^"]+)"', txt)))
_slashed = [r for r in _refs if "/" in r or "\\" in r]
print(f"贴图引用 {len(_refs)} 条，带目录 {len(_slashed)} 条")
if _slashed:
    bad.append(f"贴图引用带目录 {len(_slashed)} 条（引擎按基名查平表，"
               f"必查不到）：{_slashed[:3]}")

# 基名唯一性：撞名会在平表里互相顶掉，取到哪一张不确定。
_seen = {}
for _p in BASE.rglob("*.dds"):
    _seen.setdefault(_p.name, []).append(_p)
_dup = {k: v for k, v in _seen.items() if len(v) > 1}
print(f"mod 内 .dds 基名 {len(_seen)} 个，撞名 {len(_dup)} 个")
if _dup:
    bad.append(f"贴图基名撞车 {len(_dup)} 个（平表按基名索引，会互相顶掉）："
               f"{list(_dup)[:3]}")

# 与原版撞名同样致命 —— 原版那张会顶掉我们的。
_GM = pathlib.Path(r"E:\SteamLibrary\steamapps\common\Crusader Kings III"
                   r"\game\gfx\models")
if _GM.is_dir():
    _van = {p.name for p in _GM.rglob("*.dds")}
    _clash = sorted(set(_seen) & _van)
    print(f"与原版基名冲突 {len(_clash)} 个")
    if _clash:
        bad.append(f"贴图基名与原版冲突 {len(_clash)} 个：{_clash[:3]}")

print()
if warn:
    print("警告（不拦，但确实是缺陷）：")
    for x in warn:
        print("  -", x)
    print()
if bad:
    print("**不能进游戏**：")
    for x in bad:
        print("  -", x)
    sys.exit(1)
print("两条判据全过 —— 可以进游戏")
