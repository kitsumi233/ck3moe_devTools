"""把原版 CK3 身体动作重定向到 moe 骨架，产出可直接用的 .anim。

## 要解决的问题

之前的 moe_greet_bow 手臂僵直、下半身完全不动，是两个独立缺陷叠加：

1. **通道覆盖不足**。原版 idle 驱动 19 根骨；我们的 moe_idle_calm 只驱动 5 根
   （spine_low/spine_up/neck/l_arm/r_arm），肘、腕、锁骨、整个下半身一根都没有。
   大臂转而小臂不转 → 手臂就是一根棍子。
2. **rest 朝向不同**。实测 18 对映射骨的 rest 旋转轴夹角 7.5°~89.8°，多处接近垂直。
   直接把原版的绝对局部旋转 q 写进去，等于把骨头掰到"原版 rest 所对应的姿势"，
   结果是任意姿势且不随时间变化。

## 为什么「拷贝相对增量」不够 —— 实测否掉的第一版

第一版用父空间增量：

    Δ_parent  = q_frame_v · q_rest_v⁻¹
    q_frame_m = Δ_parent · q_rest_m

它数学上没错，但**做不出想要的结果**，因为两边 rest 姿势根本不是同一个姿势：

    关节        原版 rest    我们 rest    差
    左肘          98.3°       179.8°     81.6°
    右肘          51.8°       179.8°    128.1°
    左膝         159.2°       176.6°     17.5°

原版 `.anim` 的 `info.q` **不是骨架 bind pose，而是该动作自己的第 0 帧**
（实测每根动画骨 rest→frame0 全部 0.00°；且 idle 与 AI_bold 的 rest q 有
52/59 根不同）。也就是说原版 rest 已经是「手臂弯 98°/52° 的 idle 起始姿势」，
而我们的模板 rest 是直臂。

只传「相对自身 rest 的增量」，等于保留我们自己的直臂、再叠上原版那 3~7° 的
呼吸摆动 —— 手臂依旧是直棍。这正是用户看到的「手臂僵直」。**加通道解决不了，
因为缺的不是通道而是姿势本身。**

## 实际采用的公式：传绝对朝向 + 固定轴向修正

要的是让我们的骨摆到**原版那根骨在世界空间的朝向**。两边局部轴约定不同，
实测是一个干净的系统性差异，不是逐骨噪声：

    骨段          原版局部前向      我们局部前向     夹角
    arm→elbow    (+1, 0, 0)      ( 0, 0,+1)     90.0°
    elbow→wrist  (+1, 0, 0)      ( 0, 0,+1)     90.0°
    hip→knee     (+1, 0, 0)      ( 0, 0,+1)     90.0°
    lumbar→thor  ( 0,+1, 0)      ( 0, 0,+1)     90.0°

即原版骨沿局部 +X 指向子骨，我们沿局部 +Z。于是每根骨取一个**常量**修正：

    C_bone = q_world_v_rest⁻¹ · q_world_m_rest

再把原版的世界朝向搬过来，并转回我们的父空间（.anim 的 q 是父空间量）：

    q_world_target = q_world_v(frame) · C_bone
    q_local_m      = q_world_parent_m(frame)⁻¹ · q_world_target

按骨骼层级从根到叶求解，父骨的世界朝向用**已重定向后**的值，这样误差不累积。

平移通道要考虑两边尺度不同（踝→胸世界高度比实测 0.9115）：

    Δt_parent = t_frame_v - t_rest_v          ← 原版相对自己 rest 的位移
    t_frame_m = t_rest_m + Δt_parent * SCALE  ← 按身高比缩放后加到我们 rest 上

平移这里仍用增量而非绝对值：绝对位置涉及骨长差异，直接搬会把骨头拉断。

## 骨名映射的坑

原版命名与我们语义**错位一级**：原版 `bn_l_clavicle`（锁骨）对应我们
`bn_m_l_shoulder`（肩），原版 `bn_l_shoulder` 其实是**大臂**、对应我们
`bn_m_l_arm`。照字面同名映射会把锁骨的动作装到大臂上。

跳过的骨（判据统一是「该骨是否存在于本动作驱动的 female_body.mesh」，
不是凭骨名猜用途 —— 扫了全部 6071 个原版 mesh 得到的结论）：

  - `root`：**有** tq 通道（24.2° 摆动 + 3.3 单位漂移），但不在
    female_body.mesh 里 → 原版播放时按名字查找失败，这条通道被丢弃。
    我们的 bn_m_center 是带蒙皮的 deform 骨，灌进去等于凭空多出全身摆动。
  - `left_shackle` / `chain_1..9` / `right_shackle`：镣铐道具骨，sa=[] 无通道，
    且 0/6071 个 mesh 含这些骨名，拷过来只是 12 根死骨。
  - `camera_torso_look_at`：相机朝向辅助骨，有 tq 通道但同样不在 mesh 里，
    没有任何蒙皮；引擎按名字查找，造一根同名骨也不会有效果。

反过来说，原版 71 根 anim 骨里真正能变形网格的只有带 `female_body_rig_01:`
前缀的那些 —— 去掉前缀后与 mesh 骨名逐一对上，唯一的例外就是 `root`。

用法:
  python tools/anim/retarget_vanilla_anim.py                 # 重定向 idle
  python tools/anim/retarget_vanilla_anim.py --list           # 列可用原版动作
"""
import argparse
import io
import math
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
import pdx_parse          # noqa: E402
import pdx_anim_write     # noqa: E402

GAME = Path(r"E:\SteamLibrary\steamapps\common\Crusader Kings III"
            r"\game\gfx\models\portraits")
# 单体目录已退役（2026-08-29）：产物按子 mod 直写 mod/<mod_id>/。
# MP.BASE 解析到第一个存在的模型包；**定位具体文件用 MP.find()**，
# 它会遍历全部子 mod（flo 在 gfl2 包、lau/per/eld 在 endfield 包）。
MOE = MP.BASE

# 取 rest 姿态做 FK 用的骨架 mesh。默认芙洛伦（189 骨），
# 其他角色（洛茜等）用 --rest-mesh 指向自己的 moe_torso.mesh。
# rest 骨架（取骨序与父子链）。**必须与 `--template` 那条动画同一套骨**，
# 否则 `order`（来自这个 mesh）里有模板没有的骨，`mrest[n]` 直接 KeyError。
#
# 这个默认值曾写死 `work/florence_poc/moe_torso.mesh` 并在 `--help` 里注明
# 「芙洛伦 189 骨版」—— 那个目录其实是 **167 骨**的旧产物（还没加 fx 代理骨）。
# 对芙洛伦碰巧能跑是因为她的动画也是那一版；换角色就报
# `KeyError: 'bn_m_waistCancel_l'`（167 骨版独有的骨）。
# 现在解析到实际出货的 mesh，并且**调用方应当按角色显式传 `--rest-mesh`**
# （`build_all_anims.py` 已经自动传了）。
# **不要在 import 期解析**：本模块把 `sys.stdout` 包了一层
# `TextIOWrapper`，而 import 期再触发别的模块做同样的事会叠加包装，共享的
# buffer 被 GC 后抛 `ValueError: I/O operation on closed file`。
def _default_rest_mesh():
    p = MP.models_dir("flo") / "moe_torso.mesh"
    return p if p.is_file() else ROOT / "work" / "flo_mesh_shipped" / "moe_torso.mesh"

PFX = "female_body_rig_01:"

# 原版骨名 → 我们骨名。注意 clavicle/shoulder 的错位一级。
#
# 不含 "root"：它在 anim 里有 tq 通道（24.2° 摆动 + 3.3 单位漂移），但**不在
# female_body.mesh 里**（扫了全部 6071 个 mesh，只有 16 个不相关的 headgear/
# male_body 含同名骨，本动作驱动的 female_body.mesh 没有）。也就是说原版播放时
# 引擎按名字找不到它，这条通道被直接丢弃。而我们的 bn_m_center 是**带蒙皮的
# deform 骨**，把这份被丢弃的全身摆动灌进去，会让躯干整体相对头部晃 24°
# —— 正是之前"躯干和头不同步"的观感来源。
# 默认兼容还没有标准王座根骨的旧 mesh。main() 读到 torso 里存在字面
# `body_root` 时会把这一项切到那根骨；其余语义映射不变。
BONE_MAP = {
    PFX + "body_root":      "bn_m_groove",
    PFX + "bn_sp_lumbar":   "bn_m_spine_low",
    PFX + "bn_sp_thoracic": "bn_m_spine_up",
    PFX + "bn_l_clavicle":  "bn_m_l_shoulder",
    PFX + "bn_l_shoulder":  "bn_m_l_arm",
    PFX + "bn_l_elbow":     "bn_m_l_elbow",
    PFX + "bn_l_wrist":     "bn_m_l_wrist",
    PFX + "bn_r_clavicle":  "bn_m_r_shoulder",
    PFX + "bn_r_shoulder":  "bn_m_r_arm",
    PFX + "bn_r_elbow":     "bn_m_r_elbow",
    PFX + "bn_r_wrist":     "bn_m_r_wrist",
    PFX + "bn_l_hip":       "bn_m_l_hip",
    PFX + "bn_l_knee":      "bn_m_l_knee",
    PFX + "bn_l_ankle":     "bn_m_l_ankle",
    PFX + "bn_r_hip":       "bn_m_r_hip",
    PFX + "bn_r_knee":      "bn_m_r_knee",
    PFX + "bn_r_ankle":     "bn_m_r_ankle",
}

# CK3 props are not passive wrist children. Every vanilla clip stores a
# clip-specific local transform on bn_l_prop / bn_r_prop, often entirely in
# the animation rest values (sa is empty). Keeping the Moe bind transform
# therefore makes a book, goblet, sword, etc. follow the wrist from the wrong
# offset even though the accessory is attached to the correct node.
#
# These bones need a full transform transfer, separate from the semantic bone
# retarget above: preserve the vanilla prop's world-space offset from its
# wrist and its world-space orientation, then express both in the retargeted
# wrist's local space on every frame.
PROP_TARGETS = {
    "bn_l_prop": (PFX + "bn_l_prop", PFX + "bn_l_wrist", "bn_m_l_wrist"),
    "bn_r_prop": (PFX + "bn_r_prop", PFX + "bn_r_wrist", "bn_m_r_wrist"),
}

# 头部动作留给 morph 通道（表情靠 shapekey 烘到代理骨，骨架没有脸骨），
# 这里只做身体。neck 归身体，因为它在 torso 骨架里。
NECK_SRC = PFX + "bn_sp_thoracic"   # 原版 idle 没有独立 neck 通道

# ---------------------------------------------------------------- 蒙皮骨
# MMD 骨架把「控制」与「蒙皮」分成两套骨，这是最容易漏的一刀：
#
#   bn_m_l_hip   0 顶点    ← 控制骨（语义骨，我们原先只驱动这个）
#   bn_m_l_legD  3760 顶点 ← D 骨（分解骨），真正带蒙皮
#
# MMD 运行时由 D 骨**跟随**控制骨旋转，CK3 没有这个机制。只驱动控制骨的结果是
# 数值完美（膝角偏差 0.0°）但**画面上腿一动不动** —— 骨架线框图看不出来，
# 因为线框画的是骨骼不是皮。必须显式把同一个朝向也写给 D 骨。
#
# 实测 D 骨与对应控制骨**位置完全重合**（差 0.0000）、父链相同，只有 rest
# 朝向不同（178°/175°/135°），轴向修正会由几何骨向自动吸收。
#
# 表的每项 = (我们的骨, 原版源骨, 我们的骨向子骨, 原版的骨向子骨)。
# 骨向子骨用来求轴向修正 C；给 None 表示末端骨，沿用上游骨的 C。
_L = "female_body_rig_01:"
SKIN_TARGETS = [
    # 躯干。pelvis 不是影子骨：实测它与 spine_low 是**兄弟**（同父 waist、
    # 位置差 0.5795），是 MMD 的「下半身」、整条腿链的祖先，所以对应原版
    # body_root（躯干与腿的共同根）。它带 10524 顶点，是最大的一根蒙皮骨。
    # pelvis 暂不驱动：它是 waistCancel_*→hip 整条腿链的**祖先**，
    # 给它灌 body_root 的朝向会与 hip 自己的通道叠加（实测静态偏 100.97°，
    # 把腿整体拧歪）。它的 10524 顶点会通过父骨 waist 继承躯干运动。
    # 若以后要让臀部单独跟随，需要先从 body_root 的朝向里**扣掉** hip 已承担的部分。
    # 腿：D 骨承载全部腿部蒙皮
    ("bn_m_l_legD",       _L+"bn_l_hip",    "bn_m_l_kneeD",  _L+"bn_l_knee"),
    ("bn_m_r_legD",       _L+"bn_r_hip",    "bn_m_r_kneeD",  _L+"bn_r_knee"),
    ("bn_m_l_kneeD",      _L+"bn_l_knee",   "bn_m_l_ankleD", _L+"bn_l_ankle"),
    ("bn_m_r_kneeD",      _L+"bn_r_knee",   "bn_m_r_ankleD", _L+"bn_r_ankle"),
    ("bn_m_l_ankleD",     _L+"bn_l_ankle",  None, None),
    ("bn_m_r_ankleD",     _L+"bn_r_ankle",  None, None),
]

# ---------------------------------------------------------------- 手指
# 336/423 个原版肖像动作**有手指通道**（最多 30 根），我最初的映射表漏了它们，
# 所以手一直是僵的握拳 rest 姿势。两边各 15 根/手，一一对应：
#
#   原版 bn_l_fi_thumb1/2/3  ↔  我们 bn_m_l_thumb_uff10/11/12   ← 起始编号差 1
#   原版 bn_l_fi_index1/2/3  ↔  我们 bn_m_l_index_uff11/12/13
#   原版 bn_l_fi_mid1/2/3    ↔  我们 bn_m_l_middle_uff11/12/13  ← mid vs middle
#   原版 bn_l_fi_ring1/2/3   ↔  我们 bn_m_l_ring_uff11/12/13
#   原版 bn_l_fi_pinky1/2/3  ↔  我们 bn_m_l_pinky_uff11/12/13
#
# 用代码生成而不是手写 60 行，避免抄错编号；顺带把「骨向子骨」也一起生成，
# 末节骨（第 3 节）没有子骨，沿用上一节的轴向修正。
FINGER_MAP = {}          # 原版骨 → 我们骨
FINGER_CHILD = {}        # 原版骨 → 原版的下一节（求轴向修正用）
for _side in ("l", "r"):
    for _v, _m, _base in (("thumb", "thumb", 0), ("index", "index", 1),
                          ("mid", "middle", 1), ("ring", "ring", 1),
                          ("pinky", "pinky", 1)):
        for _i in range(3):
            _vn = f"{PFX}bn_{_side}_fi_{_v}{_i + 1}"
            _mn = f"bn_m_{_side}_{_m}_uff1{_base + _i}"
            FINGER_MAP[_vn] = _mn
            if _i < 2:
                FINGER_CHILD[_vn] = f"{PFX}bn_{_side}_fi_{_v}{_i + 2}"

# **不要**驱动 armTwist_* / wristTwist / shoulderC / waistCancel_*。
# 它们看着很诱人（armTwist_2 带 1296 顶点），但实测局部转角 ≈0°
# （armTwist 0.48°、wristTwist 0.00°），是**串在语义链上的同位中间骨**：
#
#     bn_m_l_arm → bn_m_l_armTwist → bn_m_l_elbow → bn_m_l_wristTwist → bn_m_l_wrist
#
# armTwist 是 elbow 的**父**。给它灌「原版 elbow 的朝向」等于旋转真实肘关节的
# 父空间，下游全歪 —— 实测这么做会让双腿在脚踝交叉、躯干扭曲，自穿插从
# 4869 涨到 5609。它们只要保持 rest 局部 q，就会自动继承父骨运动，蒙皮照样跟着走。
#
# 判据：**局部转角≈0 的骨是中间骨，只能继承；位置与控制骨重合(0.0000)但 rest
# 朝向不同的骨才是影子骨，需要显式驱动。**

# 平移缩放：原版 female_body 与我们 moe_torso 的躯干尺度比，运行时按
# 「踝→胸」的**世界空间**高度实测（约 0.911）。
#
# 不能直接读 rest["t"][1] 当身高：.anim 里的 t 是**父空间局部偏移**，不是世界
# 坐标。我们的 bn_m_center 局部 t 是 (0,0,69.382) —— Y 分量为 0，Z 才是 69 ——
# 因为它挂在 bn_m_root 下，而后者的 rest 旋转把局部 Z 映射到世界 Y。照
# 索引 1 取"身高"会得到 0，配合兜底逻辑静默给出 scale=1.0。
# 两边骨架在**世界空间**都是 Y-up（实测脊椎方向 原版 (0.009,1.0,-0.012)、
# 我们 (0,0.995,-0.1)），所以父空间增量公式本身成立，只是尺度要另算。
TRANSLATION_SCALE = None   # None = 自动按世界空间 FK 推算

# 平移基准：原版 female_body_idle 的 body_root rest，作为「站立状态基准摆位」。
# 实测（cache/probe_root_rest.py，走 mesh 父链 FK）：
#     female_body_idle                (2.220, 80.741,  2.712)  ← 本基准
#     throneRoom_ruler2_1/_2          (0.000, 44.925, 24.335)  ← 降 35.8、前移 21.6
#     throneRoom_ruler1_1             (0.000, 44.925, 38.310)
#     throneRoom_kneel1_1            (-5.464, 35.369,  2.712)
# **原版把绝对摆位编码在每条动画自己的 body_root rest 里**（父骨 ground_joint 在
# 原点且无旋转，所以它的局部 t 恰好等于世界坐标）。所以坐姿不是靠场景摆位，而是
# 动画自带的。传 `tf − trv` 的增量会把这一层整个丢掉 —— 表现是角色在**站立高度**
# 上摆了个坐姿。
#
# 传的时候是 `我们rest + q_conj(父骨世界朝向) · ((原版当前 − 本基准) × scale)`。
# **不能按世界轴逐轴加到局部 t 上**：.anim 的 t 是父空间量，我们的 bn_m_root
# rest q 把局部 Z 映射到世界 Y（见 TRANSLATION_SCALE 上面那段），逐轴加等于把
# 竖向偏移灌进纵深轴。
VANILLA_BODY_ROOT_REST_BASELINE = (2.22, 80.741, 2.712)

# 2026-09-01 Royal Court screenshot calibration: the ruler's hips were about
# 20 cm above the seat. Royal Court normalizes body_root at runtime, so the
# correction must be carried by bn_m_root, the first bone that actually moves
# the torso mesh. A 17.64-unit drop then sank the character too far in the
# in-game throne; visual calibration puts the correct drop at 10 units.
MOE_EXPORT_HEIGHT = 149.98
THRONE_RULER_SEAT_DROP = 10.0


# ------------------------------------------------------------------ 四元数
def q_mul(a, b):
    """PDX 存 (x,y,z,w)。返回 a·b（先施加 b 再施加 a）。"""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def q_conj(q):
    x, y, z, w = q
    return (-x, -y, -z, w)


def q_norm(q):
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / n for c in q)


def q_angle_deg(q):
    w = max(-1.0, min(1.0, abs(q_norm(q)[3])))
    return math.degrees(2.0 * math.acos(w))


def q_apply(q, v):
    """用四元数旋转向量。"""
    x, y, z, w = q
    tx = 2.0 * (y * v[2] - z * v[1])
    ty = 2.0 * (z * v[0] - x * v[2])
    tz = 2.0 * (x * v[1] - y * v[0])
    return (v[0] + w * tx + (y * tz - z * ty),
            v[1] + w * ty + (z * tx - x * tz),
            v[2] + w * tz + (x * ty - y * tx))


# ------------------------------------------------------------------ 骨架层级
def mesh_parents(path):
    """从 .mesh 读 {骨名: 父骨名}。父子关系用 ix/pa 索引表示。"""
    tree = pdx_parse.parse(Path(path).read_bytes())
    by_ix, par = {}, {}
    def walk(nd):
        for name, ch in kids(nd):
            pr = props(ch)
            if "ix" in pr:
                by_ix[pr["ix"][1][0]] = name
                par[name] = pr.get("pa", [None, [-1]])[1][0]
            walk(ch)
    walk(tree)
    return {n: (by_ix.get(p) if p >= 0 else None) for n, p in par.items()}


def world_rest(rest, parents, prefix=""):
    """把局部 rest（.anim 的 t/q 是父空间量）正向运动学解成世界坐标。"""
    WP, WQ = {}, {}
    def solve(n):
        if n in WP:
            return
        b = rest[n]
        pn = parents.get(n[len(prefix):] if prefix and n.startswith(prefix) else n)
        pn = (prefix + pn) if (pn and prefix and (prefix + pn) in rest) else pn
        if pn is None or pn not in rest:
            WP[n], WQ[n] = tuple(b["t"]), q_norm(b["q"])
        else:
            solve(pn)
            off = q_apply(WQ[pn], b["t"])
            WP[n] = tuple(WP[pn][i] + off[i] for i in range(3))
            WQ[n] = q_norm(q_mul(WQ[pn], q_norm(b["q"])))
    for n in rest:
        solve(n)
    return WP, WQ


# ------------------------------------------------------------------ 读 anim
def kids(n):
    ch = n.get("_children") or []
    return list(ch.items()) if isinstance(ch, dict) else list(ch)


def props(n):
    return n.get("_props") or {}


def load_anim(path):
    tree = pdx_parse.parse(Path(path).read_bytes())
    meta, bones, samp = {}, [], {}
    for name, nd in kids(tree):
        if name == "info":
            for k, v in props(nd).items():
                meta[k] = v[1]
            for cn, cn2 in kids(nd):
                pr = props(cn2)
                sa = pr.get("sa", [None, []])[1]
                bones.append({
                    "name": cn,
                    "sa": sa[0] if sa else "",
                    "t": pr.get("t", [None, [0, 0, 0]])[1],
                    "q": pr.get("q", [None, [0, 0, 0, 1]])[1],
                    "s": pr.get("s", [None, [1.0]])[1],
                })
        elif name == "samples":
            for k, v in props(nd).items():
                samp[k] = v[1]
    return meta, bones, samp


def unpack_samples(meta, bones, samp):
    """把帧主序的平坦数组拆成 {骨名: [每帧值]}。"""
    F = meta["sa"][0]
    t_bones = [b["name"] for b in bones if "t" in b["sa"]]
    q_bones = [b["name"] for b in bones if "q" in b["sa"]]
    ts = {n: [] for n in t_bones}
    qs = {n: [] for n in q_bones}
    flat_t, flat_q = samp.get("t", []), samp.get("q", [])
    assert len(flat_t) == F * len(t_bones) * 3, \
        f"t 长度 {len(flat_t)} != {F}*{len(t_bones)}*3"
    assert len(flat_q) == F * len(q_bones) * 4, \
        f"q 长度 {len(flat_q)} != {F}*{len(q_bones)}*4"
    for f in range(F):
        for i, n in enumerate(t_bones):
            o = (f * len(t_bones) + i) * 3
            ts[n].append(tuple(flat_t[o:o + 3]))
        for i, n in enumerate(q_bones):
            o = (f * len(q_bones) + i) * 4
            qs[n].append(tuple(flat_q[o:o + 4]))
    return F, ts, qs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="female_body/female_body_idle.anim")
    ap.add_argument("--out", default="moe_idle_calm.anim")
    ap.add_argument("--template", default="moe_idle_calm.anim",
                    help="取 moe rest 姿态与骨序的模板 .anim")
    ap.add_argument("--out-dir", default=None,
                    help="模板与输出所在目录（默认按输出名选角色 canonical 目录，写通用 moe_ 名）。"
                         "换角色时指向该角色自己的 .anim 目录，避免与"
                         "芙洛伦已有的产物撞名。")
    ap.add_argument("--rest-mesh", default=None,
                    help="取骨序与父子链的 <角色>torso.mesh 路径。**必须与 "
                         "--template 同一套骨**，否则 KeyError。换角色时一定要"
                         "传；不传则回落到芙洛伦出货的那份（见 _default_rest_mesh）。")
    ap.add_argument("--list", action="store_true")
    # 腿部 IK 默认**关闭**。实测带蒙皮渲图：开 IK 两脚会挤成一团、脚面重叠，
    # 关掉才是两只分开的脚。详见 IK_CHAINS 处的注释。
    ap.add_argument("--leg-ik", action="store_true",
                    help="给腿也做两骨 IK（默认关：实测会让两脚挤到一起）")
    # 手臂 IK 也改成默认关 —— 当初判定「相对迁移不合理」的渲图是在
    # rest=第0帧 的 bug 还在时出的，那个 bug 修掉后相对朝向拷贝就够了。
    ap.add_argument("--arm-ik", action="store_true",
                    help="给手臂做两骨 IK（默认关：改用相对朝向拷贝）")
    ap.add_argument("--lateral-damp", type=float, default=0.1,
                    help="小腿横向内收衰减(0=垂直,1=照搬原版)。默认 0.1："
                         "按**实际脚部网格**扫的，>0.2 两脚就插进彼此")
    ap.add_argument("--semantic-motion", choices=("off", "gentle-v1"), default="off",
                    help="可选的物理前语义脊椎微动；样片审核阶段默认关闭，随后必须重生成配套头部动画")
    ap.add_argument("--foot-turnout", action="store_true",
                    help="腿链 rest 对齐补上原版站立的脚尖外展（MMD 两脚平行 → 不补就内八）；默认关，产物不变")
    ap.add_argument("--spine-up-rest", action="store_true",
                    help="spine_up 沿用 spine_low 修正时一律带 rest 相对朝向（修胸颈前倾，鸣潮用）；默认关，产物不变")
    ap.add_argument("--inherit-rot", default=None, metavar="REPORT",
                    help="角色导入 report.json：按其源 PMX 的回転付与表驱动付与骨（足調整/肩調整等）；"
                         "默认不给，产物不变")
    args = ap.parse_args()

    if args.list:
        for p in sorted(GAME.rglob("*.anim")):
            if "body" in p.parent.name:
                print(f"  {p.relative_to(GAME)}")
        return

    src = GAME / args.src
    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        import moe_chars as X
        character = MP.char_of(args.out) or "flo"
        prefix = X.BY_CK[character]["prefix"]
        out_dir = X.bake_dir(character)
        if args.out.startswith(prefix):
            args.out = "moe_" + args.out[len(prefix):]
        if args.template.startswith(prefix):
            args.template = "moe_" + args.template[len(prefix):]
        if not args.rest_mesh:
            args.rest_mesh = out_dir / "moe_torso.mesh"
    tpl = out_dir / args.template
    print(f"源: {src.relative_to(GAME)}")
    print(f"模板(取 moe rest): {tpl.name}")

    vmeta, vbones, vsamp = load_anim(src)
    mmeta, mbones, msamp = load_anim(tpl)
    VF, vts, vqs = unpack_samples(vmeta, vbones, vsamp)

    # 骨名前缀**按文件而异**，不能写死。PFX 里写的是 female_body_rig_01:，但
    # 部分 clip 用的是 female_body_rig_01_updated:（如 emotion_laugh_1）。
    # 写死会让整张 BONE_MAP 一个都对不上，先前表现为
    #   KeyError: 'female_body_rig_01:bn_l_ankle'
    # 而不是「找不到骨」，因为尺度计算最先撞上去。
    # 修法：把源文件的实际前缀改写成 PFX，下游全部代码不用动。
    src_pfx = {n.split(":")[0] + ":" for n in (b["name"] for b in vbones) if ":" in n}
    if len(src_pfx) == 1:
        sp = src_pfx.pop()
        if sp != PFX:
            print(f"  源前缀 {sp} → 规范化为 {PFX}")
            for b in vbones:
                if b["name"].startswith(sp):
                    b["name"] = PFX + b["name"][len(sp):]
            for d in (vts, vqs):
                for k in [k for k in d if k.startswith(sp)]:
                    d[PFX + k[len(sp):]] = d.pop(k)

    vrest = {b["name"]: b for b in vbones}
    mrest = {b["name"]: b for b in mbones}

    # Royal Court 需要在 torso 上按字面 `body_root` 对齐王座。新骨架只在
    # torso 增加 ground_joint -> body_root -> bn_m_root；head 仍以 bn_m_root
    # 为真根，避免两个附着实体各自吃一次根变换而飞离。旧角色/旧产物没有
    # body_root 时继续走已经验证过的 bn_m_groove 兼容路径。
    bone_map = dict(BONE_MAP)
    placement_target = "body_root" if "body_root" in mrest else "bn_m_groove"
    bone_map[PFX + "body_root"] = placement_target
    print(f"摆位骨: 原版 body_root -> {placement_target}")

    print(f"原版 fps={vmeta['fps'][0]:.3f} 帧={VF} 骨={vmeta['j'][0]} "
          f"(带通道 {len(vqs)}q/{len(vts)}t)")
    print(f"我们 骨={mmeta['j'][0]}")

    # 平移缩放：按世界空间「踝→胸」高度实测。必须走 FK，理由见 TRANSLATION_SCALE。
    global TRANSLATION_SCALE
    vpar = mesh_parents(GAME / "female_body" / "female_body.mesh")
    mpar = mesh_parents(Path(args.rest_mesh) if args.rest_mesh
                        else _default_rest_mesh())
    vWP, _ = world_rest(vrest, vpar, PFX)
    mWP, _ = world_rest(mrest, mpar)

    # 原版**固定站立基准** rest 的世界解。尺度比与腿链 rest 对齐都要用它，
    # 不能用 `vrest`（= 当前源 clip 自己的 rest，坐姿/跪姿时就是那个折叠姿态）。
    vstand = {b["name"]: b for b in load_anim(
        GAME / "female_body" / "female_body_idle.anim")[1]}
    vstandWP, vstandWQ = world_rest(vstand, vpar, PFX)
    if TRANSLATION_SCALE is None:
        # **原版侧必须在固定的站立 rest 上量，不能用 `vrest`（= 当前源动画自己的
        # rest）**。.anim 的 rest 是第 0 帧姿态（见 pdx-anim-rest-is-frame0），
        # 坐姿/跪姿的 rest 里踝和胸本来就折在一起，`vh` 变小、比值被抬高：
        #     idle 0.9115 (vh 98.68) / 王座坐姿 1.2073 (74.50) / 跪姿 1.6962 (53.03)
        # 同一对骨架的尺度比却随源动画差 1.9 倍。以前只缩小增量（最大 0.13 单位）
        # 所以看不出来；一旦用它缩 36 单位的摆位偏移，坐姿就多降 10.6 单位。
        # 我们这侧不用改：模板恒定是 idle_calm（站立）。
        vh = math.dist(vstandWP[PFX + "bn_l_ankle"],
                       vstandWP[PFX + "bn_sp_thoracic"])
        mh = math.dist(mWP["bn_m_l_ankle"], mWP["bn_m_spine_up"])
        TRANSLATION_SCALE = (mh / vh) if vh > 1e-6 else 1.0
        print(f"平移缩放 = {TRANSLATION_SCALE:.4f} "
              f"(踝→胸 世界高度: 原版站立 rest {vh:.2f} / 我们 {mh:.2f})")

    # ---- 每骨常量轴向修正
    #
    # 不能用 C = q_world_v_rest⁻¹ · q_world_m_rest：原版 rest 就是这个 idle 的
    # 弯臂姿势，这样的 C 会把弯曲恰好抵消回我们的直臂 rest —— 代数上退化成被
    # 否掉的增量法（实测世界朝向吻合到 0.00°，但肘角仍是 179°）。
    #
    # 修正必须只反映**骨骼几何轴向约定**（原版沿局部 +X 指向子骨，我们沿 +Z），
    # 与任何姿势无关。所以用「把我们的局部子骨方向转到原版的局部子骨方向」的
    # 最小旋转：C = rotation_between(fwd_m_local, fwd_v_local)。
    _, vWQ = world_rest(vrest, vpar, PFX)
    mWP, mWQ = world_rest(mrest, mpar)
    vWP, _ = world_rest(vrest, vpar, PFX)

    def local_fwd(WP, WQ, bone, child):
        d = [WP[child][i] - WP[bone][i] for i in range(3)]
        n = math.sqrt(sum(x * x for x in d))
        if n < 1e-9:
            return None
        return q_apply(q_conj(WQ[bone]), [x / n for x in d])

    # 骨向在骨骼局部系里是常量（由 rest 几何决定），预先算好一张表。
    # 当前世界骨向 = q_world_now · fwd_local，不依赖当前帧子骨位置
    # —— 后者在 IK 改写过程中会变，用它会自我引用。
    IK_CHILD = {
        "bn_m_l_arm": "bn_m_l_elbow", "bn_m_r_arm": "bn_m_r_elbow",
        "bn_m_l_elbow": "bn_m_l_wrist", "bn_m_r_elbow": "bn_m_r_wrist",
        "bn_m_l_hip": "bn_m_l_knee", "bn_m_r_hip": "bn_m_r_knee",
        "bn_m_l_knee": "bn_m_l_ankle", "bn_m_r_knee": "bn_m_r_ankle",
        # D 骨也要有骨向表：IK 后的修正要同步写给它们（见 IK_TWIN），
        # 少了这几行 D 骨会被静默跳过，皮肤照旧不动。
        "bn_m_l_legD": "bn_m_l_kneeD", "bn_m_r_legD": "bn_m_r_kneeD",
        "bn_m_l_kneeD": "bn_m_l_ankleD", "bn_m_r_kneeD": "bn_m_r_ankleD",
    }
    FWD_LOCAL = {}
    for bone, child in IK_CHILD.items():
        if bone in mWP and child in mWP:
            f_ = local_fwd(mWP, mWQ, bone, child)
            if f_:
                FWD_LOCAL[bone] = f_

    def rot_between(a, b):
        """把单位向量 a 转到 b 的最小旋转四元数。"""
        d = sum(a[i] * b[i] for i in range(3))
        if d > 1.0 - 1e-9:
            return (0.0, 0.0, 0.0, 1.0)
        if d < -1.0 + 1e-9:
            ax = (1.0, 0.0, 0.0) if abs(a[0]) < 0.9 else (0.0, 1.0, 0.0)
            c = (a[1] * ax[2] - a[2] * ax[1], a[2] * ax[0] - a[0] * ax[2],
                 a[0] * ax[1] - a[1] * ax[0])
            n = math.sqrt(sum(x * x for x in c)) or 1.0
            return (c[0] / n, c[1] / n, c[2] / n, 0.0)
        c = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
             a[0] * b[1] - a[1] * b[0])
        return q_norm((c[0], c[1], c[2], 1.0 + d))

    # 语义骨的子骨（用于取骨向）。取映射表里的下游语义骨。
    CHILD_V = {
        PFX + "bn_l_shoulder": PFX + "bn_l_elbow",
        PFX + "bn_l_elbow":    PFX + "bn_l_wrist",
        PFX + "bn_r_shoulder": PFX + "bn_r_elbow",
        PFX + "bn_r_elbow":    PFX + "bn_r_wrist",
        PFX + "bn_l_hip":      PFX + "bn_l_knee",
        PFX + "bn_l_knee":     PFX + "bn_l_ankle",
        PFX + "bn_r_hip":      PFX + "bn_r_knee",
        PFX + "bn_r_knee":     PFX + "bn_r_ankle",
        PFX + "bn_sp_lumbar":  PFX + "bn_sp_thoracic",
        PFX + "bn_l_clavicle": PFX + "bn_l_shoulder",
        PFX + "bn_r_clavicle": PFX + "bn_r_shoulder",
        # 腕：迁移手指后它**不再是末端骨**，两边都有中指根可定骨向
        #   原版 bn_l_wrist → bn_l_fi_mid1
        #   我们  bn_m_l_wrist → bn_m_l_middle_uff11
        # 所以腕能拿到真几何修正，不必回退用肘的。
        PFX + "bn_l_wrist":    PFX + "bn_l_fi_mid1",
        PFX + "bn_r_wrist":    PFX + "bn_r_fi_mid1",
    }
    # TARGETS: 我们的骨 → (原版源骨, 我们骨向子骨, 原版骨向子骨)
    # 语义骨走 BONE_MAP + CHILD_V，蒙皮骨走 SKIN_TARGETS。
    # 用「我们的骨」作键，因为同一根原版骨会驱动多根我们的骨
    # （如 bn_l_elbow → elbow + 4 根 armTwist），原版骨当键装不下。
    TARGETS = {}
    for vn, mn in bone_map.items():
        vc = CHILD_V.get(vn)
        # 骨向子骨可能是语义骨也可能是手指骨（腕→中指根），两张表都查
        mc = (bone_map.get(vc) or FINGER_MAP.get(vc)) if vc else None
        TARGETS[mn] = (vn, mc, vc)
    for vn, mn in FINGER_MAP.items():
        vc = FINGER_CHILD.get(vn)
        TARGETS[mn] = (vn, FINGER_MAP.get(vc) if vc else None, vc)
    for mn, vn, mc, vc in SKIN_TARGETS:
        TARGETS[mn] = (vn, mc, vc)

    CORR = {}
    for mn, (vn, mc, vc) in TARGETS.items():
        if vn not in vWQ or mn not in mWQ:
            continue
        if vc and mc and vc in vWP and mc in mWP:
            fv = local_fwd(vWP, vWQ, vn, vc)
            fm = local_fwd(mWP, mWQ, mn, mc)
            # ---- 轴向约定的左右镜像：这里是 180° 撕裂的真正根因
            #
            # 原版**镜像**左右骨的局部轴，我们的 MMD 骨架**不镜像**：
            #   骨          我们 fm        原版 fv
            #   l_legD      (0,-0.03,-1)   (+1,0,0)
            #   r_legD      (0,-0.03,-1)   (-1,0,0)   ← 相反
            #   l_hip       (0,0,+1)       (+1,0,0)
            #   r_hip       (0,0,+1)       (-1,0,0)
            # 两侧 fm 完全相同、fv 却相反，于是 rot_between 给出两个相差
            # 约 180° 的修正 —— 公式没错，是几何要求的。
            # 后果：落盘局部 q 在 legD 右侧 169.25°、hip 左侧 150.87°
            #（hip 是 +Z 而 legD 是 -Z，所以两条链翻转的侧**相反**），
            # 跨踝接缝形变最坏 352%。
            #
            # 我先后试过"镜像 C""踝只搬骨向""把后代一起带走"，全都没用 ——
            # 因为踝只是**继承**上游 legD/hip 的翻转，病根不在踝。
            #
            # 试过"右侧把 fv 沿 X 取反"来抵消镜像 —— **更糟**（整条右腿链涨到
            # ~175°）。单独取反一个分量并不是这个坐标系里的正确镜像。
            #
            # 最终改法见下方 LEG_REST_ALIGN：腿链整体改用 rest 对齐，
            # 绕开镜像问题，而不是去猜镜像变换。
            CORR[mn] = rot_between(fm, fv) if (fv and fm) else (0.0, 0.0, 0.0, 1.0)
        else:
            CORR[mn] = None   # 末端骨，下面用上游骨的修正补齐

    # ---- 真末端骨：用 rest 朝向差，**不要**回退用上游骨的几何修正
    #
    # 只有踝是真末端：我们的骨架**没有脚趾骨**（bn_m_l_ankle/ankleD 都是叶
    # 节点），而原版有 bn_l_ankle→bn_l_ftBall→bn_l_ftToe。踝因此拿不到自己
    # 的几何骨向，原先回退用膝的修正，结果**逐帧恒定偏 90.0°** ——
    # 恒定偏差正是"轴向约定用错"的特征（膝是 +X→+Z 的 90°，脚不是这个约定）。
    #
    # 改用 C = q_world_v_rest⁻¹ · q_world_m_rest。**要说清楚**：这在代数上
    # 就是那个被否掉的增量法 —— frame0 时 q_target 化简成 q_m_rest，即"保持
    # 我们自己的 rest 脚型 + 叠加原版逐帧增量"。对肘/膝这是致命的（会抹掉
    # 原版的弯曲，留下我们僵直的手臂）；对踝**恰好是想要的**，理由有两条：
    #   1. 没有下游关节，不存在"该弯却没弯"的自由度被抹掉；
    #   2. 我们的 rest 脚型本身就是需要的中性姿态（MMD 模型平脚站立，
    #      原版 idle 也是平脚），所以保留它 = 脚踏实地。
    # 换句话说，这里用增量法是**针对叶骨的有意选择**，不是通用结论。
    # 这也印证了 PMX 盘点里的结论：`足首` rest 轴向跨模型差 42°、
    # `つま先` 差 90°，脚部必须单独处理，不能跟着腿链走。
    #
    # 腕**不在**这张表里 —— 迁移手指后它有了中指根当骨向子骨，走几何修正。
    #
    # 注：踝的 C 左右**不相等**（95.30° / 124.71°），这是对的 —— 原版左右踝
    # rest 本身不对称。我曾以为这个不对称就是撕裂根因，试图把左侧的 C 镜像
    # 到右侧，**没用**；真正的根因在 legD/hip 的轴向镜像，见下。
    #
    # ---- 整条腿链改用 rest 对齐（C = q_v_rest⁻¹ · q_m_rest）
    #
    # 这就是"增量法"，对手臂是致命的（会抹掉原版 98° 的肘弯，留下我们的直臂），
    # 但对**站立的腿**恰恰正确，实测两个数字支撑：
    #   1. 原版 idle 腿部摆动极小：hip 3.27°/3.76°、knee 6.40°/7.05°
    #      —— 没有"大幅弯曲"需要被保留，不存在被抹掉的自由度；
    #   2. 两边 rest 腿姿本来就接近：大腿差 7.68°/9.25°、小腿差 13.45°/8.60°
    #      —— 保留我们自己的站姿反而更合身（高跟鞋、窄髋）。
    # 而它天然绕开了轴向镜像问题：rest 对齐不关心两边局部轴怎么定，
    # 因为 C 就是按"rest 帧必须重合"反解出来的。
    #
    # **但这条理由只对站立动画成立。** 坐姿/跪姿的 rest **就是坐姿**（.anim 的
    # rest 是第 0 帧），用它算 `vWQ` 则 `q_conj(vWQ[vn])` 把坐姿弯曲精确抵消掉：
    #     坐姿 anim 的 rest 差异：hip 84.31°, knee 65.43°, 大腿世界方向 y:0.04
    #     产物实测大腿方向 y=-1.0（竖直向下）+ 双侧 hip 弯 150°/腿交叉 352% 撕裂
    # 所以**原版侧必须锚到固定站立 rest**（`female_body_idle` 世界朝向），不能用
    # `vrest`。我们这侧不用改（模板恒定是 idle_calm = 站立）。
    #
    # 手臂**不在**这张表里，仍走几何修正 + IK。
    # `vstandWQ` 在上面（尺度比那段）算好，见那里的注释。
    LEG_REST_ALIGN = ["bn_m_l_legD", "bn_m_r_legD",
                      "bn_m_l_kneeD", "bn_m_r_kneeD",
                      "bn_m_l_hip", "bn_m_r_hip",
                      "bn_m_l_knee", "bn_m_r_knee",
                      "bn_m_l_ankle", "bn_m_r_ankle",
                      "bn_m_l_ankleD", "bn_m_r_ankleD"]
    for mn in LEG_REST_ALIGN:
        vn = TARGETS.get(mn, (None,))[0]
        if vn and vn in vstandWQ and mn in mWQ:
            CORR[mn] = q_norm(q_mul(q_conj(vstandWQ[vn]), mWQ[mn]))
    # ---- 脚尖外展：rest 对齐把「原版站立 = 我们 rest」，但两边中性脚型不同（2026-10-10，用户「几乎所有角色脚都内八」）
    #
    # MMD 模型 rest 两脚平行（踝→脚尖 外展 0~2°），原版站立 rest 外展 24.6°/20.2°（bind 也有 8°）。
    # rest 对齐只搬增量，于是每个动作的脚都比原版**系统性内收 ~20°**：
    #     踝→脚尖外展（逐帧中位，左/右）  原版   我们
    #     idle (female_body_idle_1)       8/21   -27/-4
    #     writing                        24/25   -23/+1
    #     sitting                        -3/+2   -14/-2
    # 修法：踝的 rest 对齐里补一个绕世界竖轴的旋转，让「原版站立」时我们的脚尖方向等于原版站立的
    # 脚尖方向：C = q_v_stand⁻¹ · R_yaw · q_m_rest。只动踝（含 D 骨），膝与站距不变。
    # 默认关（--foot-turnout），开关只决定这一段，关时产物逐字节不变。
    if args.foot_turnout:
        def _horiz(v):
            h = (v[0], 0.0, v[2]); n = math.sqrt(h[0] ** 2 + h[2] ** 2) or 1.0
            return (h[0] / n, 0.0, h[2] / n)

        def _turnout_frame(WP, lh, rh, side):
            lr = [WP[lh][i] - WP[rh][i] for i in range(3)]
            f = _horiz((lr[1] * 0 - lr[2] * 1, lr[2] * 0 - lr[0] * 0, lr[0] * 1 - lr[1] * 0))  # lr × up
            out = _horiz(lr) if side == "l" else _horiz([-x for x in lr])
            return f, out

        for _s in ("l", "r"):
            _va, _vt = PFX + f"bn_{_s}_ankle", PFX + f"bn_{_s}_ftToe"
            _ma, _mt = f"bn_m_{_s}_ankleD", f"bn_m_{_s}_toe"
            if _ma not in mWP:            # 没有 D 骨的模型（鸣潮散华）皮肤直接挂在踝上
                _ma = f"bn_m_{_s}_ankle"
            _vh = (PFX + "bn_l_hip", PFX + "bn_r_hip")
            if not (all(k in vstandWP for k in (_va, _vt) + _vh)
                    and all(k in mWP for k in (_ma, _mt, "bn_m_l_hip", "bn_m_r_hip"))):
                print(f"脚尖外展: {_s} 缺骨，跳过")
                continue
            fv, ov = _turnout_frame(vstandWP, *_vh, _s)
            fm, om = _turnout_frame(mWP, "bn_m_l_hip", "bn_m_r_hip", _s)
            tv = _horiz([vstandWP[_vt][i] - vstandWP[_va][i] for i in range(3)])
            tm = _horiz([mWP[_mt][i] - mWP[_ma][i] for i in range(3)])
            # 两边各自的骨盆前向符号：rest 下脚尖必朝前
            if sum(tv[i] * fv[i] for i in range(3)) < 0: fv = tuple(-x for x in fv)
            if sum(tm[i] * fm[i] for i in range(3)) < 0: fm = tuple(-x for x in fm)
            av = math.atan2(sum(tv[i] * ov[i] for i in range(3)), sum(tv[i] * fv[i] for i in range(3)))
            am = math.atan2(sum(tm[i] * om[i] for i in range(3)), sum(tm[i] * fm[i] for i in range(3)))
            want = tuple(math.cos(av) * fm[i] + math.sin(av) * om[i] for i in range(3))
            ry = rot_between(tm, want)
            print(f"脚尖外展: {_s} 原版站立 {math.degrees(av):.1f}° 我们 rest {math.degrees(am):.1f}° → 补 {math.degrees(av - am):+.1f}°")
            # 偏航加在**本侧整条腿链**上（2026-10-13，用户「忌炎双脚在所有动作里都扭曲」）：
            # 只转踝时膝不动，踝相对膝恒定拧 +25/-19°（修前与原版都在 ±10° 内），44 人全部动作
            # 脚踝皮肤被拧成麻花；toe_out 与腿链弯曲照样「修好」，所以看不出来。整腿同转 = 从髋外旋，
            # 与原版外展的来源同构；忌炎 idle 踝相对膝 +1.3/+0.8，toe_out 0.9/18.5。
            # 验收量踝相对膝扭转，不是 toe_out。
            for mn in [n for n in LEG_REST_ALIGN if n.startswith(f"bn_m_{_s}_")]:
                vn = TARGETS.get(mn, (None,))[0]
                if vn and vn in vstandWQ and mn in mWQ:
                    CORR[mn] = q_norm(q_mul(q_conj(vstandWQ[vn]), q_mul(ry, mWQ[mn])))

    # ---- 摆位骨（原版 body_root）的**旋转**也要传（2026-09-06，用户「dance 时腰部打结」）
    #
    # 原版把整段转体放在 body_root 上（dance_spin / kagura_dance 各 180°，另有 14 条 15~40°）。
    # 摆位骨没有骨向子骨、也不在 FALLBACK 里，CORR 算成 None 被过滤 —— 于是它只传 t 不传 q，
    # 而 spine_low / 两根 hip 传的是**绝对世界朝向**，各自吞下整个 180°，中间没被驱动的
    # waist / pelvis 不转，腰被拧半圈。24 个角色 dance_spin 的数字完全相同（spine_low 178.3°、
    # hip 179.0° / 179.9°），是脚本级问题。修法与腿链同款：rest 对齐 C = q_v_stand⁻¹ · q_m_rest，
    # 摆位骨拿到原版 body_root 的世界朝向后，子骨的局部 q 自动变成相对量（原版 lumbar 21.2°）。
    # 头靠 attach 挂在 bn_m_root（body_root 的后代）上跟着转，与原版同构（原版 head mesh 也没有
    # body_root）。t 路径不变：body_root 的 t 仍在 ground_joint 空间里，与原版语义一致。
    _pl_v = PFX + "body_root"
    if _pl_v in vstandWQ and placement_target in mWQ:
        CORR[placement_target] = q_norm(q_mul(q_conj(vstandWQ[_pl_v]), mWQ[placement_target]))

    # 其余没有下游语义骨的，沿用上游同肢体骨的修正
    FALLBACK = {
        "bn_m_spine_up": "bn_m_spine_low",
        "bn_m_groove":   "bn_m_spine_low",
    }
    # 指尖（每指第 3 节）没有子骨，沿用同指第 2 节的轴向修正
    for _s in ("l", "r"):
        for _v, _m, _b in (("thumb", "thumb", 0), ("index", "index", 1),
                           ("mid", "middle", 1), ("ring", "ring", 1),
                           ("pinky", "pinky", 1)):
            FALLBACK[f"bn_m_{_s}_{_m}_uff1{_b + 2}"] = \
                f"bn_m_{_s}_{_m}_uff1{_b + 1}"
    for mn, src in FALLBACK.items():
        if CORR.get(mn) is None and CORR.get(src):
            # 修正量是在**骨自己的局部系**里定义的，只有两根骨 rest 局部轴相同时才能照抄。
            # 庄方宜/阿达希尔/寇尔芙SSR01/莱娜秘夜/黛烟玉玲珑的指尖骨 rest 轴与第 2 节差
            # 60~90°，照抄让指尖逐帧恒定扭 80~130°（2026-09-27）。带上我们自己两骨的 rest
            # 相对朝向：C_mn = C_src · q_src_rest⁻¹ · q_mn_rest；两轴相同时它就是 C_src，
            # 其余角色产物不变。
            # 只在两轴真的不同（>20°）时启用：66 个角色有 1~11° 的正常骨轴差，照抄无可见
            # 问题；出问题的 6 个（zfy/ada/das/dai/cpx/lnc）是 80~180°。其余产物逐字节不变。
            _rel = q_mul(q_conj(mWQ[src]), mWQ[mn]) if (mn in mWQ and src in mWQ) else None
            # spine_up 例外（2026-10-13，用户「部分角色身体前倾」）：脊椎两骨 rest 轴差 11~19° 时
            # 照抄让胸以上多掰这一截，鸣潮 18 人 idle 躯干前倾 +5~6°、颈 +12~20°。--spine-up-rest
            # 下 spine_up 一律带 rest 相对朝向（忌炎 trunk +5.7→+0.7）。gfl2/endfield 同样受影响，
            # 用户定只改鸣潮，所以按游戏开关；不传时产物逐字节不变。
            if _rel is not None and (q_angle_deg(_rel) > 20.0
                                     or (args.spine_up_rest and mn == "bn_m_spine_up")):
                CORR[mn] = q_norm(q_mul(CORR[src], _rel))
            else:
                CORR[mn] = CORR[src]
    # ---- 手掌的滚转：最小旋转定不住（2026-09-29，用户「阿达希尔手指仍然异常」）
    #
    # rot_between 只对齐骨向，绕骨向的滚转是任意的。腕的骨向是腕→中指根，于是手掌
    # 绕前臂的滚转没有约束。多数角色 MMD 手型与原版差 15~40°（中位 27°），看不出来；
    # 阿达希尔右手 82°、庄方宜 90°/49°、黛烟·玉玲珑 114°/92°、塞布丽娜·泳装右手
    # 146°，手指骨向逐帧与原版一致，但整只手掌翻了过去，手指朝侧面/背面弯。
    # 修法：腕的修正改由两组局部向量（骨向 f、指节线 k = 食指根→小指根）反解：
    # C·f_m = f_v，C·k_m = k_v。指节线在腕局部系里的方向与姿势无关，是纯几何约定，
    # 所以不能用 rest 对齐（原版站立 rest 的手是半握的，会被抵消）。
    # 指根试过同样处理，产物无可测差异，已撤掉。
    def _frame(f, k):
        n = math.sqrt(sum(x * x for x in f)) or 1.0
        f = [x / n for x in f]
        d = sum(f[i] * k[i] for i in range(3))
        k = [k[i] - d * f[i] for i in range(3)]
        n = math.sqrt(sum(x * x for x in k)) or 1.0
        k = [x / n for x in k]
        u = [f[1] * k[2] - f[2] * k[1], f[2] * k[0] - f[0] * k[2], f[0] * k[1] - f[1] * k[0]]
        return (f, k, u)

    def _quat_from_matrix(M):
        (m00, m01, m02), (m10, m11, m12), (m20, m21, m22) = M
        tr = m00 + m11 + m22
        if tr > 0:
            k = 0.5 / math.sqrt(tr + 1.0)
            return q_norm(((m21 - m12) * k, (m02 - m20) * k, (m10 - m01) * k, 0.25 / k))
        if m00 > m11 and m00 > m22:
            k = 2.0 * math.sqrt(1.0 + m00 - m11 - m22)
            return q_norm((0.25 * k, (m01 + m10) / k, (m02 + m20) / k, (m21 - m12) / k))
        if m11 > m22:
            k = 2.0 * math.sqrt(1.0 + m11 - m00 - m22)
            return q_norm(((m01 + m10) / k, 0.25 * k, (m12 + m21) / k, (m02 - m20) / k))
        k = 2.0 * math.sqrt(1.0 + m22 - m00 - m11)
        return q_norm(((m02 + m20) / k, (m12 + m21) / k, 0.25 * k, (m10 - m01) / k))

    for _s in ("l", "r"):
        _vw, _vc = PFX + f"bn_{_s}_wrist", PFX + f"bn_{_s}_fi_mid1"
        _mw, _mc = f"bn_m_{_s}_wrist", f"bn_m_{_s}_middle_uff11"
        _vk = (PFX + f"bn_{_s}_fi_index1", PFX + f"bn_{_s}_fi_pinky1")
        _mk = (f"bn_m_{_s}_index_uff11", f"bn_m_{_s}_pinky_uff11")
        if CORR.get(_mw) is None or not all(k in vWP for k in (_vw, _vc) + _vk) \
                or not all(k in mWP for k in (_mw, _mc) + _mk):
            continue
        _kv = [vWP[_vk[0]][i] - vWP[_vk[1]][i] for i in range(3)]
        _km = [mWP[_mk[0]][i] - mWP[_mk[1]][i] for i in range(3)]
        Fv = _frame(local_fwd(vWP, vWQ, _vw, _vc), q_apply(q_conj(vWQ[_vw]), _kv))
        Fm = _frame(local_fwd(mWP, mWQ, _mw, _mc), q_apply(q_conj(mWQ[_mw]), _km))
        M = [[sum(Fv[a][r] * Fm[a][c] for a in range(3)) for c in range(3)] for r in range(3)]
        _cw = _quat_from_matrix(M)
        _d = q_angle_deg(q_mul(q_conj(CORR[_mw]), _cw))
        # 92 角色手掌朝向偏差中位 27°（原版与 MMD 手型本就不同），只有塞布丽娜·泳装
        # 146°、黛烟·玉玲珑 114°、庄方宜 90°、阿达希尔 82° 是离群的错误；阈值 45°
        # 只改这几个，其余产物逐字节不变。
        if _d > 45.0:
            print(f"腕滚转改用指节线对齐: {_mw} 与骨向最小旋转差 {_d:.1f}°")
            CORR[_mw] = _cw
    # ---- 脊椎的滚转：同一个问题出在 spine_low 上（2026-10-09，用户「女漂泊者上半身所有动作都拧过去半圈」）
    #
    # 女漂泊者 / 阿布的 PMX 给 上半身 设了自定义局部轴（localCoordinate），导入后 rest 轴绕骨向
    # 滚了 90°；骨向最小旋转定不住它，整条脊椎逐帧恒定拧 ~50°（相对 bind 72° / 67°，其余 42 人 23~29°）。
    # 修法同腕：第二组向量用肩线（原版 l_clavicle→r_clavicle，我们 l_shoulder→r_shoulder），
    # 与姿势无关的纯几何约定。spine_up 走 FALLBACK 的 rest 相对朝向，自动跟着改。阈值 45° 同腕。
    _vs, _vsc = PFX + "bn_sp_lumbar", PFX + "bn_sp_thoracic"
    _ms, _msc = "bn_m_spine_low", "bn_m_spine_up"
    _vk = (PFX + "bn_l_clavicle", PFX + "bn_r_clavicle")
    _mk = ("bn_m_l_shoulder", "bn_m_r_shoulder")
    if CORR.get(_ms) is not None and all(k in vWP for k in (_vs, _vsc) + _vk) \
            and all(k in mWP for k in (_ms, _msc) + _mk):
        _kv = [vWP[_vk[0]][i] - vWP[_vk[1]][i] for i in range(3)]
        _km = [mWP[_mk[0]][i] - mWP[_mk[1]][i] for i in range(3)]
        Fv = _frame(local_fwd(vWP, vWQ, _vs, _vsc), q_apply(q_conj(vWQ[_vs]), _kv))
        Fm = _frame(local_fwd(mWP, mWQ, _ms, _msc), q_apply(q_conj(mWQ[_ms]), _km))
        M = [[sum(Fv[a][r] * Fm[a][c] for a in range(3)) for c in range(3)] for r in range(3)]
        _cs = _quat_from_matrix(M)
        _d = q_angle_deg(q_mul(q_conj(CORR[_ms]), _cs))
        if _d > 45.0:
            print(f"脊椎滚转改用肩线对齐: {_ms} 与骨向最小旋转差 {_d:.1f}°")
            CORR[_ms] = _cs
            # spine_up 若已从 FALLBACK 拿到旧 spine_low 修正，按新值重算
            _rel = q_mul(q_conj(mWQ[_ms]), mWQ[_msc]) if _msc in mWQ else None
            if _rel is not None and TARGETS.get(_msc, (None, None))[1] is None:
                CORR[_msc] = q_norm(q_mul(_cs, _rel)) if q_angle_deg(_rel) > 20.0 else _cs
    # ---- 锁骨的滚转：同一个问题出在 l/r_shoulder 上（2026-10-14，用户「菲比的带子还有物理，还导致头发飞起来」）
    #
    # 菲比 / 守岸人的 PMX 肩骨带自定义局部轴，骨向最小旋转让肩骨绕自身逐帧恒定滚 177~179°。
    # 手臂按世界朝向单独求解所以看不出来，但没有通道的肩下子骨（shoulderC、菲比挂在右肩下的
    # 后披风）跟着翻了半圈：基础动画里披风朝上穿过帽子，物理一放就从头顶甩下来扫过头发。
    # 修法同脊椎：第二组向量用脊椎上向（原版 lumbar→thoracic，我们 spine_low→spine_up），
    # 与姿势无关。阈值 45° 同腕/脊椎；只在 --spine-up-rest（鸣潮）下启用，其余游戏产物逐字节不变。
    if args.spine_up_rest:
        _uv = (PFX + "bn_sp_lumbar", PFX + "bn_sp_thoracic")
        _um = ("bn_m_spine_low", "bn_m_spine_up")
        for _s in ("l", "r"):
            _vs, _vsc = PFX + f"bn_{_s}_clavicle", PFX + f"bn_{_s}_shoulder"
            _ms, _msc = f"bn_m_{_s}_shoulder", f"bn_m_{_s}_arm"
            if CORR.get(_ms) is None or not all(k in vWP for k in (_vs, _vsc) + _uv) \
                    or not all(k in mWP for k in (_ms, _msc) + _um):
                continue
            _kv = [vWP[_uv[1]][i] - vWP[_uv[0]][i] for i in range(3)]
            _km = [mWP[_um[1]][i] - mWP[_um[0]][i] for i in range(3)]
            Fv = _frame(local_fwd(vWP, vWQ, _vs, _vsc), q_apply(q_conj(vWQ[_vs]), _kv))
            Fm = _frame(local_fwd(mWP, mWQ, _ms, _msc), q_apply(q_conj(mWQ[_ms]), _km))
            M = [[sum(Fv[a][r] * Fm[a][c] for a in range(3)) for c in range(3)] for r in range(3)]
            _cs = _quat_from_matrix(M)
            _d = q_angle_deg(q_mul(q_conj(CORR[_ms]), _cs))
            if _d > 45.0:
                print(f"锁骨滚转改用脊椎上向对齐: {_ms} 与骨向最小旋转差 {_d:.1f}°")
                CORR[_ms] = _cs
    # ---- 指节的滚转：同一个问题出在手指上（2026-10-10，用户「椿手指扭曲尤其书写」「尤诺指骨异常」）
    #
    # 椿/尤诺/千咲·泳装/长离/卡卡罗/凌阳的 PMX 指骨局部轴相邻两节绕骨向滚了 110~180°
    # （腕→各指根 rest 夹角 106~177°，正常角色 4~12°），骨向最小旋转只对齐骨向，每节的滚转各自
    # 任意，相邻节相对扭转逐帧中位 18~35°、最坏 165~180°（正常 1~4°）。修法同腕：第二组向量用
    # 指节线（食指根→小指根）在本节局部系里的方向。屈指是绕指节线转的，所以它在每一节局部系里
    # 几乎不随弯曲变化，原版 rest 是半握手也不影响。拇指不在指节线上，不处理。阈值 45° 同腕，
    # 其余角色产物逐字节不变。
    for _s in ("l", "r"):
        _vk = (PFX + f"bn_{_s}_fi_index1", PFX + f"bn_{_s}_fi_pinky1")
        _mk = (f"bn_m_{_s}_index_uff11", f"bn_m_{_s}_pinky_uff11")
        if not (all(k in vWP for k in _vk) and all(k in mWP for k in _mk)):
            continue
        _kv = [vWP[_vk[0]][i] - vWP[_vk[1]][i] for i in range(3)]
        _km = [mWP[_mk[0]][i] - mWP[_mk[1]][i] for i in range(3)]
        for _vf, _mf in (("index", "index"), ("mid", "middle"), ("ring", "ring"), ("pinky", "pinky")):
            for _j in (1, 2):
                _vn, _vc = PFX + f"bn_{_s}_fi_{_vf}{_j}", PFX + f"bn_{_s}_fi_{_vf}{_j + 1}"
                _mn, _mc = f"bn_m_{_s}_{_mf}_uff1{_j}", f"bn_m_{_s}_{_mf}_uff1{_j + 1}"
                if CORR.get(_mn) is None or not all(k in vWP for k in (_vn, _vc)) \
                        or not all(k in mWP for k in (_mn, _mc)):
                    continue
                Fv = _frame(local_fwd(vWP, vWQ, _vn, _vc), q_apply(q_conj(vWQ[_vn]), _kv))
                Fm = _frame(local_fwd(mWP, mWQ, _mn, _mc), q_apply(q_conj(mWQ[_mn]), _km))
                M = [[sum(Fv[a][r] * Fm[a][c] for a in range(3)) for c in range(3)] for r in range(3)]
                _cf = _quat_from_matrix(M)
                _d = q_angle_deg(q_mul(q_conj(CORR[_mn]), _cf))
                if _d > 45.0:
                    print(f"指节滚转改用指节线对齐: {_mn} 与骨向最小旋转差 {_d:.1f}°")
                    CORR[_mn] = _cf
                    # 指尖（第 3 节）走 FALLBACK，按新的第 2 节修正重算（同 FALLBACK 公式）
                    _tip = f"bn_m_{_s}_{_mf}_uff13"
                    if _j == 2 and _tip in mWQ and TARGETS.get(_tip, (None, None))[1] is None:
                        _rel = q_mul(q_conj(mWQ[_mn]), mWQ[_tip])
                        CORR[_tip] = q_norm(q_mul(_cf, _rel)) if q_angle_deg(_rel) > 20.0 else _cf
    CORR = {k: v for k, v in CORR.items() if v is not None}
    print(f"\n轴向修正 CORR: {len(CORR)} 根")
    for mn in sorted(CORR):
        print(f"   {mn:<20} {q_angle_deg(CORR[mn]):6.1f}°")

    # ---- 两骨 IK：修正肢体比例差异
    #
    # 只搬朝向的话，骨长比例差异会让末端落错位置。实测我们的骨架：
    #     整臂 0.827×原版，但上臂只有 0.744×、前臂 0.916×
    #     （躯干 0.911×、小腿 0.996× —— 上臂是明显的离群值）
    # 于是上臂偏短把肘拉近肩，方向正确的前臂就伸向身体中线：
    # 左腕相对肩中心 x 从原版 1.47 掉到 0.18（贴到中线），
    # 表现为**前臂穿过躯干、手伸到脸前**。
    #
    # 朝向法无法修正这个 —— 它保证的是"每根骨指向哪"，不是"末端到哪"。
    # 所以对四肢改用两骨 IK：按比例缩放后的原版末端位置为目标，
    # 用原版的肘/膝朝向作为 hint 保持弯曲方向，反解上下两段。
    def solve_two_bone(root, mid, end, target, hint):
        """返回 (mid_pos, end_pos)。root 固定，L1/L2 为我们的骨长。"""
        L1 = math.dist(root, mid)
        L2 = math.dist(mid, end)
        d = [target[i] - root[i] for i in range(3)]
        dist = math.sqrt(sum(x * x for x in d)) or 1e-9
        # 目标超出可达范围就伸直到最远处
        reach = min(max(dist, abs(L1 - L2) + 1e-6), L1 + L2 - 1e-6)
        n = [x / dist for x in d]
        # 余弦定理求肘在根→目标轴上的投影
        a = (L1 * L1 - L2 * L2 + reach * reach) / (2 * reach)
        h = math.sqrt(max(L1 * L1 - a * a, 0.0))
        # 弯曲平面法向：用 hint 方向去掉沿轴分量
        hv = [hint[i] - n[i] * sum(hint[j] * n[j] for j in range(3))
              for i in range(3)]
        hn = math.sqrt(sum(x * x for x in hv))
        if hn < 1e-6:
            tmp = (0.0, 0.0, 1.0) if abs(n[1]) > 0.9 else (0.0, 1.0, 0.0)
            hv = [tmp[i] - n[i] * sum(tmp[j] * n[j] for j in range(3))
                  for i in range(3)]
            hn = math.sqrt(sum(x * x for x in hv)) or 1.0
        hv = [x / hn for x in hv]
        mid_new = tuple(root[i] + n[i] * a + hv[i] * h for i in range(3))
        end_new = tuple(root[i] + n[i] * reach for i in range(3))
        return mid_new, end_new

    # 小腿横向内收的衰减系数。见下方"收窄小腿的横向内收"的完整推导与扫描表。
    LATERAL_DAMP = args.lateral_damp

    # 横向缩放基准：(原版左, 原版右, 我们左, 我们右)。
    # 用**同层的左右对称骨**量宽度：腿用髋、手臂用肩。
    # 之所以不复用躯干尺度 0.9115：髋宽比是 14.15/19.55=0.724，与躯干
    # 高度比差了 20%，用错的那个就是脚交叉的来源。
    SPAN_BASE = {
        "bn_m_l_hip": (PFX + "bn_l_hip", PFX + "bn_r_hip",
                       "bn_m_l_hip", "bn_m_r_hip"),
        "bn_m_r_hip": (PFX + "bn_l_hip", PFX + "bn_r_hip",
                       "bn_m_l_hip", "bn_m_r_hip"),
        "bn_m_l_arm": (PFX + "bn_l_shoulder", PFX + "bn_r_shoulder",
                       "bn_m_l_arm", "bn_m_r_arm"),
        "bn_m_r_arm": (PFX + "bn_l_shoulder", PFX + "bn_r_shoulder",
                       "bn_m_l_arm", "bn_m_r_arm"),
    }

    # 需要 IK 的链：(根, 中, 末)
    #
    # **手臂默认也不做 IK 了（2026-08-17 改）。** 原先硬编码开启，理由是
    # 「臂长只有原版 0.827×，末端落点会偏」—— 但当时判断相对迁移「不合理」
    # 的那批渲图，是在 rest 语义 bug 还在的时候出的：`.anim` 的 rest 是
    # **第 0 帧**而不是 bind pose，按增量迁移会得到僵直手臂（见
    # docs 与记忆 pdx-anim-rest-is-frame0-not-bindpose）。那个 bug 修掉之后
    # 相对朝向拷贝本身是成立的，IK 反而多引入一层解算误差。
    #
    # 用 --arm-ik 可以开回来做 A/B 对比。
    IK_CHAINS = []
    if args.arm_ik:
        IK_CHAINS += [
            ("bn_m_l_arm", "bn_m_l_elbow", "bn_m_l_wrist"),
            ("bn_m_r_arm", "bn_m_r_elbow", "bn_m_r_wrist"),
        ]
    # ---- 双手关系 IK（2026-09-05；起于镣铐，同日推广到全部道具与合掌动作）
    #
    # 相对朝向拷贝保得住「弯多少」，保不住「两只手在哪」：我们臂长 33~39、
    # 肩宽 16~18，原版 47.7 / 30，两手的世界间距随比例一起缩。凡是两只手之间
    # 有**绝对**几何约束的动作，这个缩放就是错的：
    #   · 手持道具：道具 mesh 是原版尺寸、按原版腕→道具世界偏移挂在一只手上
    #     （PROP_TARGETS），另一只手要放在道具上（书页、弓弦、铐环）。镣铐是
    #     最早撞上的：misc_arrest 左腕相对 `bn_r_prop` 原版 [6.7,-9.0,10.3]，
    #     我们 [-7.0,-7.2,3.9]，X 反号差 15 单位；改挂点救不了 —— 右腕跟着错。
    #   · 合掌类（praying / prostration / admiration…）：两手要**贴到**一起，
    #     原版腕距 2.3~8.2，按比例拷贝后芙洛伦 8.9、洁尔佩塔 7.6，手是分开的。
    # 两类共用一个解：让「非锚定手」的腕落到 锚点 + 原版「锚腕→该腕」世界向量。
    # 锚点 = 持道具的手（道具随它走）；无道具时取两腕中点，双臂各解一半。
    # 判定与逐帧权重在下方 HAND IK 段（需要先做完两侧 FK 预扫），这里不决定。
    # 用户 2026-09-05 的要求：涉及 attach 物品的、与有合掌动作的都走 IK 烘焙。
    # 腿部同样**默认不做 IK**，只拷贝相对朝向。腿的比例几乎一致
    #（小腿 0.996×、大腿 0.925×），纯旋转拷贝就够。
    #
    # 为什么不能用"归一化踝距对齐原版 0.32"当判据（我踩过）：
    # 那个比值量的是 ankleD **关节中心**间距，完全没考虑单只脚的皮肤宽度
    # 6.28 单位。原版 0.32 是在髋宽 19.55 的骨架上成立的；把同样的**关节**
    # 比值套到我们 14.15 的髋宽上，两只脚的**网格**就插进彼此。
    # 数值"对齐原版"了，画面上却是两脚挤成一团 —— 优化错了量。
    #
    # 带蒙皮渲图才是判据：关 IK = 两只分开的脚，开 IK = 脚面重叠。
    # 自穿插也不支持开 IK（关 5519~5576 / 开 5511~5588，基本持平）。
    if args.leg_ik:
        IK_CHAINS += [
            ("bn_m_l_hip", "bn_m_l_knee", "bn_m_l_ankle"),
            ("bn_m_r_hip", "bn_m_r_knee", "bn_m_r_ankle"),
        ]

    # ---- 逐帧：把原版世界朝向搬到我们骨上，再转回父空间
    # 必须按层级从根到叶，父骨用重定向后的世界朝向，避免误差累积。
    order = []
    def emit(n):
        if n in order:
            return
        p = mpar.get(n)
        if p:
            emit(p)
        order.append(n)
    for n in mrest:
        emit(n)

    # 用 TARGETS 而不是 BONE_MAP 反转：同一根原版骨驱动多根我们的骨
    inv_map = {mn: t[0] for mn, t in TARGETS.items()}
    inv_map.update({mn: src for mn, (src, _vw, _mw) in PROP_TARGETS.items()
                    if mn in mrest and src in vrest})
    out_q, out_t, report = {}, {}, []
    for vn, mn in bone_map.items():
        if vn not in vrest:
            report.append((vn, mn, "原版无此骨", 0.0))
        elif mn not in mrest:
            report.append((vn, mn, "我们无此骨", 0.0))

    v_order = []
    def vemit(n):
        if n in v_order:
            return
        key = n[len(PFX):] if n.startswith(PFX) else n
        p = vpar.get(key)
        p = (PFX + p) if (p and (PFX + p) in vrest) else p
        if p and p in vrest:
            vemit(p)
        v_order.append(n)
    for n in vrest:
        vemit(n)

    # ---- 逐帧 FK 抽成函数：主循环与「双手关系 IK」的预扫都要用
    def fk_vanilla(f):
        """原版第 f 帧的世界朝向 + 世界位置。"""
        vWq, vWp = {}, {}
        for n in v_order:
            key = n[len(PFX):] if n.startswith(PFX) else n
            p = vpar.get(key)
            p = (PFX + p) if (p and (PFX + p) in vrest) else p
            q = q_norm(vqs[n][f]) if n in vqs else q_norm(vrest[n]["q"])
            t = vts[n][f] if n in vts else tuple(vrest[n]["t"])
            if p and p in vWq:
                o = q_apply(vWq[p], t)
                vWp[n] = tuple(vWp[p][i] + o[i] for i in range(3))
                vWq[n] = q_norm(q_mul(vWq[p], q))
            else:
                vWp[n], vWq[n] = tuple(t), q
        return vWq, vWp

    def fk_ours(vWq):
        """我们的世界朝向 + 世界位置（纯朝向拷贝，IK 之前）。"""
        mWq, mWp = {}, {}
        for n in order:
            p = mpar.get(n)
            vn = inv_map.get(n)
            if vn and vn in vWq and n in CORR:
                world = q_norm(q_mul(vWq[vn], CORR[n]))
            else:
                lq = q_norm(mrest[n]["q"])
                world = q_norm(q_mul(mWq[p], lq)) if (p and p in mWq) else lq
            t = tuple(mrest[n]["t"])
            if p and p in mWq:
                o = q_apply(mWq[p], t)
                mWp[n] = tuple(mWp[p][i] + o[i] for i in range(3))
            else:
                mWp[n] = t
            mWq[n] = world
        return mWq, mWp

    pre_v = [fk_vanilla(f) for f in range(VF)]
    pre_m = [fk_ours(vq) for vq, _vp in pre_v]

    # ---- HAND IK：双手关系 IK 的判定（理由见 IK_CHAINS 处的长注释）
    #
    # 三个常量都来自对原版 345 条 torso 动画的实测扫描（work/evidence/prop_hands_scan，
    # 结果表已并进 README「双手关系 IK」一节）：
    #   HANDS_NEAR 9.0   原版**腕关节**距 ≤ 9 的 16 条全是合掌/抱手/双手托物，
    #                    再往上第一条是 9.66 的 frontEnd_right（两手自然下垂），
    #                    9 与 9.66 之间是空档，阈值放在这里。
    #   HANDS_FAR 20.0   合掌类动作里手分开的段落（prostration 4→58）按腕距在
    #                    9~20 之间线性淡出到纯朝向拷贝，>20 完全不管 —— 手分开
    #                    之后本来就没有约束，硬解反而把臂拉直。
    #   REACH_MAX 0.98   道具类只在**全程**够得到时启用：肩→目标 / 臂全长 超过
    #                    0.98 说明另一只手其实离道具很远（assassin 79.8、lantern
    #                    101），那时 solve 只会把臂拉成直线指向目标，不如不做。
    HAND_CHAINS = {"l": ("bn_m_l_arm", "bn_m_l_elbow", "bn_m_l_wrist"),
                   "r": ("bn_m_r_arm", "bn_m_r_elbow", "bn_m_r_wrist")}
    V_WRIST = {"l": PFX + "bn_l_wrist", "r": PFX + "bn_r_wrist"}
    M_WRIST = {"l": "bn_m_l_wrist", "r": "bn_m_r_wrist"}
    V_PROP = {"l": PFX + "bn_l_prop", "r": PFX + "bn_r_prop"}
    M_PROP = {"l": "bn_l_prop", "r": "bn_r_prop"}
    # ---- 道具挂点偏移按**手长**缩放（2026-09-05，用户：手持武器略偏）
    #
    # 腕→prop 的偏移此前原样搬原版（7.82 单位），但我们腕→中指根只有 5.0~5.6，
    # 原版 10.1 —— 挂点落在指尖之外，握柄悬在手掌前方。身高比（0.91）不对，
    # 这段偏移是「握在手掌哪里」，随手的尺寸走；用腕→中指根的比值（站立 rest
    # 上量，与尺度比同一基准），三人 0.49~0.55。挂点动了，另一只手的 IK 目标
    # 也要相对**道具**而不是相对腕（镣铐两环随道具走），见下。
    HAND_SCALE = {}
    for _s in "lr":
        _vw, _vm = V_WRIST[_s], PFX + f"bn_{_s}_fi_mid1"
        _mw, _mm = M_WRIST[_s], f"bn_m_{_s}_middle_uff11"
        if all(k in vstandWP for k in (_vw, _vm)) and all(k in mWP for k in (_mw, _mm)):
            HAND_SCALE[_s] = (math.dist(mWP[_mw], mWP[_mm])
                              / (math.dist(vstandWP[_vw], vstandWP[_vm]) or 1e-9))
        else:
            HAND_SCALE[_s] = 1.0
    print(f"  道具挂点偏移按手长缩放: 左 {HAND_SCALE['l']:.3f} 右 {HAND_SCALE['r']:.3f}")

    def our_prop_pos(side, vWp_, mWp_):
        """该帧我们道具原点的世界位置 = 我们腕 + 手长比 × 原版(腕→prop) 世界偏移。"""
        vw, vp = V_WRIST[side], V_PROP[side]
        if vp not in vWp_:
            return mWp_[M_WRIST[side]]
        s_ = HAND_SCALE[side]
        return tuple(mWp_[M_WRIST[side]][i] + s_ * (vWp_[vp][i] - vWp_[vw][i])
                     for i in range(3))
    HANDS_NEAR, HANDS_FAR, REACH_MAX = 9.0, 20.0, 0.98
    hand_roles = {}        # chain → side（'l' / 'r'），被 IK 的那几条
    hand_anchor = None     # 'l' / 'r' / 'mid'
    hand_w = [0.0] * VF    # 逐帧权重
    hand_mode = None
    _both_v = all(V_WRIST[s] in pre_v[0][1] for s in "lr")
    _both_m = all(M_WRIST[s] in pre_m[0][1] and HAND_CHAINS[s][0] in pre_m[0][1]
                  for s in "lr")
    if _both_v and _both_m:
        import moe_anim_catalog as _CAT
        _base = _CAT.base_of_source(Path(args.src).name)
        _nodes = set(_CAT.hand_prop_nodes().get(_base, ())) if _base else set()
        d_v = [math.dist(vp[V_WRIST["l"]], vp[V_WRIST["r"]]) for _vq, vp in pre_v]
        if len(_nodes) == 1:
            _a = next(iter(_nodes))
            _f = "r" if _a == "l" else "l"
            _root, _mid, _end = HAND_CHAINS[_f]
            _reach = 0.0
            for (_vq, vp), (_mq, mp) in zip(pre_v, pre_m):
                # 目标相对**道具原点**：我们的道具 + 原版「道具→另一只腕」
                _ref_v = V_PROP[_a] if V_PROP[_a] in vp else V_WRIST[_a]
                _pp = our_prop_pos(_a, vp, mp)
                tgt = tuple(_pp[i] + vp[V_WRIST[_f]][i] - vp[_ref_v][i]
                            for i in range(3))
                limb = (math.dist(mp[_root], mp[_mid])
                        + math.dist(mp[_mid], mp[_end])) or 1e-9
                _reach = max(_reach, math.dist(mp[_root], tgt) / limb)
            if _reach <= REACH_MAX:
                hand_mode, hand_anchor = "prop", _a
                hand_roles = {HAND_CHAINS[_f]: _f}
                hand_w = [1.0] * VF
                print(f"  HAND IK[prop]: 道具在{ {'l': '左', 'r': '右'}[_a] }手 "
                      f"→ 另一只手 IK 到原版腕间向量（最大可达率 {_reach:.2f}）")
            else:
                print(f"  HAND IK: 道具在{ {'l': '左', 'r': '右'}[_a] }手，但另一只手"
                      f"全程够不到（可达率 {_reach:.2f} > {REACH_MAX}），不做")
        if hand_mode is None and min(d_v) <= HANDS_NEAR:
            hand_mode = "hands"
            hand_anchor = next(iter(_nodes)) if len(_nodes) == 1 else "mid"
            if hand_anchor == "mid":
                hand_roles = {HAND_CHAINS["l"]: "l", HAND_CHAINS["r"]: "r"}
            else:
                _f = "r" if hand_anchor == "l" else "l"
                hand_roles = {HAND_CHAINS[_f]: _f}
            hand_w = [max(0.0, min(1.0, (HANDS_FAR - d) / (HANDS_FAR - HANDS_NEAR)))
                      for d in d_v]
            print(f"  HAND IK[hands]: 原版腕距 min {min(d_v):.2f} ≤ {HANDS_NEAR} "
                  f"→ 锚 {hand_anchor}，全权重帧 "
                  f"{sum(1 for w in hand_w if w >= 0.999)}/{VF}")
        for ch in hand_roles:
            if ch not in IK_CHAINS:
                IK_CHAINS.append(ch)
    hand_ik_bones = {b for ch in hand_roles for b in ch[:2]}
    hand_resid = 0.0       # 全权重帧上 腕→目标 的最大残差（判据）

    ik_stats = []
    for f in range(VF):
        vWq, vWp = pre_v[f]
        mWq, mWp = pre_m[f]
        # 双手关系 IK 的锚点位置**在任何链被改之前**取一次：mid 模式两条链都
        # 会动腕，第二条链若再读 mWp 会拿到第一条链改过的值。
        hand_mid, hand_anchor_pos = None, None
        if hand_mode:
            hand_mid = tuple((mWp[M_WRIST["l"]][i] + mWp[M_WRIST["r"]][i]) / 2
                             for i in range(3))
            if hand_anchor == "mid":
                hand_anchor_pos = None
            elif hand_mode == "prop":
                hand_anchor_pos = our_prop_pos(hand_anchor, vWp, mWp)
            else:
                hand_anchor_pos = mWp[M_WRIST[hand_anchor]]

        # ---- 两骨 IK 修正四肢：目标 = 按比例缩放的原版末端位置
        # 相对链根做缩放，这样只修正肢体比例、不引入整体位移。
        fixed_dir = {}
        for root_b, mid_b, end_b in IK_CHAINS:
            vr_, vm_, ve_ = (inv_map.get(root_b), inv_map.get(mid_b),
                             inv_map.get(end_b))
            if not (vr_ and vm_ and ve_):
                continue
            if vr_ not in vWp or ve_ not in vWp:
                continue
            # 目标 = 原版的**方向** + 原版的**伸展率**（root→end 距离 / 肢长）。
            #
            # 不能用「原版距离 × 躯干尺度」当目标：那是绝对长度，会改变伸展率。
            # 实测原版腿伸展率 0.984（近乎伸直），按 0.9115 缩放后落到我们
            # 肢长的 0.934 —— 膝盖被迫多弯 20°（155°→135°）。手臂反向偏
            # （0.757→0.834）。伸展率才是与尺度无关的量，直接搬它才能保住
            # 「弯多少」的观感。
            axis_v = [vWp[ve_][i] - vWp[vr_][i] for i in range(3)]
            dist_v = math.sqrt(sum(x * x for x in axis_v)) or 1e-9
            limb_v = (math.dist(vWp[vr_], vWp[vm_])
                      + math.dist(vWp[vm_], vWp[ve_])) or 1e-9
            limb_m = (math.dist(mWp[root_b], mWp[mid_b])
                      + math.dist(mWp[mid_b], mWp[end_b])) or 1e-9
            ratio = dist_v / limb_v
            want = ratio * limb_m
            dv = [axis_v[i] / dist_v * want for i in range(3)]
            # ---- 横向(X)分量单独按**髋宽/肩宽**缩放，不跟着肢长走
            #
            # 只按肢长缩放会把两脚并到一起：我们髋宽 14.15 是原版 19.55 的
            # 0.724 倍，踝的横向偏移被同比压缩，但**脚本身的宽度没缩**
            # （单脚皮肤宽 6.28，而压缩后踝间距只剩 1.00）→ 两脚重叠交叉。
            # 实测按髋宽归一化的踝间距：原版 6.27/19.55=0.32，我们 1.00/14.15=0.07。
            #
            # 伸展率管的是"弯多少"（沿肢体轴），站距管的是"分多开"（横向），
            # 这两个量的正确缩放基准不同，必须拆开：
            #   轴向 → 肢长比（保住膝角/肘角）
            #   横向 → 骨架宽度比（保住站距/脚不交叉）
            span_b = SPAN_BASE.get(root_b)
            if span_b:
                vw = abs(vWp[span_b[0]][0] - vWp[span_b[1]][0]) or 1e-9
                mw = abs(mWp[span_b[2]][0] - mWp[span_b[3]][0]) or 1e-9
                dv[0] = (vWp[ve_][0] - vWp[vr_][0]) * (mw / vw)
            target = tuple(mWp[root_b][i] + dv[i] for i in range(3))
            # hint：原版的肘/膝偏离「根→末端」轴的方向，决定弯曲平面
            axis = [vWp[ve_][i] - vWp[vr_][i] for i in range(3)]
            an = math.sqrt(sum(x * x for x in axis)) or 1.0
            axis = [x / an for x in axis]
            em = [vWp[vm_][i] - vWp[vr_][i] for i in range(3)]
            hint = [em[i] - axis[i] * sum(em[j] * axis[j] for j in range(3))
                    for i in range(3)]
            chain = (root_b, mid_b, end_b)
            if chain in hand_roles:
                # ---- 双手关系：目标 = 锚点 + 原版「锚腕→该腕」世界向量
                side = hand_roles[chain]
                other = "r" if side == "l" else "l"
                w = hand_w[f]
                if w <= 1e-6:
                    continue          # 手分开的段落：保持纯朝向拷贝
                # prop 模式：原版向量从**道具原点**量起（锚点也是我们的道具原点）
                _ref = (V_PROP[other] if (hand_mode == "prop"
                                          and V_PROP[other] in vWp)
                        else V_WRIST[other])
                vv = [vWp[V_WRIST[side]][i] - vWp[_ref][i] for i in range(3)]
                if hand_anchor == "mid":
                    rel = tuple(hand_mid[i] + vv[i] / 2 for i in range(3))
                else:
                    rel = tuple(hand_anchor_pos[i] + vv[i] for i in range(3))
                cur = mWp[end_b]
                target = tuple(cur[i] + w * (rel[i] - cur[i]) for i in range(3))
                # 弯曲平面取**我们当前**的肘，不取原版的：w→0 时解要退化成
                # 当前姿态本身（同平面、同伸展率 ⇒ 肘位不变），否则淡出段落
                # 的最后一帧到纯拷贝的第一帧之间肘会跳一下。
                em = [mWp[mid_b][i] - mWp[root_b][i] for i in range(3)]
                ax2 = [cur[i] - mWp[root_b][i] for i in range(3)]
                an2 = math.sqrt(sum(x * x for x in ax2)) or 1.0
                ax2 = [x / an2 for x in ax2]
                hint = [em[i] - ax2[i] * sum(em[j] * ax2[j] for j in range(3))
                        for i in range(3)]
            mid_new, end_new = solve_two_bone(
                mWp[root_b], mWp[mid_b], mWp[end_b], target, hint)
            if chain in hand_roles and hand_w[f] >= 0.999:
                hand_resid = max(hand_resid, math.dist(end_new, target))
            if f == 0:
                before = math.dist(mWp[end_b], target)
                after = math.dist(end_new, target)
                ik_stats.append((end_b, before, after))
            fixed_dir[root_b] = (mWp[root_b], mid_new)
            fixed_dir[mid_b] = (mid_new, end_new)
            mWp[mid_b], mWp[end_b] = mid_new, end_new

        # ---- 把 IK 后的骨向写回世界朝向：绕最小角把原骨向转到新骨向
        # 顺序必须根→中，改了根骨朝向后中骨的世界朝向要跟着重算
        #
        # **同一份修正必须同时写给 D 骨**，否则等于没做：IK 是在"位置"域里
        # 解的，而 .anim 只有 1 根 t 通道（bn_m_groove），腿骨全靠 q 传递。
        # D 骨拿不到 IK 后的新骨向就会保持 IK 前的旋转 —— 实测控制链踝距被
        # 修正到 4.538（比值 0.32，与原版一致），D 链却仍是 1.038，
        # 而**皮肤跟的是 D 链**，所以画面上两脚照旧交叉。
        # 这与"只驱动控制骨导致数值完美但画面不动"是同一类陷阱。
        IK_TWIN = {
            "bn_m_l_hip": "bn_m_l_legD",  "bn_m_r_hip": "bn_m_r_legD",
            "bn_m_l_knee": "bn_m_l_kneeD", "bn_m_r_knee": "bn_m_r_kneeD",
        }
        for bone in [c[0] for c in IK_CHAINS] + [c[1] for c in IK_CHAINS]:
            if bone not in fixed_dir or bone not in FWD_LOCAL:
                continue
            a, b = fixed_dir[bone]
            new = [b[i] - a[i] for i in range(3)]
            nn = math.sqrt(sum(x * x for x in new)) or 1.0
            new = [x / nn for x in new]
            for tgt in (bone, IK_TWIN.get(bone)):
                if not tgt or tgt not in mWq or tgt not in FWD_LOCAL:
                    continue
                old = q_apply(mWq[tgt], FWD_LOCAL[tgt])
                dq = rot_between(old, new)
                mWq[tgt] = q_norm(q_mul(dq, mWq[tgt]))
                # 双手关系链：同一个 dq 施加给该骨的全部后代。手臂链上大臂→肘
                # 之间串着 armTwist、肘→腕之间串着 wristTwist（见 SKIN 注释），
                # 只转大臂/肘而不带后代，中间骨会反向补偿以保住原世界朝向，
                # 皮肤在上臂/前臂中段被拧开。腿链没这么做是因为腿的膝内收
                # 段自己已经带后代（见下）；--arm-ik 的 A/B 链保持旧行为。
                if bone in hand_ik_bones:
                    for desc in order:
                        a_ = mpar.get(desc)
                        while a_:
                            if a_ == tgt:
                                mWq[desc] = q_norm(q_mul(dq, mWq[desc]))
                                break
                            a_ = mpar.get(a_)

        # ---- 收窄小腿的横向内收：防止两脚越过中线重叠
        #
        # 拷贝原版小腿方向会让踝**冲过中线**，这是算术上的必然，不是 bug：
        #   原版 rest 小腿横向偏移(踝相对膝)  左 -7.26  右 +4.43（且左右不对称）
        #   我们 rest 小腿                    左 -0.00  右 -0.00（完全垂直）
        # 原版靠"叉开的膝(+11.15/-6.80) + 大幅内收小腿"达到窄站姿；我们膝在
        # ±7.09、小腿垂直，照搬同一方向就冲过去：
        #   左: 7.09 + (-0.194 × 37.24) = -0.14  ← 过中线
        #   右: -7.09 + (+0.118 × 37.24) = -2.68
        #
        # 关键约束是**脚宽**，而且必须按**实际蒙皮后的网格**量，不能按关节距。
        # 我第一次拿"踝关节间距 - 单脚宽 6.28"当判据，选了 0.5，结果渲图上
        # 两脚仍然插在一起 —— 因为脚的网格从踝关节往两侧**各自伸出**，
        # 关节间距 6.79~7.46 看着够，实际网格间隙却是 **-3.54**（重叠）。
        #
        # 改用 LBS 算出脚部顶点的世界 X，取 (左脚 minX - 右脚 maxX) 扫：
        #   damp  最坏实际间隙
        #   0.50   -3.54   重叠
        #   0.35   -1.78   重叠
        #   0.20   -0.02   刚好贴住
        #   0.10   +1.16   ← 采用
        #   0.00   +2.34   完全垂直
        # 我们的脚相对髋宽太宽（高跟鞋 + 窄髋），**回不到原版的 0.321 站距**，
        # 这是模型比例差异，旋转解决不了。0.1 是"有间隙"的前提下能保留的
        # 最大内收量。判据教训：涉及穿插一律量网格，别量关节。
        for s in ("l", "r"):
            vk, va = inv_map.get(f"bn_m_{s}_knee"), None
            vkn = PFX + f"bn_{s}_knee"
            van = PFX + f"bn_{s}_ankle"
            if vkn not in vWp or van not in vWp:
                continue
            d = [vWp[van][i] - vWp[vkn][i] for i in range(3)]
            d[0] *= LATERAL_DAMP
            nn = math.sqrt(sum(x * x for x in d)) or 1.0
            new = [x / nn for x in d]
            # 旋转小腿时**必须把脚一起带走**。
            # mWq 存的是世界朝向，而落盘的局部 q = 父⁻¹ · 自身世界。只改
            # kneeD 而不动 ankleD，等于让脚在世界里**保持不动**，那份差异会被
            # 当成脚的局部反向旋转写进去 —— 实测 kneeD 转 21.57°(左)/170.10°(右)
            # 而 ankleD 转 0.00°，脚踝处被生生拧开，且左右幅度不同
            # （左小腿内收 -0.194 需转 10.06°，右 +0.118 只需 6.12°），
            # 所以画面上只有一侧明显别扭。
            for tgt in (f"bn_m_{s}_knee", f"bn_m_{s}_kneeD"):
                if tgt not in mWq or tgt not in FWD_LOCAL:
                    continue
                old = q_apply(mWq[tgt], FWD_LOCAL[tgt])
                dq = rot_between(old, new)
                mWq[tgt] = q_norm(q_mul(dq, mWq[tgt]))
                # 同一个 dq 施加给该骨的所有后代（脚），保持相对关系不变
                for desc in order:
                    a_ = mpar.get(desc)
                    while a_:
                        if a_ == tgt:
                            mWq[desc] = q_norm(q_mul(dq, mWq[desc]))
                            break
                        a_ = mpar.get(a_)

        # Transfer accessory attachment transforms after the wrists have
        # reached their final retargeted orientation for this frame. The
        # offset is **not** body-height-scaled: it is where the grip sits in
        # the palm, so it scales with HAND_SCALE (wrist→middle-finger-root
        # ratio, 0.49~0.55) — unscaled it landed past our fingertips.
        # Orientation is copied as-is from vanilla (world).
        for prop, (vprop, vwrist, mwrist) in PROP_TARGETS.items():
            if not all((prop in mrest, vprop in vWp, vwrist in vWp,
                        mwrist in mWp, mwrist in mWq)):
                continue
            assert mpar.get(prop) == mwrist, \
                f"{prop} parent must be {mwrist}, got {mpar.get(prop)!r}"
            _hs = HAND_SCALE["l" if prop == "bn_l_prop" else "r"]
            world_offset = tuple(_hs * (vWp[vprop][i] - vWp[vwrist][i])
                                 for i in range(3))
            parent_inv = q_conj(mWq[mwrist])
            local_t = q_apply(parent_inv, world_offset)
            local_q = q_norm(q_mul(parent_inv, vWq[vprop]))
            out_t.setdefault(prop, []).append(local_t)
            out_q.setdefault(prop, []).append(local_q)

        # ---- 转回父空间局部 q 并落盘
        for n in order:
            vn = inv_map.get(n)
            if not (vn and vn in vWq and n in CORR):
                continue
            p = mpar.get(n)
            pw = mWq.get(p) if p else None
            local = q_norm(q_mul(q_conj(pw), mWq[n])) if pw else mWq[n]
            out_q.setdefault(n, []).append(local)

    if ik_stats:
        print(f"\nIK 修正（frame0 末端到目标距离）:")
        for nm, b, a in ik_stats:
            print(f"   {nm:<18} {b:7.2f} → {a:7.2f}")
    if hand_mode:
        _ends = {ch[2] for ch in hand_roles}
        for nm, b, a in ik_stats:
            if nm in _ends:
                report.append((f"hand-IK[{hand_mode}] anchor={hand_anchor} "
                               f"(frame0, before)", nm, "t", b))
                report.append((f"hand-IK[{hand_mode}] anchor={hand_anchor} "
                               f"(frame0, after)", nm, "t", a))
        report.append((f"hand-IK[{hand_mode}] 全权重帧最大残差", "wrist", "t",
                       hand_resid))
        print(f"  HAND IK 残差（全权重帧，腕→目标最大）: {hand_resid:.3f}")

    for mn, frames in out_q.items():
        vn = inv_map[mn]
        mx = max(q_angle_deg(q_mul(frames[i], q_conj(frames[0])))
                 for i in range(len(frames)))
        report.append((vn, mn, "q", mx))

    for prop, (vprop, _vwrist, _mwrist) in PROP_TARGETS.items():
        if prop not in out_t:
            continue
        mx = max(math.dist(out_t[prop][i], out_t[prop][0])
                 for i in range(len(out_t[prop])))
        report.append((vprop, prop, "t", mx))

    for vn, mn in bone_map.items():
        if vn not in vrest or mn not in mrest:
            continue
        # 对 body_root：传世界摆位（相对站立基准），不传 delta，不然坐姿/
        # 跪姿的绝对下降+前移全丢掉（表现是角色在站立高度摆坐姿、被王座挡住）。
        # 但必须转到目标父空间（目标骨的父带旋转），不能按世界轴逐轴加到 rest t。
        is_placement_bone = (vn == PFX + "body_root")
        # **摆位骨没有 t 通道时也要传**：摆位恒定的 clip 把它整个放在 rest 里，
        # 走 `if vn in vts` 就漏掉。实测 423 条里有 8 条这样，其中
        #   praying           rest 世界 y=38.50  ← 跪姿，比站立低 42
        #   throneRoom_ruler3 rest 世界 y=38.42  ← 王座坐姿
        # 都是真需要摆位的。表现是「同样是坐姿，ruler1/ruler2 修好了 ruler3 没修」。
        # 这里把 rest t 铺满全帧，下游按普通通道处理（恒定值最后会被
        # `_drop_constant` 压回 rest，无损）。
        src_t = vts.get(vn)
        if src_t is None and is_placement_bone:
            src_t = [tuple(vrest[vn]["t"])] * VF
        if src_t is not None:
            # `tf` 是局部 t，当世界坐标用 —— 这个前提在原版 female_body 的
            # 423 条 clip 上**精确成立**（最大偏差 0.000000）：`body_root` 的父
            # `ground_joint` 恒在原点且无旋转无通道，另有 161 条干脆没有
            # `ground_joint`（`body_root` 自己就是根）。
            trv = vrest[vn]["t"]
            trm = mrest[mn]["t"]
            frames = []
            max_move = 0.0
            pn = mpar.get(mn) if is_placement_bone else None
            pq_inv = q_conj(q_norm(mWQ[pn])) if (pn and pn in mWQ) else (0, 0, 0, 1)
            # 新骨架把原版摆位写到 torso-only 的字面 body_root；旧骨架仍写
            # bn_m_groove。两者只能选一个，不能双重驱动。
            for tf in src_t:
                if is_placement_bone:
                    world_offset = tuple((tf[i] - VANILLA_BODY_ROOT_REST_BASELINE[i])
                                         * TRANSLATION_SCALE for i in range(3))
                    d = q_apply(pq_inv, world_offset)
                else:
                    d = [(tf[i] - trv[i]) * TRANSLATION_SCALE for i in range(3)]
                frames.append(tuple(trm[i] + d[i] for i in range(3)))
                max_move = max(max_move, math.sqrt(sum(x * x for x in d)))
            out_t[mn] = frames
            report.append((vn, mn, "t", max_move))

    # Royal Court owns body_root placement at runtime, so an extra offset on
    # that bone is discarded. Apply the ruler-only seat calibration to the
    # mesh-driving bn_m_root instead. Convert the desired world-space drop to
    # parent space on every frame; this remains correct if body_root rotates.
    is_throne_ruler = "throneRoom_ruler" in Path(args.src).stem
    if is_throne_ruler:
        mesh_root = "bn_m_root"
        assert mesh_root in mrest, f"王座坐姿校准需要 {mesh_root}"

        def frame_world_q(n, frame, cache):
            if n in cache:
                return cache[n]
            p = mpar.get(n)
            local_q = (out_q[n][frame] if n in out_q
                       else q_norm(mrest[n]["q"]))
            if p and p in mrest:
                world_q = q_norm(q_mul(frame_world_q(p, frame, cache), local_q))
            else:
                world_q = local_q
            cache[n] = world_q
            return world_q

        parent = mpar.get(mesh_root)
        base_frames = out_t.get(mesh_root, [tuple(mrest[mesh_root]["t"])] * VF)
        corrected = []
        world_drop = (0.0, -THRONE_RULER_SEAT_DROP, 0.0)
        for f, base_t in enumerate(base_frames):
            parent_world_q = (frame_world_q(parent, f, {})
                              if parent and parent in mrest
                              else (0.0, 0.0, 0.0, 1.0))
            local_drop = q_apply(q_conj(parent_world_q), world_drop)
            corrected.append(tuple(base_t[i] + local_drop[i] for i in range(3)))
        out_t[mesh_root] = corrected
        report.append(("Royal Court seat calibration", mesh_root, "t",
                       THRONE_RULER_SEAT_DROP))

    # ---- 相机锚骨 camera_torso_look_at 钉死在世界空间（2026-09-05）
    #
    # 所有肖像相机的 position_node / look_at_node 都是这根骨。原版给它自己的
    # t+q 通道，FK 世界位置全程恒定（idle/praying/laugh 逐帧差 0.00）——
    # 相机固定在空间里，人在镜头前动。我们的这根骨建在 bn_m_neck 下且**没有
    # 通道**，于是跟着脊椎走：laugh 16.24、conversation 4.11、idle 1.22 单位。
    # 用户在 portrait editor 里看到的就是「相机跟着身子晃」。
    #
    # 修法与原版同构：逐帧解出父骨世界变换，反算局部 t/q，让它的世界变换
    # 恒等于「模板站立 rest 的世界变换 + 本 clip 的摆位偏移」。摆位偏移取
    # 第 0 帧 bn_m_root 的 FK 世界位置减它的 rest 世界位置 —— 原版跪姿
    # (praying) 的相机比站姿低 38、body_root 低 42，就是「相机随摆位平移、
    # 不随姿态摆动」这个语义。q 也钉住（原版 q 通道同样恒定）。
    #
    # 只改上游：head 动画由 graft 按「身体驱动骨」照抄通道 + rest，所以这里
    # 写出的通道会原样进 head；两个 entity 对同名骨给同一姿态。
    # **不能只给 head 或只给 torso**：引擎按骨名共享姿态
    # （[[ck3-shared-bone-names-across-entities-share-pose]]）。
    if args.semantic_motion != "off":
        import json
        import moe_anim_catalog as motion_catalog
        import moe_semantic_motion as semantic_motion
        action = motion_catalog.base_of_source(Path(args.src).name) or "unknown"
        motion_uses = semantic_motion.loop_uses(
            action, motion_catalog.Catalog("torso").states, motion_catalog.fold)
        # Explicit allowlist contains only reviewed sustained clips. The
        # source catalog supplies prop contact exclusions, not filename guesses.
        contact_sensitive = bool(motion_catalog.hand_prop_nodes().get(action))
        # Use actual mesh bind orientations: animation rest is frame zero and
        # may already be leaning, sitting or rotated.
        bind_axes = {}
        motion_mesh = Path(args.rest_mesh or _default_rest_mesh())
        for node_path, key, _kind, values in pdx_parse.walk(pdx_parse.parse(motion_mesh.read_bytes())):
            if "/skeleton/" in node_path and key == "tx":
                import numpy as np
                inv = np.asarray(values, float).reshape(4, 3).T[:, :3]
                bind_axes[node_path.rsplit("/", 1)[-1]] = semantic_motion.axes_from_inverse_bind(inv)
        out_q, motion_report = semantic_motion.apply(
            action, VF, float(vmeta["fps"][0]), mrest, out_q, bind_axes,
            looping=bool(motion_uses["loop_states"]),
            contact_sensitive=contact_sensitive)
        motion_report["source_state_uses"] = motion_uses
        print("Semantic motion: " + json.dumps(motion_report, ensure_ascii=False))

    # ---- MMD 回転付与骨（2026-10-12，用户「忌炎/女漂泊者/相里要腿部骨骼异常」）
    #
    # 足調整 / 肩調整 / 腕-限 / 飘带根 这类骨在 MMD 里没有自己的动作，由引擎按「付与源骨的
    # 旋转 × 付与率」驱动；导入后约束丢失，它们停在 rest，蒙在上面的大腿根 / 肩头 / 上臂
    # 皮肤被钉在父骨上（坐姿大腿根 p99 偏差：忌炎 6.7、女漂泊者 6.0、白芷 24.6，阿布手臂 30）。
    # 修法照 MMD 语义补通道：源骨相对 rest 的局部增量换到世界轴（MMD 骨没有局部轴，付与量
    # 就是世界轴下的旋转），乘付与率，再换回本骨 rest 系叠在 rest 上：
    #     q_a = q_a_rest · (W_a⁻¹ · (W_t · q_t_rest⁻¹ · q_t)^rate · W_a)
    # 只处理「本骨没通道、源骨有 q 通道」的行；行表由 --inherit-rot 给（PMX 的付与表，见
    # tools/lib/moe_pmx_inherit.py）。不给参数时这一段不执行，产物逐字节不变。
    if args.inherit_rot:
        import moe_pmx_inherit
        _applied = []
        for _a, _t, _rate in moe_pmx_inherit.inherit_rows(args.inherit_rot):
            if _a in out_q or _t not in out_q or _a not in mrest or _t not in mrest:
                continue
            _qa0, _qt0 = q_norm(mrest[_a]["q"]), q_norm(mrest[_t]["q"])
            _Wa, _Wt = mWQ[_a], mWQ[_t]
            _fr = []
            for _qt in out_q[_t]:
                _d = q_mul(q_conj(_qt0), q_norm(_qt))                 # 源骨局部增量（源骨 rest 系）
                _g = q_mul(q_mul(_Wt, _d), q_conj(_Wt))               # 换到世界轴
                if _g[3] < 0:
                    _g = tuple(-x for x in _g)
                _ang = 2.0 * math.acos(max(-1.0, min(1.0, _g[3])))
                _s = math.sin(_ang / 2.0)
                if _s < 1e-9:
                    _gp = (0.0, 0.0, 0.0, 1.0)
                else:
                    _h = _ang * _rate / 2.0
                    _gp = tuple(_g[i] / _s * math.sin(_h) for i in range(3)) + (math.cos(_h),)
                _l = q_mul(q_mul(q_conj(_Wa), _gp), _Wa)              # 换回本骨 rest 系
                _fr.append(q_norm(q_mul(_qa0, _l)))
            out_q[_a] = _fr
            _applied.append(f"{_a}←{_t}×{_rate:g}")
        print(f"回転付与: 补 {len(_applied)} 骨 " + " ".join(_applied))

    CAMERA_BONE = "camera_torso_look_at"
    if CAMERA_BONE in mrest and mpar.get(CAMERA_BONE):
        def frame_world(n, frame, cache):
            """(世界 P, 世界 Q)，用重定向后的 out_t/out_q，没通道的骨用 rest。"""
            if n in cache:
                return cache[n]
            p = mpar.get(n)
            lq = q_norm(out_q[n][frame]) if n in out_q else q_norm(mrest[n]["q"])
            lt = tuple(out_t[n][frame]) if n in out_t else tuple(mrest[n]["t"])
            if p and p in mrest:
                pP, pQ = frame_world(p, frame, cache)
                off = q_apply(pQ, lt)
                wP = tuple(pP[i] + off[i] for i in range(3))
                wQ = q_norm(q_mul(pQ, lq))
            else:
                wP, wQ = lt, lq
            cache[n] = (wP, wQ)
            return cache[n]

        anchor_bone = "bn_m_root" if "bn_m_root" in mrest else placement_target
        cache0 = {}
        a0, _ = frame_world(anchor_bone, 0, cache0)
        shift = tuple(a0[i] - mWP[anchor_bone][i] for i in range(3))
        target_p = tuple(mWP[CAMERA_BONE][i] + shift[i] for i in range(3))
        target_q = q_norm(mWQ[CAMERA_BONE])
        cam_parent = mpar[CAMERA_BONE]
        cam0_free, _ = frame_world(CAMERA_BONE, 0, cache0)
        cam_t, cam_q, drift = [], [], 0.0
        for f in range(VF):
            cache = cache0 if f == 0 else {}
            pP, pQ = frame_world(cam_parent, f, cache)
            inv = q_conj(pQ)
            cam_t.append(tuple(q_apply(inv, tuple(target_p[i] - pP[i]
                                                  for i in range(3)))))
            cam_q.append(q_norm(q_mul(inv, target_q)))
            # 钉之前这根骨在世界里晃了多少（给报告看）
            free_p, _ = frame_world(CAMERA_BONE, f, cache)
            drift = max(drift, math.dist(free_p, cam0_free))
        out_t[CAMERA_BONE] = cam_t
        out_q[CAMERA_BONE] = cam_q
        report.append(("camera pinned (free drift was)", CAMERA_BONE, "t", drift))

    other_placement = "bn_m_groove" if placement_target == "body_root" else "body_root"
    assert other_placement not in out_t, \
        f"摆位只能写 {placement_target}，不得同时驱动 {other_placement}"

    print(f"\n{'原版骨':<38}{'我们骨':<22}{'通道':<6}{'最大增量'}")
    print("-" * 82)
    for vn, mn, ch, amt in report:
        unit = "°" if ch == "q" else ("单位" if ch == "t" else "")
        print(f"{vn:<38}{mn:<22}{ch:<6}{amt:>8.2f}{unit}")

    # 组装：保持模板的骨序与 rest，只改 sa 与采样
    bones_out = []
    for b in mbones:
        n = b["name"]
        sa = ("t" if n in out_t else "") + ("q" if n in out_q else "")
        bones_out.append((n, sa, b["t"], b["q"], b["s"]))

    t_names = [n for n, sa, *_ in bones_out if "t" in sa]
    q_names = [n for n, sa, *_ in bones_out if "q" in sa]
    flat_t, flat_q = [], []
    for f in range(VF):
        for n in t_names:
            flat_t.extend(out_t[n][f])
        for n in q_names:
            flat_q.extend(out_q[n][f])

    dst = out_dir / args.out
    if dst.exists():
        # 只在**首次**备份，之后不再覆盖。否则反复迭代时第二次运行就会把
        # 上一次的输出当成"原文件"存进去，真正的原始模板被永久冲掉。
        # 备份目录跟 out_dir 走，不同角色的同名基名（如 moe_body_idle.anim）
        # 不会互相冲掉对方的备份。
        bak_dir = ROOT / "work" / "retarget_backups" / out_dir.name
        bak_dir.mkdir(parents=True, exist_ok=True)
        bak = bak_dir / f"{args.out}.before_retarget"
        if bak.exists():
            print(f"\n备份已存在，保持不动 → {bak}")
        else:
            bak.write_bytes(dst.read_bytes())
            print(f"\n已备份原文件 → {bak}")
    n = pdx_anim_write.write_anim(dst, vmeta["fps"][0], VF, bones_out,
                                  flat_t, flat_q)
    print(f"写出 {dst.name}  {n} 字节  fps={vmeta['fps'][0]:.3f} 帧={VF}")

    # 这里写出未解算基础动画；默认保存在 canonical work 烘焙目录。
    # finalize 从 canonical 刷新基础缓存，并物化已验证的物理版本到模型包。
    print(f"  驱动骨 {len(q_names)} 根 q + {len(t_names)} 根 t "
          f"(原先 5 根 q, 0 根 t)")

    # 回读校验
    m2, b2, s2 = load_anim(dst)
    F2, t2, q2 = unpack_samples(m2, b2, s2)
    print(f"\n回读: fps={m2['fps'][0]:.3f} 帧={F2} 骨={m2['j'][0]} "
          f"带通道 {len(q2)}q/{len(t2)}t")
    # 抽查：帧间是否真的在变（防止写出全同帧）
    for n in list(q2)[:4]:
        d = max(q_angle_deg(q_mul(q2[n][i], q_conj(q2[n][0])))
                for i in range(F2))
        print(f"  {n:<24} 相对首帧最大转动 {d:>6.2f}°")
    if t2:
        for n in list(t2)[:2]:
            d = max(math.dist(t2[n][i], t2[n][0]) for i in range(F2))
            print(f"  {n:<24} 相对首帧最大位移 {d:>6.2f}")


if __name__ == "__main__":
    main()
