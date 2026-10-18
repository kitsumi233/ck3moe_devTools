"""材质 → CK3 部件（head / torso / clothes / drop）的分类合同。

为什么需要这个:
  build_florence_poc.py 把芙洛伦的材质名写死成三个集合（HEAD_MATS /
  TORSO_MATS / CLOTHES_MATS）。要导入其他 11 个角色就得改代码。
  但材质命名**跨系列完全不通用**（实测 12 个模型）:

    少前2   BodySkin, Face, Eyes, EyeWhite, Brows, Lashes, HairA/B, Cth1-Skirt
    鸣潮    face, eye, eye+, mei(眉), she(舌), 腿, 鞋子, 披风, 内裤, 头饰
    终末地  面, 目, 目白, 目影, 睫眉, 肌, 发, 发影, 口内, Cloth1, Cloth1Alpha

  同一系列内部却是一致的（少前2 五个角色都用 BodySkin/Face/Eyes/Cth*），
  这与骨架的结论一致：**系列内统一，跨系列必须适配**
  （见 work/pmx_skeleton_inventory/SKELETON_REPORT.md）。

所以做法是「关键词规则 + 每模型覆盖」，不是每模型手写全表:
  1. 先用 KEYWORDS 按子串匹配（大小写不敏感，中日文直接匹配）
  2. OVERRIDES 里按模型名钉死规则搞不定的个别材质
  3. classify_materials() 返回分类结果 + 未识别清单，未识别的**必须**在
     OVERRIDES 里补，不许默默丢掉（丢几何是静默错误，最难查）

分类语义（对齐 CK3 的实体划分）:
  head    脸、口腔、眼、眉、睫、头发 —— 挂 head entity，承载表情
  torso   身体皮肤、纹身 —— 挂 torso entity
  clothes 衣服、鞋袜、挂件、饰品 —— 独立 entity，shared_pose_entity = torso
  drop    武器、Hide/Unused —— 不导出
"""
import re

# ---------------------------------------------------------------- 关键词规则
# 顺序有意义：先匹配到的先赢。所以更具体的词要放前面
# （例：`目白`(眼白) 必须在 `目`(眼) 之前，否则会被 `目` 抢走；
#  `发影`(发阴影) 与 `发` 同属 head，谁先无所谓，但保持具体优先的习惯）。
KEYWORDS = [
    # ---- drop：先剔除，避免被其他规则捞走
    ("drop", [
        "weapon", "gun", "knife", "axe", "bracket",      # 武器与武器挂架
        "(hide", "hide/unused", "unused",         # 源模型标记的隐藏件
        # `新規` 不再整体丢弃（2026-10-10）：它是 PMX 编辑器给新建材质的默认名，不是「空白占位」。
        # 鸣潮 8 人的 `新規*` 全是贴着 Up_D/Face_D/Eye_D/Hair_D 的真几何（白芷腹部 671 面、折枝
        # 胸腹 4.4k 面、吟霖胸肩 906 面、阿布腰臀、今汐辫子 2.4k 面），丢掉 = 用户看到的「透明」。
        # 改由 classify_by_texture 按贴图定部位，并在 classify_one 里排除。
    ]),
    # ---- head：眼（细分项必须在 `目`/`eye` 之前）
    ("head", [
        "eyewhite", "目白",                        # 眼白
        "eyeshadow", "目影",                       # 眼睑阴影
        "eyes+", "eye+", "目hl",                   # 高光层（后续会设为不渲染）
        "eyes", "eye", "目",                       # 虹膜本体
        # 脸与口腔
        "face", "面",                              # 终末地用 `面` 做脸
        # 注意 `肌`(=皮肤) **不在这里** —— 实测终末地的 `肌` 贴的是
        # T_actor_*_body_01_D.png，是**身体皮肤**不是脸（脸是 `面`，
        # 贴 face_01_D.png）。早先把 `肌` 归 head，导致终末地四个角色
        # 全都没有 torso 分类、身体皮肤被塞进头部实体。
        "mouth", "口内", "teeth", "tongue", "she",  # she = 舌（鸣潮拼音）
        "cheek",
        # 眉睫
        "brows", "brow", "lashes", "睫眉", "mei",
        # 终末地的日文部件名
        "齿", "歯",                                # 牙齿
        "表情",                                    # 表情叠加层（同 Emotions）
        # 头发
        "hair", "bangs", "发", "髪",
        # 表情叠加层
        "emotions", "emotion",
        # 少前 2 批量（2026-09-06）：莫辛纳甘把表情贴片按表情符号命名，全贴 extra.png
        # （与 Emotions 同一张图集）；Spark/Sweat/Anger 亦然。EyeLid 是 32 面眼睑卡片。
        "////", "><", "@@", "-.-", "o.o", "spark", "sweat", "anger", "eyelid",
        "hearted",                                 # 莫辛纳甘：36 面爱心贴片（extra.png）
    ]),
    # ---- torso：身体皮肤
    ("torso", [
        "bodyskin", "body", "tattoo",
        "上body",                                  # 鸣潮：上半身皮肤
        "腿",                                      # 鸣潮：腿部皮肤
        "肌",                                      # 终末地：身体皮肤（贴 body_01_D）
        "右手", "左手",                             # 鸣潮：裸手皮肤
        "skin",                                    # 少前 2：Skin / Skin_On_Socks（皮肤在袜上）
    ]),
    # ---- clothes：其余一切穿戴物
    ("clothes", [
        "cloth", "cth", "socks", "shoes", "glove", "jacket", "skirt",
        "panties", "chocker", "choker", "goggles", "hairpin", "zip",
        "内裤", "鞋子", "披风", "头饰", "饰品", "吊饰", "包包", "包带",
        "金属", "star", "heart", "jie", "hi", "bai",
        # 中文衣物词：`裙`/`衣`/`袖`/`袜`/`手套`/`帽`。
        # 补这些是因为 is_wearable() 要靠这张表豁免头身分界校验 ——
        # 漏词会让合法的裙摆/高领被误报成「跑错部位的皮肤」。
        "裙", "衣", "袖", "袜", "手套", "帽", "腰带", "领",
        # 尾巴/毛边：是穿戴或附加部件，不是身体皮肤也不是头部。
        # 归 clothes 让它跟躯干姿势走（尾巴挂在腰骨上，与裙同理）。
        "尾巴", "tail", "fur",
        # 少前 2 批量（2026-09-06，见 work/evidence/gfl2_pmx_survey.json）：
        "suit", "uniform", "cape", "jacket", "clothing", "acessories", "accessor",
        "tassel", "amor", "armor", "metalic", "metallic", "backpack", "bracele",
        "hat", "earring", "shoulderpad", "glasses", "grenade", "gunpouch", "pouch",
        "beg", "skrt", "leisure",
    ]),
]

# ---------------------------------------------------------------- 每模型覆盖
# 规则搞不定的个别材质写在这里。键是 work/pmx_skeleton_inventory 的模型名。
#
# 为什么需要覆盖: 有些材质名完全不表意（鸣潮的 bai/bai3/jie/hi 是拼音缩写，
# MI_Up 看不出是什么），只能逐个看渲图定。留空表示该模型暂未人工核对过。
OVERRIDES = {
    # 夏洛蒂皮肤的 `新規` 是已发布产物里一直丢弃的层；全局 `新規` drop 撤销后（见 KEYWORDS）
    # 这里保持原判，发布版逐字节不变。未核对过该层几何，要放出来先看渲图。
    "gfl2_charolicskin001": {
        "新規": "drop",
    },
    # GFL2 第二批候选覆盖：武器/配件在单独模型中没有统一命名。
    "gfl2_2_002_efe68546": {
        "Axe": "drop",
    },
    # GFL2 第二批：比悠卡原皮的 Bullet 是武器/弹药部件，明确丢弃，
    # 保留 candidate-specific 记录，避免把同名材质误判为全局规则。
    "gfl2_2_007_252973e7": {
        "Bullet": "drop",
    },
    # 维普蕾晴夏回响的 TeddyBear 是随服装携带的可穿戴配件，
    # 保留为 clothes entity，不能静默丢弃几何。
    "gfl2_2_029_99c97d06": {
        "TeddyBear": "clothes",
    },
    # Endfield 候选使用日文/中文短名，单字睫毛、眉毛和双眼皮是 head
    # 贴图层；它们没有命中跨系列的组合关键词。
    "endfield_006_eaff3d48": {
        "睫": "head",
        "眉": "head",
        "二重": "head",
    },
    "endfield_007_78785330": {
        "照れ": "head",
    },
    "endfield_008_112b8397": {
        "尾": "clothes",
    },
    "endfield_009_d91bb553": {
        "睫": "head",
        "眉": "head",
    },
    "endfield_020_5f9643df": {
        "二重": "head",
    },
    # Endfield boys candidates: generic Item/ring materials are wearable
    # attachments; Wpn/weapon materials are explicitly dropped.
    "endfield_2_002_ca7bc3b1": {
        "Item": "clothes",
        "环": "clothes",
    },
    "endfield_2_003_095bf39f": {
        "Item": "clothes",
        "Wpn": "drop",
    },
    "endfield_2_004_6353c1fa": {
        "Item": "clothes",
        "Wpn": "drop",
        "鸡翅骨头": "clothes",
        "鸡翅肉": "clothes",
        "鸡翅肉2": "clothes",
    },
    "endfield_2_005_e3a553c2": {
        # 大潘用同一组皮肤/毛材质铺满头身。先交给 head，再由
        # build_florence_poc 的主导骨父链切出身体面；按材质整块归 torso 会丢脸。
        "肤": "head",
        "武器": "drop",
        "毛": "head",
    },
    "endfield_2_006_6bb0d86d": {
        "顔2": "head",
        "顔": "head",
        "尾": "clothes",
        "服_02": "clothes",
    },
    "endfield_2_010_010905ce": {
        "Wpn": "drop",
    },
    "gfl2_florence": {
        # 芙洛伦已人工核对（这套是 build_florence_poc.py 原先写死的分类）
        "Teeth.001": "head", "Hair.001": "head",
        "Socks(Hide/Unused)": "drop",
        # Tattoo = 右大腿上的红色纹字（实测 mesh[20] 包围盒 Y 59.38~70.84、
        # X 0.49~11.99 单侧）。**保留**，用户 2026-08-18 明确：它是模型的一部分。
        # 曾短暂归过 drop（当时以为是丢衣服后露出的穿模残件），已撤回。
        # 归 torso 而不是 clothes：它是皮肤上的纹身，不是穿戴物；
        # 现在衣服也并入 torso，两者殊途同归。
        # 注意它会让 check_part_bounds 报「torso 材质整体在腿部」——
        # 那是**预期告警**，不是错误。
        "Tattoo": "torso",
    },
    "wuwa_baizhi": {
        # 鸣潮的拼音缩写材质需要逐个核对，暂按几何位置归类
        "MI_Face": "head", "MI_Bangs": "head", "MI_Hair": "head",
        "MI_Star": "clothes", "MI_Up": "clothes",
    },
    # 鸣潮（2026-10-08，work/evidence/wuwa_import_20261008/gen_overrides.py 生成）：
    # emo*/Facial/星/心 = 表情贴片归 head；Up/Down/Alpha/Metal/Prop/声痕 等衣装图集归 clothes；MI_Hand 裸手归 torso。
    "wuwa_002_041dc033": {  # 今汐
        "小穗子x2": "clothes",
        "大穗子": "clothes",
    },
    "wuwa_003_3968cf78": {  # 今汐·桃夭灼灼
        "metal leg": "clothes",
        "声痕": "clothes",
        "声痕-满": "clothes",
        "纱内侧": "clothes",
        "纱外侧": "clothes",
        "emo3": "head",
    },
    "wuwa_004_adbff656": {  # 千咲·泳装
        "Facial": "head",
    },
    "wuwa_005_0ff2d71c": {  # 卜灵
        "Up_Metal": "clothes",
        "Facial": "head",
    },
    "wuwa_006_64969f8f": {  # 卡提希娅
        "Up_Metal": "clothes",
        "Down_Metal": "clothes",
        "Alpha": "clothes",
        "Facial": "head",
    },
    "wuwa_007_b4dd5d8c": {  # 守岸人
        "声痕": "clothes",
        "MI_Up02": "clothes",
        "MI_Up03": "clothes",
        "后蝴蝶结内": "clothes",
        "后蝴蝶结": "clothes",
        "手花": "clothes",
        "头巾1": "clothes",
        "头巾2": "clothes",
        "胸前": "clothes",
        "MI_Alpha": "clothes",
    },
    "wuwa_008_39312472": {  # 坎特蕾拉（少女）
        "arm_Alpha": "clothes",
        "emo3": "head",
    },
    "wuwa_009_323e61f8": {  # 尤诺
        "Facial": "head",
        "Alpha": "clothes",
    },
    "wuwa_010_eda96b7c": {  # 弗洛洛
        "Facial": "head",
    },
    "wuwa_011_b1978d1b": {  # 折枝
        "MI_Down": "clothes",
        "MI_Down.001": "clothes",  # PMX 里有两个 MI_Down，Blender 给第二个加 .001
        "MI_Glass": "clothes",
    },
    "wuwa_012_c23eeb92": {  # 椿
        "Up_Metal": "clothes",
        "Down_Metal": "clothes",
    },
    "wuwa_013_6fc32f64": {  # 洛可可
        "声痕": "clothes",
        "emo": "head",
        "星": "head",
        "emo3": "head",
    },
    "wuwa_016_f107fdd6": {  # 菲比
        "蕾丝装饰": "clothes",
        "声痕": "clothes",
        "星": "head",
        "emo3": "head",
    },
    "wuwa_018_62bb1d47": {  # 釉瑚
        "Prop_": "clothes",
        "Prop_Metal": "clothes",
        "Prop_Liusu": "clothes",
    },
    "wuwa_020_2e376a40": {  # 丹瑾
        "MI_Up": "clothes",
        "earing": "clothes",
        "shou": "clothes",
        "gebo": "clothes",
        "qian": "clothes",
        "MI_XInghen": "clothes",
    },
    "wuwa_021_bd2ef352": {  # 千咲
        "emo1": "head",
        "外套透明": "clothes",
        "emo3": "head",
    },
    "wuwa_022_45518670": {  # 吟霖
        "LED": "clothes",
        "MI_UP+": "clothes",
    },
    "wuwa_024_8b3be221": {  # 安可
        "后蝴蝶结2": "clothes",
        "胸缎带": "clothes",
        "后脑缎带": "clothes",
        "腰结x2": "clothes",
        "星星": "head",
    },
    "wuwa_025_d781b255": {  # 散华
        "眼白3": "head",
    },
    "wuwa_026_6a24b897": {  # 桃祈
        "MI_Up": "clothes",
        "胳膊": "clothes",
    },
    "wuwa_031_ad7c02e9": {  # 锁暝
        "head+": "head",
        "heng": "clothes",
        "emo1": "head",
        "emo43": "head",
    },
    "wuwa_2_002_3c09b74d": {  # 伤痕
        "Up": "clothes",
        "Up_Metal": "clothes",
    },
    "wuwa_2_003_5e7cf7cc": {  # 克里斯托弗
        "emo1": "head",
    },
    "wuwa_2_005_5553a57c": {  # 布兰特
        "裤子+": "clothes",
        "披肩+": "clothes",
        "右布+": "clothes",
        "左条+": "clothes",
        "声痕": "clothes",
        "星": "head",
        "emo3": "head",
    },
    "wuwa_2_006_fb20aaaf": {  # 相里要
        "麒麟臂": "clothes",
        "emm": "head",
        "声痕": "clothes",
        "はぁと": "head",
        "麒麟臂al": "clothes",
        "前髮alpha": "head",
        "前髮": "head",
    },
    "wuwa_2_008_d5940446": {  # 忌炎
        "星星": "head",
    },
    "wuwa_2_009_16b7b5d3": {  # 渊武
        "MI_Glass": "clothes",
    },
    "wuwa_2_010_7d1fbcc9": {  # 男漂泊者
        "白": "head",
    },
    "wuwa_2_011_e6e63103": {  # 秋水
        "星星": "head",
        "心": "head",
        "星": "head",
    },
    "wuwa_2_012_1e27c3bf": {  # 莫特斐
        "MI_Hand": "torso",
        "MI_Xinghen": "clothes",
        "MI_Glow al": "clothes",
    },
    "wuwa_2_013_c10c81d9": {  # 阿布
        "head 金": "head",
        "MI_Hand": "torso",
        "em": "head",
    },
}

# 几何「不渲染」的材质：照常注册 meshsettings，但给全透明 diffuse +
# portrait_hair_alpha（PS_hair 里 clip(Diffuse.a - 1e-5) 丢弃每个片元）。
# **不能删 meshsettings** —— shader 名写在 .mesh 二进制里，缺 meshsettings
# 引擎会去默认 pdxmesh.shader 找 portrait_*，报 Failed to create material。
#
# 为什么这些层必须关掉：CK3 的 portrait.shader **没有 additive 混合**，
# MMD 的半透明叠加层无论给哪个 shader 都出不来原意效果。
#
# `eyeshadow` 是**上眼睑白遮罩的真凶**（不是 eyes+）。它在 MMD 里是一层
# 淡色叠加，贴图本身不白，但在 CK3 的 BRDF 光照下被照亮成奶白，
# 盖住虹膜下半。2026-08-16 进游戏 A/B 确认：index 10 换全透明后遮罩消失。
#
# 这条曾经只改了产物没写回合同，重建后**回归了一次** —— 材质级的渲染决策
# 必须落在这里，改 .asset 产物是留不住的。
# 2026-10-02 gfl2 的 `eyes+`/`eye+` 移出：眼神光只画在 Eyes+ 的 eyeblend 图里，
# 关掉它批量角色就没高光（用户实机确认）。它走 EYE_HIGHLIGHT_KEYWORDS 的硬切 shader。
NO_RENDER_KEYWORDS = ["目hl", "eyeshadow", "目影",
                      # 洛茜的「发影」是头发阴影叠加层（diffuse 为 None），
                      # 与「目影」同性质，CK3 没 additive 表达不了。
                      "发影", "髪影", "hairshadow"]

PARTS = ("head", "torso", "clothes", "drop")


# ---------------------------------------------------------------- 半透明二值化
# CK3 的 portrait shader **没有 alpha blend**，只有 alpha-to-coverage（按 alpha
# 概率丢弃片元的点状抖动）。MMD 里当「淡色镜片 / 纱 / 渐变边」用的中间 alpha
# 到了 CK3 就变成**能看穿的筛子**：芙洛伦的帽檐 alpha 160~223，进游戏后
# 透过帽檐能直接看到大地图（2026-08-18 截图确认）。
#
# 这不是贴图坏了，是渲染模型对不上：原意的「半透明有色镜片」CK3 表达不了。
# 唯一能看的做法是二值化 —— 该在的地方全不透明，该镂空的地方全透明。
#
# 只对**衣服/身体类 diffuse** 生效，理由：
#   - head 侧的中间 alpha 是睫毛/眉毛的抗锯齿边（芙洛伦 face_d 只有 0.378%
#     中间值、0% 全透明），二值化会让睫毛边缘发硬，得不偿失
#   - `_properties` 的 A 通道是**粗糙度**、`_normal` 的 A 参与法线重建
#     （见 memory: ck3-portrait-properties-channel-semantics），
#     绝对不能动 —— 所以调用方必须只对 diffuse 调这个函数
#
# 阈值取 16 而不是 128：alpha 低于 16 的像素在源模型里是**真镂空**
# （cloth2_da 有 2.3% 的 alpha=0，是衣物真实开孔），必须保留；
# 其余一切（含 alpha=1..15 的脏边）一律拉到 255。
BINARIZE_ALPHA_KEYWORDS = ["cloth", "_da", "up_d", "down_d", "body_d",
                           "衣", "裙", "cth",
                           # 2026-09-11 补 hair：`moe_portrait_hair` 槽的 diffuse
                           # 的 A **是透明度**，中间值就是筛子（与帽檐透地图同机制）。
                           # 29 张 hair diffuse 里 3 张带中间 alpha：
                           # wulfa_hair_02 54.24% / ardelia_hair_01 9.91%
                           # / wulfa_hair_01 6.15%，全在 endfield 包。
                           # gfl2 那 26 张源图本就是纯二值，所以这个缺口一直没暴露。
                           # 注意别写成 "_hair"：贴图名有 `hair_01_D` 也有
                           # `slg_hair_d`，前缀分隔符不统一。
                           "hair", "_moe_opacity"]
BINARIZE_ALPHA_CUTOUT = 16

# Alpha meaning follows the actual diffuse shader slot. A texture shared by an
# opacity slot and a palette color mask must be split by the build pipeline so
# each shader receives an alpha channel with the right meaning.
ALPHA_IS_OPACITY = {
    "moe_portrait_attachment",
    "moe_portrait_attachment_alpha_to_coverage",
    "moe_portrait_hair",
    "moe_portrait_hair_alpha",
    "moe_portrait_face",
}
ALPHA_IS_COLORMASK = {
    "moe_portrait_skin",
    "moe_portrait_eye",
}
ALPHA_MID_TOLERANCE_PCT = 1.0

# ---- 表情/贴花图集：同样要二值化，但**阈值必须取中点 128 不是 16** ----
# 2026-09-11 用户「洛茜的脸有变色阴影存在，所以她的脸不像是 toon 风格的，
# 不是参数的问题」——查到真凶是共用表情图集
# `T_actor_common_female_emotion_atlas_01_D.dds`（终末地 lau/per/eld 三人共用）：
# 2×2 图集，左半边两格是大片纯红褐色块（R198 G117 B99，就是腮红），
# alpha 分布 0 占 80.9%、255 只占 3.8%、**中间值占 15.23%**。
# 这层走 `attachment_alpha_to_coverage`，那 15% 中间 alpha 被 alpha-to-coverage
# 渲成半透明点阵糊在脸上 = 用户看到的「有颜色、边缘发糊的假阴影」。
# 与帽檐透地图是同一机制（中间 alpha），但表现方向相反：不是透出背景而是糊上颜色。
#
# **不能整层 no_render**：这层是贴花槽的载体（腮红/怒气/泪滴/汗珠，见
# moe_expression_contract 的 decal_* 槽），打掉会连腮红一起废掉。
#
# **不能套衣物那个 16**：16 的语义是「只有真镂空(<16)保留透明、其余全拉 255」，
# 用在这里会把那 15% 中间 alpha 大部分拉成**不透明**，红褐色块变实心，比现在更糟。
# 取中点 128：偏低的半透明部分归 0（真正消失），偏高的才留成实心贴花。
# 腮红这类贴花本体的 alpha 是高值，二值化后仍在；糊脸的低值杂边被清掉。
BINARIZE_ALPHA_MIDPOINT_KEYWORDS = ["emotion_atlas", "emotion", "表情",
                                    # 2026-09-11 gfl2 侧的表情贴片图集就叫
                                    # `extra.png`，名字里没有任何上面那些词，
                                    # 所以**从来没被二值化过** —— 用户
                                    # 「不止那三个人有问题」指的就是它。
                                    # 它与终末地那张等价：同走
                                    # `attachment_alpha_to_coverage`、同是贴花
                                    # 载体（莫辛纳甘爱心贴片贴在它上面）。
                                    # 按 alpha 分组看 RGB 是同一个病：
                                    # alpha 1..127 的 24,318 像素 RGB 中位
                                    # **178,104,79**（红褐＝糊脸的假阴影），
                                    # alpha 128..254 的 74,762 像素中位
                                    # 187,202,195（偏白青的高光/泪光贴花，
                                    # 该留成实心）。所以走**中点 128**。
                                    "extra"]
BINARIZE_ALPHA_MIDPOINT = 128


# ------------------------------------------------------- 虹膜不接受 palette 染色
# 虹膜 diffuse 的 alpha **必须整张为 0**，那是原版专门给的「不要染色」开关。
#
# `jomini/gfx/FX/jomini/portrait.shader` 的 `PS_eye`：
#     ColorMaskStrength = Diffuse.a
#     Diffuse.rgb = GetColorMaskColorBLend( Diffuse.rgb, vPaletteColorEyes.rgb,
#                                           Input.InstanceIndex, ColorMaskStrength )
# 而 `GetColorMaskColorBLend` 的原版注释写得很直白：
#     If the texture value is 0.0, that is a special case indicating there
#     shouldn't be any palette color
# 混合式是 `lerp( Diffuse, Diffuse * PaletteColor, LerpTarget )` —— **乘法**，
# 所以 palette 的橙红会把虹膜原色直接乘掉。
#
# 我们两个角色的虹膜 alpha 都是满值 255（=要求 100% 染色），表现就是用户说的
# 「眼睛蒙了一层红色遮罩」。**它不是眼睛材质坏了、不是贴图脏、也不是我们
# shader 的 bug** —— 是没用原版给的开关。三条实测证据：
#
#   1. 洛茜 `T_actor_wulfa_iris_00_D` 是 R−B **+105.5** 的橙金，
#      芙洛伦 `c_Florence_slg_eye_d` 是 **−43.5** 的冷蓝，色差 149，
#      **渲染出来却几乎同色**（+67.4 / +71.0）—— 贴图原色被完全丢弃
#   2. 原版 `male_eyes_dark_iris_diffuse.dds` 的 alpha **95.8% 是 0**，
#      正走这条旁路；我们 0%
#   3. error.log 有 9 条 `No gene with key: eye_color`，
#      `vPaletteColorEyes` 拿到的本身就是无效值，却以满强度盖上来
#
# 只筛虹膜/眼睛的 **diffuse**：`_properties` 的 A 是粗糙度、`_normal` 的 A
# 参与法线重建，动了会坏（见 ck3-portrait-properties-channel-semantics）。
# 眼白（`目白`/`eyewhite`）也走 `portrait_eye`，同样不该被染色。
# **实机验收后决定不启用**（2026-08-25，用户看过 A/B 效果）。
# alpha=0 确实消掉了橙红（芙洛伦虹膜 R−B +58.3 → +18.0~+42.8），但走
# `return DiffuseColor` 特例后虹膜只剩贴图原色乘光照，而贴图本身是暗的
# （芙洛伦 (71,90,115)），**虹膜变成近黑深褐、丢了神采** —— palette 原本
# 还兼着提亮作用。用户比较后选择保留染色，接受眼睛偏橙红。
#
# 表留着是因为证据链有价值（见上方注释），但**表为空 = 规则不生效**。
# 要重新启用得先解决「虹膜变暗」：或补 `eye_color` 基因让 palette 拿到
# 有效值（error.log 那 9 条报错同时消掉），或把虹膜 diffuse 整体提亮。
NO_PALETTE_ALPHA_KEYWORDS = []


def wants_no_palette_alpha(texname):
    """这张 diffuse 要不要把 alpha 刷 0（不接受 palette 染色）。

    传 properties/normal 是调用方的 bug —— 那两张的 A 有别的语义。
    """
    low = str(texname).lower()
    if "_properties" in low or "_normal" in low:
        return False
    return any(k in low for k in NO_PALETTE_ALPHA_KEYWORDS)


def _kw_hit(kw, low):
    """关键词是否命中贴图名。**以 `_` 开头的关键词要锚在边界上**。

    2026-09-11：`"_da"` 原意是匹配 `..._da.png` 这类「diffuse+alpha」后缀
    （如 `cloth2_da`），但裸 `in` 让它命中**角色名**：

        'c_daiyan_slg_face_d.png'.find('_da') == 1   # 命中 c_**_da**iyan

    于是黛烟的**全部**贴图（含脸图）都被判要二值化。当前无害（她那 6 张的
    alpha 已是纯 0/255、中间值 0.000%，二值化后逐字节不变），但合同注释明确
    写了脸图不该二值化 —— 脸上的中间 alpha 是睫毛/眉毛的抗锯齿边，硬化会让
    睫毛边缘发硬，而日志里那句「alpha 二值化 N 像素」会照常打印，看不出误命中。

    所以下划线开头的关键词只在 `.` / `_` 之前或字符串末尾才算命中；
    其余关键词（cloth/hair/衣/裙…）仍是子串匹配 —— 它们是部件名，
    出现在任何位置都是真命中。
    """
    if kw.startswith("_"):
        return re.search(re.escape(kw) + r"(?=[._]|$)", low) is not None
    return kw in low


def wants_binary_alpha(texname):
    """这张 diffuse 要不要二值化 alpha。传 properties/normal 图是调用方的 bug。"""
    low = str(texname).lower()
    if "_properties" in low or "_normal" in low:
        return False
    return (any(_kw_hit(k, low) for k in BINARIZE_ALPHA_KEYWORDS)
            or any(k in low for k in BINARIZE_ALPHA_MIDPOINT_KEYWORDS))


def binary_alpha_cutout(texname):
    """这张 diffuse 二值化时用哪个阈值。

    衣物用 16（只有真镂空保留透明），表情/贴花图集用中点 128 —— 两者语义相反，
    详见 BINARIZE_ALPHA_MIDPOINT_KEYWORDS 上面的注释。**必须按图区分**：
    表情图集套 16 会把糊脸的半透明杂边拉成实心色块，比不改更糟。
    """
    low = str(texname).lower()
    if any(k in low for k in BINARIZE_ALPHA_MIDPOINT_KEYWORDS):
        return BINARIZE_ALPHA_MIDPOINT
    return BINARIZE_ALPHA_CUTOUT


# ---------------------------------------------------------------- 贴图名兜底
# 材质名不表意时（鸣潮大量拼音缩写：kou/hou/qian/bai），**贴图名反而是可靠的**
# —— 引擎资产的贴图按部位切分，名字里带部位标识:
#
#   T_...Face_D.png   脸部图集 → head   （kou/kou2/nose/hou2 都用它）
#   T_...Eye_D22.png  眼睛     → head   （`白` 是眼白）
#   T_...Hair_D22.png 头发     → head   （`辫子` 是辫子）
#   T_...Up_D.png     上装     → clothes（earing/MI_Up 挂在上装图集）
#   ..._Down_D.png    下装     → clothes（hou/qian/MI_Down）
#
# 这比逐模型手写 OVERRIDES 通用：同系列所有角色的贴图命名一致。
# 顺序同样是具体优先。
TEXTURE_KEYWORDS = [
    # `mi_eye_base` / `mi_face_base`：折枝/吟霖的重绘贴图叫 MI_Eye_Base_color（折枝 `新規` 是眼白）
    ("head", ["face_d", "eye_d", "hair_d", "brow", "mouth", "teeth", "tongue",
              "mi_eye_base", "mi_face_base"]),
    # `bady_d`：阿布的贴图把 Body 拼成 Bady（T_NM006Bady_D）
    ("clothes", ["up_d", "down_d", "cloth", "body_d", "bady_d"]),
]


def classify_by_texture(images):
    """靠贴图名判部位。images 是该材质引用的贴图文件名列表。"""
    for img in images or []:
        low = str(img).lower()
        for part, keys in TEXTURE_KEYWORDS:
            for k in keys:
                if k in low:
                    return part
    return None


def classify_one(name, model=None, images=None):
    """单个材质名 → 部件名，或 None（未识别）。

    优先级: 每模型 OVERRIDES > 材质名关键词 > 贴图名关键词。
    材质名优先于贴图名，因为共用图集的情况很常见（芙洛伦整张脸
    Face/Mouth/Teeth/EyeWhite/Brows/Lashes 共用一张 face_d），
    此时材质名才是唯一能区分部位的信息。
    """
    if model and name in OVERRIDES.get(model, {}):
        return OVERRIDES[model][name]
    low = name.lower()
    for part, keys in KEYWORDS:
        for k in keys:
            if k in low:
                return part
    return classify_by_texture(images)


def is_no_render(name):
    low = name.lower()
    return any(k in low for k in NO_RENDER_KEYWORDS)


# ---------------------------------------------------------------- 头身分界校验
# **mod 玩家要能单独换头或换身，所以 head 与 torso 的几何必须干净分开。**
# 材质名分类管不了这件事 —— 名字对了几何也可能长在别的部位（芙洛伦的
# `Tattoo` 名字命中 torso 关键词，几何却在右大腿 Y 59~71）。
#
# 分界线取**脖子根部**。CK3 原版 head 与 torso 靠 attach 在颈部相接，
# 我们同一具骨架拆件，也照这个位置分。
#
# 允许的例外只有一类：**头发**。长发垂到腰甚至更低是二次元常态，
# 它属于头（换头时要跟着走），所以 hair 类材质豁免下界检查。
# 判据用**占身高的比例**而不是绝对坐标 —— 绝对值会被两件事坑：
#   1. 轴向：Blender 里高度是 Z，导出的 .mesh 里是 Y
#   2. 尺度：build_florence_poc.py 在 STEP 3e 才把骨架放大约 8×，
#      在那之前量到的是原始尺度（角色高约 18，不是 144）
# 比例对两者都免疫，也不依赖校验插在流程哪一步。
NECK_RATIO = 0.73       # 脖子根部 / 身高（芙洛伦实测 105.95 / 144.0 ≈ 0.736）
BOUNDARY_SLACK_RATIO = 0.055   # 容差 ≈ 8/144，给下巴、耳垂、锁骨

# 兼容旧调用：绝对值版本仍可用，但新代码请传 body_height 用比例判据。
NECK_Y = 105.0
BOUNDARY_SLACK = 8.0

HAIR_EXEMPT_KEYWORDS = ["hair", "bangs", "发", "髪"]

# 头发豁免的**下限**：长发垂到腰是二次元常态，但垂到膝盖以下就不是头发了。
# 2026-08-22 洛茜 `发Alpha` 材质把垂到小腿的飘带 + 袜环混在头顶耳饰里，
# 无条件豁免让它静默进了 head mesh。0.10 ≈ 膝盖高度 / 身高。
# 注意这只是**告警**用的粗判据；真正把几何搬对位置靠
# check_skin_ownership() 的父链判据（头发确实可以垂到脚踝）。
HAIR_EXEMPT_FLOOR_RATIO = 0.10

# 人工核对过、确认「越界是对的」的材质。写在这里是为了让告警清单保持干净 ——
# 一份全是已知例外的告警清单等于没有告警清单，真的问题会被淹掉。
# 键是模型名，值是材质名集合。
BOUNDS_ACCEPTED = {
    "gfl2_florence": {
        # 右大腿上的红色纹字，属于身体皮肤的一部分（用户要求保留）。
        # 它必然触发 torso 下界告警：几何整体在 Y 59~71，远低于躯干区间。
        "Tattoo",
    },
}


def is_hair(name):
    low = name.lower()
    return any(k in low for k in HAIR_EXEMPT_KEYWORDS)


# 取景中心要量「脸」和「眼」的高度，但材质名跨系列完全不通用
# （芙洛伦 Face/Eyes/EyeWhite，洛茜 面/目/目白，鸣潮 MI_Face）。
# 写死英文名会让别的角色静默回落到原版数值 149.98，相机取景就偏了。
FACE_KEYWORDS = ["face", "面", "顔", "head_base", "肤"]  # 大潘的「肤」材质还含脸，头身再按主导骨拆分
EYE_KEYWORDS = ["eye", "目", "瞳", "眼"]
# 排除：这些含 eye 关键词但不是眼球本体，量它们会把包围盒撑大
EYE_EXCLUDE = ["eyelash", "eyebrow", "eyeshadow", "睫", "眉", "目影", "目hl"]


def is_face(name):
    """是不是脸部主材质（用于量取景中心）。"""
    low = name.lower()
    if is_hair(name):
        return False
    return any(k in low for k in FACE_KEYWORDS)


def is_eye(name):
    """是不是眼球本体（睫毛/眉毛/眼影不算）。"""
    low = name.lower()
    if any(k in low for k in EYE_EXCLUDE):
        return False
    return any(k in low for k in EYE_KEYWORDS)


def is_wearable(name):
    """是不是穿戴物（衣/裙/袜/鞋/饰品）。

    衣服并入 torso 之后，torso 会合法地跨越全身高度（裙摆到小腿、
    高领到下巴），所以头身分界校验对穿戴物豁免上下界 ——
    要查的是「皮肤类材质有没有跑错部位」，不是衣服覆盖到哪。
    """
    low = name.lower()
    for part, keys in KEYWORDS:
        if part != "clothes":
            continue
        return any(k in low for k in keys)
    return False


# ---------------------------------------------------------------- PDX shader
# 合法 shader 名取自 game/tools/pdxexporter.settings 的白名单。
# 这套规则原先是 build_florence_poc.py 里一张写死芙洛伦材质名的字典，
# 洛茜（材质名「面/目/发1/口内」）会 15 个全部落到 portrait_skin ——
# 头发不透明、眼睛无高光。所以改成按**语义**判定。
#
# 各 shader 的选择理由（都是实机 A/B 定下来的，别凭直觉改）：
#
# portrait_eye —— PS_eye 硬编码 `Out.Color = float4(Color, 1.0f)`，
#   **无视 diffuse 的 alpha，强制完全不透明**。这对虹膜本体和眼白是对的
#   （本就该整片不透明），但对「眼部特效图集」是错的：那种图大片 alpha≈0
#   却 UV 铺满，强制实色就成了眼前的遮挡块。
#
# portrait_hair —— 真发是弯曲薄片、各面法线朝向不一，必须双面，否则很多
#   角度有缺口、呈碎片感。原版这个 Effect 自己就声明了
#   rasterizer_no_culling，所以曾误用 portrait_hair_alpha（原版没声明
#   RasterizerState、走单面剔除）时翻过车。
#
#   **现在剔除不再是选 shader 的理由**：`tools/build/build_moe_shader.py` 在标准
#   `gfx/FX/jomini/portrait.shader` 路径生成独立的 `moe_portrait_*` Effect，
#   每个都带 rasterizer_no_culling，所以 6 个 Effect **全部双面**。选 shader 只按
#   光照模型和 alpha 语义（PS_hair / PS_skin / PS_eye / PS_attachment 是四
#   个不同的像素着色器），不再考虑单双面。见 README「Moe effect 隔离、
#   双面渲染与王座厅光照」。
#
# portrait_hair_alpha —— 贴在脸上的平面卡片（眉毛/睫毛），要 PS_hair 的
#   发丝光照 + hair_alpha_blend 混合。
#
# portrait_attachment_alpha_to_coverage —— 尊重 alpha 的附件层
#   （表情图集、纹身、眼部特效）。
HAIR_CARD_KEYWORDS = ["brow", "lash", "睫", "眉"]
EYE_FX_KEYWORDS = ["eyes+", "eye+", "eyeblend", "目hl", "目影", "eyeshadow"]
EYE_HIGHLIGHT_KEYWORDS = ["eyes+", "eye+"]
# 鸣潮的眼神光层命名五花八门（`Eye_+` / `eye.001+` / `Eye_Hi` / `eyehi1` / `EYE hi` / `MI_Eye hi2` /
# `目+hi` / `星目`），贴图 57~98% 透明。漏判会落进 is_eye → portrait_eye 强制不透明，透明区画成实色
# 整片盖住虹膜，实机「只剩眼白」（2026-10-09，维里奈等 20+ 人）。名字先剥 Blender 的 `.001` 后缀。
EYE_HIGHLIGHT_PATTERN = re.compile(r"(eyes?|目)[\s_\-]*\+|(eyes?|目)\+*[\s_\-]*hi\d*$|星目", re.I)
_BLENDER_DUP = re.compile(r"\.\d{3}")
_AL_LAYER = re.compile(r"(?:[\s_]|[^\x00-\x7f])al$", re.I)


def is_eye_highlight(name, images=None):
    if any(k in name.lower() for k in EYE_HIGHLIGHT_KEYWORDS):
        return True                      # 原有判据不变（gfl2 Eyes+ 等）
    low = _BLENDER_DUP.sub("", name.lower())
    if not EYE_HIGHLIGHT_PATTERN.search(low):
        return False
    # 同名却引用虹膜本体图（鸣潮 wcs/wct 的 `Eye_+` 用 Eye_D，alpha 全满）是虹膜副本，不是高光
    return not re.search(r"eye_d", " ".join(images or []).lower())
EMOTION_KEYWORDS = ["emotion", "表情"]
# 眼白。**必须与虹膜分开**：走 portrait_eye 会被 vPaletteColorEyes 染成
# 粉红（见 pdx_shader 里的实测数据）。归 portrait_skin 与脸同套光照。
SCLERA_KEYWORDS = ["eyewhite", "目白", "sclera", "白目", "眼白"]
SKIN_KEYWORDS = ["skin", "body", "face", "mouth", "teeth", "tongue",
                 "肌", "面", "口内", "歯", "舌"]
BODY_SKIN_PATTERN = re.compile(r"^(?:body[ _-]*skin|skin|肌|皮肤|肌肤)", re.I)


def is_body_skin(name):
    """Explicit exposed-skin names take precedence over outfit suffixes."""
    return bool(BODY_SKIN_PATTERN.match(name))


def pdx_shader(name, images=None):
    """材质名 → PDX shader 名。按语义判定，不查写死的材质名表。"""
    low = name.lower()
    tex = " ".join(images or []).lower()
    # **不渲染层最先判**。洛茜的「发影」既含 no-render 关键词又含 hair，
    # 顺序放后面会被 is_hair 抢先返回 portrait_hair，当成真发渲染出来
    # （2026-08-21 实机 asset 复核发现 index 8 拿到了 portrait_hair）。
    # 不渲染层靠全透明 diffuse + 尊重 alpha 的 shader 一起生效。
    if is_no_render(name):
        return "portrait_hair_alpha"
    # 鸣潮的 `X al` 层（`MI_Glow al` / `麒麟臂al`）是同几何的 alpha 遮罩副本：莫特斐的 alp.png 全透明白，
    # 相里要的 Arm_D al 82% 透明、透明处 RGB 白。落到 portrait_skin 时 alpha 是染色开关，整层画成实白
    # （莫特斐 880 面与 bai/Eye 同一眼区 y 91~93%，整片白 = 「眼睛贴图异常」，2026-10-10）。`al` 前必须是空格/下划线/非 ASCII，
    # 以免误中 Metal/crystal。
    if _AL_LAYER.search(_BLENDER_DUP.sub("", name)):
        return "portrait_attachment"
    # 眼神光层（gfl2 Eyes+）：alpha > 128 是亮斑，暗叠加只占 ~0.02%，
    # 用 moe_portrait_attachment 的 MOE_ALPHA_CUTOUT 硬切，柔光晕直接丢掉。
    if is_eye_highlight(name, images):
        return "portrait_attachment"
    # 眼部特效图集：尊重 alpha，绝不能用 portrait_eye
    if any(k in low for k in EYE_FX_KEYWORDS) or "eyeblend" in tex:
        return "portrait_attachment_alpha_to_coverage"
    # 表情图集
    if any(k in low for k in EMOTION_KEYWORDS):
        return "portrait_attachment_alpha_to_coverage"
    # 眉毛/睫毛：平面卡片，走 PS_hair 的发丝光照 + hair_alpha_blend
    if any(k in low for k in HAIR_CARD_KEYWORDS):
        return "portrait_hair_alpha"
    # 真发：PS_hair + alpha_to_coverage
    if is_hair(name):
        return "portrait_hair"
    # 眼白：**不能走 portrait_eye**。`PS_eye` 会把 `vPaletteColorEyes`
    # 乘到 diffuse 上（`ColorMaskStrength = Diffuse.a`，我们的贴图 alpha
    # 满值 = 100% 染色），于是接近白的眼白被瞳色染成粉红。
    # 实测洛茜：眼白贴图 R−B **+29.5**（接近白），渲染出来 **+63.0**，
    # 放大了 33.5；同一张贴图的脸颊只放大 14.9（+38.2 → +53.1）——
    # 多出来的那一倍就是 palette 染色。
    #
    # 语义上也该如此：**瞳色变了眼白不该跟着变**。原版眼白与虹膜是分开的
    # 贴图，我们两个角色的眼白都借用脸贴图，正好走 `portrait_skin` 与脸
    # 同一套光照，颜色也就跟脸一致。
    # 放在 is_eye 之前判 —— 眼白的名字（`目白`/`eyewhite`）同时含眼睛词。
    if any(k in low for k in SCLERA_KEYWORDS):
        return "portrait_skin"
    # 眼球本体
    if is_eye(name):
        return "portrait_eye"
    if is_face(name):
        return "portrait_face"
    # 纹身/贴花：贴在皮肤上但**必须尊重 alpha**，否则整块贴图糊在腿上。
    # 放在 SKIN_KEYWORDS 之前判 —— 它含 "tattoo" 不含皮肤词，但顺序写清楚
    # 免得以后有人加 "skin_tattoo" 这类名字时踩坑。
    if any(k in low for k in ("tattoo", "decal", "刺青", "纹")):
        return "portrait_attachment_alpha_to_coverage"
    if is_body_skin(name):
        return "portrait_skin"
    # 鸣潮的 `X+` 叠加层（`bodyDown+` / `MI_UP+` / `head+` / `裤子+` / `披肩+`）：同 UV 的高光/描边图，
    # 70~99% 透明。落到 portrait_skin 时 alpha 是 palette 染色开关不是透明度，透明区画成实色盖住衣服
    # （吟霖裙子贴图异常，2026-10-09）。真发/脸的 `+` 层在上面已按 hair/face 走了尊重 alpha 的槽。
    # 引用整张不透明底图的（莫特斐 `变1+` 用 Up_D、`teeth+` 无贴图）不是叠加层，排除。
    if _BLENDER_DUP.sub("", low).rstrip().endswith("+") and images and images[0] \
            and not re.search(r"(up|down|face|body|eye|hair|bangs)_d\d*[._]", images[0].lower()):
        return "portrait_attachment"
    if is_wearable(name):
        return "portrait_attachment"
    # 皮肤类（含脸、口腔）
    if any(k in low for k in SKIN_KEYWORDS):
        return "portrait_skin"
    return "portrait_skin"


def check_part_bounds(part, entries, body_height=None,
                      neck_ratio=NECK_RATIO, slack_ratio=BOUNDARY_SLACK_RATIO,
                      model=None):
    """校验一个部件里各材质的几何是否落在该部件应有的高度区间。

    entries: [{"name": 材质名, "y_min": float, "y_max": float}, ...]
             y_min/y_max 是**该轴的高度值**，轴向由调用方保证
             （Blender 传 Z，.mesh 传 Y）。
    body_height: 整个角色的高度（同一尺度）。传了就走**比例判据**，
             对轴向与缩放都免疫。不传则回退到绝对值 NECK_Y（旧行为）。

    返回违规列表 [(材质名, 原因)]，空 = 干净。

    head  应在脖子以上，**头发豁免**（长发垂到腰是二次元常态）
    torso 应在脖子以下，**穿戴物豁免**（衣服并入 torso 后天然跨全身）
    """
    if body_height and body_height > 0:
        neck = body_height * neck_ratio
        slack = body_height * slack_ratio
        chin = body_height * 0.17     # 脖子到下巴 ≈ 24/144
        leg = body_height * 0.14      # 判「整体在腿部」的余量 ≈ 20/144
        unit = f"(身高 {body_height:.2f} × {neck_ratio})"
    else:
        neck, slack, chin, leg = NECK_Y, BOUNDARY_SLACK, 24.0, 20.0
        unit = "(绝对值)"

    accepted = BOUNDS_ACCEPTED.get(model, set()) if model else set()

    bad = []
    for e in entries or []:
        n = e.get("name")
        lo, hi = e.get("y_min"), e.get("y_max")
        if n is None or lo is None or hi is None:
            continue
        if n in accepted:
            continue
        if part == "head":
            if is_hair(n):
                # 豁免有下限：垂到膝盖以下的「头发」很可能是划错了部位
                # （洛茜 `发Alpha` 把垂到小腿的飘带混在头顶耳饰里）。
                # 只告警 —— 长发确实能垂到脚踝，真正的归属判定看
                # check_skin_ownership() 的父链判据。
                floor = (body_height * HAIR_EXEMPT_FLOOR_RATIO
                         if body_height and body_height > 0 else 14.0)
                if lo < floor:
                    bad.append((n, f"头发材质下探到 {lo:.2f}，低于膝盖线 "
                                   f"{floor:.2f} {unit} —— 该材质可能同时"
                                   f"装着下身几何（借用头发贴图），"
                                   f"看 STEP 2c 的蒙皮归属校验结论"))
                continue
            if lo < neck - slack:
                bad.append((n, f"head 材质下探到 {lo:.2f}，低于脖子线 "
                               f"{neck - slack:.2f} {unit}；应归 torso/drop"))
        elif part == "torso":
            # 上界：超过脖子+下巴高度说明含了脸部几何。
            # 衣服并入 torso 后，高领/帽子类材质可能合法地探到头部区域，
            # 所以这条只对**皮肤类**材质生效（穿戴物由 is_wearable 豁免）。
            if hi > neck + slack + chin and not is_wearable(n):
                bad.append((n, f"torso 材质上探到 {hi:.2f}，已进入头部区域 {unit}"))
            # 下界：整体远低于躯干区间 = 腿部/大腿几何。
            # 衣服（裙/袜/鞋）天然覆盖到腿，同样豁免。
            if hi < neck - leg and not is_wearable(n):
                bad.append((n, f"torso 材质整体在 {hi:.2f} 以下，"
                               f"远低于躯干区间 {unit}；很可能是腿部几何"))
    return bad


def classify_materials(mats, model=None):
    """返回 ({part: [材质名]}, 未识别列表)。

    mats 可以是材质名列表，也可以是 [{"name":..., "images":[...]}, ...]
    （后者能用上贴图名兜底，推荐）。

    未识别的材质**不要**默默当成某一类 —— 调用方应当报错或要求人工补
    OVERRIDES。静默丢几何是最难排查的一类错误（导出没报错、游戏里少一块）。
    """
    out = {p: [] for p in PARTS}
    unknown = []
    for m in mats:
        if isinstance(m, dict):
            n, imgs = m.get("name"), m.get("images")
        else:
            n, imgs = m, None
        if not n:
            continue
        p = classify_one(n, model, imgs)
        if p is None:
            unknown.append(n)
        else:
            out[p].append(n)
    return out, unknown


if __name__ == "__main__":
    import io
    import json
    import sys
    from pathlib import Path
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    P = Path(r"D:\有趣计划\CK3_MoeMod\work\pmx_skeleton_inventory")
    total_unknown = 0
    for f in sorted(P.glob("*.json")):
        if f.stem in ("comparison", "bone_name_maps", "anim_portability"):
            continue
        if f.stem.startswith("weapon"):
            continue
        d = json.load(open(f, encoding="utf-8"))
        mats = [m for m in d.get("materials", [])
                if (m.get("name") if isinstance(m, dict) else m)]
        names = [m.get("name") if isinstance(m, dict) else m for m in mats]
        got, unknown = classify_materials(mats, f.stem)
        nr = [n for n in names if is_no_render(n)]
        print(f"\n{f.stem}  材质 {len(names)}")
        for p in PARTS:
            if got[p]:
                print(f"   {p:<8} {len(got[p]):>2}: {', '.join(got[p])}")
        if nr:
            print(f"   不渲染   {', '.join(nr)}")
        if unknown:
            total_unknown += len(unknown)
            print(f"   !! 未识别 {len(unknown)}: {', '.join(unknown)}")
    print(f"\n合计未识别 {total_unknown} 个材质"
          f"{'（需补 OVERRIDES）' if total_unknown else ''}")


# ---------------------------------------------------------------- 蒙皮归属校验
# 材质名与几何高度两道校验会**同时失效**（2026-08-22 洛茜实机踩到）：
# 她的 `发Alpha` 材质（贴图 hair_02）同时装着头顶耳饰（Y 127~135）和一整条
# 垂到小腿的飘带 + 袜环（Y 14.97~83）—— 美术为省贴图把不相干的几何塞进了
# 同一个材质槽。材质名判据看到"发"就归 head；高度判据被 check_part_bounds()
# 里的 `if is_hair(n): continue` 无条件豁免放过（豁免本意是"长发垂到腰是
# 常态"，宽到把垂到脚踝的几何也放过了）。
#
# 后果：4274 个由躯干骨驱动的顶点进了 head mesh，而 head entity 只播
# moe_*_head_*.anim（不含躯干骨的动画通道），这些顶点永远停在 rest，
# torso 一动就整块错位 —— 游戏里表现为袜环/内衣/飘带跟身体脱节，
# 且**日志零报错**（这不是引擎错误，是数据划分错误）。

# head mesh 里出现这些骨**主导**的顶点 = 部位划错。
BODY_ONLY_BONE_KEYWORDS = [
    "pelvis", "spine", "waist", "hip",
    "leg", "knee", "ankle", "foot", "toe",
    "chest", "breast", "clavicle", "shoulder",
    "arm", "elbow", "wrist", "hand",
]
# 例外：这些含上表关键词但确实属于头部/头饰的骨名片段。
BODY_ONLY_BONE_EXCLUDE = ["hair", "ear_", "head", "ribbon", "horn"]

# 头骨名：父链走到它就算 head 归属。neck 也算 —— 脖子几何本来就在 head
# 与 torso 的边界上，两边都合法（原版也是这么切的）。
# **两个命名空间都要列**：这道校验可能跑在骨名转写之前（骨名是 MMD 日文
# 原名）也可能跑在之后（bn_m_* ASCII 名）。只列一套的后果是父链一路查不到
# 根、全部返回 None，而调用方会把"判不出"当成"没问题" —— 2026-08-22 就这么
# 假阴性了一次：21015 个顶点全判不出，日志却报「蒙皮归属干净」。
HEAD_CHAIN_ROOTS = ("bn_m_head", "bn_m_neck", "頭", "首")
# 躯干链的根：走到这些说明这根骨归躯干管。
BODY_CHAIN_ROOTS = ("bn_m_spine", "bn_m_waist", "bn_m_pelvis", "bn_m_center",
                    "bn_m_groove", "bn_m_root", "bn_m_leg", "bn_m_knee",
                    "上半身", "下半身", "センター", "グルーブ", "腰",
                    "足", "ひざ", "膝")


# mmd_tools 会往网格上塞**非骨骼**顶点组，它们不对应任何骨：
#   mmd_edge_scale    描边宽度倍率，权重恒为 1.0
#   mmd_vertex_order  导出用的顶点序
# 权重 1.0 比任何真实骨都大，所以「取权重最大的组当主导骨」会**全部**选中
# mmd_edge_scale —— 2026-08-22 就是这个原因让 21015 个顶点的归属判定全部
# 落空（主导骨集合里只有 mmd_edge_scale 一个条目），而不是骨名空间问题。
NON_BONE_VGROUPS = ("mmd_edge_scale", "mmd_vertex_order")


def is_bone_vgroup(name):
    """这个顶点组是否对应一根真实骨骼。"""
    return bool(name) and name not in NON_BONE_VGROUPS


def is_body_only_bone(name):
    """这根骨是否只应驱动 torso mesh 的几何。"""
    low = (name or "").lower()
    if any(k in low for k in BODY_ONLY_BONE_EXCLUDE):
        return False
    return any(k in low for k in BODY_ONLY_BONE_KEYWORDS)


def head_chain_root(bone, bone_parent, max_depth=64):
    """顺父链上溯，返回 "head" / "body" / None（判不出）。

    先撞到头骨就是 head，先撞到躯干骨就是 body。**顺序重要**：
    neck 的父链继续往上是 spine_up → spine_low → center，所以必须
    「先撞到谁算谁」，不能遍历完再判。
    """
    cur = bone
    for _ in range(max_depth):
        if cur is None:
            return None
        low = cur.lower()
        if any(low == r or low.startswith(r) for r in HEAD_CHAIN_ROOTS):
            return "head"
        if any(low.startswith(r) for r in BODY_CHAIN_ROOTS):
            return "body"
        nxt = bone_parent.get(cur)
        if nxt == cur:
            return None
        cur = nxt
    return None


def check_skin_ownership(part, dom_bones, min_verts=20, bone_parent=None):
    """按**顶点主导骨**校验部位划分，返回违规列表 [(骨名, 原因)]。

    dom_bones: {骨名: 该骨主导的顶点数}，只统计权重最大者。
    min_verts: 少于这个数的算权重溢出噪声，不报（实测洛茜 head 里
               `bn_m_spine_up` 只有 1 个顶点、权重 0.0071，那是噪声；
               而 `bn_m_l_kneeD` 有 60 个顶点、权重整整 1.0000，那是真几何）。
    bone_parent: {骨名: 父骨名}。给了就走**父链归属**判据 —— 这是唯一
               可靠的判法，名字和高度都会骗人：
                 · 名字：`bn_m_tail_base_L_a_jnt13` 听着像尾巴，实际是
                   垂到小腿的飘带，驱动 891 个下身顶点。
                 · 高度：`bn_m_hair_side_L_a_03_jnt` rest 只有 Y 6.14
                   （垂到脚踝的长发，比那根飘带骨的 34.60 **还低**），
                   但它**确实是头发**，挂 bn_m_head 下，跟着头动是对的。
               两者的区别只在父链：hair_side/ear_base ← bn_m_head，
               而 tail_base ← bn_m_spine_low ← waist ← center。

    只对 head 生效：torso 里出现头部骨是合法的（高领、帽子跟着头动）。
    """
    if part != "head":
        return []
    bad = []
    for bn, cnt in sorted(dom_bones.items(), key=lambda kv: -kv[1]):
        if cnt < min_verts:
            continue
        if bone_parent:
            root = head_chain_root(bn, bone_parent)
            if root == "head":
                continue
            if root == "body":
                bad.append((bn, f"head mesh 里有 {cnt} 个顶点由 {bn} 主导，"
                                f"但这根骨挂在**躯干链**下（不在 bn_m_head "
                                f"之下）—— head entity 只播 head anim，"
                                f"没有躯干骨通道，这些顶点会僵在 rest，"
                                f"torso 一动就错位；应归 torso"))
                continue
            # root 未定（链断了/查不到）时退回名字判据，别静默放过
        if is_body_only_bone(bn):
            bad.append((bn, f"head mesh 里有 {cnt} 个顶点由下身骨 {bn} 主导 "
                            f"—— head entity 不播躯干动画，这些顶点会僵在 "
                            f"rest；应归 torso"))
    return bad
