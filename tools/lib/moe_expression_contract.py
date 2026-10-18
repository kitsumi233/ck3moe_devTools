"""二次元表情/动作语义合同：跨模型复用的接口层。

存在这个模块的理由是一条已验证的硬约束和一条设计选择。

硬约束：CK3 的 .anim 只有骨骼通道。
  解析全部 1995 个原版 .anim（0 失败），sa 通道组合只有
  tq/空/t/q/ts/s/tqs/qs；samples 节点属性名只有 t/q/s。全 game/gfx 里
  morph_animation / blend_shape_animation / animated_blend 零命中。
  .asset 的 blend_shape（268 条，male_bs_*_min/_max 成对）是 DNA 驱动的
  捏脸滑块，不是时间轴；additive_animation 40 项全是 .anim——连 eye_shut
  （闭眼）都是 .anim。所以 shapekey 效果虽好，但不能作为交付格式，
  必须烘焙成骨骼。

设计选择：配方用 ARKit 语义直接编写，不从原版 .anim 反推数值。
  源模型 Face 的 57 形态键是 ARKit 52 标准（逐肌肉语义命名），本身就是
  可读的语义单位。反推原版骨骼数值有两个问题：
    1. 轴向符号不可靠 —— bn_h_mouth_l_corner 在 emotion_happy 峰值是
       Y=-0.176，在 emotion_laugh_1 却是 Y=+0.561，照搬会做出反向表情。
    2. 原版是写实人脸幅度，二次元脸需要夸张，照搬会被拉平。
  所以原版 .anim 只用来参考时长与缓动（见 VANILLA_TIMING）。

四层架构（跨模型兼容性的落点）:
  配方层  EXPRESSIONS      语义强度曲线      完全复用
  接口层  CONTROL_BONES    ctrl_* 控制骨     固定命名，标准接口
  绑定层  SHAPEKEY_ALIASES 命名体系→语义槽   每模型一份，查表生成
  烘焙层  PROXY_BONES      代理骨规格        固定规格

新模型接入只需重建绑定层，配方一行不用改。这与 CK3 自己的做法同构
（61 骨固定 + 500 条共享动画）。

关键简化：代理骨不必沿用原版 61 骨命名。原版动画能白嫖的前提是骨架相同；
我们骨架不同、本来就得自己生成 .anim，所以代理骨可以少而精。
真正必须对齐的是 .asset 里注册的 animation state 名。
"""

# ---------------------------------------------------------------- 语义槽
# 表情的原子单位。命名沿用 ARKit（已是逐肌肉语义），因为源模型就是这套，
# 且它比 MMD 日文名更容易和 CK3 脸骨对应。
# 每个槽标注它需要哪些代理骨来承载烘焙结果。
# 一槽一骨，**不允许两个槽共用一根骨**。这是实测烘焙报告逼出来的修正:
#   最初让 eye_blink_l 与 eye_wide_l 共用 bn_m_fx_l_lid_up（想着「同一表情
#   内不会同时闭眼和睁大」）。但蒙皮权重是静态写死在 .mesh 里的，而两个
#   形态键的位移区域**重叠**（ウィンク 330 顶点 / びっくり左 254 顶点）：
#   刷权重时后写的覆盖前写的，于是重叠顶点的权重按「睁大」的主方向算，
#   却在 sadness 里被「闭眼」的平移量驱动 —— 那些顶点朝错误方向偏。
#   跨表情就会错，不是「同表情不并用」能规避的。
# 所以语义相反的槽必须各占一根骨。骨多几根没有代价（CK3 只关心 state 名），
# 权重错了却是静默的视觉 bug。
SEMANTIC_SLOTS = {
    # 眼 —— 上眼睑：闭眼与睁大方向相反，各自独立骨
    "eye_blink_l":      "bn_m_fx_l_lid_close",
    "eye_blink_r":      "bn_m_fx_r_lid_close",
    "eye_wide_l":       "bn_m_fx_l_lid_wide",
    "eye_wide_r":       "bn_m_fx_r_lid_wide",
    # 下眼睑：eye_squint 与 eye_narrow 必须分开，这是实测踩出来的坑:
    #   MMD 的「笑い」专指笑眼（眼轮匝肌收缩、下眼睑上推），只该用于正向表情。
    #   最初把两者合成一个 eye_squint 槽，结果 angry 配方解析到「笑い」，
    #   做出「怒容配笑眼」的矛盾表情。
    #   eye_squint = 笑眼（happy 用）；eye_narrow = 冷峻收窄（angry 用）。
    # 两者不仅语义不同、位移量也差 3 倍（笑い |T|=1.78 / 真面目 0.59），
    # 共用一根骨同样会互相污染权重，故各自独立。
    "eye_squint_l":     "bn_m_fx_l_lid_smile",
    "eye_squint_r":     "bn_m_fx_r_lid_smile",
    "eye_narrow_l":     "bn_m_fx_l_lid_narrow",
    "eye_narrow_r":     "bn_m_fx_r_lid_narrow",
    # 眉 —— 下压与外端上扬方向相反，各自独立骨
    "brow_inner_up":    "bn_m_fx_brow_inner",
    "brow_down_l":      "bn_m_fx_l_brow_down",
    "brow_down_r":      "bn_m_fx_r_brow_down",
    "brow_outer_up_l":  "bn_m_fx_l_brow_up",
    "brow_outer_up_r":  "bn_m_fx_r_brow_up",
    # 口 —— 口角上扬与下垂方向相反，各自独立骨
    "mouth_smile_l":    "bn_m_fx_l_mouth_up",
    "mouth_smile_r":    "bn_m_fx_r_mouth_up",
    "mouth_frown_l":    "bn_m_fx_l_mouth_down",
    "mouth_frown_r":    "bn_m_fx_r_mouth_down",
    "mouth_open":       "bn_m_fx_jaw",
    "mouth_pucker":     "bn_m_fx_mouth_upper",
    "mouth_press":      "bn_m_fx_mouth_lower",
    # 颊/鼻
    "cheek_squint_l":   "bn_m_fx_l_cheek",
    "cheek_squint_r":   "bn_m_fx_r_cheek",
    "nose_sneer":       "bn_m_fx_nose",

    # ---- 贴花槽 ----
    # 二次元表情的标志元素（腮红/怒气标记/泪滴/汗珠）。它们不是肌肉变形，
    # 而是把藏在头部内侧的小面片平移到脸表面。
    #
    # 判定依据是实测的「顶点数 vs 位移量」组合，极少顶点 + 极大位移 = 贴花:
    #   照れ  8 顶点 / 位移 10.14      涙  8 / 9.32
    #   怒   26 / 3.99                汗 28 / 3.78      ////  16 / 4.40
    # 对比真变形: 口角上げ 344 / 0.47、怒り左 73 / 1.25。
    # 8 个顶点位移 10 个单位不可能是腮部收缩。
    #
    # 反过来这对烘焙是好事：纯平移比变形好复现——一根权重 1.0 的代理骨
    # 平移同样距离即可完全等价，不像变形需要多骨加权近似。
    "decal_blush":      "bn_m_fx_decal_blush",
    "decal_anger":      "bn_m_fx_decal_anger",
    "decal_tear":       "bn_m_fx_decal_tear",
    "decal_sweat":      "bn_m_fx_decal_sweat",
}

# ---------------------------------------------------------------- 代理骨规格
# 烘焙目标。名字用 bn_m_fx_ 前缀（fx = face expression），
# 与既有 bn_m_* 骨区分，避免和源模型骨名冲突。
# parent 全挂 bn_m_head —— Face 网格现状就是 100% 权重在 bn_m_head 上
# （已验证：Face 1468 顶点、Mouth 134、Teeth 44、Tongue 104、EyeWhite 94、
# Brows 82、Lashes 360、EyeShadow 72 全部单骨绑定）。
# 权重不手工刷：由 shapekey 的位移区域自动推导——哪些顶点被某个 shapekey
# 推动，就是该代理骨管的区域。比手刷可靠。
#
# 这张表由 SEMANTIC_SLOTS 自动导出，不再手写，避免两处不同步。
PROXY_BONES = sorted(set(SEMANTIC_SLOTS.values()))
PROXY_PARENT = "bn_m_head"

# ---------------------------------------------------------------- 反向骨
# 用户 2026-08-18 报「表情有点淡」。实测量化（tools/verify/measure_expression_fidelity.py）
# 确认原因是**一根骨只能沿一个方向平移**：烘焙时
#   w = dot(d, T)/|T|²;  if w <= 0: continue   ← 逆向运动的顶点被焊死在 head 上
# 于是「有来有回」的形变只能重建一部分。实测重建率与补一根反向骨的收益：
#
#   槽              顶点  反向丢弃  现重建  +反向骨   残差角
#   mouth_pucker    321    196     40.9%   96.8%   140.4°  ← 整体朝反向，收益最大
#   eye_blink       330     94     75.8%   98.4%    14.3°
#   brow_inner_up    96     40     84.9%   99.4%     1.5°
#   eye_wide        254     63     84.1%   99.1%     1.4°
#   mouth_press     568    309     54.5%   85.7%    30.5°
#   mouth_frown     266     48     86.5%   94.3%    11.4°
#   mouth_open      687    173     68.8%   76.1%    11.2°
#
# 只给「反向丢弃顶点数显著」的槽加反向骨。眉毛下压/外扬与四个贴花槽
# 反向丢弃为 0（重建率已 99%+），加了纯属浪费骨位。
#
# 为什么加骨是安全的：实测源 blend 的 51148 顶点**全部占 0 格 fx 影响**，
# 占满 3 格的是 0 个 —— PDX 每顶点 4 格权重（head 占 1，fx 可用 3）完全够。
# （我曾记「860 顶点已占满 3 格」，那是从烘焙**产物** rig 量的，
#   拿来当「加骨前的约束」引用错了位置。）
#
# 注意 mouth_smile / eye_squint 不在此表：它们双骨也只到 33.8% / 49.2%，
# 因为位移是**扇形张开**（口角斜上、脸颊朝外、唇线弯曲），方向连续分布而非
# 二分，单根平移骨无论朝哪都只覆盖扇面一小片。那两个要靠旋转骨解决，
# 是独立的一步。
REVERSE_SLOTS = {
    "eye_blink_l":   "bn_m_fx_l_lid_close_rev",
    "eye_blink_r":   "bn_m_fx_r_lid_close_rev",
    "eye_wide_l":    "bn_m_fx_l_lid_wide_rev",
    "eye_wide_r":    "bn_m_fx_r_lid_wide_rev",
    "eye_narrow_l":  "bn_m_fx_l_lid_narrow_rev",
    "eye_narrow_r":  "bn_m_fx_r_lid_narrow_rev",
    "eye_squint_l":  "bn_m_fx_l_lid_smile_rev",
    "eye_squint_r":  "bn_m_fx_r_lid_smile_rev",
    "brow_inner_up": "bn_m_fx_brow_inner_rev",
    "mouth_open":    "bn_m_fx_jaw_rev",
    "mouth_pucker":  "bn_m_fx_mouth_upper_rev",
    "mouth_press":   "bn_m_fx_mouth_lower_rev",
    "mouth_frown_l": "bn_m_fx_l_mouth_down_rev",
    "mouth_frown_r": "bn_m_fx_r_mouth_down_rev",
    "mouth_smile_l": "bn_m_fx_l_mouth_up_rev",
    "mouth_smile_r": "bn_m_fx_r_mouth_up_rev",
}

REVERSE_BONES = sorted(REVERSE_SLOTS.values())

# ---------------------------------------------------------------- 双侧形态键
# MMD 的「笑い」「口角上げ」「真面目」是**单个双侧**形态键（一个键同时动左右），
# 但 SEMANTIC_SLOTS 给 _l / _r 各分了一根骨。旧烘焙用 `w / share` 把权重
# 除以 2 分给两骨 —— 后果是**幅度直接减半**，而这三个键恰好对应重建率
# 最低的三个槽（mouth_smile 24.7%、eye_squint 37.9%、eye_narrow 42.7%）。
#
# 除以 2 本来的用意是「避免两骨叠加到 2.0」，但那个担心只在两骨**同时**
# 被驱动时成立 —— 而它们本来就总是同时驱动（配方里 _l/_r 强度永远相等，
# 因为源键是双侧的，做不出单侧表情）。所以正确做法是：
# **双侧键只用一根骨、权重给满**，另一根骨不创建。
#
# 保留 _l/_r 两个语义槽名不变（配方层不用改），只在烘焙层合并到同一根骨。
# 这与 SEMANTIC_SLOTS 头部「不允许两个槽共用一根骨」的规则不冲突：
# 那条规则防的是**语义相反**的槽共用（闭眼 vs 睁大，权重会互相覆盖），
# 这里是同一个物理形变的左右两半，共用是它的本来面目。
#
# 2026-09-05 按 bake_report 实测扩展：洛茜/洁尔佩塔的 怒り / 上 / びっくり /
# 口角下げ 也是双侧单键（`shared_shapekeys` 里 share=2），此前没列在这里，
# 走了 `w/share` 老路：眉毛下压 w_max 只有 0.5，且白占一格 fx 影响——
# 再被 4 格截断按数值丢掉，`bn_m_fx_l_brow_down` 在洛茜上一个顶点都没绑，
# angry 的核心信号（眉压）在产物里基本不存在。芙洛伦的这几个键是分左右的
# （share=1），判据是 share>1，所以她不受影响。
BILATERAL_MERGE = {
    ("eye_squint_l", "eye_squint_r"):     "bn_m_fx_lid_smile",
    ("eye_narrow_l", "eye_narrow_r"):     "bn_m_fx_lid_narrow",
    ("mouth_smile_l", "mouth_smile_r"):   "bn_m_fx_mouth_up",
    ("brow_down_l", "brow_down_r"):       "bn_m_fx_brow_down",
    ("brow_outer_up_l", "brow_outer_up_r"): "bn_m_fx_brow_up",
    ("eye_wide_l", "eye_wide_r"):         "bn_m_fx_lid_wide",
    ("mouth_frown_l", "mouth_frown_r"):   "bn_m_fx_mouth_down",
}


def merged_bone_for(slot, bindings, share_count):
    """双侧键的槽 → 合并后的单骨名；非双侧返回 None。

    判据不是「槽名以 _l/_r 结尾」，而是**实测这一对槽解析到同一个形态键**
    （share_count > 1）—— 因为同一个语义槽在不同命名体系下可能是分离键
    （ARKit 的 mouthSmileLeft/Right 就是分开的，不该合并）。
    """
    for (a, b), bone in BILATERAL_MERGE.items():
        if slot not in (a, b):
            continue
        ka, kb = bindings.get(a), bindings.get(b)
        if ka and ka == kb and share_count.get(ka, 1) > 1:
            return bone
    return None

# ---------------------------------------------------------------- 视线
# 原版每条 head 动画都带 `bn_h_eye_l/r_rotate`（idle 最大 8.7°、happy 8.4°、
# conversation 17.3°），我们的眼球此前全程死盯正前方。
#
# 用**平移**不用旋转，这是有意的：三个模型的眼骨支点相对虹膜盘深浅不一
# （芙洛伦 2.6 单位、洛茜 0 / 4 单位、洁尔佩塔 2.7 / 6.5 单位），同一个转角
# 在三人身上会给出三种横移量，洛茜那根支点在盘面上的骨转起来是「盘子翻转」
# 而不是「眼珠转」。二次元虹膜本来就是贴在眼白前的平盘，MMD 的「両目」
# 转动在画面上的效果也就是虹膜横移。平移量按模型单位给，跨模型一致。
#
# 驱动目标不是固定骨名，由烘焙脚本按 mesh 实读：`bn_m_head` 的直接子骨里，
# 子树含 瞳(u77b3) / pupil / *_eye 且子树有蒙皮顶点的那些（洛茜 `bn_m_l_eye`
# 自己零蒙皮、顶点在子骨 瞳_l 上；洁尔佩塔左眼有两条链 `bn_m_l_eye` 与
# `bn_m_eyeLfJoint`，都要驱动）。见 [[mmd-control-vs-skin-bones]]。
#
# 配方里写 "eye_look": (x, y)，x 为向角色自己的左看为正（画面右）、范围 -1~1，
# y 向上为正。满幅对应 EYE_LOOK_UNITS 个模型单位。
EYE_LOOK_SLOT = "eye_look"
# 满幅位移（模型单位）。2026-09-05 第一版给 (0.45, 0.30)，配方里又只写
# 0.10~0.35，产物里眼球峰值位移 0.045 —— 虹膜盘宽约 2 单位，等于没动；
# 原版 reading 眼球转 13.5° 且停住 44% 时长。按三模型实测的余量定：
#   眼白单眼宽 3.7~4.4、虹膜宽 1.8~2.2 → 横向余量 ±0.7~1.0
#   眼白高 2.6~3.0、虹膜高 2.0~2.35   → 纵向余量 ±0.23~0.35（很紧）
# 横向满幅 0.9 配配方最大 0.6 → 0.54 单位；纵向满幅 0.5 配最大 0.6 → 0.3。
# 竖直方向超出眼白会露到皮肤上（虹膜是贴在眼白前的平盘，没有遮罩），
# 所以纵向不能再放。
EYE_LOOK_UNITS = (0.9, 0.5)
EYE_CHAIN_MARKERS = ("u77b3", "pupil", "_eye")   # 子树里出现即视为眼球链

# ---------------------------------------------------------------- 控制骨
# 制作期的标准接口。一个表情一根控制骨，location.x 在 0~1 表示强度。
# 做动画时只 K 这一条通道，shapekey 组合由 driver 自动跟随，再一键烘焙。
# 这些骨不蒙皮、不导出——Blender 的 driver 不会进 .mesh/.anim，
# 所以它们只在制作期存在，是纯粹的编辑期便利。
CONTROL_BONES = ["ctrl_happy", "ctrl_angry", "ctrl_shock", "ctrl_sadness"]
CONTROL_PARENT = "bn_m_head"

# ---------------------------------------------------------------- 绑定层
# 命名体系 → 语义槽。认三套主流命名，这是让方案通用的关键件。
# 值可以是单个键名，或候选列表（取第一个在模型里存在的）。
SHAPEKEY_ALIASES = {
    # 源模型用的就是这套（Face 57 键）
    "arkit": {
        "eye_blink_l":     "eyeBlinkLeft",
        "eye_blink_r":     "eyeBlinkRight",
        "eye_squint_l":    "eyeSquintLeft",
        "eye_squint_r":    "eyeSquintRight",
        # ARKit 不区分笑眼与冷峻收窄，两者都落到 eyeSquint；
        # 这套命名下 angry 不会误取笑眼，所以可以共用。
        "eye_narrow_l":    "eyeSquintLeft",
        "eye_narrow_r":    "eyeSquintRight",
        "eye_wide_l":      "eyeWideLeft",
        "eye_wide_r":      "eyeWideRight",
        "brow_inner_up":   "browInnerUp",
        "brow_down_l":     "browDownLeft",
        "brow_down_r":     "browDownRight",
        "brow_outer_up_l": "browOuterUpLeft",
        "brow_outer_up_r": "browOuterUpRight",
        "mouth_smile_l":   "mouthSmileLeft",
        "mouth_smile_r":   "mouthSmileRight",
        "mouth_frown_l":   "mouthFrownLeft",
        "mouth_frown_r":   "mouthFrownRight",
        "mouth_open":      "jawOpen",
        "mouth_pucker":    "mouthPucker",
        "mouth_press":     ["mouthPressLeft", "mouthShrugLower"],
        "cheek_squint_l":  "cheekSquintLeft",
        "cheek_squint_r":  "cheekSquintRight",
        "nose_sneer":      ["noseSneerLeft", "noseSneerRight"],
    },
    # MMD 日文标准。候选列表按「左右分离键优先，退回不分左右的键」排序——
    # 当前管线的 moe_head 就是这套（实测 62 键，从 Florence PMX 构建）。
    #
    # 注意两个容易踩的点:
    #   1. 数字用全角（あ２ / ウィンク２），半角 "あ2" 匹配不上，两种都列。
    #   2. 多个槽可能解析到同一个键（口角上げ 不分左右，mouth_smile_l/r 都指向它）。
    #      烘焙时必须按键去重取 max，不能累加——否则对称表情会重复叠加到 2.0。
    "mmd_jp": {
        "eye_blink_l":     ["ウィンク", "まばたき"],
        "eye_blink_r":     ["ウィンク右", "まばたき"],
        # MMD 的 笑い 是「笑眼」（眼部 morph），正好对应真笑时的眼轮匝肌收缩。
        # 不要用 なごみ/はちゅ目/ぐるぐる 当渐变收眼——实测它们动 1000+ 顶点、
        # 位移 5~6.7，是整体换眼型的风格化 morph，中间强度会出现畸形过渡；
        # 笑い 只动 660 点、位移 1.78，是正常的渐变变形。
        "eye_squint_l":    ["笑い"],
        "eye_squint_r":    ["笑い"],
        # 冷峻收窄（angry 用）。芙洛伦的 真面目 实测 406 点 / 0.59 位移，是眼部
        # 变形；但**洛茜/洁尔佩塔的 真面目 只动 30/48 个顶点**（她们的眉毛
        # 网格就这么多点）—— 同名键在不同模型上是眉毛键，angry 的「眼神收窄」
        # 在她们身上等于没有。ｷﾘｯ（洛茜 392 点 0.89 / 洁尔佩塔 440 点 0.55）与
        # じと目 才是眼部键，优先取；芙洛伦没有这两个键，仍落到 真面目。
        "eye_narrow_l":    ["ｷﾘｯ", "キリッ", "じと目", "真面目"],
        "eye_narrow_r":    ["ｷﾘｯ", "キリッ", "じと目", "真面目"],
        "eye_wide_l":      ["びっくり左", "びっくり"],
        "eye_wide_r":      ["びっくり右", "びっくり"],
        "brow_inner_up":   ["困る", "上"],
        "brow_down_l":     ["怒り左", "怒り", "下左", "下"],
        "brow_down_r":     ["怒り右", "怒り", "下右", "下"],
        "brow_outer_up_l": ["上左", "上"],
        "brow_outer_up_r": ["上右", "上"],
        "mouth_smile_l":   ["口角上げ", "にやり", "笑い"],
        "mouth_smile_r":   ["口角上げ", "にやり２", "にやり2", "にやり"],
        "mouth_frown_l":   ["口角下げ左", "口角下げ"],
        "mouth_frown_r":   ["口角下げ右", "口角下げ"],
        # 张嘴优先用 あ３ 而不是 あ。实测（芙洛伦 62 键）三者的幅度差一个量级：
        #   あ    最大位移 0.818  590 动点  嘴区平均 ΔZ -0.081
        #   あ２  最大位移 1.972  699 动点  嘴区平均 ΔZ -0.457
        #   あ３  最大位移 2.095  687 动点  嘴区平均 ΔZ -0.503
        # 用 あ 烘出来的 shock 在游戏里「嘴看着没张开」——脸高 60 单位，
        # 0.8 的位移约 1.3%，肉眼分辨不出。あ３ 与 あ 的动点重叠 98%、
        # 方向余弦 0.80，是同一处变形的放大版，可以安全替换。
        # 注意：不要用单个 T 向量判断方向。T 取的是**位移最大的那一个顶点**，
        # あ 的 T 是 (0,-0.53,+0.62)（Z 正 = 向上），看着像「闭嘴」，
        # 但全场 60% 动点的 ΔZ 是负的 —— T 不代表整个位移场。
        "mouth_open":      ["あ３", "あ3", "あ２", "あ2", "あ"],
        "mouth_pucker":    ["う", "お"],
        "mouth_press":     ["い", "ん", "え"],
        # 颊/鼻在这套 62 键里没有真变形键，故意留空（缺失是合法结果）。
        # 不要把「照れ」填进 cheek_squint、「怒」填进 nose_sneer——
        # 实测证明这两个是贴花而非变形（见下方 DECAL_ALIASES 的判定依据）,
        # 当变形用会在脸上弹出腮红/青筋符号，不是肌肉收缩。
        "cheek_squint_l":  [],
        "cheek_squint_r":  [],
        "nose_sneer":      ["むっ"],
    },
    # VRM / VRoid
    "vrm": {
        "eye_blink_l":     ["Blink_L", "Blink"],
        "eye_blink_r":     ["Blink_R", "Blink"],
        "eye_squint_l":    "Joy",
        "eye_squint_r":    "Joy",
        "eye_wide_l":      "Surprised",
        "eye_wide_r":      "Surprised",
        "brow_inner_up":   "Sorrow",
        "brow_down_l":     "Angry",
        "brow_down_r":     "Angry",
        "brow_outer_up_l": "Fun",
        "brow_outer_up_r": "Fun",
        "mouth_smile_l":   ["Joy", "Fun"],
        "mouth_smile_r":   ["Joy", "Fun"],
        "mouth_frown_l":   "Sorrow",
        "mouth_frown_r":   "Sorrow",
        "mouth_open":      ["A", "A_"],
        "mouth_pucker":    ["U", "O"],
        "mouth_press":     "I",
        "cheek_squint_l":  "Joy",
        "cheek_squint_r":  "Joy",
        "nose_sneer":      "Angry",
    },
}


# 贴花键的命名表。和变形键分开，因为判定方式和用途都不同:
# 变形键靠语义映射，贴花键靠「极少顶点 + 极大位移」的几何特征识别。
DECAL_ALIASES = {
    "mmd_jp": {
        "decal_blush": ["照れ"],
        "decal_anger": ["怒", "むっ"],
        "decal_tear":  ["涙"],
        "decal_sweat": ["汗"],
    },
    # ARKit 是纯肌肉标准，没有贴花概念。留空即缺失，配方会自动跳过。
    "arkit": {},
    "vrm": {},
}

# 贴花判定阈值。实测分界很干净:
#   贴花: 照れ 8 顶点/10.14、涙 8/9.32、怒 26/3.99、汗 28/3.78、//// 16/4.40
#   变形: 口角上げ 344/0.47、怒り左 73/1.25、笑い 660/1.78、まばたき 526/2.06
# 顶点数 < 40 且 位移 > 3.0 → 贴花。两个条件都要满足，避免误判小范围变形。
DECAL_MAX_VERTS = 40
DECAL_MIN_DISP = 3.0


# ---------------------------------------------------------------- 幅度归一
# 同一个语义槽在不同模型上的形态键幅度差 3~6 倍（模型单位，三模型身高一致）:
#   brow_down     芙洛伦 1.25   洛茜 0.43   洁尔佩塔 0.46
#   mouth_smile   芙洛伦 0.47   洛茜 0.35   洁尔佩塔 0.19
#   mouth_open    芙洛伦 2.10   洛茜 0.99   洁尔佩塔 1.28
# 配方按 0~1 强度写，等于默认「每个模型作者的 1.0 就是我们要的满幅」——
# 洛茜脸高 17 单位，眉毛压 0.43（2.5%）肉眼看不出。原版 storytelling 下颌转
# 12.8°、下巴落 ~1.1 单位；我们 talk 峰值 0.45×0.99 = 0.44。
# 所以给每个槽一个**目标满幅位移**（模型单位），烘焙时 gain = 目标/|T|，
# 只放大不缩小、上限 1.8（骨平移是形态键的线性外推，1.8 倍以内眉/嘴不撕）。
# 贴花、眨眼、笑眼不在表里：贴花是整片平移放大会跑位，眨眼 1.0 就是全闭。
SLOT_GAIN_TARGET = {
    "brow_down_l": 0.7, "brow_down_r": 0.7,
    "brow_inner_up": 0.7,
    "brow_outer_up_l": 0.7, "brow_outer_up_r": 0.7,
    "eye_wide_l": 0.6, "eye_wide_r": 0.6,
    "eye_narrow_l": 0.6, "eye_narrow_r": 0.6,
    "mouth_smile_l": 0.5, "mouth_smile_r": 0.5,
    "mouth_frown_l": 0.6, "mouth_frown_r": 0.6,
    "mouth_open": 1.5,
    "mouth_press": 0.5,
}
SLOT_GAIN_MAX = 1.8

# ---------------------------------------------------------------- 缺槽替代
# 模型没有某个语义槽的形态键时，把配方里该槽的强度按系数折算到另一个**它有的**槽上，
# 而不是直接丢掉那一层。替代在配方采样层做（substitute_missing），不加骨、不动任何
# 代理骨的 rest 位置 —— 所以躯干动画集不用重做，只重烘表情 + 嫁接 head。
#
# eye_narrow → eye_blink × 0.30（2026-09-06，少前 2 批量 20 人全缺 ｷﾘｯ/じと目/真面目）：
#   收窄键的形状就是「上眼睑下压、下眼睑几乎不动」，与眨眼（ウィンク２/まばたき）同形、
#   幅度约 30%（芙洛伦 真面目 上睑 −0.33 / まばたき −1.10；洛茜 ｷﾘｯ −0.48 / −1.09）。
#   angry 峰值 0.55 折成 0.17 的眨眼量，与芙洛伦实际收窄同量级。
#   ウィンク 在所有模型上都带下睑上推 0.2~0.3（是笑眼的左半边），0.3 倍后下睑只抬 0.07，
#   顶不出笑意，不构成「怒容配笑眼」；眨眼绑定顺序不改（改了代理骨重心会挪，躯干集要重跑）。
#   なごみ/はぅ 位移全在深度轴（>< 型贴脸闭眼），不能当收窄用。
# 同一帧目标槽已有值时取较大者，不相加（语义互斥的槽相加会抵消）。
SLOT_FALLBACK = {
    "eye_narrow_l": ("eye_blink_l", 0.30),
    "eye_narrow_r": ("eye_blink_r", 0.30),
}


def substitute_missing(curve, missing_slots):
    """对 sample_expression 的输出做缺槽替代。返回 (新曲线, {缺槽: 目标槽})。

    只替代**确实缺失**的槽；模型有 eye_narrow 键时原样返回，产物与从前逐位一致。
    """
    missing = set(missing_slots)
    todo = {s: SLOT_FALLBACK[s] for s in missing
            if s in SLOT_FALLBACK and SLOT_FALLBACK[s][0] not in missing}
    if not todo:
        return curve, {}
    out, used = [], set()
    for vals in curve:
        nv = dict(vals)
        for src, (dst, k) in todo.items():
            v = nv.pop(src, None)
            if v is None:
                continue
            used.add(src)                     # 只报告这条配方里真出现过的缺槽
            cand = round(v * k, 5)
            if cand > 1e-6 and cand > nv.get(dst, 0.0):
                nv[dst] = cand
        out.append(nv)
    return out, {s: todo[s][0] for s in sorted(used)}


def slot_gain(slot, t_len):
    """该槽的烘焙增益。|T| 已达目标返回 1.0。"""
    tgt = SLOT_GAIN_TARGET.get(slot)
    if not tgt or t_len <= 1e-9:
        return 1.0
    return max(1.0, min(SLOT_GAIN_MAX, tgt / t_len))


def classify_shapekey(moved_verts, max_disp):
    """按几何特征判断形态键是贴花还是变形。

    这个判定必须做，因为贴花和变形的烘焙方式不同:
      贴花是纯平移（整片小面片一起动），一根权重 1.0 的骨即可完全等价复现；
      变形是区域性形变，需要多骨加权近似。
    误把贴花当变形，会在脸上弹出腮红/青筋符号而不是肌肉收缩。
    """
    if moved_verts <= DECAL_MAX_VERTS and max_disp >= DECAL_MIN_DISP:
        return "decal"
    return "deform"


def detect_naming_scheme(shapekey_names):
    """给一批形态键名，判断它属于哪套命名体系。返回 (scheme, 命中数)。

    做法是数每套体系有多少键能在模型里找到，取命中最多的。
    比按单个特征键判断稳——单键可能恰好缺失。
    """
    names = set(shapekey_names)
    best, best_hits = None, 0
    for scheme, table in SHAPEKEY_ALIASES.items():
        hits = 0
        for cands in table.values():
            cands = [cands] if isinstance(cands, str) else cands
            if any(c in names for c in cands):
                hits += 1
        if hits > best_hits:
            best, best_hits = scheme, hits
    return best, best_hits


def resolve_bindings(shapekey_names, scheme=None):
    """语义槽 → 该模型实际的形态键名。返回 (bindings, missing)。

    scheme 为 None 时自动探测。找不到对应键的槽进 missing，
    调用方可据此判断该模型能覆盖哪些表情。
    """
    names = set(shapekey_names)
    if scheme is None:
        scheme, _ = detect_naming_scheme(shapekey_names)
    if scheme is None:
        return {}, list(SEMANTIC_SLOTS)
    # 变形键与贴花键合起来查，两张表的槽不重叠
    table = dict(SHAPEKEY_ALIASES[scheme])
    table.update(DECAL_ALIASES.get(scheme, {}))
    bindings, missing = {}, []
    for slot in SEMANTIC_SLOTS:
        cands = table.get(slot)
        if not cands:               # None 或空列表都算缺失（空列表是故意留空）
            missing.append(slot)
            continue
        cands = [cands] if isinstance(cands, str) else cands
        hit = next((c for c in cands if c in names), None)
        if hit:
            bindings[slot] = hit
        else:
            missing.append(slot)
    return bindings, missing


# ---------------------------------------------------------------- 配方层
# 语义强度曲线。(帧位置比例 0~1, {语义槽: 强度 0~1}) 的关键帧列表。
# 帧位置用比例而非绝对帧号，这样同一配方能重采样到任意时长。
#
# 幅度按二次元惯例给：比写实人脸夸张，眼部变形占比大。
# 首尾都回到接近中性，让表情能和 idle 平滑衔接（CK3 的 *_entry 过渡机制）。
EXPRESSIONS = {
    # 笑：嘴角上扬 + 眼睛微眯（真笑的关键是眼轮匝肌，只动嘴会像假笑）
    #     + 眉外侧上扬。二次元的笑常配轻微张口。
    "happy": [
        (0.00, {}),
        (0.25, {"mouth_smile_l": 0.75, "mouth_smile_r": 0.75,
                "eye_squint_l": 0.30, "eye_squint_r": 0.30,
                "brow_outer_up_l": 0.35, "brow_outer_up_r": 0.35,
                "cheek_squint_l": 0.40, "cheek_squint_r": 0.40,
                "mouth_open": 0.15, "decal_blush": 0.35}),
        (0.55, {"mouth_smile_l": 0.95, "mouth_smile_r": 0.95,
                "eye_squint_l": 0.45, "eye_squint_r": 0.45,
                "brow_outer_up_l": 0.45, "brow_outer_up_r": 0.45,
                "cheek_squint_l": 0.55, "cheek_squint_r": 0.55,
                "mouth_open": 0.22, "decal_blush": 0.55}),
        (0.80, {"mouth_smile_l": 0.80, "mouth_smile_r": 0.80,
                "eye_squint_l": 0.35, "eye_squint_r": 0.35,
                "brow_outer_up_l": 0.35, "brow_outer_up_r": 0.35,
                "cheek_squint_l": 0.42, "cheek_squint_r": 0.42,
                "mouth_open": 0.12, "decal_blush": 0.40}),
        (1.00, {}),
    ],
    # 怒：眉压低内聚是核心信号 + 眼神冷峻收窄 + 嘴角下拉。
    #     不加张口，闭嘴的怒更有压迫感。
    #     用 eye_narrow 而非 eye_squint —— 后者在 MMD 下解析到「笑い」(笑眼)，
    #     会做出「怒容配笑眼」的矛盾表情。这是实测踩出来的坑。
    #     配 decal_anger（怒气标记）：二次元表达怒的标志元素，比肌肉变形更直观。
    "angry": [
        (0.00, {}),
        (0.20, {"brow_down_l": 0.80, "brow_down_r": 0.80,
                "eye_narrow_l": 0.45, "eye_narrow_r": 0.45,
                "mouth_frown_l": 0.40, "mouth_frown_r": 0.40,
                "mouth_press": 0.30, "decal_anger": 0.60}),
        (0.50, {"brow_down_l": 1.00, "brow_down_r": 1.00,
                "eye_narrow_l": 0.55, "eye_narrow_r": 0.55,
                "mouth_frown_l": 0.55, "mouth_frown_r": 0.55,
                "mouth_press": 0.45, "decal_anger": 1.00}),
        (0.78, {"brow_down_l": 0.88, "brow_down_r": 0.88,
                "eye_narrow_l": 0.48, "eye_narrow_r": 0.48,
                "mouth_frown_l": 0.45, "mouth_frown_r": 0.45,
                "mouth_press": 0.35, "decal_anger": 0.80}),
        (1.00, {}),
    ],
    # 惊：起手要快（惊讶的时间特征就是突变），所以第二个关键帧压到 0.12。
    #     瞪眼 + 眉高抬 + 张口。二次元惊讶幅度可以给满。
    "shock": [
        (0.00, {}),
        (0.12, {"eye_wide_l": 0.90, "eye_wide_r": 0.90,
                "brow_inner_up": 0.85, "brow_outer_up_l": 0.75,
                "brow_outer_up_r": 0.75, "mouth_open": 0.65}),
        (0.35, {"eye_wide_l": 1.00, "eye_wide_r": 1.00,
                "brow_inner_up": 0.95, "brow_outer_up_l": 0.85,
                "brow_outer_up_r": 0.85, "mouth_open": 0.75}),
        (0.70, {"eye_wide_l": 0.70, "eye_wide_r": 0.70,
                "brow_inner_up": 0.65, "brow_outer_up_l": 0.55,
                "brow_outer_up_r": 0.55, "mouth_open": 0.40}),
        (1.00, {}),
    ],
    # 悲：眉内侧上抬是核心（AU1，这个不做就不像悲伤而像困惑）
    #     + 上眼睑下垂 + 嘴角下拉。节奏比其余三个慢，起落都拉长。
    #     配 decal_tear（泪滴）：比眼部变形更能读出悲伤，且晚于表情出现更自然。
    "sadness": [
        (0.00, {}),
        (0.30, {"brow_inner_up": 0.75, "mouth_frown_l": 0.55,
                "mouth_frown_r": 0.55, "eye_blink_l": 0.30,
                "eye_blink_r": 0.30, "mouth_press": 0.25}),
        (0.60, {"brow_inner_up": 0.90, "mouth_frown_l": 0.70,
                "mouth_frown_r": 0.70, "eye_blink_l": 0.38,
                "eye_blink_r": 0.38, "mouth_press": 0.35,
                "decal_tear": 0.70}),
        (0.85, {"brow_inner_up": 0.70, "mouth_frown_l": 0.52,
                "mouth_frown_r": 0.52, "eye_blink_l": 0.28,
                "eye_blink_r": 0.28, "mouth_press": 0.22,
                "decal_tear": 1.00}),
        (1.00, {}),
    ],
}


def _emotion_arc(pose, onset=0.24):
    """情绪源保留中性首尾；持续、入场、退出的包络由嫁接器按 state 决定。"""
    def scaled(gain):
        return {s: (tuple(round(v * gain, 5) for v in value)
                    if s == EYE_LOOK_SLOT else round(value * gain, 5))
                for s, value in pose.items()}
    return [(0.0, {}), (onset, scaled(0.82)), (0.5, dict(pose)),
            (0.78, scaled(0.90)), (1.0, {})]


# 只组合现有基础表情已经烘焙的槽，不增加代理骨或使用模型隐藏开关。
# 左右分离的模型保留不对称；双侧同键的模型由既有绑定取 max 合并。
# happy 是明显开心，smile 是闭嘴微笑；两者与张嘴、笑眼更强的大笑分开。
EXPRESSIONS.update({
    "smile": _emotion_arc({
        "mouth_smile_l": 0.62, "mouth_smile_r": 0.62,
        "eye_squint_l": 0.18, "eye_squint_r": 0.18,
        "brow_outer_up_l": 0.12, "brow_outer_up_r": 0.12,
        "cheek_squint_l": 0.18, "cheek_squint_r": 0.18,
        "eye_look": (0.08, 0.10),
    }),
    "laugh": _emotion_arc({
        "mouth_smile_l": 0.95, "mouth_smile_r": 0.95,
        "eye_squint_l": 0.72, "eye_squint_r": 0.72,
        "cheek_squint_l": 0.65, "cheek_squint_r": 0.65,
        "brow_outer_up_l": 0.38, "brow_outer_up_r": 0.38,
        "mouth_open": 0.72, "decal_blush": 0.45,
        "eye_look": (0.0, 0.15),
    }, onset=0.16),
    "flirty": _emotion_arc({
        "mouth_smile_l": 0.70, "mouth_smile_r": 0.48,
        "eye_blink_l": 0.18, "eye_blink_r": 0.38,
        "brow_outer_up_l": 0.30, "brow_outer_up_r": 0.12,
        "decal_blush": 0.65, "eye_look": (0.24, -0.12),
    }, onset=0.30),
    "worry": _emotion_arc({
        "brow_inner_up": 0.65, "brow_down_l": 0.20, "brow_down_r": 0.20,
        "eye_wide_l": 0.30, "eye_wide_r": 0.30,
        "mouth_frown_l": 0.36, "mouth_frown_r": 0.36,
        "mouth_press": 0.28, "eye_look": (0.16, -0.10),
    }),
    "shame": _emotion_arc({
        "brow_inner_up": 0.46, "eye_blink_l": 0.46, "eye_blink_r": 0.46,
        "mouth_frown_l": 0.22, "mouth_frown_r": 0.22,
        "mouth_press": 0.40, "decal_blush": 0.72,
        "eye_look": (0.18, -0.32),
    }, onset=0.32),
    "disgust": _emotion_arc({
        "brow_down_l": 0.68, "brow_down_r": 0.36,
        "eye_narrow_l": 0.50, "eye_narrow_r": 0.30,
        "mouth_frown_l": 0.56, "mouth_frown_r": 0.28,
        "mouth_press": 0.55, "eye_look": (-0.20, -0.10),
    }, onset=0.20),
    "boredom": _emotion_arc({
        "eye_blink_l": 0.43, "eye_blink_r": 0.43,
        "brow_down_l": 0.12, "brow_down_r": 0.12,
        "mouth_frown_l": 0.16, "mouth_frown_r": 0.16,
        "mouth_press": 0.12, "eye_look": (-0.22, -0.12),
    }, onset=0.32),
    "thinking": _emotion_arc({
        "brow_down_l": 0.30, "brow_outer_up_r": 0.28,
        "eye_blink_l": 0.14, "eye_blink_r": 0.14,
        "mouth_press": 0.38, "eye_look": (-0.20, 0.10),
    }, onset=0.30),
    "fear": _emotion_arc({
        "brow_inner_up": 0.88, "brow_down_l": 0.12, "brow_down_r": 0.12,
        "eye_wide_l": 0.72, "eye_wide_r": 0.72,
        "mouth_frown_l": 0.46, "mouth_frown_r": 0.46,
        "mouth_open": 0.28, "eye_look": (0.12, 0.0),
    }, onset=0.14),
})

# 嫁接元数据。hold_sample 是源中稳定表情的归一化采样位置，不能以
# 「所有骨平移最大」推断：贴花的大位移会盖过眉眼嘴的实际表演峰值。
# speech_gain 控制叠加张合幅度。眨眼始终保留完整闭合；嫁接器通过
# 同槽 max 以及眨眼时压低其它眼睑表情来避免叠加，不能靠半闭眼消除冲突。
EXPRESSION_STYLE = {
    "neutral": {"hold_sample": 0.5, "speech_gain": 1.0,
                "life_gaze_gain": 1.0, "life_blink_gain": 1.0},
}
for _name, _hold, _speech, _gaze in (
    ("happy", .55, .65, .65),
    ("angry", .50, .75, .45),
    ("shock", .35, .40, .25),
    ("sadness", .60, .50, .45),
    ("smile", .50, .85, .80),
    ("laugh", .50, .35, .45),
    ("flirty", .50, .55, .50),
    ("worry", .50, .65, .60),
    ("shame", .50, .45, .30),
    ("disgust", .50, .55, .45),
    ("boredom", .50, .55, .45),
    ("thinking", .50, .50, .35),
    ("fear", .50, .50, .40),
):
    EXPRESSION_STYLE[_name] = {
        "hold_sample": _hold, "speech_gain": _speech,
        "life_gaze_gain": _gaze, "life_blink_gain": 1.0,
    }

# 原版对应动画的时长，用来对齐节奏。取自实测：
#   female_head_emotion_happy   fps=15.01  565 帧（含循环的长表演）
#   female_head_emotion_angry   fps=15.05  150 帧
#   female_head_emotion_shock   fps=15.05  150 帧
#   female_head_emotion_sadness fps=15.05  150 帧
# happy 的 565 帧是长表演，直接照搬太长；统一按 10 秒基准做，
# 需要更长的循环由 CK3 的 looping 机制处理。

# ---- 说话（2026-09-05）----
# 原版 21 条 conversation 变体 + storytelling / messenger / chancellor 都带嘴部
# 开合节奏（throneRoom_conversation1_1 里 bn_h_mouth_l_corner 301 帧过均值 24 次，
# 约 2.4 次/秒）。我们此前这些动画全部落到中性脸，王座对话全程闭嘴。
#
# 配方由固定种子的伪随机生成（可复现）：mouth_open 在 0.05~0.45 之间以约
# 3 Hz 的不规则节奏往复，叠低幅 mouth_press / mouth_smile；首尾都收到 0
# 让循环接缝干净。时长 20 秒：嫁接时按**绝对时间平铺**而不是拉伸到身体
# 时长（graft 的 TILE_EXPRS），所以配方本身要够长以免高频重复。
def _talk_keys(seed=20260905, seconds=20.0, rate_hz=3.0):
    import random
    rng = random.Random(seed)
    keys = [(0.0, {})]
    t = 0.0
    while True:
        # 一次开合 = 张开峰 + 闭合谷；间隔按 rate_hz 抖动 ±35%
        gap = (1.0 / rate_hz) * rng.uniform(0.65, 1.35)
        t_peak = t + gap * 0.45
        t_close = t + gap
        if t_close >= seconds - 0.25:
            break
        # 峰值 0.25~0.80：第一版 0.12~0.45 配洛茜 あ２ 0.99 只落 0.44 单位，
        # 原版 storytelling 下巴落 ~1.1。视线不在这里写 —— 眨眼/扫视统一由
        # `life` 层在嫁接时叠加（否则两处各写一份会相加超出眼白）。
        amp = rng.uniform(0.25, 0.80)
        press = rng.uniform(0.0, 0.18)
        smile = rng.uniform(0.0, 0.15)
        peak = {"mouth_open": round(amp, 3), "mouth_press": round(press, 3),
                "mouth_smile_l": round(smile, 3), "mouth_smile_r": round(smile, 3)}
        valley = {"mouth_open": round(amp * rng.uniform(0.0, 0.25), 3)}
        keys.append((round(t_peak / seconds, 5), peak))
        keys.append((round(t_close / seconds, 5), valley))
        t = t_close
    keys.append((1.0, {}))
    return keys


EXPRESSIONS["talk"] = _talk_keys()


# ---- 生命层：眨眼 + 扫视（2026-09-05）----
# 原版 357 条 head 动画里 293 条带眼球转动（中位 8.6°）、288 条带眨眼
# （上眼睑 35~50°），idle 8 秒眨 2 次、扫视 ±8.5°。我们的中性脸源零眨眼零扫视，
# 169 条中性头（含 reading / idle / 王座）全程死盯前方 —— 用户第一眼看到的
# 「没有眼球动画」就是这一层缺失。
#
# 这是**独立源**，不进任何一个表情配方：嫁接时按绝对时间平铺叠在每条 head
# 动画上（graft 的 LIFE_EXPR）。理由与 talk 相同 —— 四个基础表情源只有
# 2~3.5 秒、要被拉伸到 10 秒身体时长，眨眼写进去会被拉成 0.4 秒的慢闭眼。
#
# 扫视是「快跳 + 停住」不是平滑漂移：跳 0.08 秒、停 0.6~2.5 秒。横向 ±0.55、
# 纵向 ±0.2（配方单位），与情绪偏置相加后仍在眼白余量内（见 EYE_LOOK_UNITS）。
def _life_keys(seed=20260905, seconds=20.0):
    import random
    rng = random.Random(seed)
    ev = []                                   # (t, {slot: v})
    # 眨眼：间隔 2.5~5.5 秒，闭 0.10 秒、开 0.14 秒；10% 概率连眨
    t = rng.uniform(0.8, 2.0)
    while t + 0.3 < seconds - 0.3:
        for _ in range(2 if rng.random() < 0.10 else 1):
            ev.append((t, {"eye_blink_l": 0.0, "eye_blink_r": 0.0}))
            ev.append((t + 0.10, {"eye_blink_l": 1.0, "eye_blink_r": 1.0}))
            ev.append((t + 0.24, {"eye_blink_l": 0.0, "eye_blink_r": 0.0}))
            t += 0.34
        t += rng.uniform(2.5, 5.5)
    # 扫视：目标 40% 回正中附近，其余随机；跳 0.08 秒后停住
    t = rng.uniform(0.3, 1.0)
    look = (0.0, 0.0)
    ev.append((0.0, {"eye_look": look}))
    while t + 0.2 < seconds - 0.3:
        if rng.random() < 0.40:
            nxt = (rng.uniform(-0.12, 0.12), rng.uniform(-0.08, 0.08))
        else:
            nxt = (rng.uniform(-0.55, 0.55), rng.uniform(-0.20, 0.20))
        ev.append((t, {"eye_look": look}))
        ev.append((t + 0.08, {"eye_look": nxt}))
        look = nxt
        t += rng.uniform(0.6, 2.5)
    ev.append((seconds - 0.25, {"eye_look": look}))
    ev.append((seconds, {"eye_look": (0.0, 0.0),
                         "eye_blink_l": 0.0, "eye_blink_r": 0.0}))
    # 同一时刻可能既有眨眼键又有视线键：合并成一帧。采样器把未写的槽当 0，
    # 而视线在两个扫视键之间要**保持**上一目标，所以把当前 look 显式写进每个
    # 只含眨眼的键里。
    merged = {}
    for tt, d in ev:
        merged.setdefault(round(tt / seconds, 5), {}).update(d)
    cur_look = (0.0, 0.0)
    out = []
    for frac, d in sorted(merged.items()):
        if "eye_look" in d:
            cur_look = d["eye_look"]
        else:
            d = dict(d, eye_look=cur_look)
        out.append((frac, d))
    out[0] = (0.0, {})
    out[-1] = (1.0, {})
    return out


EXPRESSIONS["life"] = _life_keys()


# ---- 阅读/书写：视线朝下扫行（2026-09-05）----
# 原版 reading 眼球偏转 13.5° 且停住 44% 时长。这类动作的视线由内容决定
# （盯着手里的书/纸），不是随机扫视，所以单独成源、按绝对时间平铺，且嫁接时
# **替代**生命层的视线（只保留生命层的眨眼）。
# 纵向 -0.5（×0.5 = 0.25 单位）是三模型眼白下缘余量的下限（洁尔佩塔 0.23）。
# 横向从 -0.45 匀速扫到 +0.45（约 1.6 秒一行），0.15 秒回行。
def _reading_keys(seed=20260905, seconds=20.0):
    import random
    rng = random.Random(seed)
    keys = [(0.0, {})]
    t = 0.35
    keys.append((round(t / seconds, 5), {"eye_look": (-0.45, -0.5)}))
    while True:
        line = rng.uniform(1.3, 1.9)
        if t + line + 0.15 >= seconds - 0.4:
            break
        t += line
        keys.append((round(t / seconds, 5), {"eye_look": (0.45, -0.5)}))
        t += 0.15
        keys.append((round(t / seconds, 5), {"eye_look": (-0.45, -0.5)}))
    keys.append((round((seconds - 0.3) / seconds, 5), {"eye_look": (0.0, -0.5)}))
    keys.append((1.0, {}))
    return keys


EXPRESSIONS["reading_gaze"] = _reading_keys()

# 四个基础表情加视线：眼珠随情绪略动，不再死盯正前方。
# happy 微微上扬、sadness 向下、shock 正视（睁大时不斜视）、angry 微俯。
# 这是**偏置**，嫁接时生命层的扫视会加在它上面，所以纵向不超过 0.35、横向
# 不超过 0.25（与 _life_keys 的 ±0.55/±0.20 相加仍在眼白余量内）。
_EYE_LOOK_BY_EXPR = {
    "happy":   (0.15, 0.30),
    "angry":   (0.00, -0.25),
    "sadness": (0.20, -0.35),
    "shock":   (0.00, 0.00),
}
for _name, _look in _EYE_LOOK_BY_EXPR.items():
    for _i, (_t, _d) in enumerate(EXPRESSIONS[_name]):
        if _d and _look != (0.0, 0.0):
            _d[EYE_LOOK_SLOT] = _look

VANILLA_TIMING = {
    "life":    {"vanilla_frames": 242, "vanilla_fps": 30.12},   # head_idle
    "reading_gaze": {"vanilla_frames": 129, "vanilla_fps": 30.23},   # head_reading
    "talk":    {"vanilla_frames": 301, "vanilla_fps": 15.05},
    "happy":   {"vanilla_frames": 565, "vanilla_fps": 15.01},
    "angry":   {"vanilla_frames": 150, "vanilla_fps": 15.05},
    "shock":   {"vanilla_frames": 150, "vanilla_fps": 15.05},
    "sadness": {"vanilla_frames": 150, "vanilla_fps": 15.05},
}

# 我们自己的输出规格。30fps 与既有 moe_idle.anim 一致。
OUTPUT_FPS = 30
# 每个表情的输出帧数。shock 短促、sadness 舒缓，其余居中。
OUTPUT_FRAMES = {
    "happy":   90,   # 3.0 秒
    "angry":   75,   # 2.5 秒
    "shock":   60,   # 2.0 秒，起手快
    "sadness": 105,  # 3.5 秒，最慢
    "talk":    600,  # 20 秒，嫁接时按绝对时间平铺，不拉伸
    "life":    600,  # 同上：眨眼/扫视层
    "reading_gaze": 600,  # 同上：阅读视线
    "smile": 90,
    "laugh": 75,
    "flirty": 105,
    "worry": 90,
    "shame": 105,
    "disgust": 75,
    "boredom": 120,
    "thinking": 105,
    "fear": 75,
}

# CK3 必须对齐的名字。骨名自由，这两套名字不自由。
#
# 实测确认这是**两个独立命名空间**，最初混为一谈了:
#   ANIM_IDS   → skeletal_animation_set 里 animation = { id = ... } 的 id，
#                原版形如 head_emotion_angry，是「动画资源名」。
#   STATE_NAMES→ entity 里 state = { name = ... } 的 name，
#                原版形如 emotion_angry（**无 head_ 前缀**），是游戏请求的状态名。
# 证据 male_head.asset:1188
#   state = { name = "emotion_angry" animation = "head_emotion_angry"
#             looping = no next_state = "emotion_angry" }
# 写成 head_emotion_angry 当 state 名的话游戏找不到，且是静默失效。
ANIM_IDS = {
    "talk":    "head_talk",          # 只做表情源，不注册 state（原版没有对应 state）
    "life":    "head_life",          # 同上：嫁接时叠加的眨眼/扫视层
    "reading_gaze": "head_reading_gaze",  # 同上：阅读视线源
    "happy":   "head_emotion_happy",
    "angry":   "head_emotion_angry",
    "shock":   "head_emotion_shock",
    "sadness": "head_emotion_sadness",
}

STATE_NAMES = {
    "talk":    None,                 # None = 不注册 state，只当嫁接的表情源
    "life":    None,
    "reading_gaze": None,
    "happy":   "emotion_happy",
    "angry":   "emotion_angry",
    "shock":   "emotion_shock",
    "sadness": "emotion_sadness",
}

# 新情绪只作为全动作嫁接的源；真正 state 名仍由 Catalog 实读原版注册。
for _name in EXPRESSION_STYLE:
    if _name != "neutral" and _name not in ANIM_IDS:
        ANIM_IDS[_name] = "head_expression_" + _name
        STATE_NAMES[_name] = None

# 每个正式 state 都配一个 _entry 过渡入场态，原版一律 next_state 指回正式态。
# 我们的配方本身就是「中性 → 峰值 → 中性」的完整弧线，所以入场态直接复用
# 同一个 .anim 即可，不需要单独做 _start 动画。
# 这一条能消掉 error.log 里 2160 次 in default 报错中的大部分——
# 日志统计显示 369 个未知 state 名里 *_entry 后缀占压倒性多数。
STATE_SUFFIXES = ["", "_entry"]

# 体型变体。原版每个表情都有 标准/Fat/FatDwarf 三套（各配 _entry），
# 因为原版头模有胖瘦捏脸维度、动画要跟着改。
# 我们只有一套几何，但 CK3 **仍会按体型请求对应 state 名**，缺了就报
# Unknown animation state。实测 error.log 里 emotion_shockFat_entry 就在报。
# 所以三套体型指向同一个 .anim —— 名字齐全比几何精确重要。
BODY_VARIANTS = ["", "Fat", "FatDwarf"]


def variant_names(base):
    """给基名生成「全部体型 × 入场」的 state 名，(正式名, 完整名) 成对返回。

    体型标记的插入位置**不统一**，必须照原版抄，不能拿一条规则套（实测）:
      emotion_shock  → emotion_shockFat / emotion_shockFatDwarf      后缀式
      idle           → idleFat / idleFatDwarf                        后缀式
      throneRoom_bow1→ throneRoom_bowFat1 / throneRoom_bowFatDwarf1   **中缀式**
    最初统一按后缀拼，生成了 throneRoom_bow1Fat —— 原版没有这个名字，
    等于白写。规则是: 基名末尾若是数字，体型标记插在数字**前**。
    """
    import re as _re
    m = _re.match(r"^(.*?)(\d+)$", base)
    out = []
    for var in BODY_VARIANTS:
        if m and var:
            canon = f"{m.group(1)}{var}{m.group(2)}"
        else:
            canon = base + var
        for suf in STATE_SUFFIXES:
            out.append((canon, canon + suf))
    return out

# state 必须在 head 与 torso **两个 entity 都注册**。这是实测踩出来的:
#   第一版只把表情注册给 moe_head_entity（想着「表情是脸的事」），
#   结果 error.log 里 emotion_happy 报 4 次，全部是 part 'torso'。
#   反过来 throneRoom_bow1_entry 只注册给 torso，就报 part 'head'。
# CK3 对每个肖像动画会同时向 head 和 torso 请求同名 state，两边都得有。
# 原版确实是两套独立动画（male_head_emotion_happy + female_body_emotion_happy），
# 身体也有情绪姿态。我们躯干没有脸部代理骨，用身体待机动画占位即可 ——
# 有名字能播总比报错好，后续可以再单独做身体情绪动作。
ENTITY_PARTS = ["head", "torso"]


def sample_expression(name, frames=None):
    """把配方重采样成逐帧的 {语义槽: 强度}。

    线性插值 + smoothstep 缓动。缓动放在段内而非全局，
    这样配方里刻意设计的节奏差异（shock 起手快）不会被抹平。
    """
    keys = EXPRESSIONS[name]
    n = frames or OUTPUT_FRAMES.get(name, 90)
    slots = sorted({s for _, d in keys for s in d})
    out = []
    for f in range(n):
        t = f / (n - 1) if n > 1 else 0.0
        # 定位所在段
        lo, hi = keys[0], keys[-1]
        for i in range(len(keys) - 1):
            if keys[i][0] <= t <= keys[i + 1][0]:
                lo, hi = keys[i], keys[i + 1]
                break
        span = hi[0] - lo[0]
        u = 0.0 if span <= 1e-9 else (t - lo[0]) / span
        u = u * u * (3.0 - 2.0 * u)          # smoothstep
        frame_vals = {}
        for s in slots:
            a = lo[1].get(s, 0.0)
            b = hi[1].get(s, 0.0)
            if s == EYE_LOOK_SLOT:
                # 视线是 (x, y) 二元组，逐分量插值；缺省为正视 (0, 0)
                a = a or (0.0, 0.0)
                b = b or (0.0, 0.0)
                vx = a[0] + (b[0] - a[0]) * u
                vy = a[1] + (b[1] - a[1]) * u
                if abs(vx) > 1e-6 or abs(vy) > 1e-6:
                    frame_vals[s] = (round(vx, 5), round(vy, 5))
                continue
            v = a + (b - a) * u
            if v > 1e-6:
                frame_vals[s] = round(v, 5)
        out.append(frame_vals)
    return out


def required_proxy_bones(expr_names=None):
    """这些表情用到的代理骨集合。用于只建必要的骨。"""
    names = expr_names or list(EXPRESSIONS)
    slots = {s for n in names for _, d in EXPRESSIONS[n] for s in d}
    return sorted({SEMANTIC_SLOTS[s] for s in slots if s in SEMANTIC_SLOTS})


# ---------------------------------------------------------------- 动作层
# 两套动作。原版 female_body 71 骨中仅 19 根带动画，其中 17 根是纯四元数
# 'q'（只旋转，与骨长无关，不受比例差异影响）——所以动作可以走骨骼迁移。
# 但 rest 朝向差异 17/17 全部 >45°（脊椎链 X 轴对齐、Y/Z 差 ~90°；
# 左右腿镜像约定不同：l_hip Z 差 177.5° vs r_hip 2.5°），是系统性坐标
# 约定不同而非噪声，不能用单一全局校正，必须逐骨算校正矩阵。
BODY_BONE_MAP = [
    ("body_root",      "bn_m_center"),
    ("bn_sp_lumbar",   "bn_m_spine_low"),
    ("bn_sp_thoracic", "bn_m_spine_up"),
    ("bn_l_clavicle",  "bn_m_l_shoulder"),
    ("bn_l_shoulder",  "bn_m_l_arm"),
    ("bn_l_elbow",     "bn_m_l_elbow"),
    ("bn_l_wrist",     "bn_m_l_wrist"),
    ("bn_r_clavicle",  "bn_m_r_shoulder"),
    ("bn_r_shoulder",  "bn_m_r_arm"),
    ("bn_r_elbow",     "bn_m_r_elbow"),
    ("bn_r_wrist",     "bn_m_r_wrist"),
    ("bn_l_hip",       "bn_m_l_hip"),
    ("bn_l_knee",      "bn_m_l_knee"),
    ("bn_l_ankle",     "bn_m_l_ankle"),
    ("bn_r_hip",       "bn_m_r_hip"),
    ("bn_r_knee",      "bn_m_r_knee"),
    ("bn_r_ankle",     "bn_m_r_ankle"),
]

# 两套动作配方。和表情一样用语义描述（骨骼语义名 + 欧拉角度），
# 而不是搬原版数值——原版 rest 朝向差异太大，且我们的比例不同。
# (帧位置比例, {语义骨名: (轴, 角度)})
ACTIONS = {
    # 站立待机：呼吸 + 重心微移 + 手臂自然摆动。可循环，首尾必须闭合。
    "idle_calm": {
        "frames": 90,
        "looping": True,
        "keys": [
            (0.00, {}),
            (0.35, {"bn_m_spine_low": ("X", 1.6), "bn_m_spine_up": ("X", 1.1),
                    "bn_m_neck": ("X", 0.7), "bn_m_l_arm": ("Z", -2.2),
                    "bn_m_r_arm": ("Z", 2.2)}),
            (0.70, {"bn_m_spine_low": ("X", -0.5), "bn_m_spine_up": ("X", -0.3),
                    "bn_m_neck": ("X", -0.4), "bn_m_l_arm": ("Z", 1.0),
                    "bn_m_r_arm": ("Z", -1.0)}),
            (1.00, {}),
        ],
    },
    # 行礼/致意：上身前倾 + 头低下 + 手臂内收。不循环，做完回中性。
    "greet_bow": {
        "frames": 75,
        "looping": False,
        "keys": [
            (0.00, {}),
            (0.30, {"bn_m_spine_low": ("X", 9.0), "bn_m_spine_up": ("X", 6.0),
                    "bn_m_neck": ("X", 7.0), "bn_m_l_shoulder": ("Z", -4.0),
                    "bn_m_r_shoulder": ("Z", 4.0), "bn_m_l_arm": ("Z", -6.0),
                    "bn_m_r_arm": ("Z", 6.0), "bn_m_l_elbow": ("Y", 12.0),
                    "bn_m_r_elbow": ("Y", -12.0)}),
            (0.55, {"bn_m_spine_low": ("X", 11.0), "bn_m_spine_up": ("X", 7.5),
                    "bn_m_neck": ("X", 8.5), "bn_m_l_shoulder": ("Z", -5.0),
                    "bn_m_r_shoulder": ("Z", 5.0), "bn_m_l_arm": ("Z", -7.0),
                    "bn_m_r_arm": ("Z", 7.0), "bn_m_l_elbow": ("Y", 14.0),
                    "bn_m_r_elbow": ("Y", -14.0)}),
            (0.82, {"bn_m_spine_low": ("X", 4.0), "bn_m_spine_up": ("X", 2.5),
                    "bn_m_neck": ("X", 3.0), "bn_m_l_arm": ("Z", -2.5),
                    "bn_m_r_arm": ("Z", 2.5), "bn_m_l_elbow": ("Y", 5.0),
                    "bn_m_r_elbow": ("Y", -5.0)}),
            (1.00, {}),
        ],
    },
}

# 动作的 state 名同样查过原版 + 实测日志，不是猜的:
#   "greeting" 在原版 asset 里**不存在**（grep 全 portraits 目录零命中），
#   最初凭直觉写了这个名字，等于白做。鞠躬的真实 state 是 throneRoom_bow1
#   （female_body.asset 有 bow1/2/3 三套，各带 _entry 与 chance 权重）。
#   idle_entry 则是 error.log 实测缺失第 2 高（296 次，torso 占 144）。
#
# 每个动作声明它要注册的**全部** state 名（一个 .anim 可挂多个 state）。
# idle_calm 同时充当 idle 与 idle_entry —— 原版就是这么干的:
#   state = { name = "idle_entry" animation = "body_idle_1" ... next_state = "idle" }
#   state = { name = "idle"       animation = "body_idle_1" ... next_state = "idle" }
# 同一个 anim 既做入场也做本体，省一份资源且视觉连续。
ACTION_STATE_NAMES = {
    "idle_calm": ["idle", "idle_entry"],
    "greet_bow": ["throneRoom_bow1", "throneRoom_bow1_entry"],
}

# 原版 idle 的循环写法是 looping = no + next_state 指回自己，靠自跳转循环，
# **不是** looping = yes（实测 female_body.asset:334）。现有 moe_portrait.asset
# 写的 looping = yes 与原版不一致，一并改过来。
# 表情类同样 looping = no + next_state 指回自己（male_head.asset:1188）。
STATE_LOOP_STYLE = "self_next"


def sample_action(name):
    """把动作配方重采样成逐帧 {骨名: (轴, 角度)}。同样 smoothstep。

    循环动作强制首尾帧一致——物理骨烘焙时接缝处会跳帧，这是根因之一。
    """
    spec = ACTIONS[name]
    keys = spec["keys"]
    n = spec["frames"]
    bones = sorted({b for _, d in keys for b in d})
    axis_of = {b: ax for _, d in keys for b, (ax, _) in d.items()}
    out = []
    for f in range(n):
        t = f / (n - 1) if n > 1 else 0.0
        lo, hi = keys[0], keys[-1]
        for i in range(len(keys) - 1):
            if keys[i][0] <= t <= keys[i + 1][0]:
                lo, hi = keys[i], keys[i + 1]
                break
        span = hi[0] - lo[0]
        u = 0.0 if span <= 1e-9 else (t - lo[0]) / span
        u = u * u * (3.0 - 2.0 * u)
        vals = {}
        for b in bones:
            a = lo[1].get(b, (axis_of[b], 0.0))[1]
            c = hi[1].get(b, (axis_of[b], 0.0))[1]
            v = a + (c - a) * u
            if abs(v) > 1e-6:
                vals[b] = (axis_of[b], round(v, 5))
        out.append(vals)
    if spec["looping"] and n > 1:
        out[-1] = dict(out[0])
    return out


if __name__ == "__main__":
    print("=" * 68)
    print("语义槽 %d 个，代理骨 %d 根，控制骨 %d 根" %
          (len(SEMANTIC_SLOTS), len(PROXY_BONES), len(CONTROL_BONES)))
    print("=" * 68)

    # 用源模型实际的 57 键验证绑定层
    face_keys = [
        "Basis", "eyeBlinkRight", "eyeBlinkLeft", "eyeSquintRight", "eyeSquintLeft",
        "jawOpen", "tongueOut", "jawRight", "jawLeft", "cheekPuff", "mouthFunnel",
        "mouthPucker", "mouthRight", "mouthLeft", "mouthSmileRight", "mouthSmileLeft",
        "mouthFrownRight", "mouthFrownLeft", "mouthDimpleRight", "mouthDimpleLeft",
        "mouthStretchLeft", "mouthStretchRight", "mouthRollUpper", "mouthRollLower",
        "mouthShrugUpper", "mouthShrugLower", "mouthPressRight", "mouthPressLeft",
        "mouthLowerDownLeft", "mouthLowerDownRight", "mouthUpperUpRight",
        "mouthUpperUpLeft", "browInnerUp", "browDownRight", "browDownLeft",
        "cheekSquintRight", "cheekSquintLeft", "browOuterUpRight", "browOuterUpLeft",
        "noseSneerRight", "noseSneerLeft", "eyeLookDownLeft", "eyeLookInLeft",
        "eyeLookOutLeft", "eyeLookUpLeft", "eyeWideLeft", "eyeLookDownRight",
        "eyeLookInRight", "eyeLookOutRight", "eyeLookUpRight", "eyeWideRight",
        "jawForward", "mouthClose",
    ]
    scheme, hits = detect_naming_scheme(face_keys)
    print("\n探测命名体系: %s（%d/%d 槽命中）" % (scheme, hits, len(SEMANTIC_SLOTS)))
    binds, missing = resolve_bindings(face_keys)
    print("绑定成功 %d 槽，缺失 %d 槽" % (len(binds), len(missing)))
    if missing:
        print("  缺失: %s" % ", ".join(missing))

    print("\n" + "=" * 68)
    print("表情配方")
    print("=" * 68)
    for name in EXPRESSIONS:
        curve = sample_expression(name)
        used = sorted({s for fr in curve for s in fr})
        peak_i = max(range(len(curve)), key=lambda i: sum(curve[i].values()))
        ok = all(s in binds for s in used)
        print("\n%-8s %d 帧 @%dfps = %.2f 秒  state=%s" %
              (name, len(curve), OUTPUT_FPS, len(curve) / OUTPUT_FPS, STATE_NAMES[name]))
        print("  用到 %d 槽，本模型全部可绑定: %s" % (len(used), "是" if ok else "否"))
        print("  首帧空=%s 末帧空=%s（能与 idle 衔接）" %
              (not curve[0], not curve[-1]))
        top = sorted(curve[peak_i].items(), key=lambda kv: -kv[1])[:5]
        print("  峰值帧 %d: %s" % (peak_i, ", ".join("%s=%.2f" % kv for kv in top)))

    print("\n" + "=" * 68)
    print("动作配方")
    print("=" * 68)
    for name in ACTIONS:
        curve = sample_action(name)
        spec = ACTIONS[name]
        used = sorted({b for fr in curve for b in fr})
        closed = curve[0] == curve[-1]
        print("\n%-10s %d 帧 = %.2f 秒  循环=%s  state=%s" %
              (name, len(curve), len(curve) / OUTPUT_FPS,
               spec["looping"], ACTION_STATE_NAMES[name]))
        print("  驱动 %d 骨: %s" % (len(used), ", ".join(b.replace("bn_m_", "") for b in used)))
        print("  首尾闭合: %s%s" % (closed, "" if not spec["looping"] or closed
                                    else "  <-- 循环动画必须闭合，有问题"))
        peak_i = max(range(len(curve)),
                     key=lambda i: sum(abs(v) for _, v in curve[i].values()))
        top = sorted(curve[peak_i].items(), key=lambda kv: -abs(kv[1][1]))[:4]
        print("  峰值帧 %d: %s" % (peak_i, ", ".join(
            "%s %s%.1f°" % (b.replace("bn_m_", ""), ax, v) for b, (ax, v) in top)))

    print("\n需要建的代理骨 %d 根:" % len(required_proxy_bones()))
    for b in required_proxy_bones():
        print("   %s" % b)
