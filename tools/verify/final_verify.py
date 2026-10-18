# -*- coding: utf-8 -*-
"""全量上线前校验。每一条都对应过去踩过的一个坑：
  骨数一致      —— 不匹配会让整个 entity 静默不渲染，本地渲图看不见
  头身帧数一致  —— 不一致头身脱节
  共享骨 <2°    —— 合并骨架特有问题（原版头身骨骼不相交所以没这毛病）
  body 零头发   —— 头发只蒙在 head.mesh，写错文件等于白烘
  head 恰 54    —— 少了说明分流漏骨
  接缝 ≈0       —— 循环重播的跳变
  单节角 ≤10.6° —— PMX 原作者的限制
"""
import sys, math, re, statistics as st
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / _d) for _d in ("lib", "anim")]
import moe_paths as MP        # 分层目录的路径解析
import moe_out as O           # find_all 在这里（此前误写成 MP.find_all，整理前就跑不起来）
import retarget_vanilla_anim as RV
# 产物按来源游戏拆在多个模型子 mod；不要再指向退役的单体目录。
# `moe_out.find_all()` 返回 (mod_id, Path)，同时覆盖 gfl2 / endfield。

# ---- 按角色参数化。骨数**不写死**：从该角色自己的 head.mesh 实读，
# 再要求所有 .anim 与它一致。写死 189 只能验芙洛伦，洛茜是 473。
_av = sys.argv[1:]
PFX = "moe_"            # 动画/网格文件名前缀，如 moe_lau_
for _i, _a in enumerate(_av):
    if _a == "--prefix" and _i + 1 < len(_av):
        PFX = _av[_i + 1]
HEAD_MESH = MP.find(PFX + "head.mesh")
TORSO_MESH = MP.find(PFX + "torso.mesh")


def _kids(x):
    ch = x.get("_children") or []
    return list(ch.items()) if isinstance(ch, dict) else list(ch)


def mesh_bone_count(mp):
    """从 .mesh 实读骨数，作为该角色的基准。"""
    import pdx_parse
    if not Path(mp).is_file():
        return None
    tree = pdx_parse.parse(Path(mp).read_bytes())
    n = [0]

    def walk(x):
        for _k, c in _kids(x):
            pr = c.get("_props") or {}
            if "ix" in pr and "tx" in pr:
                n[0] += 1
            walk(c)
    walk(tree)
    return n[0] or None


HEAD_NBONE = mesh_bone_count(HEAD_MESH)
TORSO_NBONE = mesh_bone_count(TORSO_MESH)
if HEAD_NBONE is None or TORSO_NBONE is None:
    print("!! 找不到 head/torso mesh，无法确定基准骨数")
    sys.exit(2)
print("[基准] " + PFX + "* head=" + str(HEAD_NBONE)
      + " / torso=" + str(TORSO_NBONE) + " 骨（分别从 mesh 实读）")

CLOTH = re.compile(r"Skirt|Chest|Bust|Butt")  # 与 PHYSICS_GROUPS 一致，不含 Hip（是腿骨）

def ang(a, b):
    d = abs(sum(x*y for x, y in zip(RV.q_norm(a), RV.q_norm(b))))
    return math.degrees(2*math.acos(max(-1.0, min(1.0, d))))

def load(p):
    m, bs, sm = RV.load_anim(p)
    F, ts, qs = RV.unpack_samples(m, bs, sm)
    return F, ts, qs, {b["name"]: b for b in bs}, bs

# body 基名 -> 配到它的**正名** head 基名。用于共享骨/帧数比较取对方文件。
# 一个 body 可能被多个 head 配到（变体），取字典序最小的作正名，避免
# 同名比较把正常配对误报成头身不同步。
import moe_anim_catalog as CAT
_pair = CAT.pair_head_to_torso(CAT.Catalog("torso").bases)
PAIR_REV = {}
for _hb, _tb in sorted(_pair.items()):
    if not _tb:
        continue
    _bkey = _tb[len("body_"):] if _tb.startswith("body_") else _tb
    _hkey = _hb[len("head_"):] if _hb.startswith("head_") else _hb
    # **同名优先**：body_X 若有 head_X 配到它，正名就是 X。只在没有同名
    # 配对时才收字典序最小的变体（如 body_..._start 只被 ..._start_var 配到）。
    if _hkey == _bkey or _bkey not in PAIR_REV:
        PAIR_REV[_bkey] = _hkey

issues = []
hang, cang, seams = [], [], []
n_head_hair = n_body_cloth = 0
heads = sorted(p for _mid, p in O.find_all(PFX + "head_*.anim"))
bodies = sorted(p for _mid, p in O.find_all(PFX + "body_*.anim"))
for hp in heads:
    F, ts, qs, rest, bs = load(hp)
    if len(bs) != HEAD_NBONE:
        issues.append(f"{hp.name}: 骨数 {len(bs)}")
    hair = [x for x in qs if "Hair" in x]
    if not hair:
        continue
    n_head_hair += 1
    if len(hair) != 54:
        issues.append(f"{hp.name}: 头发通道 {len(hair)}")
    for x in hair:
        hang.append(max(ang(qs[x][f], rest[x]["q"]) for f in range(0, F, 10)))
    seams.append(max(ang(qs[x][F-1], qs[x][0]) for x in hair))
for bp in bodies:
    F, ts, qs, rest, bs = load(bp)
    if len(bs) != TORSO_NBONE:
        issues.append(f"{bp.name}: 骨数 {len(bs)}")
    if [x for x in qs if "Hair" in x]:
        issues.append(f"{bp.name}: body 残留头发通道")
    cl = [x for x in qs if CLOTH.search(x)]
    if len(cl) > 2:
        n_body_cloth += 1
    for x in cl:
        cang.append(max(ang(qs[x][f], rest[x]["q"]) for f in range(0, F, 10)))
    # **按真实配对取 head，不能用同名**。原版一个 body 可以配多个 head
    # （head_..._start_var2 的脊椎取自 body_..._start_var），同名比较会把
    # 正常的配对报成「共享骨差 23°」。配对表从 .asset 的 state 表实读。
    hb = PAIR_REV.get(bp.stem[len("moe_body_"):])
    hp = MP.find(f"moe_head_{hb}.anim" if hb
              else bp.name.replace("moe_body_", "moe_head_", 1), missing_ok=True)
    if hp.exists():
        F2, ts2, qs2, rest2, bs2 = load(hp)
        if F != F2:
            issues.append(f"{bp.stem}: 帧数 body{F}/head{F2}")
        for k in sorted(set(qs) & set(qs2)):
            if "Hair" in k:
                continue
            d = max(ang(qs2[k][f], qs[k][f]) for f in range(0, min(F, F2), 10))
            if d > 2.0:
                issues.append(f"{bp.stem}: 共享骨 {k} 差 {d:.1f}°")
print(f"head {len(heads)} 个（带头发 {n_head_hair}）"
      f"  body {len(bodies)} 个（带衣物物理 {n_body_cloth}）")
print(f"问题 {len(issues)}")
for i in issues[:12]:
    print("  ", i)
if hang:
    print(f"头发单节角 中位 {st.median(hang):.1f}°  "
          f"P99 {sorted(hang)[int(len(hang)*.99)]:.1f}°  最大 {max(hang):.1f}°")
if cang:
    print(f"衣物单节角 中位 {st.median(cang):.1f}°  最大 {max(cang):.1f}°")
if seams:
    print(f"循环接缝   最大 {max(seams):.4f}°")

# ---------------------------------------------------------------- mesh 侧判据
# 这两条查的是 .mesh 而不是 .anim，且**本地渲图永远看不出来**
# （tools/render/render_anim_skinned.py 自己会归一化权重，数据错了画面还是对的）。
# 必须直接量数据或进游戏截图。


def mesh_weight_and_ownership(mp, part):
    """返回 (权重和异常顶点数, 总顶点数, [(骨名, 主导顶点数)])。

    权重和 ≠ 1.0：LBS 位移与权重和成正比，和为 2.0 就是位移翻倍，
    顶点被甩到两倍距离外 —— 视觉上是从脸里刺出的放射状细针。
    洛茜实测 552 个顶点（2.62%）和为 1.24~2.00、中位正好 2.0000，
    根因是表情烘焙归一化只统计 fx 骨、漏掉了重叠的牙齿/舌头原生骨。

    head 里的躯干骨：head entity 只播 <pfx>head_*.anim，那份 anim 不含
    躯干骨的动画通道，所以这些顶点永远停在 rest，torso 一动就整块错位
    （游戏里表现为袜环/内衣/飘带与身体脱节，**日志零报错**）。
    """
    import pdx_parse
    import moe_material_contract as MC
    if not Path(mp).is_file():
        return None
    tree = pdx_parse.parse(Path(mp).read_bytes())
    order, pa = [], {}

    def walk(x):
        for k, c in _kids(x):
            pr = c.get("_props") or {}
            if "ix" in pr and "tx" in pr:
                order.append(k)
                pa[k] = pr["pa"][1][0] if "pa" in pr else -1
            walk(c)
    walk(tree)
    parent = {b: (order[pa[b]] if 0 <= pa[b] < len(order) else None)
              for b in order}

    n_bad_sum, n_vert, dom = 0, 0, {}
    sums = []
    for _, obj in _kids(tree):
        for _mn, mn in _kids(obj):
            for _sm, cn in _kids(mn):
                pr = cn.get("_props") or {}
                if "p" not in pr:
                    continue
                nv = len(pr["p"][1]) // 3
                for n4, gn in _kids(cn):
                    gp = gn.get("_props") or {}
                    if n4 != "skin" or "bones" not in gp:
                        continue
                    npb = gp["bones"][1][0]
                    ix, w = gp["ix"][1], gp["w"][1]
                    for v in range(nv):
                        n_vert += 1
                        tot, best = 0.0, (-1.0, None)
                        for sl in range(npb):
                            bi = ix[v * npb + sl]
                            if bi < 0 or bi >= len(order):
                                continue
                            ww = w[v * npb + sl]
                            tot += ww
                            if ww > best[0]:
                                best = (ww, order[bi])
                        if abs(tot - 1.0) > 1e-3:
                            n_bad_sum += 1
                            sums.append(tot)
                        if best[1]:
                            dom[best[1]] = dom.get(best[1], 0) + 1
    bad_own = MC.check_skin_ownership(part, dom, bone_parent=parent)
    return n_bad_sum, n_vert, sums, [(b, dom[b]) for b, _ in bad_own]


print()
for _mp, _part in ((HEAD_MESH, "head"), (TORSO_MESH, "torso")):
    _r = mesh_weight_and_ownership(_mp, _part)
    if _r is None:
        print(f"  {Path(_mp).name}: 文件缺失")
        continue
    _nb, _nv, _sums, _own = _r
    _tag = "OK" if _nb == 0 else "!! 异常"
    _extra = ""
    if _sums:
        _ss = sorted(_sums)
        _extra = (f"  范围 {_ss[0]:.4f}~{_ss[-1]:.4f} "
                  f"中位 {_ss[len(_ss) // 2]:.4f}")
    print(f"  {Path(_mp).name}: 权重和异常 {_nb}/{_nv} 顶点  {_tag}{_extra}")
    if _own:
        _n = sum(c for _, c in _own)
        print(f"     !! {len(_own)} 根躯干骨在 {_part} 里主导了 {_n} 个顶点 "
              f"—— 这些几何会僵在 rest（见 STEP 2c）")
        for _b, _c in _own[:6]:
            print(f"        {_b}  {_c} 顶点")
    elif _part == "head":
        print(f"     蒙皮归属干净：head 里没有躯干骨主导的顶点")
