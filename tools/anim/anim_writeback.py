"""`.anim` 写回底座：蒙皮归属、head/torso 配对、写回、接缝平滑、重采样。

**这个模块不烘物理。** 它原名 `bake_physics_into_anim.py`，里面确实有一个
自研的二自由度角度弹簧解算器；2026-08-20 从 PMX 实读到原作者参数后那条路被
否掉（刚度全 0 / 角度限制 ±10° / 满阻尼，是「受约束的摆」不是弹簧振子，
自研解算器建在 `−k·ax` 回归项上，等于在错的方程里找参数）。现役烘焙器是
`bake_hair_bullet.py`。

2026-08-27 把死掉的解算器部分切掉（`solve` / `main` / 全部 `DRIVE_*`
`COLLISION_*` 参数等 643 行），只留 Bullet 路线**真正在 import 的底座**。
被切掉的整份原文件在
`trash/cleanup_20260827/superseded/bake_physics_into_anim_full.py`。
改名的理由：一个叫「bake_physics_into_anim」却不烘物理的模块会误导下一个人
去读它的解算器，而那套结论已经被推翻。

## 为什么写回要全程在 .anim 自己的空间里算

试过「.anim → blend action → 算 → 写回」，卡在坐标空间：部署的 anim 共享一个
rest，它与 blend 骨架的 bind pose **逐骨有差异**，没有单一刚体变换能消掉
（穷举 6 种公式 × 8 个候选轴修正，最优残差仍 19.58，见
`work/evidence/map_spaces.py`）；那个 rest 也**不是第 0 帧**（手肘差 106.59°），
所以「相对 rest 的增量」连正确零点都没有。五版推导全部渲出错误姿态。

所以：读文件 → 自建 FK → 算 → 把摇骨通道写回文件。身体骨的采样数据原样搬运，
一个字节不改，不存在空间对齐问题。

## 接口

    HEAD_MESH / TORSO_MESH             调用方可覆盖的**模块级属性**
    BODY_PREFIX / HEAD_PREFIX          同上
    skin_owner_map(...)                骨 → head/torso 归属（**靠蒙皮实测**）
    companion_path(...)                找配对的另一件 anim
    write_back(...)                    写回摇骨通道
    smooth_seam(...)                   循环接缝平滑
    resample(...)                      按归一化时间重采样（配对写回保留对方帧数）

`HEAD_MESH` 这些是**模块级属性而不是默认参数** —— 调用方必须
`BPI.HEAD_MESH = x` 覆盖，之后的函数调用才会生效（`bake_hair_bullet.py`
就是这么用的）。写成默认参数会在 import 时固化，多角色场景下静默用错 mesh。

**配对写回必须保留对方自己的帧数**：`min(cF, F)` 会把 head 压掉 80 帧
（见 memory `ck3-companion-write-must-keep-own-framecount`）。
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import moe_paths as MP    # noqa: E402  资源路径解析（子 mod 感知）

# 原先这里 import moe_physics_contract —— 那是弹簧解算器的摇骨参数合同，
# 随解算器一起切掉了（模块也已移进 trash）。Bullet 路线的参数直接从 PMX
# 实读刚体/关节，不再有「参数合同」这一层。
import pdx_anim_write                  # noqa: E402
import pdx_parse
import retarget_vanilla_anim as RV     # noqa: E402

# 两个角色共用同一个部署目录（ANIM_DIR 不用按角色区分），但文件名前缀和
# mesh 文件名不同（芙洛伦 moe_body_/moe_head_/moe_torso.mesh/moe_head.mesh，
# 洛茜 moe_lau_body_/moe_lau_head_/moe_lau_torso.mesh/moe_lau_head.mesh）。
# 调用方（如 bake_hair_bullet.py）换角色时直接改这四个模块属性即可，
# skin_owner_map/companion_path 运行时读取，不是导入时固化。
HEAD_MESH = "moe_head.mesh"
TORSO_MESH = "moe_torso.mesh"
BODY_PREFIX = "moe_body_"
HEAD_PREFIX = "moe_head_"

# ---------------------------------------------------------------- 四元数工具
# 用 RV 里已经验证过的实现，不另写一套（免得两处约定漂移）
q_mul, q_conj, q_norm, q_apply = RV.q_mul, RV.q_conj, RV.q_norm, RV.q_apply


def q_angle_between(a, b):
    d = abs(sum(x * y for x, y in zip(q_norm(a), q_norm(b))))
    return 2.0 * math.acos(max(-1.0, min(1.0, d)))


# 2026-09-06 切掉：classify / build_chains（引用已退役的 PC.PHYSICS_GROUPS，死代码）、
# 纯 Python 弹簧解算器 Spring 与 bind 空间碰撞 Collider（自研弹簧路 2026-08-20 被否，
# Blender Bullet 路 2026-09-05 被原生 Bullet 取代，两者都不再有调用方）。
# 原文在 git 历史（e496d20 之前的 tools/anim/anim_writeback.py）。


def smooth_seam(frames, tail, tol_deg):
    """尾段 smoothstep 混合回首帧。与 bake_hair_physics.smooth_loop_seam 同理
    （用户要求的「最后几帧做平滑滤波」），这里用元组四元数做 slerp。"""
    n = len(next(iter(frames.values())))
    if n < tail + 2:
        return 0.0, 0
    touched = 0
    for nm, qlist in frames.items():
        head = qlist[0]
        if math.degrees(q_angle_between(qlist[-1], head)) <= tol_deg:
            continue
        touched += 1
        for k in range(tail):
            i = n - tail + k
            t = (k + 1) / tail
            w = t * t * (3 - 2 * t)     # 两端斜率为 0，无速度突变
            qlist[i] = slerp(qlist[i], head, w)
    after = max(math.degrees(q_angle_between(q[-1], q[0]))
                for q in frames.values())
    return after, touched


def resample(seq, n):
    """按归一化时间重采样（原 phys_channels._resample）。帧数相同就逐帧拷贝（零误差）。

    配对写回必须保留对方自己的帧数（[[ck3-companion-write-must-keep-own-framecount]]），
    物理是连续摆动，线性插值无损。"""
    m = len(seq)
    if m == n:
        return list(seq)
    if m == 1:
        return [tuple(seq[0])] * n
    out = []
    for i in range(n):
        x = i * (m - 1) / (n - 1) if n > 1 else 0.0
        a = int(x)
        b = min(a + 1, m - 1)
        f = x - a
        out.append(tuple(seq[a]) if f <= 0.0
                   else tuple(p + (q - p) * f for p, q in zip(seq[a], seq[b])))
    return out


def slerp(a, b, t):
    d = sum(x * y for x, y in zip(a, b))
    if d < 0.0:
        b = tuple(-x for x in b)
        d = -d
    if d > 0.9995:
        return q_norm(tuple(a[i] + (b[i] - a[i]) * t for i in range(4)))
    th0 = math.acos(max(-1.0, min(1.0, d)))
    th = th0 * t
    s0 = math.sin(th0 - th) / math.sin(th0)
    s1 = math.sin(th) / math.sin(th0)
    return q_norm(tuple(a[i] * s0 + b[i] * s1 for i in range(4)))


# ------------------------------------------------- 摇骨的蒙皮归属
# 为什么需要这张表（2026-08-20，用户指出「为啥全都没物理了」）：
# 头发只蒙在 moe_head.mesh 上（36 根头发骨带皮），moe_torso.mesh 里
# **一根头发骨都没有**；反过来 skirt/chest/butt 只蒙在 torso 上。
# 而 CK3 是头身分离播放的（原版就是 `default = { head = "..." torso = "..." }`，
# 两份独立 .anim，原版头 61 骨 / 身 134 骨），每件 mesh 只吃自己那份 anim。
# 于是「把全部摇骨通道写进 body anim」这件事，等于把头发物理烘到了一件
# 没有头发的 mesh 上：头发几何听 head anim 的，而 head anim 里头发骨零通道
# —— 头发就按 rest 完全静止。裙子/胸/臀反而是对的（它们确实在 torso 上），
# 这正是当时「衣服粗看没问题、头发不动」的原因。
# 所以摇骨通道必须按**蒙皮归属**分流写到对应的那份 .anim 里。
# 见 [[ck3-head-torso-need-paired-anims]]。
_SKIN_OWNER_CACHE = {}


def skin_owner_map(mesh_names=None):
    """{骨名: 蒙它的 mesh 文件名}。只认权重 > 0 的骨，rest 里存在但没带皮的
    骨（root/camera 那类）不算 —— 见 [[ck3-anim-bone-must-exist-in-mesh]]。"""
    if mesh_names is None:
        mesh_names = (HEAD_MESH, TORSO_MESH)
    cache_key = tuple(str(m) for m in mesh_names)
    if cache_key in _SKIN_OWNER_CACHE:
        return _SKIN_OWNER_CACHE[cache_key]
    # Workers and the debug server can load several characters in one process.
    # Their bone names overlap, but the owning mesh is character-specific.
    owners = {}
    for mf in mesh_names:
        # **必须走 MP.find**：mesh 在 BASE/models/ 而 ANIM_DIR 是 BASE 根。
        # 直接拼会得到不存在的路径，然后被 `if not exists: continue` 静默
        # 跳过 —— 结果 owner 表为空，所有骨都判成「没蒙皮」。
        try:
            mp = MP.find(mf)
        except Exception:
            continue
        if not mp.exists():
            continue
        tree = pdx_parse.parse(mp.read_bytes())
        bn = []

        def _kids(n):
            ch = n.get("_children") or []
            return list(ch.items()) if isinstance(ch, dict) else list(ch)

        def _props(n):
            return n.get("_props") or {}

        def walk(n):
            for k, c in _kids(n):
                pr = _props(c)
                if "ix" in pr and "tx" in pr:
                    bn.append(k)
                walk(c)
        walk(tree)
        for _, obj in _kids(tree):
            for _, mn in _kids(obj):
                for _, cn in _kids(mn):
                    pr = _props(cn)
                    if "p" not in pr:
                        continue
                    for n4, gn in _kids(cn):
                        gp = _props(gn)
                        if n4 == "skin" and "bones" in gp:
                            ix, w = gp["ix"][1], gp["w"][1]
                            for i, b in enumerate(ix):
                                if b < 0 or b >= len(bn):
                                    continue
                                if w and w[i] <= 0.0:
                                    continue
                                owners.setdefault(bn[b], mf)
    _SKIN_OWNER_CACHE[cache_key] = owners
    return owners


_PAIR_CACHE = {}


def config_pairs():
    """从 animations.txt 实读 torso→head 的配对。

    为什么不能只靠改名（moe_body_X → moe_head_X）：原版有的动作**故意**不配
    同名头动画，例如 `animation = { head = "idle" torso = "idleInfant_entry" }`
    —— 婴儿只换身体、头用普通 idle。所以 moe_head_idleInfant 根本不存在，
    改名法会把这类的头发通道整批丢掉。
    见 [[ck3-portrait-animation-four-name-layers]]：名字有四层，要读配置。
    """
    if _PAIR_CACHE:
        return _PAIR_CACHE
    # animations.txt 归核心（它是机制文件，不是模型资产）。
    import moe_out as _O
    cfg = (_O.root(_O.CORE) / "gfx" / "portraits"
           / "portrait_animations" / "animations.txt")
    if not cfg.exists():
        return _PAIR_CACHE
    txt = cfg.read_text(encoding="utf-8", errors="ignore")
    for m in re.finditer(r'head\s*=\s*"([^"]+)"\s*torso\s*=\s*"([^"]+)"', txt):
        h, t = m.group(1), m.group(2)
        _PAIR_CACHE.setdefault(t, h)
    for m in re.finditer(r'torso\s*=\s*"([^"]+)"\s*head\s*=\s*"([^"]+)"', txt):
        t, h = m.group(1), m.group(2)
        _PAIR_CACHE.setdefault(t, h)
    return _PAIR_CACHE


def companion_path(base, owner_mesh, source_dir=None):
    """给某件 mesh 找它该配的那份 .anim。
    我们的命名是 moe_body_* / moe_head_*（洛茜是 moe_lau_body_*/moe_lau_head_*，
    前缀整体替换，BODY_PREFIX/HEAD_PREFIX 决定），配对关系要实读文件、
    不能凭猜（见 [[ck3-portrait-animation-four-name-layers]]）。"""
    character = MP.char_of(HEAD_MESH)
    if not character:
        raise ValueError(f"Unknown companion character: {HEAD_MESH}")
    if owner_mesh not in (HEAD_MESH, TORSO_MESH):
        return None
    if base.startswith('moe_body_'):
        base = BODY_PREFIX + base[len('moe_body_'):]
    elif base.startswith('moe_head_'):
        base = HEAD_PREFIX + base[len('moe_head_'):]
    directory = Path(source_dir).resolve() if source_dir is not None else MP.require_base_anims(character)
    if directory.is_relative_to((ROOT / "mod").resolve()):
        raise ValueError("Companion input must not come from shipped model animations")
    if directory.is_relative_to((ROOT / "work" / "physics_release").resolve()):
        raise ValueError("Physics release outputs cannot be used as companion inputs")

    def base_candidate(filename):
        # Respect an explicit input directory; never mix another build's head.
        direct = directory / filename
        if direct.is_file():
            return direct
        prefix = MP.CHARACTERS[character]['prefix']
        generic = directory / ('moe_' + filename[len(prefix):])
        return generic if generic.is_file() else direct

    if owner_mesh == HEAD_MESH:
        want = base.replace(BODY_PREFIX, HEAD_PREFIX, 1)             if base.startswith(BODY_PREFIX) else None
    elif owner_mesh == TORSO_MESH:
        want = base.replace(HEAD_PREFIX, BODY_PREFIX, 1)             if base.startswith(HEAD_PREFIX) else None
    else:
        return None
    if want:
        # missing_ok：find 默认找不到就抛错，会截断下面「查配置的真实配对」这一步
        # （2026-09-06 洛茜 moe_lau_body_emotion_loss 没有同名 head，全量烘焙在这里炸掉）。
        cp = base_candidate(f"{want}.anim")
        if cp.exists():
            return cp
    # 同名法找不到就查配置的真实配对（见 config_pairs 的注释）。
    # animations.txt 里的名字是 state 名（不带 moe_body_/moe_head_ 前缀，
    # 可能带 _entry 后缀），要还原成文件名再试。
    if owner_mesh == HEAD_MESH and base.startswith(BODY_PREFIX):
        state = base[len(BODY_PREFIX):]
        pairs = config_pairs()
        for key in (state, state + "_entry"):
            h = pairs.get(key)
            if not h:
                continue
            for cand in (h, h[:-len("_entry")] if h.endswith("_entry") else h):
                cp = base_candidate(f"{HEAD_PREFIX}{cand}.anim")
                if cp.exists():
                    return cp
    return None


# ---------------------------------------------------------------- 写回
def write_back(path, meta, bones, ts, qs, F, sway_frames, absolute=False,
               sway_moves=None, *, observable_bones=None):
    """把摇骨通道并进 .anim 并写出。

    身体骨的采样值**原样搬运**（这是方案 2 的全部意义：不做任何空间换算，
    所以不可能引入姿态错误）。摇骨从 sa='' 升级为 sa='q' 并补上采样。

    `sway_moves`（可选）: {骨名: [每帧 (x,y,z)]}，给 PMX 模式 1 的物理骨补
    **位移**通道 —— 这类骨的位置也由刚体驱动。给了就把该骨的 sa 升级为含
    't'，并按帧写 3 个 float。语义是 `Wp[n] = Wp[parent] + q_apply(Wq[parent],
    t)`，即骨在父骨局部系里的完整位置，引擎照常做 FK。
    裙子/披风的运动几乎全在位移里，不写 t 它们在游戏里就是静止的
    （2026-08-23：`.anim` 里 skirt 转角仅 0.01°，而刚体位移中位 16.74）。

    采样数组是**帧主序**的：每帧先按骨序写完所有 t，再写完所有 q
    （见 RV.unpack_samples 的拆包逻辑，这里是它的逆）。
    """
    sway_moves = sway_moves or {}
    out_bones = []
    for b in bones:
        nm = b["name"]
        sa = b["sa"]
        if nm in sway_frames and "q" not in sa:
            sa = sa + "q"
        if nm in sway_moves and "t" not in sa:
            # 't' 必须排在 'q' 前面：sa 是标志串，但采样的**顺序**由
            # unpack_samples 固定为「每帧先所有 t、再所有 q」，
            # 而 t_bones/q_bones 各自按骨序遍历，所以串内顺序不影响拆包。
            # 这里仍写成 't'+其余，与原版 .anim 的写法保持一致。
            sa = "t" + sa
        out_bones.append((nm, sa, list(b.get("t", (0, 0, 0))),
                          list(b.get("q", (0, 0, 0, 1))),
                          list(b.get("s", (1.0,)))))

    rest_q_of = {b["name"]: tuple(b.get("q", (0, 0, 0, 1))) for b in bones}
    rest_t_of = {b["name"]: tuple(b.get("t", (0.0, 0.0, 0.0))) for b in bones}
    t_bones = [nm for nm, sa, *_ in out_bones if "t" in sa]
    q_bones = [nm for nm, sa, *_ in out_bones if "q" in sa]
    flat_t, flat_q = [], []
    for f in range(F):
        for nm in t_bones:
            if nm in sway_moves:
                # 物理骨的位移。**不能回落到 ts**：这类骨原本 sa 里没有 't'，
                # ts 里根本没有它的条目（KeyError），有也不是物理解。
                flat_t.extend(sway_moves[nm][f])
            elif nm in ts:
                flat_t.extend(ts[nm][f])
            else:
                # 原本就带 't' 却没采样值的骨（理论上不该有），退回 rest
                flat_t.extend(rest_t_of.get(nm, (0.0, 0.0, 0.0)))
        for nm in q_bones:
            if nm in sway_frames:
                # sway_frames 里是**弹簧增量**，而 .anim 每帧 q 存的是骨骼的
                # **绝对局部朝向**（见 [[pdx-anim-rest-is-frame0-not-bindpose]]）。
                # 2026-08-19 的 bug：这里直接写了增量，等于把每根摇骨的 rest
                # 朝向整个丢掉、硬拽到接近单位四元数。hair 的 rest 离单位中位
                # 只有 19.5°（偏了但不显眼），skirt/chest/butt 却是 165~180°
                # —— 那些骨被翻转近 180°，几何内外翻，用户看到的就是
                # 「裙子和部分衣服没了」。
                # 解算器自己用的是 q_mul(基准朝向, 增量)，写回必须同一套。
                if absolute:
                    # Bullet 那条路（bake_hair_bullet.py）给的已经是**绝对**
                    # 局部朝向，不能再乘基准，否则等于把 rest 叠两遍。
                    flat_q.extend(q_norm(sway_frames[nm][f]))
                else:
                    base = (qs[nm][f] if nm in qs
                            else rest_q_of.get(nm, (0.0, 0.0, 0.0, 1.0)))
                    flat_q.extend(q_norm(q_mul(base, sway_frames[nm][f])))
            elif nm in qs:
                flat_q.extend(qs[nm][f])
            else:
                flat_q.extend(rest_q_of.get(nm, (0.0, 0.0, 0.0, 1.0)))
    size = pdx_anim_write.write_anim(path, meta["fps"][0], F, out_bones,
                                    flat_t, flat_q, observable_bones=observable_bones)
    # 不再另存档案（work/phys_baked 机制 2026-09-06 退役）：物理版由 moe_bullet_bake.py
    # 直接写进 moe_physics_<pack> 包，基础版与物理版天然分离，重跑 graft 不会抹掉物理。
    return size


def _ck_from_path(path):
    """从 .anim 路径推角色代号：.../portraits/moe/<ck>/anims/xxx.anim"""
    parts = Path(str(path)).resolve().parts
    for i, p in enumerate(parts):
        if p == "anims" and i >= 1:
            return parts[i - 1]
    return ""
