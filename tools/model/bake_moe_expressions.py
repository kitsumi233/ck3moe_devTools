"""把 shapekey 表情烘焙成 CK3 能吃的骨骼动画。Blender 后台脚本。

用法:
  blender.exe -b work/blenderProj/florence_poc.blend --python tools/model/bake_moe_expressions.py

为什么需要烘焙这一步（已实测确认，不是猜测）:
  CK3 的 .anim 只有骨骼通道。解析全部 1995 个原版 .anim（0 失败），sa 通道
  组合只有 tq/空/t/q/ts/s/tqs/qs，samples 属性名只有 t/q/s；全 game/gfx 里
  morph_animation / blend_shape_animation / animated_blend 零命中。
  .asset 的 blend_shape 是 DNA 驱动的捏脸滑块（male_bs_*_min/_max 成对），
  不是时间轴；additive_animation 40 项全是 .anim——连 eye_shut（闭眼）都是。
  所以 shapekey 效果虽好，但不能作为交付格式。

核心数学: 单骨线性混合蒙皮等价于形态键位移
  形态键 S 给顶点 i 的位移 d_i。取位移最大的顶点方向为骨平移 T = d_max。
  令权重 w_i = dot(d_i, T) / |T|²，裁剪到 [0,1]。
  蒙皮结果 = w_i · T，对 d_i 平行于 T 的顶点精确，其余是最小二乘投影。
  强度 s 时骨平移 s·T，顶点得 w_i·s·T = s·d_i —— 强度线性正确。

  贴花（腮红/怒气标记/泪滴）是纯平移：全部 d_i 相同，于是 w_i ≡ 1，
  烘焙是**精确**的而非近似。这类元素反而最容易搬。

  权重归一化不破坏 rest 姿态: 顶点 = (1-w)·head + w·proxy，两者 bind 矩阵
  都由同一 rest 姿态算出，rest 时结果不变；proxy 平移 T 时顶点移动 w·T。
  且 proxy 是 bn_m_head 的子骨，头部整体运动照常继承。

共享形态键的处理（关键设计）:
  多个语义槽可能解析到同一形态键（mouth_smile_l/r 都 → 口角上げ）。
  蒙皮权重写死在 .mesh 里、不能随动画变，所以不能让两根骨各自用满权重
  —— 那样对称表情会叠加成 2 倍。
  做法: 每个语义槽一根骨（骨名固定、跨模型可复用），共享同一形态键的
  N 个槽把权重均分 1/N。两骨各出一半，合起来正好等于完整形态键。
  代价是单侧驱动会两边各半，但形态键本身就是对称的，做不出单侧，无可避免。

坐标空间: 代理骨建成世界轴对齐（head=c, tail=c+(0,L,0), roll=0），
  于是 pose bone 的 location 直接等于世界位移，不需要额外换基。
  父骨 bn_m_head 的旋转会带着偏移一起转，这正是想要的（贴花跟着头动）。
"""
import json
import hashlib
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector, Quaternion

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import moe_paths as MP    # noqa: E402  资源路径解析（子 mod 感知）
import moe_expression_contract as C  # noqa: E402
from moe_expression_fit import fit_translations  # noqa: E402

OUT_DIR = ROOT / "work" / "moe_anim_bake"
# 单体目录已退役（2026-08-29）：产物按子 mod 直写 mod/<mod_id>/。
# MP.BASE 解析到第一个存在的模型包；**定位具体文件用 MP.find()**，
# 它会遍历全部子 mod（flo 在 gfl2 包、lau/per/eld 在 endfield 包）。
MOD_DIR = MP.BASE
OUT_BLEND = ROOT / "work" / "blenderProj" / "moe_expression_rig.blend"
SOURCE_BLEND = bpy.data.filepath
PDX_ADDON = "bl_ext.user_default.io_pdx_mesh"

# 不同角色跑本脚本必须落到各自独立的暂存目录 —— OUT_DIR 默认指向芙洛伦的
# work/moe_anim_bake/，里面已有她烘好的 moe_head.mesh / moe_idle_calm.anim
# 等产物。洛茜的 blend 用的是**同样的对象名**（moe_head/moe_torso），如果
# 直接跑会静默覆盖芙洛伦的产物。用 `-- --out-dir <dir> --out-blend <path>`
# 传角色专属目录。
if "--" in sys.argv:
    _argv = sys.argv[sys.argv.index("--") + 1:]
    if "--out-dir" in _argv:
        OUT_DIR = Path(_argv[_argv.index("--out-dir") + 1])
    if "--out-blend" in _argv:
        OUT_BLEND = Path(_argv[_argv.index("--out-blend") + 1])

# ---- 2026-09-29 面部毛刺修正（已定为默认：cap 4 / min_fit 0.01 / protect-lid）----
# 27 个角色的 fx 最大平移由 192 单位降到 < 9，实机预览通过后合入主工具。
# --fx-translation-cap K : 每个形态键的 fx 骨平移 ≤ K × 该键最大源位移（岭回归收缩）
#                          col mouth_up 曾 27→192 单位、mis 29/23 互相抵消，混合时甩针
# --fx-min-fit-weight W  : 该键 fx 预算 < W 的顶点不参与平移求解（仍参与权重更新）
# --fx-no-protect-lid    : 关掉「3 格截断时睫毛/眼皮顶点额外保住 eye_squint 槽」
# --fx-legacy            : 三项全关，复现修正前的产物（逐位对照用）
# --fx-lateral-guard     : 单侧槽（_l/_r）位移全落在对侧脸（源模型左右键复制错）时不建骨。
#                          默认关：它把 l/r_lid_wide 并成一根骨，head/torso 骨数会对不上
FX_CAP = 4.
FX_MIN_FIT_W = .01
FX_PROTECT_LID = True
FX_LATERAL_GUARD = False
if "--" in sys.argv:
    if "--fx-legacy" in _argv:
        FX_CAP, FX_MIN_FIT_W, FX_PROTECT_LID = None, 0., False
    if "--fx-translation-cap" in _argv:
        FX_CAP = float(_argv[_argv.index("--fx-translation-cap") + 1])
    if "--fx-min-fit-weight" in _argv:
        FX_MIN_FIT_W = float(_argv[_argv.index("--fx-min-fit-weight") + 1])
    if "--fx-protect-lid" in _argv:
        FX_PROTECT_LID = True
    if "--fx-no-protect-lid" in _argv:
        FX_PROTECT_LID = False
    FX_LATERAL_GUARD = "--fx-lateral-guard" in _argv
# --mouth-open-key NAME : 候选 A/B 用，强制 mouth_open 槽绑定到指定形态键（如 あ２）。
#   朝晖 あ３ 位移 2.04 + 口角上げ 被 SLOT_GAIN_MAX 推满 1.8，laugh 被报「吓人」。
MOUTH_OPEN_KEY = None
if "--" in sys.argv and "--mouth-open-key" in _argv:
    MOUTH_OPEN_KEY = _argv[_argv.index("--mouth-open-key") + 1]

# moe_clothes 必须也在这里：它自己没有表情形态键（衣服不做表情），但
# **骨架必须与 head/torso 完全一致**。本脚本会往骨架里加 22 根 fx 代理骨，
# 只导 head/torso 的话衣服还是 167 骨，而 .anim 是 189 骨 —— 骨序不匹配，
# 引擎按索引取骨就会取错，表现为衣服乱飞或整件不动。
# （同样的坑在 work/florence_poc/*.mesh 上踩过：那是 167 骨的旧产物，
#  一整轮渲图都用的它，与部署的 189 骨不是一回事。）
MESH_OBJECTS = ["moe_head", "moe_torso", "moe_clothes"]
PROXY_BONE_LEN = 1.0
MIN_DISP = 1e-5          # 小于此位移视为未动
WEIGHT_EPS = 1e-4        # 小于此权重不写入顶点组

log_lines = []


def log(msg):
    print(msg)
    log_lines.append(str(msg))


def section(title):
    log("")
    log("=" * 70)
    log(title)
    log("=" * 70)


# ================================================================ 0. 准备
section("STEP 0: 环境")
try:
    bpy.ops.preferences.addon_enable(module=PDX_ADDON)
    log(f"  io_pdx_mesh 已启用 ({PDX_ADDON})")
except Exception as e:
    log(f"  !! io_pdx_mesh 启用失败: {e}")

OUT_DIR.mkdir(parents=True, exist_ok=True)

arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
if not arms:
    raise SystemExit("找不到骨架")
rig = max(arms, key=lambda o: len(o.data.bones))
log(f"  骨架 {rig.name}: {len(rig.data.bones)} 骨")

meshes = [bpy.data.objects.get(n) for n in MESH_OBJECTS]
meshes = [m for m in meshes if m is not None]
for m in meshes:
    n_sk = len(m.data.shape_keys.key_blocks) if m.data.shape_keys else 0
    log(f"  网格 {m.name}: {len(m.data.vertices)} 顶点, {n_sk} 形态键")

if C.PROXY_PARENT not in rig.data.bones:
    raise SystemExit(f"缺少父骨 {C.PROXY_PARENT}")


# ================================================================ 1. 绑定层
section("STEP 1: 解析形态键绑定")
# 以 moe_head 的键表为准探测命名体系（脸部表情都在头部网格上）
head = bpy.data.objects.get("moe_head") or meshes[0]
key_names = [k.name for k in head.data.shape_keys.key_blocks] if head.data.shape_keys else []
scheme, hits = C.detect_naming_scheme(key_names)
bindings, missing = C.resolve_bindings(key_names, scheme)
log(f"  命名体系 = {scheme}（{hits}/{len(C.SEMANTIC_SLOTS)} 槽命中）")
log(f"  绑定 {len(bindings)} 槽，缺失 {len(missing)} 槽: {', '.join(missing) if missing else '无'}")
if MOUTH_OPEN_KEY:
    if MOUTH_OPEN_KEY not in key_names:
        raise SystemExit(f"--mouth-open-key {MOUTH_OPEN_KEY} 不在模型形态键里")
    log(f"  !! mouth_open 强制改绑: {bindings.get('mouth_open')} → {MOUTH_OPEN_KEY}")
    bindings["mouth_open"] = MOUTH_OPEN_KEY
    if "mouth_open" in missing:
        missing.remove("mouth_open")

# ---- 单侧键的侧别校验（--fx-lateral-guard）----
# 寇尔芙的 びっくり右 与 びっくり左 逐顶点完全相同（位移全在 +X = 角色左侧），
# 于是 eye_wide_r 的骨刷到了左眼上：左眼顶点挂着 r_lid_wide，右眼睁眼时左眼
# 睫毛被拽走、3 格截断又挤掉了 lid_smile —— 笑眼时睫毛不跟眼皮走。
# 世界 +X 是角色自己的左（ウィンク 位移全在 x>0，四个样本一致）。
lateral_rebound = {}
if FX_LATERAL_GUARD and head.data.shape_keys:
    _kb = head.data.shape_keys.key_blocks
    _mw = head.matrix_world

    def _side_fraction(key):
        """该键位移 >0.05 的顶点里，落在角色左侧（世界 +X）的比例；无位移返回 None。"""
        if key not in _kb:
            return None
        n = l = 0
        for i, (b, k) in enumerate(zip(_kb["Basis"].data, _kb[key].data)):
            if (k.co - b.co).length > 0.05:
                n += 1
                l += (_mw @ b.co).x > 0
        return (l / n) if n else None

    _table = C.SHAPEKEY_ALIASES.get(scheme, {})
    for slot in sorted(bindings):
        if not slot.endswith(("_l", "_r")):
            continue
        want_left = slot.endswith("_l")
        frac = _side_fraction(bindings[slot])
        if frac is None or (frac > 0.9 if want_left else frac < 0.1) or 0.1 <= frac <= 0.9:
            continue
        # 整个键落在对侧：改用合同里下一个候选（通常是双侧键），双侧键时配对槽一起改绑
        cands = _table.get(slot) or []
        cands = [cands] if isinstance(cands, str) else cands
        alt = next((c for c in cands if c != bindings[slot] and c in _kb
                    and (_side_fraction(c) or 0) > 0.1 and (_side_fraction(c) or 1) < 0.9), None)
        partner = slot[:-2] + ("_r" if want_left else "_l")
        if alt:
            lateral_rebound[slot] = (bindings[slot], alt)
            bindings[slot] = alt
            if partner in bindings and alt in (_table.get(partner) or []):
                lateral_rebound[partner] = (bindings[partner], alt)
                bindings[partner] = alt
        else:
            lateral_rebound[slot] = (bindings.pop(slot), None)
            missing.append(slot)
    for s, (old, new) in sorted(lateral_rebound.items()):
        log(f"  !! 侧别校验: {s} 绑定 {old} 位移在对侧脸 → " + (f"改绑 {new}" if new else "移除该槽"))

# 本次要做的表情实际用到哪些槽
used_slots = set()
for name in C.EXPRESSIONS:
    for _, d in C.EXPRESSIONS[name]:
        used_slots.update(d)
# 视线槽不是形态键，走独立的眼骨平移通道（见 STEP 3b），不进 bindings
used_slots.discard(C.EYE_LOOK_SLOT)
active_slots = sorted(s for s in used_slots if s in bindings)
skipped = sorted(s for s in used_slots if s not in bindings)
log(f"  表情用到 {len(used_slots)} 槽，可烘焙 {len(active_slots)}，跳过 {len(skipped)}")
if skipped:
    log(f"    跳过（模型无对应键）: {', '.join(skipped)}")

# 共享同一形态键的槽要均分权重，否则对称表情叠加成 2 倍
share_count = {}
for s in active_slots:
    k = bindings[s]
    share_count[k] = share_count.get(k, 0) + 1
shared = {k: n for k, n in share_count.items() if n > 1}
if shared:
    log("  共享形态键（权重按数量均分）:")
    for k, n in sorted(shared.items()):
        owners = [s for s in active_slots if bindings[s] == k]
        log(f"    {k} ← {n} 槽 {owners}，各占 1/{n}")


# ================================================================ 2. 测量位移场
section("STEP 2: 测量形态键位移场")


def _head_sub_names():
    out = set()
    stack = [rig.data.bones[C.PROXY_PARENT]]
    while stack:
        b = stack.pop()
        out.add(b.name)
        stack.extend(b.children)
    return out


_HEAD_SUB = _head_sub_names()


def measure(obj, keyname):
    """返回 (moved_indices, deltas, T, kind)。T 为最大位移向量。

    只收 head 子树带权的顶点：有的 PMX（鸣潮 忌炎/守岸人/相里要/女漂泊者/阿布）把
    口角下げ/笑い 等表情键也做在了鞋上（733 个踝/膝顶点，head 权重为 0）。不过滤的话
    这些顶点被刷上 fx 骨（挂在 head 下），脚踝就跟着脸走、不跟腿（2026-10-14）。"""
    sk = obj.data.shape_keys
    if not sk or keyname not in sk.key_blocks:
        return None
    basis = sk.key_blocks["Basis"].data
    kb = sk.key_blocks[keyname].data
    gname = {g.index: g.name for g in obj.vertex_groups}
    verts = obj.data.vertices
    idxs, deltas = [], []
    tmax, imax = 0.0, -1
    for i in range(len(basis)):
        if not any(g.weight > WEIGHT_EPS and gname.get(g.group) in _HEAD_SUB for g in verts[i].groups):
            continue
        d = kb[i].co - basis[i].co
        m = d.length
        if m > MIN_DISP:
            idxs.append(i)
            deltas.append(d.copy())
            if m > tmax:
                tmax, imax = m, len(deltas) - 1
    if not idxs:
        return None
    T = deltas[imax].copy()
    kind = C.classify_shapekey(len(idxs), tmax)
    return idxs, deltas, T, kind


# slot -> {"T": 全网格统一主方向, "objs": {obj_name: (idxs, deltas, kind)}}
# T 必须全网格统一: 一个槽只有一根骨、骨只能有一个平移量，若各网格各用自己的 T
# 算权重，非主网格（torso）就会拿着错误的参考方向刷权重，位移方向偏掉。
slot_data = {}
for slot in active_slots:
    keyname = bindings[slot]
    per_obj = {}
    for obj in meshes:
        r = measure(obj, keyname)
        if r:
            per_obj[obj.name] = r
    if not per_obj:
        log(f"  !! {slot} ({keyname}) 在所有网格上都无位移，跳过")
        continue
    # 取跨网格位移最大的那个顶点方向作为该槽唯一的 T
    T = max((r[2] for r in per_obj.values()), key=lambda v: v.length).copy()
    objs = {on: (idxs, deltas, kind) for on, (idxs, deltas, _T, kind) in per_obj.items()}
    slot_data[slot] = {"T": T, "objs": objs}
    parts = [f"{on}: {len(i)}顶点 {k}" for on, (i, _d, k) in objs.items()]
    log(f"  {slot:<18} {keyname:<10} |T|={T.length:.3f}  {'; '.join(parts)}")

active_slots = [s for s in active_slots if s in slot_data]
log(f"  有效槽 {len(active_slots)}")


# ================================================================ 3. 建代理骨
section("STEP 3: 创建代理骨")
bpy.ops.object.select_all(action="DESELECT")
rig.select_set(True)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
eb = rig.data.edit_bones
parent_eb = eb.get(C.PROXY_PARENT)

# 槽 -> 骨名。用合同里的固定命名，保证跨模型一致。
#
# 两项 2026-08-18 的改动（用户报「表情有点淡」，实测量化后确定的修法，
# 见 tools/verify/measure_expression_fidelity.py 与 contract 里 REVERSE_SLOTS 的注释）:
#
#   1. 双侧形态键合并成一根骨。旧做法 `w/share` 把双侧键的权重除以 2
#      分给 _l/_r 两骨，幅度直接减半，而那三个键恰好是重建率最低的槽。
#      合并后 share 视为 1，权重给满。
#   2. 给「反向丢弃顶点数显著」的槽加一根反向骨，承接 dot(d,T)<=0
#      那批被焊死在 head 上的顶点。
#
# 实测叠加收益（重建率）:
#   eye_squint 37.9% → 98.4%   eye_narrow 42.7% → 99.3%
#   mouth_smile 24.7% → 67.6%  mouth_pucker 40.9% → 96.8%
#   eye_blink 75.8% → 98.4%    mouth_press 54.5% → 85.7%
slot_bone = {}          # 槽 -> 正向骨名（双侧对的两个槽指向同一根）
slot_rev_bone = {}      # 槽 -> 反向骨名（没有反向骨的槽不在表里）
slot_share = {}         # 槽 -> 实际用于刷权重的 share（合并后为 1）
merged_slots = {}       # 记录哪些槽被合并了，写进报告
for slot in active_slots:
    keyname = bindings[slot]
    merged = C.merged_bone_for(slot, bindings, share_count)
    if merged:
        slot_bone[slot] = merged
        slot_share[slot] = 1
        merged_slots[slot] = merged
    else:
        slot_bone[slot] = C.SEMANTIC_SLOTS[slot]
        slot_share[slot] = share_count.get(keyname, 1)
    if slot in C.REVERSE_SLOTS:
        rev = C.REVERSE_SLOTS[slot]
        # 双侧合并时反向骨也要合并，否则两个槽各建一根反向骨、又被 /2
        if merged:
            rev = merged + "_rev"
        slot_rev_bone[slot] = rev

if merged_slots:
    log("  双侧形态键合并（权重不再 /2，幅度翻倍）:")
    for s, b in sorted(merged_slots.items()):
        log(f"     {s:<18} → {b}")
if slot_rev_bone:
    log(f"  反向骨 {len(set(slot_rev_bone.values()))} 根（承接逆向运动顶点）")

# 建骨：正向骨 + 反向骨。反向骨与正向骨同位置（骨位置只为可读性，
# 平移驱动不依赖它），所以复用同一个质心。
bone_of_slot_all = {}
for slot in active_slots:
    bone_of_slot_all.setdefault(slot, []).append(slot_bone[slot])
    if slot in slot_rev_bone:
        bone_of_slot_all[slot].append(slot_rev_bone[slot])

def slot_centroid(slot):
    """该槽位移区域的加权质心（仅为可读性，平移驱动不依赖骨位置）。"""
    acc, wsum = Vector((0, 0, 0)), 0.0
    for on, (idxs, deltas, kind) in slot_data[slot]["objs"].items():
        obj = bpy.data.objects[on]
        basis = obj.data.shape_keys.key_blocks["Basis"].data
        for j, i in enumerate(idxs):
            w = deltas[j].length
            acc += obj.matrix_world @ basis[i].co * w
            wsum += w
    c = (acc / wsum) if wsum > 1e-9 else parent_eb.head.copy()
    return rig.matrix_world.inverted() @ c


# 从上一轮的 rig 工作区重烘时，旧 fx 骨与其顶点组会残留：合并规则一变
# （2026-09-05 brow/eye_wide/mouth_frown 改成单骨），旧的 bn_m_fx_l_brow_down
# 仍留在骨架里带着旧权重 —— 骨数虚高、且旧权重参与归一化。所以先把**所有**
# 现存 fx 骨与顶点组清掉，本轮从零建。权重全部由本轮 shapekey 推导重刷。
_stale = [b.name for b in eb if b.name.startswith("bn_m_fx_")]
for nm in _stale:
    eb.remove(eb[nm])
for obj in meshes:
    for vg in [vg for vg in obj.vertex_groups if vg.name.startswith("bn_m_fx_")]:
        obj.vertex_groups.remove(vg)
if _stale:
    log(f"  清掉上一轮残留的 fx 骨 {len(_stale)} 根（及其顶点组），本轮从零建")

created = []
for slot in active_slots:
    for bname in bone_of_slot_all[slot]:
        if bname in eb:
            continue
        c = slot_centroid(slot)
        b = eb.new(bname)
        # 世界轴对齐 + roll=0 → pose location 直接是世界位移，无需换基
        b.head = c
        b.tail = c + Vector((0.0, PROXY_BONE_LEN, 0.0))
        b.roll = 0.0
        b.parent = parent_eb
        b.use_connect = False
        b.use_deform = True
        created.append(bname)

# ---------------------------------------------------------------- 道具挂点骨
# **已移至 add_torso_court_roots() 函数内**
# `bn_l_prop` / `bn_r_prop` 现在只在 torso 导出时添加，与 ground_joint/body_root
# 同一个钩子。head 导出时不含这两根骨，避免 accessory 挂到 head 那份（道具错位）。
#
# 背景：accessory 按骨名挂载，head + torso 两个 entity 都有 prop 骨时引擎挂到
# head 那份；而 head 动画不驱动手臂，骨停在 T 字手位，表现为书飘在体侧。
# 2026-08-29 已确诊过一次（[[ck3-prop-bones-must-be-torso-only]]）但修法未落地，
# 2026-09-03 再次实机遇到。现在 prop 骨只存在于 torso。
PROP_BONES = {"bn_l_prop": ("bn_m_l_wrist", Vector((2.14, -8.17, -2.22))),
              "bn_r_prop": ("bn_m_r_wrist", Vector((-2.14, -8.17, -2.22)))}
prop_made, prop_skip = [], []
# **prop 骨构建已移至 torso 导出前的钩子，此处不再建立**

# ---------------------------------------------------------------- 清理退役的王座锚点骨
# 曾尝试在 bn_m_root 之上插入 `ground_joint -> body_root`，让宫廷管理器按
# 字面骨名找到王座锚点。实机证明这个假设不成立：即使两根骨绑定变换恒等、
# 自身没有动画通道，CK3 仍会对 head/torso 附着实体施加额外根变换，导致正常
# 站姿的身体、头和帽子分离。洛茜/洁尔佩塔以 bn_m_root 为真根时没有该问题。
#
# 这里也负责迁移已生成的工作区：两根骨必须保持零权重，恢复 bn_m_root 为根后
# 删除。王座摆位继续由原版 body_root 动作映射到 bn_m_groove；若仍有宫廷
# 偏移，应在动画/场景合同上解决，不能再污染模型根层级。
RETIRED_ROOT_BONES = ("body_root", "ground_joint")
for obj in meshes:
    for name in RETIRED_ROOT_BONES:
        vg = obj.vertex_groups.get(name)
        if vg is None:
            continue
        weighted = False
        for vert in obj.data.vertices:
            if any(g.group == vg.index and g.weight > WEIGHT_EPS for g in vert.groups):
                weighted = True
                break
        if weighted:
            raise RuntimeError(f"{obj.name}/{name} 意外带蒙皮权重，拒绝删除根骨")

root_eb = eb.get("bn_m_root")
root_removed = []
if root_eb is not None and root_eb.parent is not None \
        and root_eb.parent.name in RETIRED_ROOT_BONES:
    root_eb.parent = None
    root_eb.use_connect = False
for name in RETIRED_ROOT_BONES:
    bone = eb.get(name)
    if bone is not None:
        eb.remove(bone)
        root_removed.append(name)

bpy.ops.object.mode_set(mode="OBJECT")
log(f"  新建 {len(created)} 根代理骨（父骨 {C.PROXY_PARENT}）")
for b in created:
    log(f"    {b}")

# ---- 3b. 视线驱动目标：按 mesh 实读，不写死骨名 ----
# 洛茜的 bn_m_l_eye 自己零蒙皮（虹膜在子骨 瞳_l 上），洁尔佩塔左眼有两条
# 独立链（bn_m_l_eye 与 bn_m_eyeLfJoint）。规则：名字含 EYE_CHAIN_MARKERS、
# 子树内有蒙皮顶点、且没有同样满足条件的祖先（驱动父骨就带动子骨，别双驱）。
# 排除眼睑/睫毛/眉/fx 代理骨。
section("STEP 3b: 视线驱动目标（眼球链实读）")
_skinned = set()
for obj in meshes:
    _gn = {g.index: g.name for g in obj.vertex_groups}
    for v in obj.data.vertices:
        for g in v.groups:
            if g.weight > WEIGHT_EPS:
                _skinned.add(_gn[g.group])
_EXCL = ("lash", "brow", "lid", "fx_", "shadow", "white")


def _subtree(b):
    out = [b]
    for c in b.children:
        out += _subtree(c)
    return out


_cands = []
for b in rig.data.bones:
    nm = b.name.lower()
    if not any(m in nm for m in C.EYE_CHAIN_MARKERS):
        continue
    if any(x in nm for x in _EXCL):
        continue
    if not any(sb.name in _skinned for sb in _subtree(b)):
        continue
    _cands.append(b)
_cand_names = {b.name for b in _cands}
eye_bones = []
for b in _cands:
    anc = b.parent
    shadowed = False
    while anc is not None:
        if anc.name in _cand_names:
            shadowed = True
            break
        anc = anc.parent
    if not shadowed:
        eye_bones.append(b.name)
eye_bones.sort()
if eye_bones:
    log(f"  眼球链 {len(eye_bones)} 根: {eye_bones}")
    for bn in eye_bones:
        _names = {sb.name for sb in _subtree(rig.data.bones[bn])}
        nverts = sum(1 for obj in meshes for v in obj.data.vertices
                     for g in v.groups
                     if obj.vertex_groups[g.group].name in _names
                     and g.weight > WEIGHT_EPS)
        log(f"    {bn:<30} 子树蒙皮顶点 {nverts}")
else:
    log("  !! 没找到眼球链，视线槽将无效")
# prop_made/prop_skip 现在由 add_torso_court_roots() 填充
if root_removed:
    log(f"  删除 {len(root_removed)} 根退役王座锚点骨: {root_removed}")
log(f"  骨架现有 {len(rig.data.bones)} 骨")

# 同骨多槽是**硬错误**，不是警告。第一版当警告放过去了，实际后果:
#   两个槽的形态键位移区域重叠时，刷权重后写的覆盖前写的，于是重叠顶点的
#   权重按 A 的主方向算，却在另一个表情里被 B 的平移量驱动 —— 朝错方向偏。
#   跨表情就会错，「同表情内不并用」规避不了。所以直接拦住。
#
# 唯一放行的例外是 BILATERAL_MERGE 声明的双侧对：那两个槽解析到**同一个
# 形态键**（MMD 的 笑い/口角上げ/真面目 是单个双侧键），是同一处物理形变的
# 左右两半，共用一根骨是它的本来面目，不存在「按 A 的方向算却被 B 驱动」
# 的问题 —— 它们的 T 完全相同，且配方里强度永远相等。
bone_slots = {}
for s in active_slots:
    bone_slots.setdefault(slot_bone[s], []).append(s)
conflict = {}
for b, ss in bone_slots.items():
    if len(ss) <= 1:
        continue
    # 同一个形态键 + 在 BILATERAL_MERGE 里声明过 → 合法合并
    keys = {bindings[s] for s in ss}
    declared = any(set(ss) == {a, b2} for (a, b2) in C.BILATERAL_MERGE)
    if len(keys) == 1 and declared:
        continue
    conflict[b] = ss
if conflict:
    log("  !! 同骨多槽 —— 权重会互相覆盖，必须在 SEMANTIC_SLOTS 里拆成独立骨:")
    for b, ss in sorted(conflict.items()):
        log(f"     {b} ← {ss}")
    raise SystemExit("同骨多槽，拒绝继续烘焙（见上）")
log("  一槽一骨检查通过（双侧合并已放行）")


# ================================================================ 4. 刷权重
section("STEP 4: 计算并写入蒙皮权重")
# w_i = dot(d_i, T)/|T|²，裁剪 [0,1]。
# 这个式子让 w_i·T ≈ d_i：贴花（全体同位移）得 w≡1 精确复现，
# 变形键是沿主方向的投影。
#
# 2026-08-18 起**正负分流**（此前 w<=0 的顶点直接 continue，被焊死在 head 上，
# 是「表情淡」的主因）:
#   dot(d,T) > 0  → 正向骨，权重 w
#   dot(d,T) <= 0 → 反向骨（若该槽有），主方向 T_rev 取这批顶点自己的主成分
# 一个顶点只会落在其中一侧，两根骨的权重区域**不重叠**，所以不存在互相覆盖。
#
# T_rev 用主成分而非 -T：逆向那批顶点的运动方向通常不是正向的严格反号
# （实测 mouth_pucker 残差角 140.4°、mouth_press 30.5°），用 -T 会欠拟合。


def principal_axis(deltas):
    """位移集合的第一主成分。3x3 协方差幂迭代，30 步足够，不引 numpy。"""
    cxx = cxy = cxz = cyy = cyz = czz = 0.0
    for d in deltas:
        cxx += d.x * d.x; cxy += d.x * d.y; cxz += d.x * d.z
        cyy += d.y * d.y; cyz += d.y * d.z; czz += d.z * d.z
    v = max(deltas, key=lambda d: d.length).normalized()
    for _ in range(30):
        n = Vector((cxx * v.x + cxy * v.y + cxz * v.z,
                    cxy * v.x + cyy * v.y + cyz * v.z,
                    cxz * v.x + cyz * v.y + czz * v.z))
        if n.length < 1e-12:
            return v
        v = n.normalized()
    return v


weight_report = {}
slot_rev_T = {}          # 槽 -> 反向骨的平移向量，驱动时要用
for slot in active_slots:
    bname = slot_bone[slot]
    rname = slot_rev_bone.get(slot)
    keyname = bindings[slot]
    share = slot_share[slot]          # 双侧合并后为 1
    T = slot_data[slot]["T"]
    t2 = T.length_squared
    if t2 < 1e-12:
        continue

    # 先跨网格收集逆向顶点，统一求一个 T_rev（同一根骨只能有一个平移量，
    # 各网格各算会让非主网格朝错方向偏 —— 与正向 T 必须全网格统一同理）
    T_rev = None
    if rname:
        neg = [d for (idxs, deltas, _k) in
               (slot_data[slot]["objs"][on] for on in slot_data[slot]["objs"])
               for d in deltas if d.dot(T) <= 0]
        if neg:
            ax = principal_axis(neg)
            mag = max((d.dot(ax) for d in neg), default=0.0)
            if mag > 1e-9:
                T_rev = ax * mag
                slot_rev_T[slot] = T_rev
            else:
                log(f"  {slot:<18} 反向骨 {rname} 不建：逆向顶点 {len(neg)} 个"
                    f"但主成分投影 {mag:.2e} 过小")
        else:
            # 同一份合同、两个模型结果不同（洛茜 口角上げ 无反向骨而洁尔佩塔有）
            # 就是这里：逆向顶点数算出 0。必须打印，不能静默跳过。
            log(f"  {slot:<18} 反向骨 {rname} 不建：dot(d,T)<=0 的顶点为 0")
    r2 = T_rev.length_squared if T_rev else 0.0

    for on, (idxs, deltas, kind) in slot_data[slot]["objs"].items():
        obj = bpy.data.objects[on]
        vg = obj.vertex_groups.get(bname) or obj.vertex_groups.new(name=bname)
        vgr = None
        if T_rev:
            vgr = (obj.vertex_groups.get(rname)
                   or obj.vertex_groups.new(name=rname))
        n_fwd = n_rev = 0
        wmax = rmax = 0.0
        for j, i in enumerate(idxs):
            d = deltas[j]
            p = d.dot(T)
            if p > 0:
                w = min(1.0, p / t2) / share
                if w >= WEIGHT_EPS:
                    vg.add([i], w, "REPLACE")
                    n_fwd += 1
                    wmax = max(wmax, w)
            elif vgr and r2 > 1e-12:
                w = d.dot(T_rev) / r2
                if w > 0:
                    w = min(1.0, w) / share
                    if w >= WEIGHT_EPS:
                        vgr.add([i], w, "REPLACE")
                        n_rev += 1
                        rmax = max(rmax, w)
        weight_report.setdefault(slot, {})[on] = {
            "shapekey": keyname, "kind": kind, "share": share,
            "bone": bname, "rev_bone": rname if T_rev else None,
            "verts": n_fwd, "rev_verts": n_rev,
            "w_max": round(wmax, 4), "rev_w_max": round(rmax, 4),
            "T": [round(v, 5) for v in T],
            "T_rev": [round(v, 5) for v in T_rev] if T_rev else None,
        }
        extra = f" +反向 {n_rev:>4} 顶点 w_max={rmax:.3f}" if n_rev else ""
        log(f"  {slot:<18} {on:<10} {kind:<6} 写入 {n_fwd:>4} 顶点 "
            f"w_max={wmax:.3f} (1/{share}) |T|={T.length:.3f}{extra}")


# ---------------------------------------------------------------- 权重归一化
# **必须做，不是可选的优化。** 上面每个槽独立按 REPLACE 写权重，彼此不知道
# 对方也在写同一个顶点。眉心那圈顶点同时属于 brow_inner / brow_down /
# brow_up 三个槽，各自都算出 ~1.0，再叠上 bn_m_head 的 1.0：
#
#     实测最坏顶点 bn_m_head 1.0 + l_brow_down 1.0 + brow_inner 1.0
#                  + l_brow_up 0.942  =  权重和 3.942
#
# LBS 按权重线性叠加位移，权重和 3.9 就是**位移放大 3.9 倍**。实测顶点被推到
# 9.32 单位，而真实形态键位移只有 0.24~2.09 —— 这就是 head 撕裂的真因
# （表情 53.8%~105.3% 跨骨边形变，idle 0.00%）。
#
# 归一化方式不能是「fx 之间按比例缩放」: 每个表情在运行期是**单独驱动**的，
# 一个表情播放时其他 fx 骨停在 rest，按比例缩放会让单表情幅度变小。
# 正确做法是**限制总量、余量归 bn_m_head**:
#   fx 总和 > 1 时整体缩到 1（保持各槽相对比例，即形状不变）；
#   bn_m_head 补 1 - fx总和，让每个顶点权重和恰好 1.0。
# 这样单表情满驱动时顶点位移 = 形态键位移，不放大；
# 且 fx 覆盖不到的顶点仍 100% 跟随头骨。
section("STEP 4b: 权重归一化（权重和必须为 1.0）")
# 必须把**反向骨也算进来**：它们和正向骨一样是 fx 骨、一样参与 LBS 叠加。
# 漏掉的话反向骨权重不进归一化，顶点权重和会超过 1，位移被放大。
proxy_names = set(slot_bone.values()) | set(slot_rev_bone.values())

# ---- 4b-0: PDX 每顶点最多 4 个骨骼影响（head 占 1，fx 最多 3）----
# 反向骨 + 双侧合并把嘴部区域的 fx 骨密度推高了：jaw/l_mouth_down/
# r_mouth_down/mouth_lower/mouth_up 五个语义槽的位移区域天然重叠
# （嘴唇顶点同时被张嘴、嘴角下垂、下唇收紧、嘴角上扬驱动），加上各自
# 的反向骨版本，实测 188 个 moe_head 顶点同时挂了 5~6 根 fx 骨。
#
# io_pdx_mesh 导出器会静默处理这种情况（pdx_blender/blender_import_export.py
# get_mesh_skin_info: 按权重排序、只留最大的 4 个、重新归一化），不会导出
# 失败或写坏数据，但**丢弃哪一个完全由数值大小决定**，可能砍掉某个表情下
# 唯一驱动该顶点的骨。用户 2026-08-18 要求主动控制，不依赖导出器兜底：
# 在归一化之前，把超过 3 根 fx 骨的顶点按权重从小到大丢弃，直到剩 3 根。
MAX_FX_PER_VERT = 3
#
# **按语义保底，再按数值丢**（2026-09-05）。纯按权重从小到大丢的后果：洛茜
# 眉毛 1610 个顶点被十几根眼部 fx 骨的位移区域覆盖（眼皮 morph 会带动眉毛
# 下缘），眉骨权重最多 0.5 永远先被丢，angry 里唯一驱动眉毛的骨被砍光——
# `bn_m_fx_l_brow_down` 一个顶点都没绑。所以每个顶点先锁住「它位移最大的
# 那个语义槽」的骨（正向或反向那根，取实际给它刷了权重的），再丢其余。
best_slot_of_vert = {}     # (obj名, 顶点) -> 该顶点位移最大的槽
_best_mag = {}
for slot in active_slots:
    for on, (idxs, deltas, _k) in slot_data[slot]["objs"].items():
        for j, i in enumerate(idxs):
            m = deltas[j].length
            key = (on, i)
            if m > _best_mag.get(key, 0.0):
                _best_mag[key] = m
                best_slot_of_vert[key] = slot
capped_total = 0
capped_log = []
protected_kept = 0
# --fx-protect-lid: 眼部顶点（睫毛/眼皮）同时被眨眼、睁眼、笑眼三个槽覆盖，
# 位移最大槽常是眨眼，笑眼 lid_smile 的权重又小，于是总被丢 —— 寇尔芙笑眼时
# 睫毛原地不动（源 0.99 → 烘焙 0）。额外保住 eye_squint 槽的骨。
LID_EXTRA_SLOTS = ("eye_squint_l", "eye_squint_r")
lid_extra_verts = {}
if FX_PROTECT_LID:
    for slot in LID_EXTRA_SLOTS:
        if slot not in slot_data:
            continue
        for on, (idxs, deltas, _k) in slot_data[slot]["objs"].items():
            for j, i in enumerate(idxs):
                if deltas[j].length > 0.05:
                    lid_extra_verts.setdefault((on, i), set()).add(slot)
lid_protected = 0


def _plan_cap(rescue):
    """算出每个超格顶点要丢哪些骨（先不动顶点组）。rescue = 截断后会变空的槽的骨，
    作为最高一级保底（今汐 mouth_frown 只有 4+26 个顶点，全被其他槽挤掉，2026-10-09）。"""
    plan, n_prot, n_lid = [], 0, 0
    for obj in meshes:
        if not any(vg.name in proxy_names for vg in obj.vertex_groups):
            continue
        for v in obj.data.vertices:
            got = [(obj.vertex_groups[g.group].name, g.weight) for g in v.groups
                   if obj.vertex_groups[g.group].name in proxy_names
                   and g.weight > WEIGHT_EPS]
            if len(got) <= MAX_FX_PER_VERT:
                continue
            names = {n for n, _ in got}
            pslot = best_slot_of_vert.get((obj.name, v.index))
            protect = set()
            if pslot:
                protect = {slot_bone[pslot], slot_rev_bone.get(pslot)} & names
            extra = set()
            for s_ in lid_extra_verts.get((obj.name, v.index), ()):
                extra |= {slot_bone[s_], slot_rev_bone.get(s_)} & names
            if extra - protect:
                n_lid += 1
            # 三级保底：会变空的槽 > 位移最大槽 > 眼部笑眼槽 > 其余按权重
            got.sort(key=lambda t: (t[0] in rescue, t[0] in protect, t[0] in extra, t[1]))   # 受保护的排最后
            drop = got[:len(got) - MAX_FX_PER_VERT]
            if protect and not any(n in protect for n, _ in drop):
                n_prot += 1
            plan.append((obj, v.index, [n for n, _ in drop]))
    return plan, n_prot, n_lid


def _counts_after(plan):
    dropped = {(o.name, i): set(d) for o, i, d in plan}
    cnt = {}
    for obj in meshes:
        _gn = {g.index: g.name for g in obj.vertex_groups}
        for v in obj.data.vertices:
            gone = dropped.get((obj.name, v.index), ())
            for g in v.groups:
                n = _gn[g.group]
                if g.weight > WEIGHT_EPS and n in proxy_names and n not in gone:
                    cnt[n] = cnt.get(n, 0) + 1
    return cnt


_rescue = set()
while True:
    _plan, protected_kept, lid_protected = _plan_cap(_rescue)
    _cnt = _counts_after(_plan)
    _new = {slot_bone[s_] for s_ in active_slots if _cnt.get(slot_bone[s_], 0) == 0} - _rescue
    if not _new:
        break
    _rescue |= _new
    log(f"  !! 截断会让 {sorted(_new)} 变空，升为最高级保底后重算")
for obj, vi, drop in _plan:
    fx_vgs = {vg.name: vg for vg in obj.vertex_groups}
    capped_total += 1
    capped_log.append((obj.name, vi, drop))
    for nm in drop:
        fx_vgs[nm].remove([vi])
log(f"  fx 骨超 {MAX_FX_PER_VERT} 格的顶点: {capped_total}（先锁住位移最大的槽，"
    f"再丢权重最小的；语义保底生效 {protected_kept} 次；"
    f"下一步归一化时权重会补给 bn_m_head，不会静默丢给导出器决定）")
if FX_PROTECT_LID:
    log(f"  眼部笑眼槽二级保底: 候选 {len(lid_extra_verts)} 顶点，截断时额外保住 {lid_protected} 次")

# 截断后每个槽必须仍有顶点，否则该表情信号在产物里不存在（洛茜眉压的教训）
_after = {}
for obj in meshes:
    _gn = {g.index: g.name for g in obj.vertex_groups}
    for v in obj.data.vertices:
        for g in v.groups:
            if g.weight > WEIGHT_EPS and _gn[g.group] in proxy_names:
                _after[_gn[g.group]] = _after.get(_gn[g.group], 0) + 1
_empty = [s_ for s_ in active_slots if _after.get(slot_bone[s_], 0) == 0]
for s_ in active_slots:
    weight_report.setdefault(s_, {})["verts_after_cap"] = _after.get(slot_bone[s_], 0)
if _empty:
    raise SystemExit(f"4 格截断后这些槽一个顶点都没剩: {_empty} —— 拒绝导出")
log(f"  截断后各槽顶点数最小值: "
    f"{min(_after.get(slot_bone[s_], 0) for s_ in active_slots)}")
if capped_log:
    for on, i, dropped in capped_log[:5]:
        log(f"    示例 {on} v{i}: 丢弃 {dropped}")

fixed_total = 0
for obj in meshes:
    vgs = {vg.name: vg for vg in obj.vertex_groups}
    fx_vgs = [(n, vg) for n, vg in vgs.items() if n in proxy_names]
    if not fx_vgs:
        continue
    head_vg = vgs.get(C.PROXY_PARENT) or obj.vertex_groups.new(name=C.PROXY_PARENT)
    n_over = 0
    for v in obj.data.vertices:
        got = {}
        for g in v.groups:
            nm = obj.vertex_groups[g.group].name
            if nm in proxy_names and g.weight > WEIGHT_EPS:
                got[nm] = g.weight
        if not got:
            continue
        # **非 fx 的原生骨也必须计入总量**（2026-08-21 洛茜实机针刺的根因）：
        # 此前这里只把 fx 权重缩到和为 1、再让 bn_m_head 补 1-s 的余量，
        # 完全没看该顶点原本还挂着哪些骨。洛茜的嘴部 fx 槽覆盖压到了牙齿
        # (bn_m_u7259u4e0a/下) 与舌头 (bn_m_tongue_*) 的原生骨上，于是
        # 最终权重和 = fx(1.0) + head(0.0) + 牙(1.0) = 2.0，LBS 位移放大
        # 两倍，把嘴部顶点甩成放射状细针（实测 552 个顶点权重和中位正好
        # 2.0000）。芙洛伦的 fx 覆盖没碰到牙舌，所以只在洛茜身上现形。
        # 见 [[fx-morph-bake-needs-normalization]]：同一类 bug 的第二种成因。
        others = {}
        for g in v.groups:
            nm = obj.vertex_groups[g.group].name
            if (nm not in proxy_names and nm != C.PROXY_PARENT
                    and g.weight > WEIGHT_EPS):
                others[nm] = g.weight
        s_other = sum(others.values())
        s = sum(got.values())
        # fx 与原生骨的总量一起限制在 1.0 以内：超了就整体等比缩小
        # （保持形状不变，理由同上面 fx 之间不按比例缩放的注释）
        if s + s_other > 1.0:
            n_over += 1
            k = 1.0 / (s + s_other)
            for nm, w in got.items():
                vgs[nm].add([v.index], w * k, "REPLACE")
            for nm, w in others.items():
                vgs[nm].add([v.index], w * k, "REPLACE")
            s, s_other = s * k, s_other * k
        # 余量给头骨（可能为 0，此时顶点纯由 fx + 原生骨驱动）
        head_vg.add([v.index], max(0.0, 1.0 - s - s_other), "REPLACE")
    fixed_total += n_over
    log(f"  {obj.name:<10} 缩放了 {n_over} 个权重和超 1.0 的顶点"
        f"（含原生骨），其余补 bn_m_head 余量")
log(f"  合计修正 {fixed_total} 个权重和 >1.0 的顶点")

# All bones share the exporter's four influence slots. Native jaw/eye bones
# count too; limiting only fx bones lets the exporter silently remove another
# influence after the deformation has already been fitted.
section("STEP 4c: 最终四骨蒙皮与表情形变拟合")
import numpy as np

skin_capped = 0
skin_normalized = 0
for obj in meshes:
    names = {g.index: g.name for g in obj.vertex_groups}
    for vertex in obj.data.vertices:
        got = [(g.group, g.weight) for g in vertex.groups if g.weight > 0.]
        if any(names[index] not in rig.data.bones for index, _ in got):
            raise SystemExit(f"{obj.name}/{vertex.index} 带非骨骼顶点组，拒绝导出")
        if len(got) > 4:
            ordered = sorted(got, key=lambda item: -item[1])
            for index, _ in ordered[4:]:
                obj.vertex_groups[index].remove([vertex.index])
            got = ordered[:4]
            skin_capped += 1
        total = sum(weight for _, weight in got)
        if not total:
            raise SystemExit(f"{obj.name}/{vertex.index} 没有蒙皮权重")
        if abs(total - 1.) > 1e-7:
            for index, weight in got:
                obj.vertex_groups[index].add([vertex.index], weight / total, 'REPLACE')
            skin_normalized += 1
log(f"  原生骨与 fx 合计超四格: {skin_capped} 顶点；归一化: {skin_normalized} 顶点")

# A vertex's weights were shared/normalized across overlapping expression
# slots above. Reusing the original maximum-displacement vector afterwards
# loses much of each expression. Fit the translations to the final weights,
# measuring the complete unit shape field rather than inflating a global gain.
key_slots = {}
for slot in active_slots:
    key_slots.setdefault(bindings[slot], []).append(slot)
fit_drives = {}
fit_report = {}
for keyname, slots in key_slots.items():
    driving = {}
    for slot in slots:
        driving[slot_bone[slot]] = slot_data[slot]['T']
        if slot in slot_rev_T:
            driving[slot_rev_bone[slot]] = slot_rev_T[slot]
    bone_names = sorted(driving)
    weight_rows, target_rows = [], []
    for obj in meshes:
        groups = {obj.vertex_groups[name].index: index
                  for index, name in enumerate(bone_names) if name in obj.vertex_groups}
        keys = obj.data.shape_keys.key_blocks if obj.data.shape_keys else None
        transform = np.asarray(obj.matrix_world.to_3x3(), dtype=float)
        for vertex in obj.data.vertices:
            row = [0.] * len(bone_names)
            for group in vertex.groups:
                if group.group in groups:
                    row[groups[group.group]] = group.weight
            delta = (keys[keyname].data[vertex.index].co - keys['Basis'].data[vertex.index].co
                     if keys is not None and keyname in keys else Vector((0., 0., 0.)))
            if max(row, default=0.) > 0. or delta.length > MIN_DISP:
                weight_rows.append(row)
                target_rows.append(transform @ np.asarray(delta, dtype=float))
    old_world = [np.asarray((rig.matrix_world @ rig.data.bones[name].matrix_local).to_3x3())
                 @ np.asarray(driving[name]) for name in bone_names]
    solved, evidence = fit_translations(weight_rows, target_rows, old_world)
    for name, translation in zip(bone_names, solved):
        matrix = (rig.matrix_world @ rig.data.bones[name].matrix_local).to_3x3()
        fit_drives[name] = matrix.inverted() @ Vector(translation)
    evidence['bones'] = bone_names
    evidence['stage'] = 'fixed_weight_initialization_before_constrained_refinement'
    evidence['translations_local'] = {name: list(fit_drives[name]) for name in bone_names}
    fit_report[keyname] = evidence
    log(f"  {keyname}: 形变向量误差 {evidence['normalized_rmse_before']:.3f}"
        f" → {evidence['normalized_rmse_after']:.3f}（{len(bone_names)} 骨）")

# Refine only expression weights within the actual four-influence skin budget.
# Native weights are fixed; an existing opposite blink proxy may occupy a
# genuinely unused influence slot, never a fifth slot.
from moe_expression_fit import refine_expression_weights

refine_bones = sorted(fit_drives)
refine_index = {name: i for i, name in enumerate(refine_bones)}
refine_key_bones = {key: [refine_index[name] for name in info['bones']]
                    for key, info in fit_report.items()}
vertex_count = sum(len(obj.data.vertices) for obj in meshes)
refine_weights = np.zeros((vertex_count, len(refine_bones)))
native_weights = np.zeros((vertex_count, 4))
refine_positions = np.zeros((vertex_count, 3))
refine_targets = {key: np.zeros((vertex_count, 3)) for key in refine_key_bones}
material_triangles = {}
material_names = {}
mesh_offsets = []
offset = 0
for obj in meshes:
    count = len(obj.data.vertices)
    mesh_offsets.append((obj, offset, count))
    group_names = {g.index: g.name for g in obj.vertex_groups}
    for vertex in obj.data.vertices:
        native_column = 0
        for group in vertex.groups:
            if group.weight <= 0:
                continue
            name = group_names[group.group]
            if name in refine_index:
                refine_weights[offset + vertex.index, refine_index[name]] = group.weight
            else:
                if native_column >= 4:
                    raise SystemExit('Native influences exceed the four-bone skin budget')
                native_weights[offset + vertex.index, native_column] = group.weight
                native_column += 1
    basis = np.empty(count * 3)
    keys = obj.data.shape_keys.key_blocks if obj.data.shape_keys else None
    (keys['Basis'].data if keys is not None else obj.data.vertices).foreach_get('co', basis)
    linear = np.asarray(obj.matrix_world.to_3x3(), dtype=float)
    refine_positions[offset:offset+count] = basis.reshape(-1, 3) @ linear.T + np.asarray(obj.matrix_world.translation)
    if keys is not None:
        for key, target in refine_targets.items():
            if key not in keys:
                continue
            values = np.empty(count * 3)
            keys[key].data.foreach_get('co', values)
            target[offset:offset+count] = (values-basis).reshape(-1, 3) @ linear.T
    obj.data.calc_loop_triangles()
    regions = {}
    for triangle in obj.data.loop_triangles:
        regions.setdefault(triangle.material_index, []).append([offset + i for i in triangle.vertices])
    for material, triangles in regions.items():
        material_key = f'{obj.name}/{material}'
        material_triangles[material_key] = np.asarray(triangles, dtype=int)
        slot = obj.data.materials[material] if material < len(obj.data.materials) else None
        material_names[material_key] = slot.name if slot is not None else ''
    offset += count

blink_keys = {bindings[slot] for slot in ('eye_blink_l', 'eye_blink_r')
              if slot in bindings and bindings[slot] in refine_key_bones}
refined_weights, refined_translations, refinement_report = refine_expression_weights(
    refine_weights, refine_targets, refine_key_bones, refine_positions,
    material_triangles, native_weights, blink_keys,
    translation_cap_ratio=FX_CAP, min_fit_weight=FX_MIN_FIT_W,
)
if FX_CAP is not None or FX_MIN_FIT_W:
    log(f"  fx 平移约束: cap={FX_CAP}×源最大位移, min_fit_weight={FX_MIN_FIT_W}; "
        f"岭收缩生效的键 {refinement_report.get('ridge_keys')}; "
        f"最大平移 {refinement_report['maximum_translation']:.3f}")
for key, translations in refined_translations.items():
    fit_report[key]['refinement'] = refinement_report['keys'][key]
    fit_report[key]['final_translations_local'] = {}
    for column, translation in zip(refine_key_bones[key], translations):
        name = refine_bones[column]
        matrix = (rig.matrix_world @ rig.data.bones[name].matrix_local).to_3x3()
        fit_drives[name] = matrix.inverted() @ Vector(translation)
        fit_report[key]['final_translations_local'][name] = list(fit_drives[name])

# Mouth fields overlap on the same lip vertices. Giving each key exclusive
# bones and truncating their supports creates holes and pointed lip edges.
# Share the existing mouth proxies across native keys, preserving the already
# authored native/non-mouth weights and each vertex's exact mouth budget.
from moe_expression_fit import fit_shared_expression_region

key_drives = {key: {name: Vector(value) for name, value in info['final_translations_local'].items()}
              for key, info in fit_report.items()}
mouth_keys = sorted({bindings[slot] for slot in active_slots
                     if slot.startswith('mouth_') and bindings[slot] in refine_key_bones})
shared_mouth_report = None
if len(mouth_keys) > 1:
    mouth_columns = sorted({column for key in mouth_keys for column in refine_key_bones[key]})
    other_columns = [i for i in range(len(refine_bones)) if i not in mouth_columns]
    # Some thin tooth sheets need accurate native depth to remain behind the
    # lips. Weight only that coordinate's fitting loss; never move the rest
    # geometry inward or weaken the expression to conceal a penetration.
    from moe_chars import CHARS
    source_character = next((c for c in CHARS if c['slug'] == Path(SOURCE_BLEND).stem), None)
    head_budget_floor = float((source_character or {}).get('expression_head_budget_floor', 0.))
    if '--' in sys.argv and '--mouth-head-budget-floor' in _argv:
        head_budget_floor = float(_argv[_argv.index('--mouth-head-budget-floor') + 1])
    if not math.isfinite(head_budget_floor) or not 0. <= head_budget_floor <= 1.:
        raise ValueError('Head-attached mouth budget floor must be finite in [0, 1]')
    head_budget_report = dict(enabled=bool(head_budget_floor), floor=head_budget_floor,
        parent=C.PROXY_PARENT, changes=[], target='unchanged native shape-key displacement',
        contract='Only head weight may transfer to direct child mouth proxies; all other weights fixed')
    if head_budget_floor:
        # A projection onto one displacement axis can leave a moved vertex
        # without any mouth budget. No later fixed-budget fit can recover it.
        # These proxies are direct children of head. At their neutral local
        # transforms their skin matrices equal head's for every parent pose;
        # transferring weight is consequently a source allocation, not a
        # change to the character's underlying head attachment or body motion.
        # The release gate additionally checks this on all emitted head frames.
        mouth_names = {refine_bones[i] for i in mouth_columns}
        for name in mouth_names:
            bone = rig.data.bones[name]
            if bone.parent is None or bone.parent.name != C.PROXY_PARENT:
                raise ValueError(f'{name}: mouth budget allocation requires direct head parent')
        moved = np.max(np.asarray([np.linalg.norm(refine_targets[key], axis=1)
                                   for key in mouth_keys]), axis=0) > 1e-5
        for obj, offset, count in mesh_offsets:
            names = {g.index: g.name for g in obj.vertex_groups}
            head_group = obj.vertex_groups.get(C.PROXY_PARENT)
            for vertex in obj.data.vertices:
                index = offset + vertex.index
                before = float(refined_weights[index, mouth_columns].sum())
                if not moved[index] or before >= head_budget_floor:
                    continue
                fixed_groups = [(names[g.group], g.weight) for g in vertex.groups
                                if g.weight > 0 and names[g.group] not in mouth_names]
                if (len(fixed_groups) != 1 or fixed_groups[0][0] != C.PROXY_PARENT
                        or np.any(refined_weights[index, other_columns] > 0)):
                    raise ValueError(f'{obj.name}/{vertex.index}: mouth budget allocation is not pure head attached')
                old_head = float(fixed_groups[0][1])
                if abs(old_head + before - 1.) > 1e-5:
                    raise ValueError('Head and mouth source budget must already sum to one')
                after_head = 1. - head_budget_floor
                native_weights[index] *= after_head / old_head
                head_group.add([vertex.index], after_head, 'REPLACE')
                if before > 0:
                    refined_weights[index, mouth_columns] *= head_budget_floor / before
                else:
                    refined_weights[index, mouth_columns[0]] = head_budget_floor
                head_budget_report['changes'].append(dict(mesh=obj.name, vertex=vertex.index,
                    source_row=index, old_head_weight=old_head, new_head_weight=after_head,
                    old_mouth_budget=before, new_mouth_budget=head_budget_floor,
                    reason='native mouth field needs head-equivalent proxy budget'))
        head_budget_report['changed_vertices'] = len(head_budget_report['changes'])
        log(f"  Head-equivalent mouth source allocation: {head_budget_report['changed_vertices']} vertices; floor {head_budget_floor}")
    tooth_depth_weight = float((source_character or {}).get('expression_tooth_depth_weight', 1.))
    tooth_coupling_weight = float((source_character or {}).get('expression_tooth_coupling_weight', 0.))
    if '--' in sys.argv and '--mouth-tooth-depth-weight' in _argv:
        tooth_depth_weight = float(_argv[_argv.index('--mouth-tooth-depth-weight') + 1])
    if '--' in sys.argv and '--mouth-tooth-coupling-weight' in _argv:
        tooth_coupling_weight = float(_argv[_argv.index('--mouth-tooth-coupling-weight') + 1])
    if not math.isfinite(tooth_depth_weight) or not 1. <= tooth_depth_weight <= 65536.:
        raise ValueError('Mouth tooth depth fitting weight must be finite in [1, 65536]')
    if not math.isfinite(tooth_coupling_weight) or not 0. <= tooth_coupling_weight <= 65536.:
        raise ValueError('Mouth tooth coupling fitting weight must be finite in [0, 65536]')
    mouth_point_weights = None
    tooth_vertices = np.asarray([], dtype=int)
    tooth_coupled_edges = None
    if tooth_depth_weight > 1. or tooth_coupling_weight > 0.:
        tooth_regions = [triangles for material, triangles in material_triangles.items()
                         if 'teeth' in material_names[material].lower()]
        if not tooth_regions:
            raise ValueError('Requested native tooth depth fit but no tooth materials matched')
        tooth_vertices = np.unique(np.concatenate(tooth_regions))
        mouth_point_weights = np.ones_like(refine_positions)
        # Source meshes use Blender world Z up, Y depth; export maps depth to Z.
        mouth_point_weights[tooth_vertices, 1] = tooth_depth_weight
        if tooth_coupling_weight:
            faces = [triangles for material, triangles in material_triangles.items()
                     if material_names[material].lower().split('.')[0] == 'face']
            if not faces:
                raise ValueError('Native tooth surface coupling requires a named Face material')
            face_vertices = np.unique(np.concatenate(faces))
            tooth_coupled_edges = []
            for vertex in tooth_vertices:
                distances = np.linalg.norm(refine_positions[face_vertices] - refine_positions[vertex], axis=1)
                nearby = face_vertices[np.argsort(distances, kind='stable')[:2]]
                tooth_coupled_edges.extend((int(vertex), int(neighbor)) for neighbor in nearby)
    mouth_weights, mouth_translations, shared_mouth_report = fit_shared_expression_region(
        refined_weights[:, mouth_columns], {key: refine_targets[key] for key in mouth_keys},
        refine_positions, material_triangles,
        np.column_stack((native_weights, refined_weights[:, other_columns])),
        point_weights=mouth_point_weights,
        coupled_edges=tooth_coupled_edges, coupling_weight=tooth_coupling_weight,
        translation_cap_ratio=FX_CAP, min_fit_weight=FX_MIN_FIT_W,
    )
    shared_mouth_report['tooth_depth_fit'] = dict(
        weight=tooth_depth_weight, source_world_axis=1,
        material_names=sorted({material_names[k] for k in material_names
                               if 'teeth' in material_names[k].lower()}),
        vertices=len(tooth_vertices), target='unchanged native shape-key displacement')
    shared_mouth_report['head_attachment_budget'] = head_budget_report
    refined_weights[:, mouth_columns] = mouth_weights
    for key, translations in mouth_translations.items():
        key_drives[key] = {}
        fit_report[key]['bones'] = [refine_bones[i] for i in mouth_columns]
        fit_report[key]['final_translations_local'] = {}
        fit_report[key]['shared_fit'] = shared_mouth_report['fields'][key]
        for column, translation in zip(mouth_columns, translations):
            name = refine_bones[column]
            matrix = (rig.matrix_world @ rig.data.bones[name].matrix_local).to_3x3()
            drive = matrix.inverted() @ Vector(translation)
            key_drives[key][name] = drive
            fit_report[key]['final_translations_local'][name] = list(drive)
    log(f"  Shared mouth fit: {len(mouth_keys)} native keys / {len(mouth_columns)} existing proxies; "
        f"max actual native+FX support {shared_mouth_report['maximum_total_positive_influences']}")
for obj, offset, count in mesh_offsets:
    groups = {i: obj.vertex_groups.get(name) for i, name in enumerate(refine_bones)}
    for i, name in enumerate(refine_bones):
        old = refine_weights[offset:offset+count, i]
        new = refined_weights[offset:offset+count, i]
        changed = np.flatnonzero(old != new)
        if len(changed) and groups[i] is None:
            groups[i] = obj.vertex_groups.new(name=name)
        for vertex_index in changed:
            value = float(new[vertex_index])
            if value > 0:
                groups[i].add([int(vertex_index)], value, 'REPLACE')
            else:
                groups[i].remove([int(vertex_index)])
    # Check the float weights actually saved by Blender, including every native
    # bone. The exporter must not be asked to repair an over-budget candidate.
    for vertex in obj.data.vertices:
        positive = [g.weight for g in vertex.groups if g.weight > 0]
        if len(positive) > 4 or abs(sum(positive)-1) > 1e-5:
            raise SystemExit(f'{obj.name}/{vertex.index}: refined skin violates four-bone/normalization contract')
log('  Refined expression weights: native weights fixed; all vertices satisfy <=4 total influences')


# 确认蒙皮修饰器存在且指向本骨架
for obj in meshes:
    mods = [m for m in obj.modifiers if m.type == "ARMATURE"]
    if not mods:
        m = obj.modifiers.new(name="Armature", type="ARMATURE")
        m.object = rig
        log(f"  {obj.name} 补上 Armature 修饰器")
    else:
        for m in mods:
            if m.object is not rig:
                m.object = rig


# ================================================================ 5. 烘焙动画
section("STEP 5: 烘焙表情与动作")
scn = bpy.context.scene
scn.render.fps = C.OUTPUT_FPS

# 形态键值全部归零：表情由骨骼驱动，形态键只用来推导权重。
# 若留着非零值，导出的 .mesh 会带上已变形的静态几何。
for obj in meshes:
    sk = obj.data.shape_keys
    if sk:
        for kb in sk.key_blocks:
            if kb.name != "Basis":
                kb.value = 0.0
log("  已把所有形态键值归零（几何回到 Basis）")


def clear_pose():
    bpy.ops.object.mode_set(mode="POSE")
    bpy.ops.pose.select_all(action="SELECT")
    bpy.ops.pose.transforms_clear()
    bpy.ops.object.mode_set(mode="OBJECT")


def new_action(name):
    if rig.animation_data is None:
        rig.animation_data_create()
    act = bpy.data.actions.new(name)
    # use_fake_user 必须设：不设的话，下一次 new_action 把 rig 的引用换掉后，
    # 这个 action 引用计数归零，保存 .blend 时被丢弃。
    # 导出的 .anim 不受影响（导出发生在赋值之后立刻），但事后想复查/重渲染
    # 就只剩最后一个 action 了 —— 实测踩过，渲染脚本找不到 moe_happy。
    act.use_fake_user = True
    rig.animation_data.action = act
    return act


def fcurve_count(act):
    n = len(getattr(act, "fcurves", []))
    if n:
        return n
    for layer in getattr(act, "layers", []):
        for strip in getattr(layer, "strips", []):
            for bag in getattr(strip, "channelbags", []):
                n += len(bag.fcurves)
    return n


baked = {}

# ---- 表情：代理骨平移 ----
bpy.ops.object.select_all(action="DESELECT")
rig.select_set(True)
bpy.context.view_layer.objects.active = rig

slot_fallbacks = {}      # {表情: {缺槽: 替代槽}}，进 bake_report
sampled_expression_targets = {}
sampled_expression_slots = {}
for expr in C.EXPRESSIONS:
    clear_pose()
    curve = C.sample_expression(expr)
    # 缺槽替代（合同 SLOT_FALLBACK）：模型没有的槽按系数折到它有的槽上，不加骨。
    curve, _fb = C.substitute_missing(curve, missing)
    if _fb:
        for _s, _d in sorted(_fb.items()):
            slot_fallbacks.setdefault(expr, {})[_s] = _d
        log(f"  {expr:<9} 缺槽替代: " + ", ".join(f"{_s}→{_d}×{C.SLOT_FALLBACK[_s][1]}" for _s, _d in sorted(_fb.items())))
    sampled_expression_slots[expr] = curve
    target_curve = []
    for values in curve:
        targets = {}
        for slot, strength in values.items():
            if slot in slot_data:
                key = bindings[slot]
                value = strength * C.slot_gain(slot, slot_data[slot]['T'].length)
                targets[key] = max(targets.get(key, 0.), value)
        target_curve.append(targets)
    sampled_expression_targets[expr] = target_curve
    n = len(curve)
    scn.frame_start = 1
    scn.frame_end = n
    act = new_action(f"moe_{expr}")
    bpy.ops.object.mode_set(mode="POSE")

    # 所有需要写通道的骨：正向骨 + 反向骨。反向骨必须与正向骨**同步**驱动
    # （同一个强度 s），因为它承载的是同一个形态键里逆向运动的那批顶点 ——
    # 漏掉它，那批顶点就不动，等于回到「表情淡」的老状态。
    all_fx_bones = set(slot_bone.values()) | set(slot_rev_bone.values())

    driven = set()
    for f, vals in enumerate(curve, start=1):
        scn.frame_set(f)
        # 视线：眼球链骨沿世界轴平移（+X = 角色自己的左，+Y = 上）。
        # pose.location 在骨局部系，要用该骨的 rest 朝向把世界向量转过去。
        look = vals.get(C.EYE_LOOK_SLOT)
        for bn in eye_bones:
            pb = rig.pose.bones.get(bn)
            if not pb:
                continue
            if look:
                # **Blender 是 Z 向上**（bn_m_head 在 z≈124，Y 是前后深度）。
                # 第一版写成 (x, y, 0)，纵向视线全进了深度轴：用产物反推，
                # happy 的 look (0.10, 0.20) 在世界系里是 (0.045, 0, 0.06) ——
                # 眼球前后进出、上下为零。垂直分量必须给 Z。
                wv = Vector((look[0] * C.EYE_LOOK_UNITS[0], 0.0,
                             look[1] * C.EYE_LOOK_UNITS[1]))
                pb.location = pb.bone.matrix_local.to_3x3().inverted() @ wv
                driven.add(bn)
            else:
                pb.location = Vector((0, 0, 0))
            pb.keyframe_insert(data_path="location", frame=f)
        # 先把本帧所有相关骨归零，再写本帧值，避免上一帧残留
        per_bone = {}
        # Shared semantic slots have already been combined by native key with
        # max strength above. Distinct native keys add their own fitted fields,
        # including when several keys deliberately share the same mouth proxy.
        for key, strength in target_curve[f - 1].items():
            for bname, translation in key_drives[key].items():
                per_bone[bname] = per_bone.get(bname, Vector((0, 0, 0))) + translation * strength
        for bname in all_fx_bones:
            pb = rig.pose.bones.get(bname)
            if not pb:
                continue
            pb.location = per_bone.get(bname, Vector((0, 0, 0)))
            pb.keyframe_insert(data_path="location", frame=f)
            if bname in per_bone:
                driven.add(bname)

    bpy.ops.object.mode_set(mode="OBJECT")
    scn.frame_set(1)
    baked[expr] = {
        "kind": "expression", "frames": n, "fps": C.OUTPUT_FPS,
        "anim_id": C.ANIM_IDS[expr],
        "state": C.STATE_NAMES[expr], "driven_bones": sorted(driven),
        "fcurves": fcurve_count(act), "action": act.name,
    }
    log(f"  {expr:<9} {n:>4} 帧  驱动 {len(driven)} 骨  {fcurve_count(act)} fcurve")

# ---- 动作：语义骨旋转 ----
AXIS = {"X": Vector((1, 0, 0)), "Y": Vector((0, 1, 0)), "Z": Vector((0, 0, 1))}
for aname in C.ACTIONS:
    clear_pose()
    curve = C.sample_action(aname)
    n = len(curve)
    scn.frame_start = 1
    scn.frame_end = n
    act = new_action(f"moe_{aname}")
    bpy.ops.object.mode_set(mode="POSE")

    bones = sorted({b for fr in curve for b in fr})
    have = [b for b in bones if b in rig.pose.bones]
    lack = [b for b in bones if b not in rig.pose.bones]
    for b in have:
        rig.pose.bones[b].rotation_mode = "QUATERNION"
    for f, vals in enumerate(curve, start=1):
        scn.frame_set(f)
        for b in have:
            pb = rig.pose.bones[b]
            if b in vals:
                ax, deg = vals[b]
                pb.rotation_quaternion = Quaternion(AXIS[ax], math.radians(deg))
            else:
                pb.rotation_quaternion = Quaternion((1, 0, 0, 0))
            pb.keyframe_insert(data_path="rotation_quaternion", frame=f)
    bpy.ops.object.mode_set(mode="OBJECT")
    scn.frame_set(1)
    baked[aname] = {
        "kind": "action", "frames": n, "fps": C.OUTPUT_FPS,
        "anim_id": f"moe_{aname}",
        "state": C.ACTION_STATE_NAMES[aname],
        "looping": C.ACTIONS[aname]["looping"],
        "driven_bones": have, "missing_bones": lack,
        "fcurves": fcurve_count(act), "action": act.name,
    }
    log(f"  {aname:<11} {n:>4} 帧  驱动 {len(have)} 骨"
        + (f"  !! 缺骨 {lack}" if lack else ""))


# ================================================================ 6. 运动验证
section("STEP 6: 验证真有运动（不能只看 action 存在）")
verify = {}
for name, info in baked.items():
    rig.animation_data.action = bpy.data.actions[info["action"]]
    n = info["frames"]
    probe = info["driven_bones"][:6]
    peak = 0.0
    per_bone = {}
    for b in probe:
        scn.frame_set(1)
        dg = bpy.context.evaluated_depsgraph_get()
        ev = rig.evaluated_get(dg)
        p1 = (ev.matrix_world @ ev.pose.bones[b].head).copy()
        best = 0.0
        for f in (n // 3, n // 2, 2 * n // 3):
            f = max(1, min(n, f))
            scn.frame_set(f)
            dg = bpy.context.evaluated_depsgraph_get()
            ev = rig.evaluated_get(dg)
            p = ev.matrix_world @ ev.pose.bones[b].head
            best = max(best, (p - p1).length)
        per_bone[b] = round(best, 5)
        peak = max(peak, best)
    verify[name] = {"peak_bone_travel": round(peak, 5), "per_bone": per_bone}
    ok = "OK" if peak > 1e-4 else "!! 无运动"
    log(f"  {name:<11} 峰值骨位移={peak:.5f}  {ok}")
    for b, v in per_bone.items():
        log(f"      {b:<26} {v}")
scn.frame_set(1)

# 顶点级验证：表情是否真的让脸动了（骨动但权重没刷上会在这里暴露）
section("STEP 6b: 验证顶点真的跟着动")
vert_verify = {}
for name, info in baked.items():
    if info["kind"] != "expression":
        continue
    rig.animation_data.action = bpy.data.actions[info["action"]]
    n = info["frames"]
    obj = head
    scn.frame_set(1)
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    base = [v.co.copy() for v in ev.data.vertices]
    scn.frame_set(max(1, n // 2))
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    mid = [v.co.copy() for v in ev.data.vertices]
    moved = 0
    mx = 0.0
    for a, b in zip(base, mid):
        d = (b - a).length
        if d > 1e-4:
            moved += 1
            mx = max(mx, d)
    vert_verify[name] = {"moved_verts": moved, "max_disp": round(mx, 5)}
    ok = "OK" if moved > 0 else "!! 顶点没动，权重可能没生效"
    log(f"  {name:<11} 中间帧动了 {moved:>5} 顶点  最大位移={mx:.4f}  {ok}")
scn.frame_set(1)


# ================================================================ 7. 导出
section("STEP 7: 导出 .mesh 与 .anim")
export = {}


def export_mesh_obj(obj):
    """用 rig 的当前骨架导出一个选中的 mesh。"""
    out = OUT_DIR / f"{obj.name}.mesh"
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.io_pdx_mesh.export_mesh(
            filepath=str(out), check_existing=False,
            chk_mesh=True, chk_skel=True, chk_locs=False, chk_selected=True,
        )
        sz = out.stat().st_size if out.is_file() else 0
        export[obj.name + ".mesh"] = {"ok": out.is_file(), "size": sz}
        log(f"  {obj.name}.mesh  {sz:,} bytes")
    except Exception as e:
        export[obj.name + ".mesh"] = {"ok": False, "error": str(e)}
        log(f"  !! {obj.name}.mesh 导出失败: {e}")


# head 保持 bn_m_root 为真根。Royal Court 的标准根骨只允许出现在 torso
# （以及共享 torso pose 的独立 clothes）里；head 也加同名根骨会让 CK3 对
# 两个附着实体分别施加根变换，实机表现为普通站姿头身飞离。
#
# **head 导出前必须先删除 prop 骨**（如果存在的话）。prop 骨现在只在 torso
# 导出时由 add_torso_court_roots() 添加。如果 blend 文件中已有 prop 骨（从
# 之前的运行遗留），head 导出会把它们包含进去 → 道具错位。
bpy.context.view_layer.objects.active = rig
rig.select_set(True)
bpy.ops.object.mode_set(mode="EDIT")
eb = rig.data.edit_bones
prop_removed_before_head = []
for name in ("bn_l_prop", "bn_r_prop"):
    bone = eb.get(name)
    if bone is not None:
        eb.remove(bone)
        prop_removed_before_head.append(name)
bpy.ops.object.mode_set(mode="OBJECT")
if prop_removed_before_head:
    log(f"  head 导出前删除遗留 prop 骨: {prop_removed_before_head}")

head_meshes = [obj for obj in meshes if obj.name == "moe_head"]
torso_meshes = [obj for obj in meshes if obj.name != "moe_head"]
for obj in head_meshes:
    export_mesh_obj(obj)

# 表情源属于 head，先按基础 200 骨骨架全部导出。torso 稍后会有自己的
# `moe_torso_idle_calm.anim` 模板，不能覆盖这里的中性表情源：头部嫁接会读
# `moe_idle_calm.anim`，两侧模板混用会把 head 也变成 202 骨。
bpy.ops.object.select_all(action="DESELECT")
rig.select_set(True)
bpy.context.view_layer.objects.active = rig
for name, info in baked.items():
    rig.animation_data.action = bpy.data.actions[info["action"]]
    scn.frame_start = 1
    scn.frame_end = info["frames"]
    fn = f"moe_{name}.anim"
    out = OUT_DIR / fn
    try:
        bpy.ops.io_pdx_mesh.export_anim(
            filepath=str(out), check_existing=False,
            int_start=1, int_end=info["frames"], chk_uniform_scale=True,
        )
        sz = out.stat().st_size if out.is_file() else 0
        export[fn] = {"ok": out.is_file(), "size": sz,
                      "anim_id": info["anim_id"], "state": info["state"]}
        log(f"  {fn:<24} {sz:>9,} bytes  id={info['anim_id']} state={info['state']}")
    except Exception as e:
        export[fn] = {"ok": False, "error": str(e)}
        log(f"  !! {fn} 导出失败: {e}")


def add_torso_court_roots():
    """临时建立 torso-only 的原版王座根契约与道具挂点骨，返回扩展后的骨数。

    这里同时建立 prop 骨（bn_l_prop / bn_r_prop），因为道具只应挂在 torso，
    不能出现在 head mesh 里（否则引擎会挂到 head 那份、停在 T 字手位）。
    """
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = rig.data.edit_bones
    root = eb.get("bn_m_root")
    groove = eb.get("bn_m_groove")
    if root is None or groove is None:
        bpy.ops.object.mode_set(mode="OBJECT")
        raise RuntimeError("缺少 bn_m_root/bn_m_groove，不能建立王座根契约")
    if root.parent is not None:
        parent_name = root.parent.name
        bpy.ops.object.mode_set(mode="OBJECT")
        raise RuntimeError(f"bn_m_root 已有父骨 {parent_name}，拒绝重复套根")
    if eb.get("ground_joint") is not None or eb.get("body_root") is not None:
        bpy.ops.object.mode_set(mode="OBJECT")
        raise RuntimeError("基础 rig 意外残留 ground_joint/body_root")

    ground = eb.new("ground_joint")
    ground.head = Vector((0.0, 0.0, 0.0))
    ground.tail = Vector((0.0, PROXY_BONE_LEN, 0.0))
    ground.roll = 0.0
    ground.use_deform = True

    body = eb.new("body_root")
    # body_root 必须落在实际骨盆摆位点，而不是场景原点。宫廷管理器会把
    # 这根骨的当前动画位置对齐到 throne ruler_locator；放在原点等价于此前
    # 的 entity locator 实验，会把坐姿 root 高度叠到座面上而悬空。
    body.head = groove.head.copy()
    body.tail = body.head + Vector((0.0, PROXY_BONE_LEN, 0.0))
    body.roll = 0.0
    body.parent = ground
    body.use_connect = False
    body.use_deform = True

    # 改父级不会改变 edit bone 的 armature-space head/tail，因此现有网格的
    # bind 世界姿态保持不变；body_root 的动画位移会整体带动 bn_m_root 子树。
    root.parent = body
    root.use_connect = False

    # ======== 建立道具挂点骨（torso-only）========
    # 几何照原版实读（female_body / male_body 一致）：
    #   父骨 = 同侧腕骨；**绑定朝向 = 单位矩阵**（世界轴对齐，不带任何旋转）；
    #   世界位置 = 腕骨世界位置 + (±2.14, -8.17, -2.22)。
    # 朝向是恒等这点是反直觉的 —— 道具网格本身按世界轴建，prop 骨只负责定位，
    # 所以照抄恒等朝向就与原版一致，腕骨轴向的镜像差异不需要补偿。
    for bname, (wrist, off) in PROP_BONES.items():
        if bname in eb:
            continue
        web = eb.get(wrist)
        if web is None:
            prop_skip.append(f"{bname}（找不到父骨 {wrist}）")
            continue
        b = eb.new(bname)
        # 世界轴对齐 + roll=0 → 绑定朝向就是单位矩阵，与原版一致。
        b.head = web.head + off
        b.tail = b.head + Vector((0.0, PROXY_BONE_LEN, 0.0))
        b.roll = 0.0
        b.parent = web
        b.use_connect = False
        # 零蒙皮骨。**use_deform 必须为 True** —— io_pdx_mesh 只导出参与形变的
        # 骨，False 的骨会被丢掉，于是 mesh 里根本没有这根骨、accessory 照样报
        # invalid node，而且没有任何报错提示。没有顶点权重指向它不影响画面。
        b.use_deform = True
        prop_made.append(bname)

    body_pos = tuple(body.head)
    bpy.ops.object.mode_set(mode="OBJECT")

    # 日志打印
    if prop_made:
        log(f"  新建 {len(prop_made)} 根道具挂点骨（torso-only）: {prop_made}")
    for s in prop_skip:
        log(f"  !! 道具挂点骨没建成: {s} —— 手持道具会报 invalid node")

    return len(rig.data.bones), body_pos


def remove_torso_court_roots():
    """恢复基础 rig，保证保存的 expression 工作区仍是 head 的骨架（不含 prop 骨）。"""
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = rig.data.edit_bones
    root = eb.get("bn_m_root")
    if root is not None and root.parent is not None \
            and root.parent.name == "body_root":
        root.parent = None
        root.use_connect = False
    # 删除王座根骨与道具挂点骨（它们都只应存在于 torso）
    for name in ("body_root", "ground_joint", "bn_l_prop", "bn_r_prop"):
        bone = eb.get(name)
        if bone is not None:
            eb.remove(bone)
    bpy.ops.object.mode_set(mode="OBJECT")


if not torso_meshes:
    raise RuntimeError("找不到 torso/clothes 网格，无法导出王座根骨版本")

court_bone_count = None
try:
    court_bone_count, court_root_pos = add_torso_court_roots()
    log(f"  torso-only 王座根骨: ground_joint -> body_root -> bn_m_root，"
        f"body_root={tuple(round(v, 4) for v in court_root_pos)}，"
        f"共 {court_bone_count} 骨")
    for obj in torso_meshes:
        export_mesh_obj(obj)

    idle = baked.get("idle_calm")
    if idle is None:
        raise RuntimeError("缺少 idle_calm action，不能生成 torso 独立模板")
    rig.animation_data.action = bpy.data.actions[idle["action"]]
    scn.frame_start = 1
    scn.frame_end = idle["frames"]
    torso_idle = OUT_DIR / "moe_torso_idle_calm.anim"
    # export_mesh_obj() 会把刚导出的 mesh 留作活动对象；动画导出器只读取
    # active object，并不会自行沿 Armature modifier 找回 rig。若不显式切回，
    # 插件会把 Mesh 当骨架并报 `'Mesh' object has no attribute 'bones'`。
    bpy.ops.object.select_all(action="DESELECT")
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.io_pdx_mesh.export_anim(
        filepath=str(torso_idle), check_existing=False,
        int_start=1, int_end=idle["frames"], chk_uniform_scale=True,
    )
    sz = torso_idle.stat().st_size if torso_idle.is_file() else 0
    export[torso_idle.name] = {"ok": torso_idle.is_file(), "size": sz,
                               "anim_id": idle["anim_id"],
                               "state": idle["state"]}
    log(f"  {torso_idle.name:<24} {sz:>9,} bytes  ({court_bone_count} 骨)")
finally:
    remove_torso_court_roots()
    log(f"  导出后恢复基础 rig: {len(rig.data.bones)} 骨（head 工作区）")


# ================================================================ 8. 报告
# ================================================================ 7b. asset 片段
section("STEP 7b: 生成 .asset 注册片段")
# 三条实测得来的写法，与最初的猜测都不同:
#   1. animation id 与 state name 是两个命名空间。
#      id 形如 head_emotion_angry，state 形如 emotion_angry（无 head_ 前缀）。
#   2. 循环靠 looping = no + next_state 指回自己，不是 looping = yes。
#      （female_body.asset:334、male_head.asset:1188）
#   3. 每个正式 state 都配 _entry，可复用同一个 anim。
anim_lines = []
# state 双边注册：head 与 torso 各一份完整列表。
# 表情在 torso 上没有脸部代理骨可驱动，用身体待机动画占位 —— CK3 会向
# torso 请求同名 state，缺了就报错；有名字能播总比报错好。
part_states = {p: [] for p in C.ENTITY_PARTS}
IDLE_ID = "moe_idle_calm"


def state_line(name, anim_id, nxt):
    return (f'\tstate = {{ name = "{name}"'.ljust(56)
            + f'animation = "{anim_id}" looping = no next_state = "{nxt}" }}')


def emit(part, base, anim_id):
    """给一个 state 基名生成全部体型 × 入场 组合。

    体型标记的插入位置不统一（后缀式 vs 中缀式），交给合同层的
    variant_names 处理 —— 那里记着实测的原版命名规律。
    _entry 的 next_state 指向**同体型**的正式态。
    """
    for canon, full in C.variant_names(base):
        part_states[part].append(state_line(full, anim_id, canon))


for expr in C.EXPRESSIONS:
    aid = C.ANIM_IDS[expr]
    if C.STATE_NAMES.get(expr) is None:
        continue          # 纯表情源（talk），不注册 state
    anim_lines.append(f'\tanimation = {{ id = "{aid}"'.ljust(56)
                      + f'type = "moe_{expr}.anim" }}')
    emit("head", C.STATE_NAMES[expr], aid)
    # torso 用待机占位（没有脸部骨可动）
    emit("torso", C.STATE_NAMES[expr], IDLE_ID)

for aname in C.ACTIONS:
    aid = f"moe_{aname}"
    names = C.ACTION_STATE_NAMES[aname]
    if isinstance(names, str):
        names = [names]
    anim_lines.append(f'\tanimation = {{ id = "{aid}"'.ljust(56)
                      + f'type = "moe_{aname}.anim" }}')
    # 动作是身体的事，但 head 也会被请求同名 state，两边都注册。
    # 注意 ACTION_STATE_NAMES 里已显式列出 idle 与 idle_entry，
    # 而 emit() 会自己加 _entry，所以这里只取基名去重，避免生成
    # idle_entry_entry 这种垃圾。
    bases = sorted({n[:-6] if n.endswith("_entry") else n for n in names})
    for base in bases:
        for part in C.ENTITY_PARTS:
            emit(part, base, aid)

frag = ["# 由 tools/model/bake_moe_expressions.py 生成，勿手改",
        "# --- skeletal_animation_set 内追加 ---"] + anim_lines
for part in C.ENTITY_PARTS:
    frag += ["", f"# --- moe_{part}_entity 内追加 ---"] + part_states[part]
fragment_path = OUT_DIR / "asset_states.txt"
fragment_path.write_text("\n".join(frag) + "\n", encoding="utf-8")
log(f"  {len(anim_lines)} 条 animation")
for part in C.ENTITY_PARTS:
    log(f"  {len(part_states[part])} 条 {part} state")
log(f"  片段 {fragment_path}")


section("STEP 8: 保存")
bpy.ops.wm.save_as_mainfile(filepath=str(OUT_BLEND))
log(f"  工作区 {OUT_BLEND}")

report = {
    "source_blend": SOURCE_BLEND,
    "output_blend": str(OUT_BLEND),
    "naming_scheme": scheme,
    "fx_fix_options": {"translation_cap": FX_CAP, "min_fit_weight": FX_MIN_FIT_W,
                       "protect_lid": FX_PROTECT_LID, "lateral_guard": FX_LATERAL_GUARD,
                       "mouth_open_key": MOUTH_OPEN_KEY,
                       "lateral_rebound": {s: list(v) for s, v in lateral_rebound.items()}},
    "bindings": bindings,
    "missing_slots": missing,
    "skipped_slots": skipped,
    "slot_fallbacks": slot_fallbacks,
    "shared_shapekeys": shared,
    "same_bone_multi_slot": conflict,
    "proxy_bones_created": created,
    "final_bone_count": len(rig.data.bones),
    "bilateral_merged": merged_slots,
    "reverse_bones": {s: b for s, b in slot_rev_bone.items() if s in slot_rev_T},
    # PDX 每顶点 4 骨上限的主动限制结果。非空说明有顶点的 fx 骨密度触顶，
    # 那些位置的形变会略欠拟合 —— 但比让导出器按数值大小硬砍可控。
    "fx_slot_capped": {
        "max_fx_per_vert": MAX_FX_PER_VERT,
        "vert_count": capped_total,
        "lid_protect": {"enabled": FX_PROTECT_LID, "candidates": len(lid_extra_verts),
                        "kept": lid_protected},
        "samples": [{"mesh": on, "vert": i, "dropped": d}
                    for on, i, d in capped_log[:50]],
    },
    "weights": weight_report,
    "expression_fit": fit_report,
    "expression_refinement": refinement_report,
    "shared_mouth_fit": shared_mouth_report,
    "final_skin": {"max_influences": 4, "capped_vertices": skin_capped,
                   "normalized_vertices": skin_normalized},
    "sampled_expression_targets": sampled_expression_targets,
    "sampled_expression_slots": sampled_expression_slots,
    "expression_contract_sha256": hashlib.sha256(Path(C.__file__).read_bytes()).hexdigest(),
    "baked": baked,
    "bone_motion_verify": verify,
    "vertex_motion_verify": vert_verify,
    "export": export,
    "anim_ids": C.ANIM_IDS,
    "expression_states": {k: [v + s for s in C.STATE_SUFFIXES]
                          for k, v in C.STATE_NAMES.items() if v},
    "eye_bones": eye_bones,
    "eye_look_units": list(C.EYE_LOOK_UNITS),
    # 每槽实际用的增益，配 weights[slot][mesh]["T"] 可算出产物里的满幅位移
    "slot_gain": {s_: round(C.slot_gain(s_, slot_data[s_]["T"].length), 3)
                  for s_ in active_slots},
    "action_states": C.ACTION_STATE_NAMES,
    "asset_fragment": str(fragment_path),
}
rp = OUT_DIR / "bake_report.json"
rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
if shared_mouth_report is not None:
    import moe_expression_basis as EB
    import graft_spine_into_head_anim as G
    expression_basis = EB.from_bake(report, OUT_DIR, G.read_anim, G.unpack)
    (OUT_DIR / EB.FILENAME).write_text(json.dumps(expression_basis, ensure_ascii=False, indent=2), encoding='utf-8')
    log(f"  Shared mouth source basis verified: maximum export residual {expression_basis['source_reconstruction_error_max']:.8f}")
log(f"  报告 {rp}")
(OUT_DIR / "bake_log.txt").write_text("\n".join(log_lines), encoding="utf-8")

section("汇总")
log(f"  代理骨 {len(created)} 根，骨架总计 {len(rig.data.bones)} 骨")
log(f"  烘焙 {len([k for k,v in baked.items() if v['kind']=='expression'])} 表情 + "
    f"{len([k for k,v in baked.items() if v['kind']=='action'])} 动作")
bad = [k for k, v in export.items() if not v.get("ok")]
log(f"  导出 {len(export)-len(bad)}/{len(export)} 成功" + (f"，失败: {bad}" if bad else ""))
still = [k for k, v in verify.items() if v["peak_bone_travel"] <= 1e-4]
log(f"  运动验证: {'全部有运动' if not still else '无运动的有 ' + str(still)}")
dead = [k for k, v in vert_verify.items() if v["moved_verts"] == 0]
log(f"  顶点验证: {'全部生效' if not dead else '未生效的有 ' + str(dead)}")
