"""为每个身体动作生成配套的头部动画（表情 fx + 该动作的脊椎），修「头脱开躯干」。

## 问题（实测，tools/verify/check_head_torso_sync.py）

head 与 torso 是两个独立 entity，各播自己的 animation。实测通道
（tools/anim/anim_channels.py）:

    表情 anim  8 个 t 通道，**全是 fx 代理骨**；脊椎/颈/头全静态
    身体 anim  53 个 q 通道，含 bn_m_spine_low / bn_m_spine_up / bn_m_groove
               但**不含** bn_m_neck / bn_m_head / bn_m_root

我们的 attach 是 `bn_m_root → bn_m_root`，而 `bn_m_root` 在身体动作里是
静态骨。躯干脊椎倾 18~60°，头部实体按自己那份（脊椎静止的）表情动画摆位:

    最坏 moe_body_grief   颈错位 17.3 单位   头错位 21.5 单位
    （头部 Y 跨度约 60，即错开 36% 头高 —— 头会明显飘在躯干外）

## 为什么原版没这问题

原版 `male_head.mesh` 只有 61 骨、**没有脊椎**，attach 写
`bn_sp_thoracic → head_root`，而胸椎**正是 torso 动画驱动的骨**
（实测 `female_body_idle.anim` 里 `bn_sp_thoracic` 带 q，`bn_sp_cervical` 反而静态）。
头骑在被驱动的骨上，躯干一倾头自然跟着 —— attach 是动态的。

我们的 head.mesh 从全身骨架拆出，带完整脊椎链
（`bn_m_head ← bn_m_neck ← bn_m_spine_up ← bn_m_spine_low ← bn_m_waist
   ← bn_m_groove ← bn_m_center ← bn_m_root`），
attach 又必须同名骨互贴（head 顶点已是世界坐标，见 10_moe_types.txt 注释）。
所以头部实体**自己能算脊椎**，缺的只是脊椎的动画数据。

## 为什么必须一个身体动作配一个头部动画

不能只改那 4 个表情文件。`.asset` 里 head 侧是**多对一**映射:

    moe_idle_calm         41 个 state
    head_emotion_sadness  24 个（sadness / grief / worry / stress 共用）
    head_emotion_happy    18 个（happiness / laugh / flirtation 共用）

而这些块的躯干脊椎摆幅各不相同（grief 23.4° vs laugh 60.5°）。一份文件
配不了四种脊椎。所以按**身体动作**逐个生成 `moe_head_<base>.anim`，
每个 = 对应情绪的表情 fx 通道 + 该身体动作自己的脊椎通道。

## 重采样

两边帧率/帧数不同（表情 30fps，身体约 15fps）。身体通道逐帧复制；
表情源按入场、持续循环、退出或单次表演重采样，循环保留情绪峰值。
说话、眨眼、扫视按真实时间采样，再叠加到情绪。四元数用近弧 slerp。

## rest 的处理

`.anim` 的 rest 是**该动作自己的第 0 帧**，不是 bind pose
（见 README「原版动作重定向结论」）。所以脊椎骨的 rest 必须一并取自
身体动画，否则第 0 帧与后续帧不连续，播放起来第一帧会跳。

用法:
  python tools/anim/graft_spine_into_head_anim.py --dry-run
  python tools/anim/graft_spine_into_head_anim.py
"""
import argparse
import hashlib
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import moe_paths as MP    # noqa: E402  资源路径解析（子 mod 感知）
import pdx_parse as P          # noqa: E402
import pdx_anim_write as W     # noqa: E402
import moe_expression_contract as C  # noqa: E402

# stdout 转 utf-8。**用 reconfigure 不用 TextIOWrapper** —— 后者每次都新建一个
# 包装对象，多个模块各包一层时外层被 GC 会关掉共享的 buffer，于是后面任何
# print 都抛 `ValueError: I/O operation on closed file`（动画其实已经写完了，
# 只是收尾的 print 炸掉，看起来像整条失败）。reconfigure 原地改编码，天然幂等。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 默认是芙洛伦的 mod 目录（向后兼容）。**换角色必须用 --out-dir 覆盖**：
# 表情源、身体动画、输出头部动画都在这个目录里找/写。
# 单体目录已退役（2026-08-29）：产物按子 mod 直写 mod/<mod_id>/。
# MP.BASE 解析到第一个存在的模型包；**定位具体文件用 MP.find()**，
# 它会遍历全部子 mod（flo 在 gfl2 包、lau/per/eld 在 endfield 包）。
ANIM_DIR = MP.BASE

# 要移植的骨。只取**脊椎链**:
#   不含 bn_m_center / bn_m_root —— 它们带整体位移，头部实体已通过 attach
#   跟着躯干走，再叠一次就是双倍位移。
#   含 bn_m_groove —— 它带 t 通道（呼吸起伏），不移植头就不随呼吸升降。
#
# 不含 `ground_joint` / `body_root`：这两根兼容骨会改变 CK3 对附着实体的根
# 变换，已从模型骨架退役。原版 body_root 的有效动作仍由 bn_m_groove 承担。
SPINE_BONES = ["bn_m_groove", "bn_m_waist", "bn_m_spine_low", "bn_m_spine_up"]

# 相机锚骨 camera_torso_look_at 与脊椎同等对待：rest 与通道**永远**照抄身体动画
# （2026-09-05）。重定向脚本把它钉死在世界空间时，恒定的补偿量会被
# `_drop_constant` 压进 rest、通道消失 —— 于是它不在 `body_driven` 里，
# head 侧就会沿用表情源那份**未补偿**的 rest，两个 entity 对同一根骨给出
# 两个不同的世界位置。引擎按骨名共享姿态，相机会在两份之间随机。
BODY_ANCHORED = SPINE_BONES + ["camera_torso_look_at"]

# 按绝对时间平铺（而不是拉伸到身体时长）的表情源：节奏型内容。
TILE_EXPRS = {"talk", "life", "reading_gaze"}

# ## 生命层（2026-09-05）
#
# 眨眼 + 扫视，叠在**每一条** head 动画上。与表情源同目录、由 bake 产出。
# 原版 357 条 head 动画 293 条眼球在转、288 条在眨眼；我们此前 169 条中性头
# （reading / idle / 王座全在内）眼睛一动不动 —— 用户看 reading 时第一眼就
# 是「没有眼球动画」。表情源自己不带这层（happy 只有 3 秒、被拉到 10 秒后
# 眨眼会变成慢闭眼），所以在这里按绝对时间平铺后叠加：
#   眼球骨（t）    相加：情绪偏置 + 扫视
#   眨眼 fx 骨（t）取模长大的：sadness 的半闭 0.38 遇到眨眼 1.0 时眨眼赢
# 阅读/书写类动作用 `moe_reading_gaze.anim` **替换**生命层的视线（低头扫行），
# 眨眼仍取生命层。判据是产物基名含 READING_KEYWORDS。
LIFE_EXPR = "moe_life.anim"
READING_EXPR = "moe_reading_gaze.anim"
READING_KEYWORDS = ("reading", "writ", "page_flipping")
# 节奏层在片段两端的平滑过渡时长（秒）。首尾各保留一个静止采样间隔，
# 同时对齐循环位置和离散速度；只作用于 talk/life/reading，不压掉持续情绪。
TILE_FADE_SEC = 0.30

# 身体动作文件名 → base（与 register_body_anims.BODY_ANIMS 的 base 一致）
BODY_FILE_TO_BASE = {
    "moe_body_bold.anim":          "AI_bold",
    "moe_body_content.anim":       "AI_content",
    "moe_body_compassionate.anim": "AI_compassionate",
    "moe_body_callous.anim":       "AI_callous",
    "moe_body_happy.anim":         "emotion_happy",
    "moe_body_sadness.anim":       "emotion_sadness",
    "moe_body_angry.anim":         "emotion_angry",
    "moe_body_stressed.anim":      "emotion_stressed",
    "moe_body_boredom.anim":       "emotion_boredom",
    "moe_body_worry.anim":         "emotion_worry",
    "moe_body_laugh.anim":         "emotion_laugh",
    "moe_body_fear.anim":          "emotion_fear",
    "moe_body_disgust.anim":       "emotion_disgust",
    "moe_body_grief.anim":         "emotion_grief",
    "moe_body_praying.anim":       "praying",
    "moe_body_disapproving.anim":  "emotion_disapproving",
    "moe_body_flirty.anim":        "emotion_flirty",
}

# 没有配套身体动作的头部动画，脊椎源用 idle。
#
# idle 本身: moe_idle_calm 是 idle 系 head state 的目标，它自己既是中性脸源
# 又是脊椎源 —— 原地重写有自赋值风险，所以输出到独立文件名。
#
# shock: 原版**没有** shock 身体动作（fear 已经配给 emotion_fear 了），
# 所以 emotion_shock 的 torso 播的是 moe_idle_calm。head 侧若播纯表情
# moe_shock.anim（脊椎全静态），就会与躯干的 idle 脊椎错开 11.2 单位。
# 必须也生成一份「shock 表情 + idle 脊椎」。
EXTRA_PAIRS = [
    ("moe_idle_calm.anim", "moe_idle_calm.anim", "moe_head_idle_calm.anim"),
    ("moe_shock.anim",     "moe_idle_calm.anim", "moe_head_emotion_shock.anim"),
]


def read_anim(path):
    tree = P.parse(Path(path).read_bytes())
    fps = nf = None
    bones, cur = [], None
    samples = {}
    for p, name, _k, val in P.walk(tree):
        if p == "/info":
            if name == "fps":
                fps = val[0]
            elif name == "sa":
                nf = val[0]
        elif p.startswith("/info/") and p.count("/") == 2:
            bn = p.split("/info/", 1)[1].split("[")[0]
            if cur is None or cur["name"] != bn:
                cur = {"name": bn}
                bones.append(cur)
            cur[name] = val
        elif p == "/samples":
            samples[name] = val
    for b in bones:
        b["sa_s"] = (b.get("sa") or [""])[0] or ""
    return {"fps": fps, "nf": nf, "bones": bones, "samples": samples}


def unpack(a):
    qb = [b["name"] for b in a["bones"] if "q" in b["sa_s"]]
    tb = [b["name"] for b in a["bones"] if "t" in b["sa_s"]]
    qs, ts = a["samples"].get("q") or [], a["samples"].get("t") or []
    Q = {b: [] for b in qb}
    T = {b: [] for b in tb}
    for fr in range(a["nf"] or 0):
        qbase = fr * len(qb) * 4
        if qbase + len(qb) * 4 > len(qs):
            break
        for i, b in enumerate(qb):
            Q[b].append(tuple(qs[qbase + i * 4: qbase + i * 4 + 4]))
        tbase = fr * len(tb) * 3
        for i, b in enumerate(tb):
            if tbase + i * 3 + 3 <= len(ts):
                T[b].append(tuple(ts[tbase + i * 3: tbase + i * 3 + 3]))
    return Q, T


def slerp(a, b, u):
    d = sum(x * y for x, y in zip(a, b))
    if d < 0:                      # 走近弧，否则会甩头
        b = tuple(-x for x in b)
        d = -d
    if d > 0.9995:
        v = tuple(x + (y - x) * u for x, y in zip(a, b))
    else:
        th = math.acos(max(-1.0, min(1.0, d)))
        s = math.sin(th)
        w1, w2 = math.sin((1 - u) * th) / s, math.sin(u * th) / s
        v = tuple(x * w1 + y * w2 for x, y in zip(a, b))
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return tuple(x / n for x in v)


def _resample(seq, n_out, interp):
    if not seq:
        return []
    if len(seq) == 1:
        return [seq[0]] * n_out
    out = []
    for i in range(n_out):
        u = i / max(1, n_out - 1) * (len(seq) - 1)
        i0 = int(math.floor(u))
        i1 = min(i0 + 1, len(seq) - 1)
        out.append(interp(seq[i0], seq[i1], u - i0))
    return out


def resample_q(seq, n):
    return _resample(seq, n, slerp)


def resample_t(seq, n):
    return _resample(seq, n, lambda a, b, f:
                     tuple(x + (y - x) * f for x, y in zip(a, b)))


def expression_kind(path):
    """Recognize both moe_talk and deployed names such as moe_jia_talk."""
    stem = Path(path).stem.lower()
    for kind in sorted(set(C.EXPRESSIONS) | {"idle_calm"}, key=len, reverse=True):
        if stem == kind or stem.endswith("_" + kind):
            return kind
    return "neutral"


def _smoothstep(x):
    x = max(0.0, min(1.0, x))
    return x * x * x * (x * (x * 6.0 - 15.0) + 10.0)


def _lerp(a, b, u):
    return tuple(x + (y - x) * u for x, y in zip(a, b))


def _sample(seq, frame, interp):
    frame = max(0.0, min(float(len(seq) - 1), frame))
    i = int(frame)
    return interp(seq[i], seq[min(i + 1, len(seq) - 1)], frame - i)


def _phase_fraction(key, layer):
    data = hashlib.sha256(f"{key}/{layer}".encode("utf-8")).digest()
    return int.from_bytes(data[:8], "big") / 2**64


def _is_face_bone(name):
    low = name.lower()
    return low.startswith("bn_m_fx_") or (
        any(mark in low for mark in C.EYE_CHAIN_MARKERS)
        and not any(mark in low for mark in ("lash", "brow", "lid", "shadow", "white")))


def _check_source(E, label):
    if not E["nf"] or E["nf"] < 1 or not E["fps"] or E["fps"] <= 0:
        raise ValueError(f"{label}: invalid frame count/fps")
    names = [b["name"] for b in E["bones"]]
    if len(set(names)) != len(names):
        raise ValueError(f"{label}: duplicate bone names")
    for channel, width in (("q", 4), ("t", 3)):
        expected = E["nf"] * width * sum(channel in b["sa_s"] for b in E["bones"])
        samples = E["samples"].get(channel) or []
        if len(samples) != expected or not all(math.isfinite(x) for x in samples):
            raise ValueError(f"{label}: invalid {channel} sample array")
    if any("s" in b["sa_s"] for b in E["bones"]):
        raise ValueError(f"{label}: animated scale is unsupported by this writer")


def _check_layer(E, L, label):
    _check_source(L, label)
    expected = [b["name"] for b in E["bones"]]
    actual = [b["name"] for b in L["bones"]]
    if expected != actual:
        mismatch = next((i for i, (a, b) in enumerate(zip(expected, actual)) if a != b),
                        min(len(expected), len(actual)))
        raise ValueError(f"{label}: bone table/order mismatch at {mismatch} "
                         f"({len(expected)} vs {len(actual)} bones)")
    # Source clips share a neutral first pose. Equal names with incompatible rest
    # transforms would make adding their deltas physically meaningless.
    for a, b in zip(E["bones"], L["bones"]):
        if not _is_face_bone(a["name"]):
            continue
        for channel, fallback in (("t", [0, 0, 0]), ("q", [0, 0, 0, 1])):
            av, bv = a.get(channel) or fallback, b.get(channel) or fallback
            if max(abs(x - y) for x, y in zip(av, bv)) > 1e-5:
                raise ValueError(f"{label}: incompatible face rest {a['name']}:{channel}")


def tile_to_body(E, EQ, ET, B, n, phase_key="", layer="life"):
    """Sample rhythm sources at real time, with deterministic per-action phase.

    Quintic windows return only the rhythm layers to neutral at both ends. The
    first and last sample intervals are stationary, so joining repeated clips
    matches both sampled position and velocity. Sustained emotion is separate.
    """
    source_fps, body_fps = E["fps"], B["fps"]
    period = max(1, E["nf"] - 1)
    offset = _phase_fraction(phase_key, layer) * period if phase_key else 0.0
    positions = [(offset + i * source_fps / body_fps) % period for i in range(n)]
    fade_n = min(TILE_FADE_SEC * body_fps, max(1.0, (n - 3) / 2))
    weights = [_smoothstep(min(i - 1, n - 2 - i) / fade_n) for i in range(n)]
    rest = {b["name"]: b for b in E["bones"]}

    def channels(source, channel, fallback, interp):
        result = {}
        for name, seq in source.items():
            if not seq or not _is_face_bone(name):
                continue
            neutral = tuple(rest[name].get(channel) or fallback)
            result[name] = [interp(neutral, _sample(seq, pos, interp), w)
                            for pos, w in zip(positions, weights)]
        return result

    return (channels(EQ, "q", [0, 0, 0, 1], slerp),
            channels(ET, "t", [0, 0, 0], _lerp))


def emotion_to_body(E, EQ, ET, B, n, phase, style):
    """Resample source performance for entry, sustained loop, exit or one shot."""
    hold = max(0.0, min(1.0, style.get("hold_sample", 0.5)))
    transition = min(0.7 * B["fps"], max(1, n - 1))
    rest = {b["name"]: b for b in E["bones"]}

    def channels(source, channel, fallback, interp):
        result = {}
        for name, seq in source.items():
            if not seq or not _is_face_bone(name):
                continue
            neutral = tuple(rest[name].get(channel) or fallback)
            peak = _sample(seq, hold * (len(seq) - 1), interp)
            out = []
            for i in range(n):
                if phase == "loop":
                    # Hold expression at the seam, with a gentle 4% variation.
                    # No hash phase here: entry -> loop -> exit share the peak.
                    u = max(0.0, min(1.0, (i - 1) / max(1, n - 3)))
                    gain = 0.98 + 0.02 * math.cos(2 * math.pi * u)
                    value = interp(neutral, peak, gain)
                elif phase == "entry":
                    position = hold * _smoothstep(i / transition)
                    value = _sample(seq, position * (len(seq) - 1), interp)
                elif phase == "exit":
                    position = hold + (1 - hold) * _smoothstep(i / transition)
                    value = _sample(seq, position * (len(seq) - 1), interp)
                else:
                    value = _sample(seq, i / max(1, n - 1) * (len(seq) - 1), interp)
                out.append(value)
            result[name] = out
        return result

    return (channels(EQ, "q", [0, 0, 0, 1], slerp),
            channels(ET, "t", [0, 0, 0], _lerp))


def _overlay_translation(target, E, layer, L, gain=1.0, maximum=False):
    """Add gaze deltas or select the stronger same-slot expression/blink/talk.

    Maximum composition caps mouth opening at an existing source envelope;
    emotional jaw opening and speech cannot double the fitted deformation.
    """
    erest = {b["name"]: b for b in E["bones"]}
    lrest = {b["name"]: b for b in L["bones"]}
    count = 0
    for name, seq in layer.items():
        if not seq or not _is_face_bone(name):
            continue
        origin = lrest[name].get("t") or [0, 0, 0]
        neutral = erest[name].get("t") or [0, 0, 0]
        delta = [tuple((a - b) * gain for a, b in zip(frame, origin)) for frame in seq]
        if not any(abs(v) > 1e-9 for frame in delta for v in frame):
            continue
        current = target.get(name) or [tuple(neutral)] * len(seq)
        if maximum:
            out = []
            for frame, d in zip(current, delta):
                old = tuple(a - b for a, b in zip(frame, neutral))
                out.append(frame if sum(x*x for x in old) >= sum(x*x for x in d)
                           else tuple(a + b for a, b in zip(neutral, d)))
        else:
            out = [tuple(a + b for a, b in zip(frame, d)) for frame, d in zip(current, delta)]
        target[name] = out
        count += 1
    return count


def _make_room_for_blink(target, E, blink, L, gain):
    """Release wide/squint/narrow controls as each eyelid closes fully.

    Those slots use different proxy bones, so a maximum on the blink slot alone
    cannot prevent an expression's wide eye from counteracting complete closure.
    Ratios use this character's full blink source, never model-unit constants.
    """
    erest = {b["name"]: b for b in E["bones"]}
    lrest = {b["name"]: b for b in L["bones"]}
    _q, source = unpack(L)

    def slot_bones(slot):
        names = [C.SEMANTIC_SLOTS[slot], C.REVERSE_SLOTS.get(slot)]
        for pair, merged in C.BILATERAL_MERGE.items():
            if slot in pair:
                names.extend((merged, merged + "_rev"))
        return [name for name in names if name]

    closures = {}
    for side in ("l", "r"):
        slot = "eye_blink_" + side
        candidates = [name for name in slot_bones(slot) if name in blink and name in source]
        if not candidates:
            continue
        def max_delta(name):
            rest = lrest[name].get("t") or [0, 0, 0]
            return max((math.sqrt(sum((a-b)**2 for a, b in zip(frame, rest)))
                        for frame in source[name]), default=0.0)
        driver = max(candidates, key=max_delta)
        neutral = lrest[driver].get("t") or [0, 0, 0]
        def magnitude(frame):
            return math.sqrt(sum((a-b)**2 for a, b in zip(frame, neutral)))
        full = max_delta(driver)
        if full < 1e-9:
            continue
        closures[side] = [_smoothstep(magnitude(frame) * gain / full) for frame in blink[driver]]

    affected = {}
    for side, closure in closures.items():
        for opposing in ("eye_wide_", "eye_squint_", "eye_narrow_"):
            for name in slot_bones(opposing + side):
                if name in target:
                    affected.setdefault(name, []).append(closure)
    for name, influences in affected.items():
        # A bilateral merged proxy is one shared deformation. Apply the stronger
        # eyelid closure once, not twice (which would over-suppress half blinks).
        closure = [max(amounts) for amounts in zip(*influences)]
        rest = erest[name].get("t") or [0, 0, 0]
        target[name] = [_lerp(frame, rest, amount)
                        for frame, amount in zip(target[name], closure)]


def head_relevant_bones(mesh_path):
    """head.mesh 实际蒙皮到的骨 + 它们到根的祖先链。

    为什么要筛: 中性脸源 `moe_idle_calm.anim` 带全部 53 个 q 通道（含手臂、
    腿、手指）。头部实体的 head.mesh 没有手臂顶点，这些通道不产生画面效果，
    但会让「头部动画」里带着一整套与躯干动作**不一致**的四肢姿势 ——
    数据无害却极易误导后续排查。只保留头链需要的骨，文件也小一半。
    """
    tree = P.parse(Path(mesh_path).read_bytes())
    bones, cur = [], None
    for p, name, _k, val in P.walk(tree):
        if "/skeleton/" not in p:
            continue
        bn = p.split("/skeleton/", 1)[1].split("[")[0]
        if cur is None or cur["name"] != bn:
            cur = {"name": bn}
            bones.append(cur)
        cur[name] = val
    order = sorted(bones, key=lambda b: (b.get("ix") or [0])[0])
    name_by_ix = {(b.get("ix") or [0])[0]: b["name"] for b in order}
    pa_by = {b["name"]: (b.get("pa") or [-1])[0] for b in order}

    used = set()
    cur_ix = None
    for p, name, _k, val in P.walk(tree):
        if name == "ix" and "/skin" in p:
            cur_ix = val
        elif name == "w" and "/skin" in p and cur_ix:
            for j, wv in enumerate(val):
                if wv > 1e-6 and j < len(cur_ix):
                    used.add(cur_ix[j])
            cur_ix = None
    keep = set()
    for ix in used:
        nm = name_by_ix.get(ix)
        while nm:
            if nm in keep:
                break
            keep.add(nm)
            pa = pa_by.get(nm, -1)
            nm = name_by_ix.get(pa) if pa >= 0 else None
    return keep


def build(expr_path, body_path, out_path, keep_bones=None, dry=False,
          life_path=None, reading_path=None, *, talk_path=None, performance=None,
          phase_key=None, native_path=None, expression_basis=None):
    """表情的 fx 通道 + 身体的脊椎通道 → 新头部动画。

    ## 帧数/帧率跟**身体动作**走，不跟表情走

    第一版让帧数跟表情走，残留错位与「帧数被压缩多少倍」严格正相关:

        laugh    身体 361 帧 → 头 90 帧（4× 抽帧）   残留 2.70
        grief    身体 151 帧 → 头 105 帧             残留 1.21
        boredom  身体 151 帧 → 头 75 帧              残留 1.24
        praying  身体 161 帧 → 头 75 帧（同 30fps 时长一致）残留 0.000

    抽帧丢的正是脊椎的高频摆动，头就跟不上躯干。改成**帧数与帧率都照抄
    身体动作**，原生共享骨逐帧拷贝、零重采样误差。表情按 state 阶段采样，
    说话与生命层按真实时间采样；最终全部服从身体动作的帧数与帧率。
    """
    expr_path, body_path, out_path = map(Path, (expr_path, body_path, out_path))
    E, B = read_anim(expr_path), read_anim(body_path)
    _check_source(E, str(expr_path))
    _check_source(B, str(body_path))
    EQ, ET = unpack(E)
    BQ, BT = unpack(B)
    n = B["nf"]
    import moe_anim_catalog as CAT
    character = MP.char_of(out_path.name)
    prefix = MP.CHARACTERS[character]["prefix"] if character else ""
    base = out_path.stem[len(prefix):] if prefix else out_path.stem
    if performance is None:
        performance = CAT.performance_for(base)
    phase = performance["phase"]
    kind = expression_kind(expr_path)
    # Native breathing is an independent source contract. Before the facial
    # expansion only the old neutral selection contributed native animation;
    # all other sources had identical static native transforms (31-role audit).
    # New emotions must not silently add/remove neck breathing and invalidate
    # previously solved cloth/hair motion.
    native_q, native_t = {}, {}
    native_breathing = performance.get("native_breathing")
    if native_breathing is None:
        native_breathing = CAT.native_breathing_for(base)
    if native_breathing:
        if native_path is None:
            suffix = kind if kind != "neutral" else "idle_calm"
            source_prefix = expr_path.stem[:-len(suffix)]
            native_path = expr_path.with_name(source_prefix + "idle_calm.anim")
        if not Path(native_path).is_file():
            raise ValueError(f"missing native breathing source: {native_path}")
        N = read_anim(native_path)
        _check_layer(E, N, "native breathing")
        for a, b in zip(E["bones"], N["bones"]):
            if not _is_face_bone(a["name"]):
                for channel in ("q", "t", "s"):
                    if a.get(channel) != b.get(channel):
                        raise ValueError(f"native breathing: incompatible rest {a['name']}:{channel}")
        NQ, NT = unpack(N)
        native_q = {name: resample_q(seq, n) for name, seq in NQ.items()
                    if seq and not _is_face_bone(name)}
        native_t = {name: resample_t(seq, n) for name, seq in NT.items()
                    if seq and not _is_face_bone(name)}
    style = C.EXPRESSION_STYLE.get(performance.get("emotion", kind), {})
    identity = phase_key or f"{expr_path.parent.name}/{out_path.name}"
    if kind in TILE_EXPRS:
        EQ, ET = tile_to_body(E, EQ, ET, B, n, identity, kind)
    else:
        EQ, ET = emotion_to_body(E, EQ, ET, B, n, phase, style)

    notes = []
    import moe_expression_basis as EB
    if expression_basis is None:
        basis_path = expr_path.parent / EB.FILENAME
        expression_basis = EB.load(basis_path) if basis_path.is_file() else None
    elif isinstance(expression_basis, (str, Path)):
        expression_basis = EB.load(expression_basis)
    if performance.get("talk") and kind != "talk":
        if talk_path is None:
            suffix = kind if kind != "neutral" else "idle_calm"
            prefix = expr_path.stem[:-len(suffix)]
            talk_path = expr_path.with_name(prefix + "talk.anim")
        if not Path(talk_path).is_file():
            raise ValueError(f"missing speech source: {talk_path}")
        S = read_anim(talk_path)
        _check_layer(E, S, "speech")
        SQ, ST = unpack(S)
        _SQ, ST = tile_to_body(S, SQ, ST, B, n, identity, "talk")
        speech = {name: seq for name, seq in ST.items() if name.startswith("bn_m_fx_")}
        if expression_basis is not None:
            mouth = set(expression_basis['bones'])
            count = EB.overlay(ET, E, speech, S, expression_basis, style.get("speech_gain", 1.0))
            count += _overlay_translation(ET, E, {name: seq for name, seq in speech.items() if name not in mouth},
                                          S, style.get("speech_gain", 1.0), True)
        else:
            count = _overlay_translation(ET, E, speech, S, style.get("speech_gain", 1.0), True)
        notes.append(f"speech={count}")
    elif expression_basis is not None:
        count = EB.project_emotion(ET, E, expression_basis)
        notes.append(f"native_mouth={count}")

    if life_path is not None:
        if not Path(life_path).is_file():
            raise ValueError(f"missing life source: {life_path}")
        if expr_path.resolve() != Path(life_path).resolve():
            L = read_anim(life_path)
            _check_layer(E, L, "life")
            LQ, LT = unpack(L)
            _LQ, LT = tile_to_body(L, LQ, LT, B, n, identity, "life")
            blink = {name: seq for name, seq in LT.items() if name.startswith("bn_m_fx_")}
            gaze = {name: seq for name, seq in LT.items() if not name.startswith("bn_m_fx_")}
            gaze_source = L
            reading = (performance.get("gaze") == "reading"
                       or any(k in out_path.name.lower() for k in READING_KEYWORDS))
            if reading and reading_path is not None:
                if not Path(reading_path).is_file():
                    raise ValueError(f"missing reading source: {reading_path}")
                R = read_anim(reading_path)
                _check_layer(E, R, "reading")
                RQ, RT = unpack(R)
                _RQ, RT = tile_to_body(R, RQ, RT, B, n, identity, "reading")
                gaze = {name: seq for name, seq in RT.items() if not name.startswith("bn_m_fx_")}
                gaze_source = R
            blink_gain = style.get("life_blink_gain", 1.0)
            _make_room_for_blink(ET, E, blink, L, blink_gain)
            n_blink = _overlay_translation(ET, E, blink, L, blink_gain, True)
            n_gaze = _overlay_translation(ET, E, gaze, gaze_source, style.get("life_gaze_gain", 1.0))
            notes.append(f"life(gaze={n_gaze},blink={n_blink},reading={reading})")
    life_note = ("".join(" +" + note for note in notes)
                 + f" [{phase},native_breathing={bool(native_breathing)}]")

    EQ.update(native_q)
    ET.update(native_t)
    # Preserve the established head attachment contract: copy body-animated bones
    # and the explicit spine/camera anchors, but retain all other native source
    # transforms. In particular, copying the torso's static bn_m_root translation
    # would apply the attach offset twice (JIA differs by 74.1556 model units).
    bb_names = {b["name"] for b in B["bones"]}
    body_driven = [b["name"] for b in E["bones"]
                   if b["name"] in bb_names and not _is_face_bone(b["name"])
                   and (BQ.get(b["name"]) or BT.get(b["name"]))]
    moved = []
    for bn in BODY_ANCHORED + [n for n in body_driven if n not in BODY_ANCHORED]:
        # 脊椎逐帧拷贝，不重采样 —— 这是零误差的关键。
        #
        # **通道有无也要照抄**：身体动作没有的通道必须从头部动画里**删掉**，
        # 不能留着表情源那份。踩过一次: moe_body_praying 的 groove 只有 q
        # 没有 t，而中性脸源 moe_idle_calm 的 groove 有 t（呼吸起伏）。
        # 只覆盖不删除 → 头按 idle 的呼吸上下浮动、躯干不动，
        # 量出 0.802 单位错位，且**只有这一个动作坏**，极易误判成重采样误差。
        if bn not in bb_names:
            continue
        if BQ.get(bn):
            EQ[bn] = list(BQ[bn])
            moved.append(bn + ":q")
        else:
            EQ.pop(bn, None)
        if BT.get(bn):
            ET[bn] = list(BT[bn])
            moved.append(bn + ":t")
        else:
            ET.pop(bn, None)
    # 身体侧脊椎**全静态**是合法情形（bind_pose / dead / praying /
    # emotion_worry 四条实测如此），不是错误：躯干脊椎不动，头也不该动脊椎，
    # 表情源本身就是正确产物。
    #
    # 以前这里 `return None` 放弃写出 —— 后果是**旧产物留在原地**。重烘 mesh
    # 之后（473 → 475 骨）这 4 条就停在旧骨数上，而引擎要求 anim 骨数严格等于
    # mesh 骨数，差一根就让**整件 mesh 上每条动画被拒播、entity 完全不渲染**。
    # 也就是说「4 条无害的跳过」会让整个头不见，且本地渲图看不出来。
    #
    # 现在照常写出：脊椎通道已在上面被删干净（`EQ.pop` / `ET.pop`），
    # 表情通道已重采样到身体帧数，产物骨表来自表情源 —— 骨数天然正确。
    static_spine = not any(m.split(":")[0] in SPINE_BONES for m in moved)

    # 剔掉与头无关、身体也不驱动的通道。脊椎链必须留，它是头的祖先；
    # 身体驱动的骨必须留（见上）。
    if keep_bones:
        allow = set(keep_bones) | set(BODY_ANCHORED) | set(body_driven)
        EQ = {k: v for k, v in EQ.items() if k in allow}
        ET = {k: v for k, v in ET.items() if k in allow}

    bb = {b["name"]: b for b in B["bones"]}
    bones, order_q, order_t = [], [], []
    for b in E["bones"]:
        nm = b["name"]
        has_t = bool(ET.get(nm))
        has_q = bool(EQ.get(nm))
        sa = ("t" if has_t else "") + ("q" if has_q else "")
        # 脊椎骨与身体驱动骨的 rest 取自身体动画（rest = 该动作第 0 帧，
        # 不是 bind pose）—— 通道与 rest 必须同源，否则第 0 帧跳一下。
        src = bb[nm] if ((nm in BODY_ANCHORED or nm in body_driven) and nm in bb) else b
        bones.append((nm, sa,
                      list((src.get("t") or b.get("t") or [0.0, 0.0, 0.0])[:3]),
                      list((src.get("q") or b.get("q") or [0.0, 0.0, 0.0, 1.0])[:4]),
                      list((src.get("s") or b.get("s") or [1.0])[:1])))
        if has_t:
            order_t.append(nm)
        if has_q:
            order_q.append(nm)

    sample_t, sample_q = [], []
    for fr in range(n):
        for nm in order_t:
            sample_t.extend(ET[nm][fr])
        for nm in order_q:
            sample_q.extend(EQ[nm][fr])

    msg = (f"{out_path.name:<34} = {Path(expr_path).name:<19}"
           f"+ {Path(body_path).name:<28} {n:>4}帧 "
           f"q{len(order_q):<3} t{len(order_t)}{life_note}")
    if static_spine:
        msg += " [身体脊椎全静态，只用表情通道]"
    if dry:
        return msg, None
    # fps 也照抄身体动作：帧数已对齐，fps 不一致会让两个 entity 时长不同，
    # 播到后面又错开（looping = no + next_state 自跳转会更明显）。
    size = W.write_anim(str(out_path), B["fps"], n, bones, sample_t, sample_q)

    # 表情源与身体源都不含物理通道，所以这里写出的就是**基础版**（头发僵的），
    # 它留在模型 mod 里。物理版由 moe_bullet_bake.py 直接写进物理补丁 mod（2026-09-06 起），
    # 这里不再回注档案。
    return msg + f" → {size:,}B", None


def main():
    global ANIM_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    # 下面三个是**换角色必须给**的。上层 build_all_anims.py 一直在传
    # --out-dir / --out-prefix，而本脚本以前不认，于是换角色跑到 head 阶段
    # 会整段报 unrecognized arguments。
    ap.add_argument("--out-dir", default=None,
                    help=".anim 的读写目录（表情源、身体动画、输出头部动画）。"
                         "默认按前缀选角色 canonical 目录并使用通用 moe_ 名；也可指定 "
                         "work/<短名>_anim_bake/")
    ap.add_argument("--head-mesh", default=None,
                    help="取「head 相关骨」的 moe_head.mesh。**换角色不能省** ——"
                         "它决定剔除哪些通道，用错角色的 mesh 会让通道集不匹配，"
                         "而骨数写在 .anim 文件头，不匹配就是引擎整段拒播 + "
                         "entity 静默不渲染。")
    ap.add_argument("--out-prefix", default=None,
                    help="产物文件名前缀，与 build_all_anims 的同名参数对齐。"
                         "本脚本的输出名来自 BODY_FILE_TO_BASE，前缀在这里拼。")
    ap.add_argument("--limit", type=int, default=0,
                    help="只处理前 N 个（调试）")
    ap.add_argument("--skip-existing", action="store_true",
                    help="已存在的输出跳过（增量重跑）")
    ap.add_argument("--jobs", type=int, default=0,
                    help="接受但当前忽略（本脚本是串行的）；"
                         "留着是为了让 build_all_anims 能无条件传")
    a = ap.parse_args()

    pfx = a.out_prefix or "moe_"
    if a.out_dir:
        ANIM_DIR = Path(a.out_dir)
    else:
        import moe_chars as X
        ANIM_DIR = X.bake_dir(MP.char_of(pfx) or "flo")
        pfx = "moe_"
        if not a.head_mesh:
            a.head_mesh = str(ANIM_DIR / "moe_head.mesh")

    def prefixed(name):
        return pfx + name[len("moe_"):] if name.startswith("moe_") else name

    print(f"移植脊椎骨: {', '.join(SPINE_BONES)}")
    print(f"anim 目录: {ANIM_DIR}")
    _hm = Path(a.head_mesh) if a.head_mesh else MP.find(prefixed("moe_head.mesh"))
    if not _hm.is_file():
        raise SystemExit(f"**找不到 head mesh: {_hm}**")
    print(f"head mesh: {_hm}")
    keep = head_relevant_bones(_hm)
    print(f"head.mesh 相关骨 {len(keep)} 根（蒙皮骨 + 祖先链），"
          f"其余通道剔除")
    print(f"{'输出':<34}  来源\n" + "-" * 100)

    jobs = []
    for ep, bp, op in EXTRA_PAIRS:
        jobs.append((ANIM_DIR / prefixed(ep), ANIM_DIR / prefixed(bp),
                     ANIM_DIR / prefixed(op)))
    import moe_anim_catalog as CAT
    for bodyfile, base in sorted(BODY_FILE_TO_BASE.items(), key=lambda kv: kv[1]):
        expr = CAT.performance_for(base)["expression"]
        # 输出走 ANIM_DIR 而不是 MP.find —— 后者解析的是**已部署的 mod 目录**，
        # 换角色时会把新角色的头部动画写进 mod 里别人的目录。
        out = ANIM_DIR / f"{pfx}head_{base}.anim"
        if a.skip_existing and out.is_file():
            continue
        jobs.append((ANIM_DIR / prefixed(expr), ANIM_DIR / prefixed(bodyfile), out))
    if a.limit:
        jobs = jobs[:a.limit]

    ok = fail = 0
    for expr, body, out in jobs:
        if not expr.exists():
            print(f"  跳过（缺表情）: {expr.name}")
            fail += 1
            continue
        if not body.exists():
            print(f"  跳过（缺身体动画）: {body.name}")
            fail += 1
            continue
        try:
            msg, err = build(expr, body, out, keep, a.dry_run,
                             ANIM_DIR / prefixed(LIFE_EXPR), ANIM_DIR / prefixed(READING_EXPR),
                             talk_path=ANIM_DIR / prefixed("moe_talk.anim"))
        except (OSError, ValueError) as exc:
            msg, err = None, str(exc)
        print("  " + (msg or f"!! {out.name}: {err}"))
        if msg:
            ok += 1
        else:
            fail += 1
    print(f"\n{'(dry-run) ' if a.dry_run else ''}生成 {ok} 个头部动画")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
