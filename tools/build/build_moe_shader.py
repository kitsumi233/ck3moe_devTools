"""生成 mod 自己的 portrait shader 副本，把所有用到的材质改成双面渲染。

**为什么需要这个文件**：CK3 的 `portrait.shader` 里只有 hair 系几个 Effect
声明了 `RasterizerState = "rasterizer_no_culling"`，其余（`portrait_skin` /
`portrait_eye` / `portrait_attachment` /
`portrait_attachment_alpha_to_coverage` / `portrait_hair_alpha`）都没声明，
走默认背面剔除 = 单面。MMD 模型大量使用开放曲面（裙摆、袖口、领口、
飘带），单面渲染会看穿内侧。

**为什么必须整份复制原版文件，而不是追加一个小 shader**：
Effect 里的 `VertexShader` / `PixelShader` 引用的是**同文件内**的 shader
块名，跨文件引用不成立（原版每个只含 Effect 的小 shader 文件都自带完整
PixelShader 块，例如 `game/gfx/FX/river_bottom.shader`）。

**为什么不能直接用现成的双面 hair Effect**：`PS_skin`（次表面散射 +
半透）、`PS_eye`（强制不透明）、`PS_attachment`、`PS_hair` 是四个不同的
像素着色器。拿 `portrait_hair_double_sided` 套皮肤会丢掉次表面散射，
脸变塑料感。

**第二项改动：卡通渲染（二值硬边赛璐璐）**。在 `CommonPixelShaderColor`
里、引擎算完光照之后，把连续的明暗渐变切成「亮/暗」两档。

为什么切在那里而不是自己算 `NdotL`：肖像用的是引擎配置的**三点布光**
（`LIGHT_COUNT 3`，聚光/点光/平行光混合，见 `portrait_lighting.fxh`），
自己拿单一方向算 NdotL 会把整套布光丢掉、脸的受光方向直接错。取引擎
**已经算好**的漫反射亮度做阈值，布光方向就被完整保留。

**量化的对象必须是光照强度，不是最终颜色**（2026-08-24 修）。引擎每一项
都形如「光照 × 反射率」（见 `clausewitz/gfx/FX/cw/lighting.fxh`）：

    DiffuseLight = DiffuseBRDF * _DiffuseColor * LightIntensity * NdotL
    DiffuseIBL   = DiffuseRad  * _DiffuseColor

除掉 `MaterialProps._DiffuseColor` 就得到纯光照强度。第一版直接在
`Color` 上切，等于把反射率一起量化了 —— 同样的受光强度，亮皮肤和深色
布料会落在阈值两侧，阈值在不同材质上语义不同，根本调不出一致的明暗线。
实测症状：整张脸**没有一个像素进入亮部**（贴图 (242,188,183) 的干净粉色
渲成最亮 (178,137,106)、最暗 (72,41,32)），二值化"生效"了却把整张脸判成
暗面，看不到硬边、只看到一片被压暗的连续渐变，还无条件套了一层暗部色偏。

第一版还用「定向光贡献占环境光的比例」做归一化，那个分母假设也不成立：
肖像里三点布光是主力、环境光很弱，比值要么爆到 1 以上被 saturate 削平，
要么在背光侧恒为 0。改成直接用光照强度的绝对值，语义清楚且可调。

镜面项**单独切一档**：不切的话脸上会留一片连续的油光渐变，跟硬边的
漫反射打架，看着像两层贴图错位。

**第三项改动：SSAO 降权**。`SSAO` 是**全屏后处理**，在本 shader 之后
用 `SourceBlend = "dest_color"` **乘**到画好的画面上（见
`jomini/gfx/FX/jomini/ssao_apply.shader` 的 `SSAOApplyColor`），强度是
`SSAOSample * ColorSample.a`，而 `ColorSample` 正是我们在 `PS_*` 里写进
`Out.SSAOColor` 的值。所以像素着色器里做的任何硬边都会被它再叠一层连续的
接触阴影 —— 脸上眼窝、鼻侧、下巴那些浑浊斑块就是它（贴图侧已排除：脸的
diffuse 是干净平涂、properties 的散射 mask 全 0）。唯一能从 shader 侧
控制的就是那个 alpha，整体降权而非设 0（全关会让头发与脸、衣领与脖子
糊成一片，失去层次）。

只有一处逻辑改动就能覆盖**全部**材质 —— `PS_skin`、`PS_eye`、
`PS_attachment`、`PS_hair` 都调同一个 `CommonPixelShaderColor`。
**我起初以为 `PS_hair` 走 `portrait_hair_lighting.fxh` 的独立路径而把它
排除了，那是错的**：实读 `MainCode PS_hair`，里面就是
`CommonPixelShader( Diffuse, Properties, NormalSample, Input )`。
头发占 10 处引用（hair + hair_alpha），漏掉会出现「衣服卡通、头发写实」
的割裂感 —— 二次元观感里最显眼的一处。

**代价**：产物是原版文件的冻结副本。CK3 更新 `portrait.shader` 后要重跑
这个脚本，否则我们的副本落后于原版（新增的光照特性拿不到）。

用法：
  python tools/build/build_moe_shader.py
"""
import sys as _sys
from pathlib import Path as _P
_sys.path[:0] = [str(_P(__file__).resolve().parents[2] / "tools" / _d) for _d in ("lib", "anim")]   # 子目录化后底座不在脚本同目录
import moe_out as O          # 产物归属：谁写谁声明
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GAME = Path(r"E:\SteamLibrary\steamapps\common\Crusader Kings III")
SRC = GAME / "jomini" / "gfx" / "FX" / "jomini" / "portrait.shader"
COURT_SRC = GAME / "game" / "gfx" / "FX" / "court_scene.shader"
DST = O.root(O.SHADER) / "gfx" / "FX" / "jomini" / "portrait.shader"
COURT_DST = O.root(O.SHADER) / "gfx" / "FX" / "court_scene.shader"

# mod 实际用到的 Effect（与 `moe_material_contract.pdx_shader()` 的返回值
# 一一对应）。加进这张表的前提是 pdx_shader 真会返回它 —— 多加无害但会
# 让副本与原版的 diff 变大，不利于下次更新时核对。
BASE_TARGETS = [
    "portrait_skin",
    "portrait_face",
    "portrait_eye",
    "portrait_attachment",
    "portrait_attachment_alpha_to_coverage",
    "portrait_hair_alpha",
    "portrait_hair",          # 原版已声明双面，这里会跳过
]
EFFECT_ALIASES = {name: f"moe_{name}" for name in BASE_TARGETS}
TARGETS = list(EFFECT_ALIASES.values())

# 「不接受投影」只给皮肤。**眼睛也算皮肤的一部分**：眼球紧贴在脸上，
# 前发的影子同样会糊在虹膜上，而脸不受影而眼睛受影会出现「眼睛比脸暗一档」
# 的割裂。衣服与头发**不加** —— 衣褶、衣领在脖子上的阴影、头发之间的层次
# 都要留着，否则人物会变得很平（那正是光源级 `affected_by_shadow = no`
# 的问题，也是我们改用材质级宏的理由）。
#
# **`portrait_skin` 必须在这里**（2026-09-13 补，用户认可）。它就是「皮肤」
# 本身（脖子、手臂、腿，以及眼白 `目白`/`EyeWhite`），09-11 补进 TOON_EFFECTS
# 时漏了这张表 —— 于是走 skin 的皮肤**做了卡通二值化、却仍接受 shadow map
# 投影**，帽檐与头发的投影被二值化从渐变放大成硬边色块。症状：洛茜鼻下/嘴周/
# 下巴一片深褐硬边块（随光照角度变、不随表情变），而黛烟同场景干净 —— 差别
# 只是洛茜戴大红帽、投影源多。
#
# 代价是脖子上的衣领投影也一起没了、那里会平一些。这是「皮肤不接受投影」这条
# 原则本身的代价，不是新问题：face/eye 早就这样。**凡是往 TOON_EFFECTS 加槽位,
# 都要同时问它该不该进这张表** —— 两张表漏配的后果就是本条。
NO_SHADOW_TARGETS = [
    EFFECT_ALIASES["portrait_face"],
    EFFECT_ALIASES["portrait_eye"],
    EFFECT_ALIASES["portrait_skin"],
]

# 材质层墨线（MOE_INK）挂哪些 Effect。2026-09-07 起除眼睛外全挂；2026-09-08 晚用户「只给脸、手臂、腿、
# 身体加描边」→ 只挂皮肤。衣服/头发的分块边由 GUI 层色度差内部线负责，整体剪影由 GUI 层剪影负责。
INK_EFFECTS = [
    EFFECT_ALIASES["portrait_face"],
]

# 卡通化（MOE_TOON）挂哪些 Effect。2026-09-09 用户定方向：「衣服走原版光照、只有皮肤等保留卡通化，
# 现在主流是这个思路」（对照版实机 shot_vanillalight_dai.png 认可）。眼睛跟皮肤走，否则「眼睛比脸暗一档」。
#
# 2026-09-11 补上 `portrait_skin`：**当初漏了它，而它就是「皮肤」本身**。
# 用户报「洛茜眼白和瞳孔的亮度绝不应该低于脸部皮肤」，查出来不是眼睛的问题 ——
# 眼白（`目白`/`EyeWhite`）按材质合同走 `portrait_skin`（走 eye 会被 vPaletteColorEyes
# 染成粉红，那个判断是对的、不改），而 skin 不在本表里，于是**眼白留在原版连续光照、
# 紧邻的脸走卡通两档**，同一块皮肤两套模型。这轮加 ToonGain 把脸亮档抬了 23%
# （黛烟实测 157.5 → 194.5）之后差距被进一步拉开，眼白就显得暗了。
#
# 同一个根因还解释了 todo 里记的「大腿绝对领域区域和肉色丝袜是两套渲染方式，
# 造成色差太大」：大腿 `portrait_skin`（原版光照）+ 丝袜 attachment + 脸卡通 = 三套并存。
# 代价说清楚：skin 覆盖全身皮肤（两个模型包共 189 处槽位引用），手臂/腿/身体
# 都会转成硬边两档，不再是连续光照。这是方向性变化，用户 09-11 明确「改，方向是对的」。
TOON_EFFECTS = [
    EFFECT_ALIASES["portrait_face"],
    EFFECT_ALIASES["portrait_eye"],
    EFFECT_ALIASES["portrait_skin"],
]
# 背光轮廓亮边（MOE_RIM）只留皮肤：它是卡通两档之上的加法项，衣服走原版光照后没有 ToonEdge 可乘。
RIM_EFFECTS = [
    EFFECT_ALIASES["portrait_face"],
]

FACE_FILL_TARGETS = list(TARGETS)

HEADER = '''# moe_portrait.shader —— 由 tools/build/build_moe_shader.py 生成，**不要手改**。
#
# 原版 jomini/gfx/FX/jomini/portrait.shader 的副本，三项改动：
#   1. 给 mod 用到的每个 Effect 加 RasterizerState = "rasterizer_no_culling"
#      （CullMode = "none"，双面渲染）
#   2. 卡通渲染（二值硬边赛璐璐）：给这些 Effect 加 MOE_TOON 宏，并在
#      CommonPixelShaderColor 里把**光照强度**（不是最终颜色）切成亮/暗两档
#   3. SSAO 降权：SSAO 是全屏后处理、在本 shader 之后乘上来，会把卡通硬边
#      重新糊成连续的接触阴影，所以调低写进 Out.SSAOColor 的 alpha
#
# 调风格改 MOE_TOON_* 那几个宏就行（明暗阈值/亮部增益/暗部压暗/暗部色偏/
# 高光阈值/高光强度/SSAO 强度），shader 是运行时编译的，**改完不需要重烘
# 任何动画**，同步 mod 即可。
#
# 整份复制的理由、不能套用 hair 双面 Effect 的理由、为什么必须量化光照而
# 不是最终颜色、SSAO 为什么只能从 alpha 侧控制、以及冻结副本的代价，
# 都写在 tools/build/build_moe_shader.py 的模块 docstring 里。
#
# 基于 CK3 {ver}。CK3 更新后重跑生成脚本。
#
'''

# 皮肤不接受投影。插在 GetSunLightingProperties 之后、任何用到
# _ShadowTerm 的地方之前。
NO_SHADOW_BODY = '''
			// ==================== 皮肤不接受投影（MOE_NO_SHADOW）====================
			// 阴影只通过 `LightingProps._ShadowTerm` 一个入口进来
			// （GetSunLightingProperties 里由 CalculateShadow 算出，随后传给
			//  CalculatePortraitLights 与 CalculatePortraitTranslucentLights）。
			// 1.0 的语义是「完全不被遮挡」，所以强制成 1 = 这个材质不接受
			// 任何投影。**必须放在所有读 _ShadowTerm 的调用之前**。
			//
			// 只有 portrait_skin 带这个宏：前发投在额头/眼睛/颧骨上的影子被
			// shadowmap_kernelscale=8 过滤糊成边界不清的灰绿脏块（实测芙洛伦
			// 右颊、下巴、鼻侧、嘴角都有）。二次元立绘里头发落在脸上的硬阴影
			// 是画师手绘的，不该靠实时 shadow map 生成。
			//
			// 衣服与头发**不加**这个宏，照常接受投影 —— 衣褶、衣领在脖子上的
			// 阴影、头发之间的层次都要留着，否则人物会变得很平。这是材质级
			// 宏相对光源级 `affected_by_shadow = no` 的全部意义。
			#ifdef MOE_NO_SHADOW
				LightingProps._ShadowTerm = 1.0f;
			#endif
			// ======================================================================
'''


TOON_PARAMS = '''		// ===================== 卡通化参数（MOE_TOON）=====================
		// 改这几个值就能调风格，不用碰下面的逻辑。
		#ifdef MOE_TOON
			// 明暗分界，单位是**光照强度**（不是最终颜色亮度）。
			// 大 = 暗部更多。三点布光在脸上的光照强度量级只有 0.1~0.5。
			//
			// **别用「脸部暗部面积占比」当判据**（2026-08-24 踩过）：我写过
			// tools/verify/measure_face_shading.py 扫脸部像素做亮度直方图、按面积比
			// 调这个值，结果阈值 0.18→0.08 时暗部面积反而 71%→81%。
			// 那不是 bug，是判据本身不成立 —— 洛茜的脸贴图**自带腮红**
			// （T_actor_wulfa_face_01_D.dds 实采 R−B +42，腮红处 +88），
			// 脸上的明暗大头来自贴图内容而不是二值化，所以那个占比根本不受
			// 阈值控制。**芙洛伦的脸贴图才是干净平涂**（(242,188,183)，
			// R−B +59 但无斑块），两个角色不能共用一套判据。
			//
			// 0.18 是按芙洛伦实测校准的：受光颊亮度 147.7、暗部 47~70，
			// 明暗比 2.6~3.1 倍且有硬边。调这个值要**分角色分别看**，
			// 并且只在「明暗交界线的位置」上判断，不看面积比。
			//
			// **2026-09-13 降到 0.16**：用户报洛茜鼻侧到嘴周一片难看的深褐硬边块
			// （黛烟同动作同光照下干净）。染色探针确认那是本阈值分出来的暗档 ——
			// 把 SHADOW_TINT 临时改成纯品红，那片色块变品红，脖子/胸口/手臂也一起变。
			// 洛茜脸的几何法线让鼻侧的 ToonKey 掉到阈值下方，黛烟整张脸都在上方：
			// 同参数同光照，差别全在模型几何，所以这是**交界线位置**问题。
			//
			// **不能降到补光值或更低**：补光 MOE_FACE_FILL_INTENSITY / MOE_SKIN_FILL_INTENSITY
			// 当前都是 0.05，阈值 ≤ 补光时补光单独就把全部像素垫过阈值、二值化静默失效
			// （自检有硬闸拦这个，见记忆 face-fill-above-toon-threshold-kills-banding）。
			// 注意：进 shader 的注释里**不许出现连续两个右方括号** —— 它会提前
			// 闭合 PDX 的 Code 块（那个块以 Code 加两个左方括号开头），从下一行起
			// 整个 shader 被当脚本解析（2026-09-13 踩过，硬闸见
			// _assert_no_pdx_block_end）。所以记忆双链在这里只写裸名字。
			// 想再往下走必须**同时降补光**，那会让背光侧的脸变暗。
			//
			// **2026-09-13 第二轮：0.16 → 0.12，补光同步 0.14 → 0.10**（方案 A）。
			// 0.16 那一版的实机是修完注释里的两个右方括号之后才第一次真正跑起来的
			// （见记忆 shader-comment-double-bracket-breaks-pdx-block）——
			// 在那之前 shader 根本没编译，参数从未生效。0.16 实测：洛茜鼻侧那片深褐
			// 从「鼻侧到嘴周一整片」缩到「鼻梁右侧一道细线 + 嘴角下方一小块」，
			// 方向对但交界线仍切过鼻子和嘴。**阈值与补光必须一起降**，单降阈值会
			// 撞上面那道硬闸。代价：补光低了，背光侧的脸会暗一些。
			//
			// **2026-09-13 第三轮：0.12 → 0.07，补光同步 0.10 → 0.05**（用户定）。
			// 0.12 + 0.10 实测洛茜鼻侧暗块已消失、只剩鼻尖下极淡过渡，用户要继续下探。
			// 注意这一档已经很低：暗档缩到只剩强背光部位，**赛璐璐硬边可能基本消失**、
			// 接近全平涂；补光只有初版 0.14 的三分之一，背光侧明显偏暗。
			// 若实机「太平」，往回走一档 0.10 + 0.08 大概是甜点区。
			//
			// 已排除、别重查：shadow map 投影（face/eye/skin 都已 MOE_NO_SHADOW）、
			// 法线与 properties 贴图（两人逐字节相同且都是常量小图）、脸 diffuse
			// 亮度（159.2 vs 154.1，几乎相同）、墨线（墨色近黑中性，掺不出 R-B +82.8
			// 的暖褐）、Effect 分配（两人结构一致）。
			// ==================== 平涂开关（MOE_TOON_FLAT）====================
			// 1 = 皮肤/脸/眼**不分档**，全部落亮档，没有明暗交界线；
			// 0 = 按 MOE_TOON_THRESHOLD 二值化（前三轮的行为）。
			//
			// 2026-09-13 用户定案开这个：交界线的位置由**几何法线**决定，逐角色不同
			// （洛茜鼻侧有、黛烟没有，同参数同光照），靠调阈值调不干净 ——
			// 0.18 → 0.16 → 0.12 三轮只是把线往轮廓外推，换个角色又冒出来。
			// 衣服/头发/附件不挂 MOE_TOON，走原版连续光照，阴影照旧。
			//
			// **别改成把 MOE_TOON_THRESHOLD 设 0 来实现**：ToonBand = 阈值 × 0.04，
			// 阈值 0 会让 smoothstep 上下界相等（除零，结果未定义）；而且自检硬闸
			// 要求补光 < 阈值，阈值 0 时任何正补光都过不去。
			#ifndef MOE_TOON_FLAT
				#define MOE_TOON_FLAT 1
			#endif
			#ifndef MOE_TOON_THRESHOLD
				#define MOE_TOON_THRESHOLD 0.07f
			#endif
			// 两档的**光照强度常量**。赛璐璐的实质是：同一档里的光照强度
			// 完全一样，塑形靠明暗交界线的形状，不靠档内渐变。
			//
			// **这两个必须是常量，不能写成「原光照 × 系数」**（2026-08-25 修）。
			// 上一版是 `lerp( ToonDiffLight * 0.88, ToonDiffLight * 1.10, edge )`
			// —— 两档都还乘着原始的连续光照、只差 ±11%，所以根本没有二值化，
			// 输出仍是原版渐变。表现就是「看不到硬边、脸部亮度直方图是单峰、
			// 阈值从 0.18 调到 0.08 几乎没变化」。我当时把这三个症状分别归因于
			// 贴图腮红、判据不成立、SSAO，全都没找到这个共同的真因。
			//
			// 比值 0.26/0.11 = 2.4 倍。**这两个常量就是最终光照强度的绝对值**
			// —— ToonHue 是归一化色相（亮度≈1），所以乘出来直接决定档位亮度，
			// 不是「相对原光照的系数」。这一点定错过两次值：
			//     0.45/0.18 → 用户「有一点太亮了」
			//     0.38/0.18 → 仍偏亮，实测暗档峰落在 120-140、P90 179，
			//                 而脸贴图本身亮度才 ~200 —— 阴影只压到 65%，
			//                 两档全堆在直方图上半部，整体发白
			// 判据是**暗档峰的绝对亮度**：要明显低于贴图自身亮度（~200）的
			// 一半，否则「阴影」看着不像阴影。别看两档比值，比值对了照样过亮。
			// 暗档取 0.18 而不是更低：二次元阴影是「有颜色的暗」不是欠曝，
			// 压太狠会把五官细节吃掉（上一版 SHADOW_MUL 0.5 就是这个毛病）。
			#ifndef MOE_TOON_LIT_LEVEL
				#define MOE_TOON_LIT_LEVEL 0.4f
			#endif
			#ifndef MOE_TOON_SHADOW_LEVEL
				#define MOE_TOON_SHADOW_LEVEL 0.045f   // 09-08 晚：0.11 用户「阴影灰、对比不够」，0.09 未装机即再压
			#endif
			// 场景亮度增益（2026-09-11，用户「脸部为何感觉很暗，受光照后亮度不变么」）。
			// 两档取常量 ⇒ 档位亮度与场景光脱钩：实测主菜单黛烟脸 65 / 同人大腿（原版连续光照）150 /
			// 原版国王脸 116。增益 = clamp( sqrt( ToonKey / REF ), MIN, MAX ) 让档位跟随该像素
			// 按原版光照算出的强度，同时把变化压平以保住硬边。
			// REF 0.18 = 明暗阈值：光照刚过阈值处增益≈1，即旧行为，所以基准不偏移。
			// MIN 0.70 防夜间/背光全黑，MAX 2.20 是把脸从 65 抬到接近原版 116 的上限（65×2.2/1.2≈119）。
			// 调亮整体优先动 LIT_LEVEL；只想改「随光变化的幅度」才动 MIN/MAX。
			#ifndef MOE_TOON_GAIN_REF
				#define MOE_TOON_GAIN_REF 0.18f
			#endif
			#ifndef MOE_TOON_GAIN_MIN
				#define MOE_TOON_GAIN_MIN 0.70f
			#endif
			#ifndef MOE_TOON_GAIN_MAX
				#define MOE_TOON_GAIN_MAX 2.20f
			#endif
			// 暗档提饱和（2026-09-08 用户「阴影颜色不够深，偏发灰」）。之前暗档 = 光照色相 × 常量 × 淡冷色，
			// 等于把固有色整体乘一个中性系数：亮度降了、饱和度原地不动，压暗的皮肤就是灰。二次元阴影
			// 是「同色相更饱和一档」——固有色按自身色相方向拉：albedo × lerp(1, albedo/luma(albedo), SAT)，
			// 除以亮度让这一步只改饱和不改亮度量级，深浅仍由 SHADOW_LEVEL 定。0 = 关。
			#ifndef MOE_TOON_SHADOW_SAT
				#define MOE_TOON_SHADOW_SAT 0.55f
			#endif
			// 暗部色偏（冷紫调）。纯灰度压暗会得到「欠曝」的脏感，
			// 往蓝紫偏一点才是动画片里的阴影。R 略降、B 略升。
			#ifndef MOE_TOON_SHADOW_TINT
				#define MOE_TOON_SHADOW_TINT float3( 0.90f, 0.93f, 1.12f )   // 09-08 冷偏加强：淡冷色压不住灰
			#endif
			// 高光的二值化阈值。镜面项单独切一档，否则脸上会留一片连续的
			// 油光渐变，跟硬边的漫反射打架，看着像两层贴图错位。
			#ifndef MOE_TOON_SPEC_THRESHOLD
				#define MOE_TOON_SPEC_THRESHOLD 0.22f
			#endif
			// 高光保留强度。二次元高光是一小块纯亮斑，不是大面积反光。
			// 0.65 → 0.45（2026-09-07 用户「反光过了头」；同轮还收了亮边与半透，三项都是加在两档之上的加法光）。
			#ifndef MOE_TOON_SPEC_MUL
				#define MOE_TOON_SPEC_MUL 0.45f
			#endif
			// SSAO 抵消强度。SSAO 是**全屏后处理**、在本 shader 之后乘上来，
			// 会把硬边重新糊成连续的接触阴影（眼窝/鼻侧/下巴的浑浊斑块就是
			// 它）。写进 SSAOColor 的 alpha 是它的作用强度，这里整体降权。
			// 0 = 完全关掉 SSAO，1 = 原版强度。**别设 0**：完全没有接触阴影
			// 时头发与脸、衣领与脖子会糊成一片，失去层次。
			#ifndef MOE_TOON_SSAO_MUL
				#define MOE_TOON_SSAO_MUL 0.35f
			#endif
		#endif
		#ifdef MOE_FACE_FILL
			// ---------------------- 面部补光（MOE_FACE_FILL）----------------------
			// **不能靠加第 4 个引擎光源来给脸补光**：`portrait_lighting.fxh` 写死
			// `#define LIGHT_COUNT 3`，而光源数据是 `Light_Position_Radius[3]` 等
			// **ConstantBuffer 数组**（portrait.shader:181-184），布局由引擎 C++ 侧
			// 填充。改成 [4] 只会把后面的 `DecalCount` 等字段偏移全推移、读到垃圾
			// 值，引擎并不会多传一盏灯。所以补光只能在像素着色器里自己加一项。
			//
			// 这盏灯**跟随相机**（半兰伯特），所以正对镜头的脸永远受光均匀，
			// 不会因为角色转身而忽明忽暗 —— 二次元立绘要的就是这个。
			// 半兰伯特（`NdotL * 0.5 + 0.5`）而不是 `saturate(NdotL)`：后者让
			// 侧脸直接掉进 0、边缘一圈死黑，前者给背光侧留一个下限。
			//
			// **补光强度必须低于卡通亮档阈值 MOE_TOON_THRESHOLD（0.18）**，否则
			// 二值化在带补光的材质上直接失效。2026-09-11 实测到这个坑：脸部补光
			// 一度设 0.26 > 0.18，补光单独就垫过了阈值 —— 脸上每个像素不管法线
			// 朝向都被判进亮档，暗档面积归零。表现是「脸没有二值化」：肤色亮度
			// 全挤在 109~200 的窄带里，两个「峰」只相距 14 级（168 / 182），
			// 那是连续渐变的噪声起伏不是分档；同一张图里补光 0.14 的腿部两档比
			// 1.98~2.61、暗档 55~79，分档清楚。用户 09-11「部分角色的脸没有二值化」。
			//
			// 0.26 当初是为了「让弱主光下的正脸挤进亮档」（09-11 早），那个目的
			// 现在由 ToonGain 承担（两档随场景光缩放），补光不必再兼这个职责，
			// 于是与身体统一到 0.14。**以后要调脸的亮度请动 MOE_TOON_LIT_LEVEL
			// 或 MOE_TOON_GAIN_MAX，不要再提补光** —— 提补光只会抬暗档、压平对比。
			#ifndef MOE_FACE_FILL_INTENSITY
				#define MOE_FACE_FILL_INTENSITY 0.4f
			#endif
			#ifndef MOE_SKIN_FILL_INTENSITY
				#define MOE_SKIN_FILL_INTENSITY 0.4f
			#endif
		#endif
		// =================================================================

'''

INK_PARAMS = """		// ===================== 内部墨线参数（MOE_INK）=====================
		// 法线与视线夹角接近 90° 的片元涂成墨色：鼻梁/下颌/衣褶/发束转折处出线，正对相机的
		// 平坦区不出线（那正是轮廓线的几何定义）。只挂在 moe 材质上，眼睛不加。
		// GUI 层的剪影描边（moe_gui_portrait.shader）负责外轮廓，这里只补内部线。
		#ifdef MOE_INK
			// N·V 低于这个值算边缘。0.22 ≈ 77°；调大线更粗、曲面缓的地方也出线。
			// 2026-09-07 实机（1.5 像素 / 0.22）用户「身体、脸部、大腿描边还是很不明显」→ 试 0.26 / 2.5 像素，
			// 09-08 用户「这版黑边太粗，刚才那种也略粗」→ 0.22 / 1.2 像素。「缺的是腿挡在尾巴前那种遮挡边」——
			// N·V 墨线只能画自身轮廓带，遮挡边要靠反向外壳（inverted hull），见 README。
			#ifndef MOE_INK_NDOTV
				#define MOE_INK_NDOTV 0.22f
			#endif
			// 过渡带半宽（smoothstep），给 MSAA 留抗锯齿余量。
			#ifndef MOE_INK_BAND
				#define MOE_INK_BAND 0.06f
			#endif
			// 线宽（屏幕像素）。固定 N·V 阈值下线宽∝曲率半径：腿/手指/发束这类细圆柱 N·V 从 0.22 到 0 只占
			// 半径的 2.4%，屏幕上不到 1 像素就被 MSAA 吃掉，脸这种大曲面却有 3~4 像素——用户 2026-09-07
			// 「脸、腿边缘描边没完全生效」。阈值按 fwidth(N·V)（N·V 每像素变化量）抬高 MOE_INK_PX 倍，
			// 线在屏幕上近似恒定 MOE_INK_PX 像素宽；fwidth 原版 alpha_to_coverage.fxh 就在肖像里用。
			#ifndef MOE_INK_PX
				#define MOE_INK_PX 1.2f
			#endif
			#ifndef MOE_INK_COLOR
				#define MOE_INK_COLOR float3( 0.06f, 0.05f, 0.06f )
			#endif
			// 墨线不透明度。1 = 纯墨色；略低让线底下透一点固有色，不发死。
			#ifndef MOE_INK_STRENGTH
				#define MOE_INK_STRENGTH 0.9f
			#endif
		#endif
"""
RIM_PARAMS = """		// ===================== 背光轮廓亮边参数（MOE_RIM）=====================
		// 暗档侧、N·V 低于阈值的边带加一档定色亮边（二次元立绘的轮廓光）。只在 toon 块内生效，
		// 用 ToonEdge 判暗档侧；墨线（MOE_INK 0.22）压在它更外圈，可见带约 0.22~0.42。
		#ifdef MOE_RIM
			#ifndef MOE_RIM_NDOTV
				#define MOE_RIM_NDOTV 0.42f
			#endif
			#ifndef MOE_RIM_SOFT
				#define MOE_RIM_SOFT 0.05f
			#endif
			// 亮边是加法光。0.12 = LIT_LEVEL 0.26 的 46%，实机暗档侧整圈发亮、用户「不自然的高亮」（2026-09-07），
			// 降到 0.04（15%）：只剩一线轮廓光。
			#ifndef MOE_RIM_INTENSITY
				#define MOE_RIM_INTENSITY 0.04f
			#endif
			#ifndef MOE_RIM_COLOR
				#define MOE_RIM_COLOR float3( 1.0f, 0.97f, 0.92f )
			#endif
		#endif
		// ===================== 皮肤半透的卡通化（MOE_TOON）=====================
		// 半透项原本在卡通化之后连续叠加。阈值/强度都是**光照强度**量级。
		// LEVEL 0.08 加在暗档 0.11 上等于把皮肤暗部亮了 70%，实机脸上一片浮亮（2026-09-07 用户反馈），降到 0.03。
		#ifdef MOE_TOON
			#ifndef MOE_TOON_TRANS_THRESHOLD
				#define MOE_TOON_TRANS_THRESHOLD 0.06f
			#endif
			#ifndef MOE_TOON_TRANS_LEVEL
				#define MOE_TOON_TRANS_LEVEL 0.03f
			#endif
		#endif
"""
TOON_PARAMS = TOON_PARAMS + INK_PARAMS + RIM_PARAMS

KEY_ROTATE_FN = """		// SceneLum 可用标记：本块含 MoeCalculatePortraitLights,
		// 它累加 SceneLum（场景光强，无法线项）。court_scene 不注入本块,
		// 所以那一侧没有这个宏、平涂的 ToonGain 回退常量 1.0。
		#define MOE_HAS_SCENE_LUM 1
		// ============ 主光的方向与强度参数 ============
		// **必须放在 `#ifdef MOE_TOON` 外面。** 这些参数服务的是下面的
		// 布光函数，和卡通化无关。曾经写在 TOON_PARAMS 里（那整块被
		// `#ifdef MOE_TOON` 包着），结果不带 MOE_TOON 的 35 个 Effect
		// 编译时宏不存在：
		//     error X3004: undeclared identifier 'MOE_KEY_SIDE_DEG'
		//     error X3013: 'radians': no matching 1 parameter intrinsic
		// 一个 Effect 编译失败 = **整份 shader 作废**，画面退回默认渲染。
		//
		// 本地自检查不出这类问题 —— 它只看文本（宏在不在、有没有被引用），
		// 不做条件编译的作用域分析。宏定义在 A 块、使用在 B 块，两处文本
		// 都在，判据全绿。**只有游戏日志能发现**（同类：骨数不匹配）。
		// ------------------ 自定义三盏灯的方向（MOE_LIGHT_DIRS）------------------
		// **不搬光源，直接构造方向向量**。前三版都在「把原版光源搬到新位置」
		// 上翻车：光源位置是世界坐标、`look_at_node = camera_torso_look_at`
		// （目标是躯干节点不是原点）、`cylindrical` 的角度符号未知，
		// 每项都要实测标定，错一处整个角色就只剩环境光。实测记录：
		//     符号 A + 旋 30°  →  脸左右差  -9.5（右亮），偏移没抵消
		//     符号 B + 旋 30°  →  脸左右差 +16.0（左亮），偏到另一侧
		// 两次都过冲。而 `GGXSpotLight` 最终只用 `_ToLightDir`，
		// 所以直接给方向就绕开了全部位置计算。
		//
		// 约定：**-Z 是角色正前方**（朝相机那侧），+Y 向上。
		// 这个前后符号仍需实测确认，但只剩一个量要标，且判据很直接：
		// 主光从下往上时下巴/鼻下变亮而额头变暗 —— 底光特征一眼可辨。
		//
		// 主光从下往上的程度。0 = 水平正前方，1 = 完全从正下方。
		// 从下往上是舞台底光/恐怖片打光，会让鼻孔、下巴、眉骨投出向上的
		// 阴影。
		//
		// **方向映射是反直觉的：这个值越大，光源位置越低。** 它是「从下往上
		// 照的程度」，不是高度 —— 所以「抬高光源」要**减小**它。
		// 负值 = 光源在角色上方、从上往下照（公式支持，向量归一化后照样成立）。
		//
		// 调整记录：0.75 用户反馈「太仰视」→ 0.45 →「抬高一点点」→ 0.35。
		// 2026-09-01 第一次抬高约 10 cm：导出身高 149.98 对应画面约 170 cm，
		// 标准主光到 torso 目标约 194 单位；10 cm≈8.82 单位，换算后
		// UP_MIX 约 0.328，取 0.33。2026-09-02 用户确认有效后再抬约
		// 10 cm，沿同一标定步长降到 0.31。0 = 水平正前。
		// ---------------- 主光是否恢复原版（MOE_KEY_VANILLA）----------------
		// 09-08 用户看了原版打光：「脸部阴影太重，挪到脸的正前方看看」→ 开关回 0，UP_MIX 0 / SIDE 0 / 强度 1.0
		// = 正前方水平直射（LightDirTo = (0,0,1)）。两档卡通化下正前光会让阴影只剩掠射角一线。
		// 1 = 主光与两盏次光一样，按引擎传入的位置/类型走原版分支（build_moe_lighting.py 的布光配置
		//     已与原版逐字一致，所以这就是原版打光）；下面的方向重构与强度缩放不参与。
		// 0 = 走下面的「正前方偏底光」方向重构方案。
		// 2026-09-07 用户：底光方案是在卡通化 shader 没做对时用来掩盖问题的，现在两档常量 + 墙线都对了，
		// 不合适位置的光源反而起反效果 → 恢复原版。参数保留是为了能 A/B 回去。
		#ifndef MOE_KEY_VANILLA
			#define MOE_KEY_VANILLA 1
		#endif
		#ifndef MOE_KEY_UP_MIX
			#define MOE_KEY_UP_MIX 0.0f
		#endif
		// 主光的水平偏角（度）。0 = 正前方，正数 = 偏向角色**右侧**、
		// 负数 = 偏向左侧。10 ≈ 略微侧光，能给鼻梁和一侧脸颊带一点结构，
		// 又不至于让另半边脸掉进暗部。
		//
		// **左右符号没有单独实机验过**：认可过的对称布光左右对调看不出差别，
		// 「都在右侧」那版用户也没评价照到哪一侧。若实机看着偏错方向，
		// 把这个值改成负数即可 —— 别去动向量里 -sin 的符号，那是和次光
		// 共用的约定（方位角 0=正前、90=正右）。
		#ifndef MOE_KEY_SIDE_DEG
			#define MOE_KEY_SIDE_DEG 0.0f
		#endif
		// 主光强度的缩放系数。1.0 = 原版强度（hsv 的 v=1.5）。
		//
		// **注意这个参数在卡通化材质上不改变亮度，只改变暗部面积。**
		// ToonHue 把光照归一化成亮度≈1 的色相，最终亮度只由
		// MOE_TOON_LIT_LEVEL / MOE_TOON_SHADOW_LEVEL 决定。主光强度降下去：
		//     ToonKey 变小 → 更多像素掉到阈值下方 → **暗部面积变大**
		//     亮部亮度 = ToonHue * LIT_LEVEL，**一点不变**
		// 所以它的实际作用是「主光照射范围收窄」。要整体调暗得改 LIT_LEVEL。
		//
		// 镜面项（ToonSpec）没做归一化，所以高光是真的会跟着变弱。
		#ifndef MOE_KEY_INTENSITY_MUL
			#define MOE_KEY_INTENSITY_MUL 1.0f
		#endif
		// 两盏次光（i == 1 / 2）**完全不动**，沿用原版的轮廓光：
		//     右侧 spot   cylindrical{160 200 -120}   暖色，勾右边缘
		//     左侧 直射光 cylindrical{200 100 -160}   冷色，勾左边缘
		// 两盏都在角色**背后**，作用是勾边把角色从背景里分出来。
		// 中途试过搬到「左前 / 右前」，也试过「都放右侧」，用户 2026-08-25
		// 决定回到原版这两盏不动 —— 所以这里不再有次光参数。
		// ============ 主光旋向正面：CalculatePortraitLights 的替换版 ============
		// **不能改 `jomini/portrait_lighting.fxh`** —— 那是 include 的共享文件，
		// 改了原版所有角色一起变。所以在这里重写一份同逻辑的函数，只在主光
		// （i == 0）上多做一步绕 Y 轴旋转，再把调用点换成这个版本。
		//
		// 普通肖像在局部原点附近，但 Royal Court 是完整 3D 世界。引擎给本
		// shader 的肖像灯位置仍按角色局部空间表达；拿它减 WorldSpacePos 会把
		// 距离放大到场景坐标量级，CalcLightFalloff 随即把主光压到 0。实机
		// 表现就是普通肖像有光、王座厅只剩 cubemap 底色。
		//
		// 主光因此直接按方向走 CalculateLightingFromLight，不再读取位置、距离
		// 或聚光锥。旧实现本来就逐像素把光源搬到同一方向并让锥精确指回当前
		// 像素，等价于方向光，只多了一层易受坐标空间影响的距离衰减。两盏
		// 轮廓光仍逐字沿用原版三种光源分支。
		void MoeCalculatePortraitLights( float3 WorldSpacePos, float ShadowTerm, SMaterialProperties MaterialProps, inout float3 DiffuseLightOut, inout float3 SpecularLightOut, out float SceneLumOut )
		{
			// 场景光强累加器：**只含强度 × 距离衰减 × 聚光锥，不含任何法线项**。
			// 平涂模式用它当整体亮度系数，所以绝不能掺入 N·L —— 掺了等于把明暗
			// 交界线放回脸上，而那正是 MOE_TOON_FLAT 要去掉的东西。
			SceneLumOut = 0.0f;
			for( int i = 0; i < LIGHT_COUNT; ++i )
			{
				float3 DiffuseLight = vec3( 0.0f );
				float3 SpecularLight = vec3( 0.0f );

				float4 Color_Falloff = Light_Color_Falloff[ i ];
				float LightShadowTerm = Light_InnerCone_OuterCone_AffectedByShadows[ i ].z > 0.5f ? ShadowTerm : 1.0f;

				float4 PosRadius = Light_Position_Radius[ i ];
				float3 ConeDir = Light_Direction_Type[ i ].xyz;

				// ---- 只改主光（i == 0）的方向，两盏次光完全不动 ----
				// 次光是原版的轮廓光，在角色背后勾边（见参数块）。
				// MOE_KEY_VANILLA 为 1 时主光也落到下面的原版分支（默认，2026-09-07 起）。
				//
				// 主光直接构造「从光源指向角色」的方向。主光计算必须留在
				// i == 0 分支；两盏轮廓光继续读取引擎传入的位置与类型。
				if( i == 0 && MOE_KEY_VANILLA == 0 )
				{
					// 正前方 + 从下往上。光源在下方，所以「指向角色」的方向是
					// 向上偏后。UP_MIX 越大越像舞台底光。
					// 仰角由 Y 与水平模长的比值决定，所以偏角只能在**水平面内**转：
					// X/Z 各乘同一个 KeyHoriz。若直接写 (-sin, UP, cos)，水平模长会从
					// 1-UP 变成 1，仰角被悄悄压平 —— 用户刚调好的 0.45 就失效了。
					// 偏角为 0 时下面这个向量与「(0, UP, 1-UP)」逐位相同。
					float KeyAz = radians( MOE_KEY_SIDE_DEG );
					float KeyHoriz = 1.0f - MOE_KEY_UP_MIX;
					float3 LightDirTo = normalize( float3(
						-sin( KeyAz ) * KeyHoriz,
						MOE_KEY_UP_MIX,
						cos( KeyAz ) * KeyHoriz ) );

					// 主光强度缩放。**只乘 rgb，不能碰 alpha** —— alpha 是 falloff
					// 参数，参与 CalcLightFalloff 的距离衰减曲线，乘上去会把衰减
					// 半径一起改掉。主光的直接计算从 Color_Falloff.rgb 取强度，
					// 所以只缩放 rgb。
					Color_Falloff.rgb *= MOE_KEY_INTENSITY_MUL;

					SLightingProperties KeyLightingProps;
					KeyLightingProps._ToCameraDir = normalize( CameraPosition - WorldSpacePos );
					KeyLightingProps._ToLightDir = -LightDirTo;
					KeyLightingProps._LightIntensity = Color_Falloff.rgb;
					KeyLightingProps._ShadowTerm = LightShadowTerm;
					KeyLightingProps._CubemapIntensity = 0.0f;
					KeyLightingProps._CubemapYRotation = Float4x4Identity();
					CalculateLightingFromLight( MaterialProps, KeyLightingProps, DiffuseLight, SpecularLight );
					// 方向光无距离概念，衰减恒 1；强度已乘过 MOE_KEY_INTENSITY_MUL
					SceneLumOut += dot( Color_Falloff.rgb, float3( 0.2126f, 0.7152f, 0.0722f ) );
				}

				else if( Light_Direction_Type[ i ].w == LIGHT_TYPE_SPOTLIGHT )
				{
					float InnerAngle = Light_InnerCone_OuterCone_AffectedByShadows[ i ].x;
					float OuterAngle = Light_InnerCone_OuterCone_AffectedByShadows[ i ].y;
					SPortraitSpotLight Spot = GetPortraitSpotLight( PosRadius, Color_Falloff, ConeDir, InnerAngle, OuterAngle );
					GGXSpotLight( Spot, WorldSpacePos, LightShadowTerm, MaterialProps, DiffuseLight, SpecularLight );
					// 与 GGXSpotLight 内部逐字一致的衰减与锥，但**不取 N·L**
					{
						float3 SpotToLight = Spot._PointLight._Position - WorldSpacePos;
						float SpotDist = length( SpotToLight );
						float SpotAtten = CalcLightFalloff( Spot._PointLight._Radius, SpotDist, Spot._PointLight._Falloff );
						float SpotPdotL = dot( -( SpotToLight / max( SpotDist, 0.0001f ) ), Spot._ConeDirection );
						SpotAtten *= smoothstep( Spot._ConeOuterCosAngle, Spot._ConeInnerCosAngle, SpotPdotL );
						SceneLumOut += dot( Color_Falloff.rgb, float3( 0.2126f, 0.7152f, 0.0722f ) ) * SpotAtten;
					}
				}
				else if( Light_Direction_Type[ i ].w == LIGHT_TYPE_POINTLIGHT )
				{
					SPortraitPointLight Light = GetPortraitPointLight( PosRadius, Color_Falloff );
					GGXPointLight( Light, WorldSpacePos, LightShadowTerm, MaterialProps, DiffuseLight, SpecularLight );
					{
						float PointDist = length( Light._Position - WorldSpacePos );
						float PointAtten = CalcLightFalloff( Light._Radius, PointDist, Light._Falloff );
						SceneLumOut += dot( Color_Falloff.rgb, float3( 0.2126f, 0.7152f, 0.0722f ) ) * PointAtten;
					}
				}
				else if( Light_Direction_Type[ i ].w == LIGHT_TYPE_DIRECTIONAL )
				{
					SLightingProperties LightingProps;
					LightingProps._ToCameraDir = normalize( CameraPosition - WorldSpacePos );
					LightingProps._ToLightDir = -ConeDir;
					LightingProps._LightIntensity = Color_Falloff.rgb;
					LightingProps._ShadowTerm = LightShadowTerm;
					LightingProps._CubemapIntensity = 0.0f;
					LightingProps._CubemapYRotation = Float4x4Identity();

					CalculateLightingFromLight( MaterialProps, LightingProps, DiffuseLight, SpecularLight );
					SceneLumOut += dot( Color_Falloff.rgb, float3( 0.2126f, 0.7152f, 0.0722f ) );
				}

				DiffuseLightOut += DiffuseLight;
				SpecularLightOut += SpecularLight;
			}
		}
		// ======================================================================

"""

COURT_SCENE_LUM_FN = '''		// ============ 王座厅的场景光强（MOE_TOON_FLAT 用）============
		// 与 portrait 侧的 SceneLum 同一用途：给平涂模式一个「这个场景多亮」的标量,
		// **只含强度 × 距离衰减 × 角衰减，不含任何法线项** —— 掺入法线就等于把明暗
		// 交界线放回脸上。衰减只依赖位置，整张脸的世界坐标差几个单位、衰减值几乎
		// 恒定，所以给出的是平滑的整体亮度。
		//
		// 逐字复用 court 自己的衰减（CalculatePointLightAttenuation / GetAngleAttenuation
		// / RemapClamped），**不套用 portrait 那份**：两侧的 ConstantBuffer 名字、
		// 数组大小、衰减函数、强度量级（court 多一个 ×1000）全都不同。
		//
		// LIGHT_TYPE_DISC 原版走 CalcDiscDiffuse，那个函数**吃法线**，所以这里只取
		// 强度与角衰减、丢掉它。
		float MoeCourtSceneLum( float3 WorldSpacePos )
		{
			float Lum = 0.0f;
			for( int i = 0; i < CurrentLightCount; ++i )
			{
				float3 PosToLight = Light_Position_Radius[i].xyz - WorldSpacePos;
				float ScaledRadius = Light_Position_Radius[i].w;
				float BaseLum = dot( Light_Color_Intensity[i].xyz, float3( 0.2126f, 0.7152f, 0.0722f ) )
								* Light_Color_Intensity[i].w;

				if( Light_Direction_Type[i].w == LIGHT_TYPE_SPOTLIGHT )
				{
					float3 LightDirection = normalize( Light_Direction_Type[i].xyz );
					float Attenuation = CalculatePointLightAttenuation( PosToLight, ScaledRadius );
					float InnerAngle = RemapClamped( Light_InnerCone_OuterCone_ShadowToUse[i].x, 0.0f, 1.0f, 0.0f, PI / 2.0f );
					float OuterAngle = RemapClamped( Light_InnerCone_OuterCone_ShadowToUse[i].y, 0.0f, 1.0f, 0.0f, PI / 2.0f );
					float AngleAtten = GetAngleAttenuation( normalize( PosToLight ), -LightDirection, InnerAngle, OuterAngle );
					Lum += BaseLum * 1000.0f * Attenuation * AngleAtten;
				}
				else if( Light_Direction_Type[i].w == LIGHT_TYPE_POINTLIGHT )
				{
					float Attenuation = CalculatePointLightAttenuation( PosToLight, ScaledRadius );
					Lum += BaseLum * 1000.0f * Attenuation;
				}
				else if( Light_Direction_Type[i].w == LIGHT_TYPE_DIRECTIONAL )
				{
					// 方向光无距离衰减；注意原版这支用的是 ColorIntensity.rgb（不乘 .w 也不乘 1000）
					Lum += dot( Light_Color_Intensity[i].xyz, float3( 0.2126f, 0.7152f, 0.0722f ) );
				}
				else if( Light_Direction_Type[i].w == LIGHT_TYPE_DISC )
				{
					// 只取强度与角衰减 —— CalcDiscDiffuse 吃法线，不能用
					float3 LightDirection = normalize( Light_Direction_Type[i].xyz );
					float InnerAngle = RemapClamped( Light_InnerCone_OuterCone_ShadowToUse[i].x, 0.0f, 1.0f, 0.0f, PI );
					float OuterAngle = RemapClamped( Light_InnerCone_OuterCone_ShadowToUse[i].y, 0.0f, 1.0f, 0.0f, PI );
					float AngleAtten = GetAngleAttenuation( normalize( PosToLight ), -LightDirection, InnerAngle, OuterAngle );
					Lum += BaseLum * AngleAtten;
				}
			}
			return Lum;
		}
'''

FILL_BODY = '''
			// ==================== 面部补光（MOE_FACE_FILL）====================
			// 必须加在 Color 组装**之前**、卡通化之前 —— 补光要一起参与二值化，
			// 否则它会在两档之上再叠一层连续渐变，把刚做出来的硬边糊回去。
			//
			// 为什么不用引擎光源：LIGHT_COUNT 硬编码 3，光源数组在
			// ConstantBuffer 里由 C++ 侧填充，加不了第 4 盏（见参数块注释）。
			#ifdef MOE_FACE_FILL
			{
				float3 FillDir = normalize( CameraPosition - Input.WorldSpacePos );
				// 半兰伯特：背光侧留下限，避免侧脸边缘一圈死黑
				float FillN = dot( Normal, FillDir ) * 0.5f + 0.5f;
				float FillIntensity = MOE_SKIN_FILL_INTENSITY;
				#ifdef MOE_TOON
					FillIntensity = MOE_FACE_FILL_INTENSITY;
				#endif
				float3 FillLighting = max( DiffuseLight + DiffuseIBL, vec3( 0.0f ) )
					/ max( MaterialProps._DiffuseColor, vec3( 0.0001f ) );
				float FillLuma = dot( FillLighting, float3( 0.2126f, 0.7152f, 0.0722f ) );
				float3 FillColor = FillLuma > 0.0001f ? FillLighting / FillLuma : vec3( 1.0f );
				// 补光在平涂模式下也要跟随场景光，否则这份固定底光会在暗场景把脸
				// 垫平、破坏「整体变亮变暗」的效果（补光 0.4 实际贡献 0.2~0.4，
				// 与 LIT_LEVEL 0.4 同量级，不跟随的话暗处会显得脸在发光）。
				float FillSceneMul = 1.0f;
				#if defined( MOE_TOON_FLAT ) && defined( MOE_HAS_SCENE_LUM )
					#if MOE_TOON_FLAT
						#ifdef MOE_COURT_GAIN_REF
							FillSceneMul = clamp( sqrt( max( MoeSceneLum, 0.0f ) / MOE_COURT_GAIN_REF ),
												  MOE_TOON_GAIN_MIN, MOE_TOON_GAIN_MAX );
						#else
							FillSceneMul = clamp( sqrt( max( MoeSceneLum, 0.0f ) / MOE_TOON_GAIN_REF ),
												  MOE_TOON_GAIN_MIN, MOE_TOON_GAIN_MAX );
						#endif
					#endif
				#endif
				DiffuseLight += MaterialProps._DiffuseColor * FillColor
								* ( FillN * FillIntensity * FillSceneMul );
			}
			#endif
'''

TOON_BODY = '''
			// ============================ 卡通化（MOE_TOON）============================
			// 二值硬边：把连续的光照渐变切成「亮 / 暗」两档，明暗交界是一条硬边。
			//
			// **量化的对象是光照强度，不是最终颜色**（2026-08-24 修）。
			// 引擎的每一项都形如「光照 × 反射率」：
			//     DiffuseLight = DiffuseBRDF * _DiffuseColor * LightIntensity * NdotL
			//     DiffuseIBL   = DiffuseRad  * _DiffuseColor
			// （见 clausewitz/gfx/FX/cw/lighting.fxh 的 CalculateLightingFromLight
			//   与 CalculateLightingFromIBL）
			// 所以除掉 _DiffuseColor 就得到纯光照强度。第一版直接在 Color 上切，
			// 等于把反射率一起量化了 —— 同样的受光强度，亮皮肤和深色布料会落在
			// 阈值的两侧，阈值在不同材质上语义不同，根本调不出一致的明暗线。
			//
			// 为什么不自己算 NdotL：肖像是引擎配置的三点布光（LIGHT_COUNT 3，
			// 聚光/点光/平行光混合，见 portrait_lighting.fxh），自己拿单一方向
			// 算会把整套布光丢掉、脸的受光方向直接错。取引擎**已经算好**的
			// 漫反射光照做阈值，布光方向就被完整保留。
			#ifdef MOE_TOON
			{
				// 除掉反射率，得到纯漫反射光照强度。
				// 分母加下限防止黑色材质（_DiffuseColor≈0）除爆。
				float3 ToonAlbedo = max( MaterialProps._DiffuseColor, vec3( 0.04f ) );
				float3 ToonDiffLight = ( DiffuseLight + DiffuseIBL ) / ToonAlbedo;

				// 亮度取感知权重（Rec.709）。用平均值会让偏色光源下的
				// 明暗线偏移到错误的位置。
				float ToonKey = dot( ToonDiffLight, float3( 0.2126f, 0.7152f, 0.0722f ) );

				// 硬边：用 smoothstep 留极窄的过渡带而不是 step。
				// 纯 step 在 MSAA 下边缘会出锯齿与闪烁；这个宽度视觉上仍是硬边，
				// 但抗锯齿能正常工作。宽度取阈值的 4%，让它随阈值等比缩放。
				float ToonBand = MOE_TOON_THRESHOLD * 0.04f;
				float ToonEdge = smoothstep( MOE_TOON_THRESHOLD - ToonBand,
											 MOE_TOON_THRESHOLD + ToonBand, ToonKey );
				#if MOE_TOON_FLAT
					// 平涂：全部像素落亮档。下面的 lerp 因此恒取 ToonLitLight × ToonAlbedo,
					// SHADOW_LEVEL / SHADOW_SAT / SHADOW_TINT 都不再参与（留着是为了
					// 关掉 MOE_TOON_FLAT 就能一行切回二值化）。
					// 连带：MOE_RIM 乘 (1 - ToonEdge) 自动归零；墨线与高光不受影响。
					ToonEdge = 1.0f;
				#endif

				// 二值化后的**光照**，再乘回反射率。这样贴图的花纹与肤色
				// 完整保留，只有明暗被切成两档 —— 这才是赛璐璐。
				//
				// **两档取常量光照，不能保留原始渐变**（见参数块的长注释）：
				// 写成 `ToonDiffLight * 系数` 等于没做二值化，只是整体缩放。
				// 档内还带着原光照的色彩关系（偏色光源下阴影不会变成纯灰），
				// 所以用 normalize 后的色相乘常量强度，而不是直接给灰度。
				float3 ToonHue = ToonDiffLight / max( ToonKey, 0.0001f );
				// 场景亮度增益（2026-09-11，用户「脸部为何感觉很暗，受光照后亮度不变么？
				// 能再叠一个环境光亮度吗」）。两档取常量的代价是**亮度与场景光完全脱钩**：
				// 实测主菜单黛烟脸亮度 65，同一个人走原版连续光照的大腿 150（2.3 倍），
				// 原版国王脸 116 —— 脸暗和「丝袜/绝对领域色差」是同一个根因。
				// 修法不是抬死数字，而是让档位亮度跟随**这个像素按原版光照算出的亮度**
				// （ToonKey，已除掉固有色 = 纯光照强度）：
				//   增益 = clamp( sqrt( ToonKey / 参考 ), 下限, 上限 )
				// 开平方压缩动态范围（暗处不全黑、亮处不过曝）；夹上下限保证增益在角色身上
				// 变化平缓，**硬边不会被糊回渐变** —— 直接用 ToonKey 线性缩放就等于取消二值化。
				float ToonGain = clamp( sqrt( max( ToonKey, 0.0f ) / MOE_TOON_GAIN_REF ),
										MOE_TOON_GAIN_MIN, MOE_TOON_GAIN_MAX );
				#if MOE_TOON_FLAT
					// 平涂：增益不能读 ToonKey（逐像素法线的函数）—— 那 0.70~2.20 共
					// 3.1 倍的跨度会让脸上照旧有连续明暗，只是没有硬边。
					// 2026-09-13 用户实机「平涂了还有脸部阴影」就是这一项。
					//
					// 改读 **SceneLum**：三盏灯的强度 × 距离衰减 × 聚光锥求和，
					// **不含任何法线项**。衰减只依赖位置，整张脸的世界坐标差几个单位、
					// 衰减值几乎恒定 —— 所以它给出平滑的整体亮度（宫殿亮、暗处暗），
					// 绝不会造出明暗交界线。这就是「整体变亮、整体变暗」。
					//
					// court_scene 侧不注入 MoeCalculatePortraitLights（走原版光照），
					// 拿不到 SceneLum，所以按宏回退常量 1.0 —— 那一侧亮度不跟随场景光。
					#ifdef MOE_HAS_SCENE_LUM
						// court 的光强量级与 portrait 差着一个 ×1000，所以两侧各用自己的
						// 基准：court 走 MOE_COURT_GAIN_REF，portrait 走 MOE_TOON_GAIN_REF。
						#ifdef MOE_COURT_GAIN_REF
							ToonGain = clamp( sqrt( max( MoeSceneLum, 0.0f ) / MOE_COURT_GAIN_REF ),
											  MOE_TOON_GAIN_MIN, MOE_TOON_GAIN_MAX );
						#else
							ToonGain = clamp( sqrt( max( MoeSceneLum, 0.0f ) / MOE_TOON_GAIN_REF ),
											  MOE_TOON_GAIN_MIN, MOE_TOON_GAIN_MAX );
						#endif
					#else
						ToonGain = 1.0f;
					#endif
				#endif
				float3 ToonShadowLight = ToonHue * MOE_TOON_SHADOW_LEVEL * ToonGain
										 * MOE_TOON_SHADOW_TINT;
				float3 ToonLitLight    = ToonHue * MOE_TOON_LIT_LEVEL * ToonGain;
				// 暗档的固有色按自身色相提饱和（见 MOE_TOON_SHADOW_SAT 注释）：分母是固有色亮度，
				// 所以这一步不改亮度量级；亮档固有色原样。
				float ToonAlbedoLum = max( dot( ToonAlbedo, float3( 0.2126f, 0.7152f, 0.0722f ) ), 0.02f );
				float3 ToonShadowAlbedo = ToonAlbedo * lerp( vec3( 1.0f ), ToonAlbedo / ToonAlbedoLum, MOE_TOON_SHADOW_SAT );
				float3 ToonDiffOut = lerp( ToonShadowLight * ToonShadowAlbedo,
										   ToonLitLight * ToonAlbedo, ToonEdge );

				// 镜面项单独切一档。不切的话脸上会留一片连续的油光渐变，
				// 跟硬边的漫反射打架，看着像两层贴图错位。
				float3 ToonSpec = SpecularLight + SpecularIBL;
				float ToonSpecLum = dot( ToonSpec, float3( 0.2126f, 0.7152f, 0.0722f ) );
				float ToonSpecEdge = smoothstep( MOE_TOON_SPEC_THRESHOLD * 0.96f,
												 MOE_TOON_SPEC_THRESHOLD * 1.04f,
												 ToonSpecLum );
				ToonSpec *= ToonSpecEdge * MOE_TOON_SPEC_MUL;

				Color = ToonDiffOut + ToonSpec;
				// ==================== 背光轮廓亮边（MOE_RIM）====================
				// 暗档侧（1-ToonEdge）× N·V 边带 × 常量色，加法叠在两档之上；墨线在它之后压最外圈。
				#ifdef MOE_RIM
				{
					float3 RimView = normalize( CameraPosition - Input.WorldSpacePos );
					float RimNdotV = abs( dot( Normal, RimView ) );
					float RimBand = 1.0f - smoothstep( MOE_RIM_NDOTV - MOE_RIM_SOFT, MOE_RIM_NDOTV + MOE_RIM_SOFT, RimNdotV );
					Color += MOE_RIM_COLOR * ( MOE_RIM_INTENSITY * RimBand * ( 1.0f - ToonEdge ) );
				}
				#endif
			}
			#endif
			// ==================== 内部墨线（MOE_INK）====================
			// 放在卡通化之后、次表面/半透之前：线要压在二值化后的两档之上，不能被两档再切一次。
			#ifdef MOE_INK
			{
				float3 InkView = normalize( CameraPosition - Input.WorldSpacePos );
				// 双面材质背面法线已翻转（DOUBLE_SIDED_ENABLED）；再取 abs 防止残留的背向法线把整面涂黑。
				float InkNdotV = abs( dot( Normal, InkView ) );
				// 阈值随屏幕空间变化率抬高：细圆柱上 N·V 一像素就掉一大截，阈值跟着抬，线宽恒定 MOE_INK_PX 像素
				float InkThr = MOE_INK_NDOTV + MOE_INK_PX * fwidth( InkNdotV );
				float InkEdge = smoothstep( InkThr - MOE_INK_BAND, InkThr + MOE_INK_BAND, InkNdotV );
				Color = lerp( MOE_INK_COLOR, Color, lerp( 1.0f - MOE_INK_STRENGTH, 1.0f, InkEdge ) );
			}
			#endif
			// ========================== 卡通化结束 ==========================
'''


def _assert_no_pdx_block_end(text, what):
    """注入的注释里出现 `]]` 会提前闭合 PDX 的 `Code [[ ... ]]` 块。

    2026-09-13 踩过：阈值注释里引了记忆双链
    `[[face-fill-above-toon-threshold-kills-banding]]`，那个 `]]` 让引擎从下一行
    起把 HLSL 当 PDX 脚本解析，error.log 4.4 MB、全是
    `Unexpected token: <我写的中文注释>`。**HLSL 编译器根本没跑到**，写出的
    `.scache` 全是空的 —— 症状与缓存损坏一模一样（`entrypoint not found` 4136 次、
    `Failed creating shader state` 4136 次），但清 shadercache 无效、每次重现且
    日志字节数逐字节相同。「逐字节相同」就是区分二者的判据。

    只查注释：HLSL 里 `]]` 本身合法（如 `a[b[i]]`），全文禁会误伤。
    """
    # A BOM anywhere but byte 0 breaks the PDX parser for the whole file, and the
    # symptom (thousands of "Failed creating shader state") looks like cache damage.
    if "﻿" in text:
        raise SystemExit(f"[shader] {what}：文件中间含 BOM（U+FEFF），源文件要用 utf-8-sig 读")
    bad = []
    for i, line in enumerate(text.split(chr(10)), 1):
        c = line.find("//")
        if c >= 0 and "]]" in line[c:]:
            bad.append((i, line.strip()[:90]))
    if bad:
        for i, ln in bad:
            print(f"[shader] **{what} 第 {i} 行注释含 `]]`**：{ln}", file=sys.stderr)
        raise SystemExit(f"[shader] {what}：注释里的 `]]` 会提前闭合 Code [[ ]] 块，"
                         f"改成单方括号或去掉")


def effect_block_pattern(name):
    return re.compile(
        r"(^Effect[ \t]+" + re.escape(name) + r"[ \t]*\r?\n\{)(.*?)(^\})",
        re.S | re.M)


def duplicate_effect_aliases(src):
    """Add Moe-only render, shadow, and court-selection effect aliases."""
    added = []
    for base, alias in EFFECT_ALIASES.items():
        for suffix in ("", "Shadow", "_selection"):
            source_name = ("portrait_skin" if base == "portrait_face" else base) + suffix
            alias_name = alias + suffix
            if effect_block_pattern(alias_name).search(src):
                continue
            match = effect_block_pattern(source_name).search(src)
            if not match:
                continue
            block = match.group(0)
            alias_block = re.sub(
                r"^Effect[ \t]+" + re.escape(source_name) + r"(?=[ \t]*$)",
                f"Effect {alias_name}", block, count=1, flags=re.M)
            src = src[:match.end()] + "\n\n" + alias_block + src[match.end():]
            added.append(alias_name)
    return src, added


def add_define(src, eff, macro):
    """Add a compile define to one exact Effect block."""
    pat = effect_block_pattern(eff)
    match = pat.search(src)
    if not match:
        return src, "missing"
    body = match.group(2)
    if macro in body:
        return src, "have"
    defines = re.search(r"^\tDefines = \{(.*?)\}[ \t]*$", body, re.M)
    if defines:
        newbody = (body[:defines.start()]
                   + '\tDefines = {' + defines.group(1).rstrip()
                   + f' "{macro}" }}'
                   + body[defines.end():])
    else:
        newbody = (body.rstrip("\r\n")
                   + f'\n\tDefines = {{ "{macro}" }}\n')
    return src[:match.start(2)] + newbody + src[match.end(2):], "ok"


def add_no_culling(src, effects):
    changed, already, missing = [], [], []
    for eff in effects:
        match = effect_block_pattern(eff).search(src)
        if not match:
            missing.append(eff)
            continue
        body = match.group(2)
        if "RasterizerState" in body:
            already.append(eff)
            continue
        newbody = (body.rstrip("\r\n")
                   + '\n\tRasterizerState = "rasterizer_no_culling"\n')
        src = src[:match.start(2)] + newbody + src[match.end(2):]
        changed.append(eff)
    return src, changed, already, missing


def add_ssao_weight(src):
    """Reduce screen-space AO only in MOE_TOON shader variants."""
    note = ("\n"
            "\t\t\t#ifdef MOE_TOON\n"
            "\t\t\t\tOut.SSAOColor.a *= MOE_TOON_SSAO_MUL;\n"
            "\t\t\t#endif")
    hits = 0
    for palette in ("vPaletteColorSkin", "vPaletteColorEyes", "vPaletteColorHair"):
        old = f"\t\t\t\tOut.SSAOColor.rgb *= {palette}.rgb;"
        count = src.count(old)
        if count:
            src = src.replace(old, old + note)
            hits += count
    attachment = "\t\t\t\tOut.SSAOColor = float4( vec3( 0.0f ), 1.0f );"
    count = src.count(attachment)
    if count:
        src = src.replace(attachment, attachment + note)
        hits += count
    return src, hits



def add_attachment_cutout(src):
    """Discard masked texels before they can replace the portrait depth/alpha."""
    src, state = add_define(src, EFFECT_ALIASES["portrait_attachment"], "MOE_ALPHA_CUTOUT")
    if state == "missing":
        raise RuntimeError("ordinary attachment effect not found")
    block = re.search(r"(^[ \t]*MainCode\s+PS_attachment\s*$.*?^[ \t]*\]\])", src, re.S | re.M)
    if not block:
        raise RuntimeError("ordinary attachment MainCode not found")
    body = block.group(1)
    line = re.compile(r"^([ \t]*)float4 Diffuse = PdxTex2D\( DiffuseMap, UV0 \);$", re.M)
    match = line.search(body)
    if not match:
        raise RuntimeError("ordinary attachment diffuse anchor not found")
    indent = match.group(1)
    replacement = (match.group(0) + "\n"
                   f"{indent}#ifdef MOE_ALPHA_CUTOUT\n"
                   f"{indent}\tclip( Diffuse.a - 0.5f );\n"
                   f"{indent}\tDiffuse.a = 1.0f;\n"
                   f"{indent}#endif")
    body = body[:match.start()] + replacement + body[match.end():]
    return src[:block.start()] + body + src[block.end():]


def add_skin_response(src):
    """Apply the same matte skin response in portrait and court shaders."""
    for effect in (EFFECT_ALIASES["portrait_face"], EFFECT_ALIASES["portrait_skin"]):
        src, state = add_define(src, effect, "MOE_SKIN_RESPONSE")
        if state == "missing":
            raise RuntimeError(f"skin response effect not found: {effect}")
    block = re.search(r"(^[ \t]*MainCode\s+PS_skin\s*$.*?^[ \t]*\]\])", src, re.S | re.M)
    if not block:
        raise RuntimeError("skin MainCode not found")
    pattern = re.compile(r"^([ \t]*)float3 Color = CommonPixelShader\( Diffuse, Properties, NormalSample, Input(?:, HoverMult)? \);$", re.M)
    match = pattern.search(block.group(1))
    if not match:
        raise RuntimeError("skin shading anchor not found")
    indent = match.group(1)
    response = (
        f"{indent}#ifdef MOE_SKIN_RESPONSE\n"
        f"{indent}\tProperties.g = min( Properties.g, 0.20f );\n"
        f"{indent}\tProperties.b = 0.0f;\n"
        f"{indent}\tProperties.a = max( Properties.a, 0.70f );\n"
        f"{indent}\tNormalSample = normalize( float3( NormalSample.xy * 0.35f, NormalSample.z ) );\n"
        f"{indent}#endif\n"
    )
    insertion = block.start(1) + match.start()
    return src[:insertion] + response + src[insertion:]


def add_finite_color_guard(src):
    """Keep finite lighting unchanged, including valid back-facing lighting."""
    pattern = re.compile(r"^([ \t]*)Out\.Color = float4\( Color, (?:Diffuse\.a|1\.0f) \);$", re.M)

    def replace(match):
        indent = match.group(1)
        return (f"{indent}#ifdef DOUBLE_SIDED_ENABLED\n"
                f"{indent}\tuint3 MoeColorBits = asuint( Color );\n"
                f"{indent}\tif( any( ( MoeColorBits & 0x7f800000u ) == 0x7f800000u ) )\n"
                f"{indent}\t{{\n"
                f"{indent}\t\tColor = Diffuse.rgb * 0.30f;\n"
                f"{indent}\t}}\n"
                f"{indent}#endif\n" + match.group(0))

    result, count = pattern.subn(replace, src)
    if count < 5:
        raise RuntimeError(f"finite color output anchors: {count}, expected >= 5")
    return result, count


def build_court_shader(ver):
    if not COURT_SRC.exists():
        print(f"[shader] court shader not found: {COURT_SRC}", file=sys.stderr)
        return 1
    court = io.open(COURT_SRC, encoding="utf-8-sig", errors="replace").read()
    court, aliases = duplicate_effect_aliases(court)
    court, changed, already, missing = add_no_culling(court, TARGETS)
    if missing:
        print(f"[shader] court aliases missing: {missing}", file=sys.stderr)
        return 1

    for eff in TOON_EFFECTS:
        court, state = add_define(court, eff, "MOE_TOON")
        if state == "missing":
            print(f"[shader] court effect missing MOE_TOON: {eff}", file=sys.stderr)
            return 1
    for eff in FACE_FILL_TARGETS:
        court, state = add_define(court, eff, 'MOE_FACE_FILL')
        if state == 'missing':
            raise RuntimeError(f'Court face light effect missing: {eff}')
    for eff in TARGETS:
        court, state = add_define(court, eff, "DOUBLE_SIDED_ENABLED")
        if state == "missing":
            print(f"[shader] court effect missing DOUBLE_SIDED_ENABLED: {eff}",
                  file=sys.stderr)
            return 1
    court = add_attachment_cutout(court)
    court = add_skin_response(court)
    court, finite_sites = add_finite_color_guard(court)
    _plain = "NormalSample = UnpackRRxGNormal( PdxTex2D( NormalMap, UV0 ) );"
    court = court.replace(
        _plain,
        _plain + "\n\t\t\t\t#ifdef DOUBLE_SIDED_ENABLED\n"
        "\t\t\t\t\tNormalSample *= ( PDX_IsFrontFace ? 1 : -1 );   // moe\n"
        "\t\t\t\t#endif")

    signature = ("\t\tfloat3 CommonPixelShaderColor( float4 Diffuse, "
                 "float4 Properties, float3 Normal, "
                 "in VS_OUTPUT_PDXMESHPORTRAIT Input, float HoverMult )")
    if court.count(signature) != 1:
        print(f"[shader] court CommonPixelShaderColor hits: {court.count(signature)}",
              file=sys.stderr)
        return 1
    # court 侧：TOON_PARAMS 之后追加自己的 SceneLum 函数与标记宏。
    # **不能套用 portrait 那份** —— 两侧 ConstantBuffer 名字/数组大小/衰减函数/
    # 强度量级都不同（court 多一个 ×1000）。所以 court 用独立的 GAIN_REF。
    court_lum_macros = (
        "\t\t#define MOE_HAS_SCENE_LUM 1\n"
        "\t\t#ifndef MOE_COURT_GAIN_REF\n"
        "\t\t\t#define MOE_COURT_GAIN_REF 0.18f\n"
        "\t\t#endif\n")
    court = court.replace(
        signature, TOON_PARAMS + court_lum_macros + COURT_SCENE_LUM_FN + signature, 1)

    color_line = ("\t\t\tfloat3 Color = DiffuseIBL + SpecularIBL "
                  "+ DiffuseLight + SpecularLight + DiffuseTranslucency;")
    if court.count(color_line) != 1:
        print(f"[shader] court color assembly hits: {court.count(color_line)}",
              file=sys.stderr)
        return 1
    court_toon = TOON_BODY.replace(
        "Color = ToonDiffOut + ToonSpec;",
        "Color = ToonDiffOut + ToonSpec;\n"
        "\t\t\t\tfloat TrLum = dot( DiffuseTranslucency, float3( 0.2126f, 0.7152f, 0.0722f ) );\n"
        "\t\t\t\tfloat TrEdge = smoothstep( MOE_TOON_TRANS_THRESHOLD * 0.9f, MOE_TOON_TRANS_THRESHOLD * 1.1f, TrLum );\n"
        "\t\t\t\tColor += ( DiffuseTranslucency / max( TrLum, 0.0001f ) ) * ( MOE_TOON_TRANS_LEVEL * TrEdge );")
    court_lum_calc = ("\t\t\tfloat MoeSceneLum = MoeCourtSceneLum( Input.WorldSpacePos );\n")
    court = court.replace(
        color_line, court_lum_calc + FILL_BODY + color_line + court_toon, 1)
    court, ssao_hits = add_ssao_weight(court)

    for original in BASE_TARGETS:
        if original == "portrait_face":
            continue
        match = effect_block_pattern(original).search(court)
        if not match:
            print(f"[shader] original court effect disappeared: {original}",
                  file=sys.stderr)
            return 1
        if "MOE_TOON" in match.group(2):
            print(f"[shader] original court effect was modified: {original}",
                  file=sys.stderr)
            return 1

    header = (f"# Generated by tools/build/build_moe_shader.py from CK3 {ver}.\n"
              "# Original effects remain unchanged; moe_* aliases use court scene lights.\n\n")
    COURT_DST.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_pdx_block_end(header + court, "court_scene.shader")
    io.open(COURT_DST, "w", encoding="utf-8", newline="").write(header + court)
    print(f"[shader] court: {COURT_SRC} -> {COURT_DST}")
    print(f"[shader] court aliases {len(aliases)}, double-sided {len(changed)}, "
          f"already double-sided {len(already)}, SSAO sites {ssao_hits}")
    return 0


def main():
    if not SRC.exists():
        print(f"[shader] 找不到原版 shader：{SRC}", file=sys.stderr)
        return 1
    # utf-8-sig: CK3 1.19 ships portrait.shader with a BOM. Read as plain utf-8, the
    # BOM lands after our HEADER, mid-file, and the engine fails to parse the whole
    # file ("Unexpected token: ﻿Includes") -> every portrait shader fails.
    s = io.open(SRC, encoding="utf-8-sig", errors="replace").read()
    s, aliases = duplicate_effect_aliases(s)

    s, changed, already, missing = add_no_culling(s, TARGETS)
    s = add_attachment_cutout(s)

    # ---------------------------------------------------- 卡通化注入
    # **两处都必须成功**，只成一处会静默退化：
    #   · 只插逻辑不加宏  → #ifdef MOE_TOON 永远为假，卡通化完全不生效
    #   · 只加宏不插逻辑  → 宏无处引用，编译通过但同样没效果
    # 所以两处都用 assert 式检查，任一失败就报错退出，不留「看着成功
    # 其实没生效」的中间状态。
    anchor_params = ("		float3 CommonPixelShaderColor( float4 Diffuse, "
                     "float4 Properties, float3 Normal, "
                     "in VS_OUTPUT_PDXMESHPORTRAIT Input )")
    if s.count(anchor_params) != 1:
        print(f"[shader] **找不到 CommonPixelShaderColor 签名"
              f"（命中 {s.count(anchor_params)} 次）** —— 原版可能改了签名，"
              f"核对后更新 anchor_params", file=sys.stderr)
        return 1
    s = s.replace(anchor_params, TOON_PARAMS + anchor_params, 1)

    # -------------------------------------------- 主光旋向正面
    # 替换版 CalculatePortraitLights 插在 CommonPixelShaderColor 之前
    # （HLSL 要求先定义后使用），再把那一处调用换成我们的版本。
    # **不改 jomini/portrait_lighting.fxh** —— 那是共享 include，
    # 改了原版所有角色一起变。见 KEY_ROTATE_FN 的注释。
    anchor_fill_call = ("			CalculatePortraitLights( Input.WorldSpacePos, "
                        "LightingProps._ShadowTerm, MaterialProps, "
                        "DiffuseLight, SpecularLight );")
    s = s.replace(anchor_params, KEY_ROTATE_FN + anchor_params, 1)
    n_call = s.count(anchor_fill_call)
    if n_call != 1:
        print(f"[shader] **CalculatePortraitLights 调用命中 {n_call} 次（应 1）**"
              f" —— 原版可能改了光照组装，核对后更新 anchor_fill_call",
              file=sys.stderr)
        return 1
    moe_fill_call = anchor_fill_call.replace(
        "CalculatePortraitLights(", "MoeCalculatePortraitLights(")
    # 多接一个出参 SceneLum（场景光强，只含强度×距离衰减×锥，无法线项）。
    # 变量声明在 #ifdef 外面 —— 平涂分支的 ToonGain 要读它，而两处都在
    # #ifdef MOE_TOON 内，但声明放外面对 #else 分支也无害（未使用）。
    moe_fill_call = moe_fill_call.replace(
        "SpecularLight );", "SpecularLight, MoeSceneLum );")
    conditional_fill_call = (
        "\t\t\tfloat MoeSceneLum = 0.0f;\n"
        "\t\t\t#ifdef MOE_TOON\n"
        + moe_fill_call + "\n"
        "\t\t\t#else\n"
        + anchor_fill_call + "\n"
        "\t\t\t#endif")
    s = s.replace(anchor_fill_call, conditional_fill_call, 1)

    # 逻辑插在 Color 组装完之后。锚点选这一整行而不是片段：
    # 原版里 `float3 Color =` 出现多次（PS_hair 等也有），必须整行匹配。
    anchor_body = ("			float3 Color = DiffuseIBL + SpecularIBL "
                   "+ DiffuseLight + SpecularLight;")
    if s.count(anchor_body) != 1:
        print(f"[shader] **找不到 Color 组装行（命中 "
              f"{s.count(anchor_body)} 次）** —— 原版可能改了光照组装方式，"
              f"核对后更新 anchor_body", file=sys.stderr)
        return 1
    s = s.replace(anchor_body, anchor_body + TOON_BODY, 1)
    # 皮肤半透在 MOE_TOON 下单独切一档（2026-09-07）：原版 `Color += DiffuseTranslucency;` 在卡通化之后
    # 叠回连续渐变。非 toon 路径保持原版行。
    _tr_old = "\t\t\t\tColor += DiffuseTranslucency;\n"
    if s.count(_tr_old) != 1:
        print(f"[shader] **Color += DiffuseTranslucency 命中 {s.count(_tr_old)} 次（应 1）**", file=sys.stderr)
        return 1
    s = s.replace(_tr_old, """\t\t\t\t#ifdef MOE_TOON
\t\t\t\t{
\t\t\t\t\t// 半透单独切一档：过阈值才加，加归一化色相 × 常量强度（与漫反射/高光同一套做法）
\t\t\t\t\tfloat TrLum = dot( DiffuseTranslucency, float3( 0.2126f, 0.7152f, 0.0722f ) );
\t\t\t\t\tfloat TrEdge = smoothstep( MOE_TOON_TRANS_THRESHOLD * 0.9f, MOE_TOON_TRANS_THRESHOLD * 1.1f, TrLum );
\t\t\t\t\tColor += ( DiffuseTranslucency / max( TrLum, 0.0001f ) ) * ( MOE_TOON_TRANS_LEVEL * TrEdge );
\t\t\t\t}
\t\t\t\t#else
\t\t\t\tColor += DiffuseTranslucency;
\t\t\t\t#endif
""", 1)

    s = s.replace(anchor_body, FILL_BODY + anchor_body, 1)

    # -------------------------------------------- 皮肤不接受投影
    # **只让皮肤忽略 shadow map，衣服和头发照常接受**（2026-08-24）。
    #
    # 阴影只通过一个入口进来：`LightingProps._ShadowTerm`
    # （`GetSunLightingProperties` 里由 `CalculateShadow` 算出，
    #  再传给 `CalculatePortraitLights` 与 translucency）。
    # `_ShadowTerm = 1.0` 的语义就是「完全不被遮挡」，所以强制成 1
    # 等于这个材质不接受任何投影。
    #
    # **为什么在 shader 里做而不是关光源投影**：`affected_by_shadow = no`
    # 是光源级的开关，一关就是全身都不投影 —— 衣褶、衣领在脖子上的阴影、
    # 头发之间的层次全部消失，人物会变得很平。而问题只在脸上：前发投在
    # 额头/眼睛/颧骨的影子被 shadowmap_kernelscale=8 过滤糊成灰绿脏块。
    # 二次元立绘里头发落在脸上的硬阴影是画师手绘的，不该靠实时 shadow map。
    # 材质级的宏能精确到「只有皮肤」，这是光源开关做不到的。
    # 锚点必须**用正则锚定行首**（`^\t\t\t` + 后面不能再有 tab）。原版有
    # 两处一模一样的调用：
    #   · 第 276 行 `CommonPixelShaderColor` 里，缩进 3 个 tab  ← 要改这个
    #   · 第 664 行 `MainCode PS_anisotropic_hair` 里，缩进 4 个 tab
    # 后者是各向异性头发的独立路径（我们的 .asset 没引用它），不该动。
    #
    # **纯字符串 count 分不开这两处**：拿 3 个 tab 的字符串去数，4-tab 那行的
    # 后三个 tab 同样匹配，count 仍是 2、脚本报错退出。我连着踩了两次 ——
    # 先是不带缩进（命中 2），再是带 3 tab 的裸字符串（还是 2）。
    pat_shadow = re.compile(
        r"^\t{3}SLightingProperties LightingProps = "
        r"GetSunLightingProperties\( Input\.WorldSpacePos, "
        r"ShadowTexture \);$", re.M)
    hits = pat_shadow.findall(s)
    if len(hits) != 1:
        print(f"[shader] **找不到 CommonPixelShaderColor 里的 "
              f"GetSunLightingProperties 调用（命中 {len(hits)} 次，应 1）**"
              f" —— 原版可能改了缩进或光照初始化方式，核对后更新 pat_shadow",
              file=sys.stderr)
        return 1
    s = pat_shadow.sub(lambda m: m.group(0) + NO_SHADOW_BODY, s, count=1)

    # ------------------------------------------------ SSAO 降权
    # **SSAO 是全屏后处理，在本 shader 之后乘上来**（2026-08-24 查实）。
    # 见 jomini/gfx/FX/jomini/ssao_apply.shader：`SSAOApplyColor` 用
    # `SourceBlend = "dest_color"` 把 SSAO 结果**乘**到已经画好的画面上，
    # 强度是 `SSAOSample * ColorSample.a` —— `ColorSample` 就是我们在
    # PS_* 里写进 `Out.SSAOColor` 的那个值，**它的 alpha 是作用强度**。
    #
    # 所以在像素着色器里做的任何硬边，都会被 SSAO 再叠一层连续的接触阴影。
    # 实测就是脸上眼窝、鼻侧、下巴那些浑浊斑块的来源 —— 贴图本身是干净的
    # 平涂粉色（c_Florence_slg_face_d.dds 实采 (242,188,183)，无任何深色
    # 斑块），properties 的散射 mask 全 0，所以浑浊只可能来自这里。
    #
    # 唯一能从 shader 侧控制它的就是这个 alpha。整体降权而不是设 0：
    # 完全没有接触阴影时头发与脸、衣领与脖子会糊成一片，失去层次。
    n_ssao = 0
    ssao_note = ("\n"
                 "\t\t\t#ifdef MOE_TOON\n"
                 "\t\t\t\t// SSAO 是全屏后处理、在本 shader 之后**乘**上来，会把\n"
                 "\t\t\t\t// 卡通硬边重新糊成连续的接触阴影。alpha 是它的作用\n"
                 "\t\t\t\t// 强度（见 ssao_apply.shader 的 SSAOFactor），降权。\n"
                 "\t\t\t\tOut.SSAOColor.a *= MOE_TOON_SSAO_MUL;\n"
                 "\t\t\t#endif")
    for pal in ("vPaletteColorSkin", "vPaletteColorEyes", "vPaletteColorHair"):
        old = f"\t\t\t\tOut.SSAOColor.rgb *= {pal}.rgb;"
        cnt = s.count(old)
        if not cnt:
            continue
        s = s.replace(old, old + ssao_note)
        n_ssao += cnt
    # **`PS_attachment` 走的是另一条分支**，不带调色板乘法：
    #     Out.SSAOColor = float4( vec3( 0.0f ), 1.0f );
    # alpha 硬编码 1.0 = SSAO **全强度**生效，而这条正是衣服与挂件
    # （portrait_attachment / portrait_attachment_alpha_to_coverage，
    # 我们 .asset 里 16 处引用）。只改调色板那 5 处会漏掉它，出现
    # 「脸卡通、衣服糊」的割裂 —— 与当初漏掉 PS_hair 是同一类错误。
    old_att = "\t\t\t\tOut.SSAOColor = float4( vec3( 0.0f ), 1.0f );"
    cnt_att = s.count(old_att)
    if cnt_att:
        s = s.replace(old_att, old_att + ssao_note)
        n_ssao += cnt_att
    # 原版共 6 处写 Out.SSAOColor 的像素着色器：调色板乘法 5 处
    # （skin / eye / hair / hair_double_sided / anisotropic_hair）
    # + attachment 的常量分支 1 处。少于这个数说明原版改了写法，必须报错。
    if n_ssao < 6:
        print(f"[shader] **SSAO 降权只命中 {n_ssao} 处（应 ≥6）** —— "
              f"原版可能改了 Out.SSAOColor 的写法，核对后更新",
              file=sys.stderr)
        return 1

    # 给每个目标 Effect 加 MOE_TOON 宏。有 Defines 行就追加，没有就新建。
    # **头发也要加**（2026-08-24 核实）：`PS_hair` 同样调
    # `CommonPixelShader` → `CommonPixelShaderColor`，所以加宏就能一起卡通化。
    # 我起初以为它走 `portrait_hair_lighting.fxh` 的独立路径而排除了它 ——
    # 那是错的，实读 MainCode PS_hair 里就是 `CommonPixelShader( ... )`。
    # 头发占 10 处引用（hair + hair_alpha），漏掉会出现「衣服卡通、头发写实」
    # 的割裂感，是二次元观感里最显眼的一处。
    def add_define(src, eff, macro):
        """给一个 Effect 块加宏。返回 (新文本, 状态)。

        状态 'ok' = 加上了，'have' = 本来就有，'missing' = 找不到该 Effect。
        Effect 名必须**整行精确匹配**，否则 `portrait_skin` 会先命中
        `portrait_skin_face` / `portrait_skinShadow`（原版都有），宏加错
        地方而目标 Effect 反而没拿到。
        """
        pat = re.compile(
            r"(^Effect[ \t]+" + re.escape(eff)
            + r"[ \t]*\r?\n\{)(.*?)(^\})", re.S | re.M)
        m = pat.search(src)
        if not m:
            return src, "missing"
        body = m.group(2)
        if macro in body:
            return src, "have"
        dm = re.search(r"^\tDefines = \{(.*?)\}[ \t]*$", body, re.M)
        if dm:
            newbody = (body[:dm.start()]
                       + '\tDefines = {' + dm.group(1).rstrip()
                       + f' "{macro}" }}'
                       + body[dm.end():])
        else:
            # 原版 portrait_eye 就没有 Defines 行，必须新建而不是追加。
            newbody = (body.rstrip("\r\n")
                       + f'\n\tDefines = {{ "{macro}" }}\n')
        return src[:m.start(2)] + newbody + src[m.end(2):], "ok"

    s = add_skin_response(s)

    # 2026-09-09 用户定方向：衣服/头发走原版光照，只有皮肤与眼睛卡通化（主流做法）。
    toon_effects = list(TOON_EFFECTS)
    toned, toon_bad = [], []
    for eff in toon_effects:
        s, st = add_define(s, eff, "MOE_TOON")
        if st == "missing":
            toon_bad.append(eff)
        elif st == "ok":
            toned.append(eff)
    if toon_bad:
        print(f"[shader] **这些 Effect 没拿到 MOE_TOON：{toon_bad}**",
              file=sys.stderr)
        return 1

    # MOE_INK（内部墨线）：除眼睛外的全部 moe 材质。虹膜/眼白是小球面，边缘一圈墨会把眼睛框黑。
    INK_TARGETS = list(INK_EFFECTS)
    inked, ink_bad = [], []
    for eff in INK_TARGETS:
        s, st = add_define(s, eff, "MOE_INK")
        if st == "missing":
            ink_bad.append(eff)
        elif st == "ok":
            inked.append(eff)
    if ink_bad:
        print(f"[shader] **这些 Effect 没拿到 MOE_INK：{ink_bad}**", file=sys.stderr)
        return 1

    # 边缘光维持除眼睛外全挂（与墨线的 INK_EFFECTS 独立，2026-09-08 晚拆开）。
    RIM_TARGETS = list(RIM_EFFECTS)   # 2026-09-09：轮廓亮边只留皮肤（衣服走原版光照）
    rim_bad = []
    for eff in RIM_TARGETS:
        s, st = add_define(s, eff, "MOE_RIM")
        if st == "missing":
            rim_bad.append(eff)
    if rim_bad:
        print(f"[shader] **这些 Effect 没拿到 MOE_RIM：{rim_bad}**", file=sys.stderr)
        return 1

    # **MOE_NO_SHADOW 只给皮肤**。衣服与头发照常接受投影 —— 衣褶、衣领在
    # 脖子上的阴影、头发之间的层次都要留着，否则人物会变得很平。
    # 这正是材质级宏相对光源级 `affected_by_shadow = no` 的意义所在。
    noshadow, ns_bad = [], []
    for eff in NO_SHADOW_TARGETS:
        s, st = add_define(s, eff, "MOE_NO_SHADOW")
        if st == "missing":
            ns_bad.append(eff)
        elif st == "ok":
            noshadow.append(eff)
    if ns_bad:
        print(f"[shader] **这些 Effect 没拿到 MOE_NO_SHADOW：{ns_bad}**",
              file=sys.stderr)
        return 1

    # -------------------------------------------- 双面 = 不剔除 + 背面法线翻转
    # `rasterizer_no_culling` 只是**不丢弃**背面片元；背面的法线仍然朝外
    # （朝着离开相机的方向），主光 N·L 为负，卡通二值化把它压到
    # SHADOW_LEVEL（0.11）—— 裙子/衣领内侧渲出来是近黑的一片，在深色肖像
    # 底上看着像「看穿了」（用户 2026-09-05：洁尔佩塔服装内侧变透明）。
    # 原版真正的双面材质（PS_hair_double_sided / court 的 DOUBLE_SIDED_ENABLED
    # 分支）都是 `Normal * ( PDX_IsFrontFace ? 1 : -1 )`：背面按翻转后的法线
    # 受光。PS_attachment 里已有这条 `#ifdef DOUBLE_SIDED_ENABLED` 分支，
    # 但原版没有任何 Effect 定义这个宏，所以从没编译过；这里给 6 个 moe
    # Effect 全部加上。PS_skin / PS_eye / PS_hair 原版没有这条分支，下面用
    # 同一个宏把它们的法线采样也包成条件翻转（原版 Effect 不定义宏，原文不变）。
    dbl, dbl_bad = [], []
    for eff in TARGETS:
        s, st = add_define(s, eff, "DOUBLE_SIDED_ENABLED")
        if st == "missing":
            dbl_bad.append(eff)
        elif st == "ok":
            dbl.append(eff)
    if dbl_bad:
        print(f"[shader] **这些 Effect 没拿到 DOUBLE_SIDED_ENABLED：{dbl_bad}**",
              file=sys.stderr)
        return 1
    plain_normal = ("NormalSample = UnpackRRxGNormal( PdxTex2D( NormalMap, UV0 ) );")
    n_plain = s.count(plain_normal)
    if n_plain == 0:
        print("[shader] **找不到 PS_skin/PS_eye/PS_hair 的法线采样行** —— 原版改了写法，"
              "背面法线翻转没接上", file=sys.stderr)
        return 1
    s = s.replace(
        plain_normal,
        "NormalSample = UnpackRRxGNormal( PdxTex2D( NormalMap, UV0 ) );\n"
        "\t\t\t\t#ifdef DOUBLE_SIDED_ENABLED\n"
        "\t\t\t\t\tNormalSample *= ( PDX_IsFrontFace ? 1 : -1 );   // moe: 背面按翻转法线受光\n"
        "\t\t\t\t#endif")
    s, _n_out = add_finite_color_guard(s)
    print(f"[shader] 双面背面 NaN 守卫：{_n_out} 处输出行")
    print(f"[shader] 双面法线：{len(dbl)} 个 Effect 加 DOUBLE_SIDED_ENABLED，"
          f"{n_plain} 处法线采样加条件翻转")

    # -------------------------------------------- 面部补光的宏
    facefill, ff_bad = [], []
    for eff in FACE_FILL_TARGETS:
        s, st = add_define(s, eff, "MOE_FACE_FILL")
        if st == "missing":
            ff_bad.append(eff)
        elif st == "ok":
            facefill.append(eff)
    if ff_bad:
        print(f"[shader] **这些 Effect 没拿到 MOE_FACE_FILL：{ff_bad}**",
              file=sys.stderr)
        return 1

    # 版本号从 game 目录的 launcher settings 里读，读不到就写 unknown
    ver = "unknown"
    for cand in (GAME / "launcher" / "launcher-settings.json",):
        if cand.exists():
            m = re.search(r'"rawVersion"\s*:\s*"([^"]+)"',
                          cand.read_text(encoding="utf-8", errors="replace"))
            if m:
                ver = m.group(1)
            break

    DST.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_pdx_block_end(HEADER.format(ver=ver) + s, "portrait.shader")
    io.open(DST, "w", encoding="utf-8", newline="").write(
        HEADER.format(ver=ver) + s)

    print(f"[shader] 源 {SRC}")
    print(f"[shader] 出 {DST}  ({len(s.splitlines())} 行, CK3 {ver})")
    print(f"[shader] 加了双面 {len(changed)} 个：{changed}")
    print(f"[shader] 原版已双面 {len(already)} 个：{already}")
    if missing:
        print(f"[shader] **原版找不到这些 Effect：{missing}** —— "
              f"原版可能改名了，核对后更新 TARGETS", file=sys.stderr)
        return 1
    # 自检：每个目标 Effect 的块内都必须有 no_culling
    bad = []
    for eff in TARGETS:
        m = re.search(r"^Effect[ \t]+" + re.escape(eff)
                      + r"[ \t]*\r?\n\{(.*?)^\}", s, re.S | re.M)
        if not m or "rasterizer_no_culling" not in m.group(1):
            bad.append(eff)
    if bad:
        print(f"[shader] **自检失败，这些 Effect 没拿到双面：{bad}**",
              file=sys.stderr)
        return 1
    # 卡通化自检：逻辑、参数、宏三者都要在
    toon_bad2 = []
    if ("MOE_TOON_SHADOW_SAT" not in s or "ToonShadowLight * ToonShadowAlbedo" not in s
            or "ToonAlbedo / ToonAlbedoLum, MOE_TOON_SHADOW_SAT" not in s):
        toon_bad2.append("暗档提饱和（MOE_TOON_SHADOW_SAT）缺失或没乘在暗档上")
    # 内部墨线自检：参数在、用了、挂在 5 个 Effect 上（眼睛不挂）；反向：墨色不能是字面量。
    _ink_code = "\n".join(ln for ln in s.splitlines() if not ln.lstrip().startswith("//"))
    _ink_eff = [eff for eff in TARGETS if (m := effect_block_pattern(eff).search(s)) and "MOE_INK" in m.group(2)]
    if ("MOE_INK_NDOTV" not in s or "lerp( MOE_INK_COLOR, Color," not in _ink_code
            or "abs( dot( Normal, InkView ) )" not in _ink_code
            or "MOE_INK_PX * fwidth( InkNdotV )" not in _ink_code
            or sorted(_ink_eff) != sorted(INK_EFFECTS)):
        print(f"[shader] **内部墨线自检失败：挂了 {_ink_eff}，应为 {INK_EFFECTS}**", file=sys.stderr)
        return 1
    if re.search(r"lerp\( float3\( 0\.0[0-9]f", _ink_code):
        print("[shader] **墨色写成了字面量**", file=sys.stderr)
        return 1
    _rim_eff = [eff for eff in TARGETS if (m := effect_block_pattern(eff).search(s)) and "MOE_RIM" in m.group(2)]
    if (sorted(_rim_eff) != sorted(RIM_EFFECTS)
            or "( 1.0f - ToonEdge )" not in _ink_code or "MOE_RIM_INTENSITY * RimBand" not in _ink_code):
        print(f"[shader] **轮廓亮边自检失败：挂了 {_rim_eff}**", file=sys.stderr)
        return 1
    # 半透：非注释代码里 `Color += DiffuseTranslucency;` 只能剩 #else 那一处，且 toon 分支用了常量档
    if _ink_code.count("Color += DiffuseTranslucency;") != 1 or "MOE_TOON_TRANS_LEVEL * TrEdge" not in _ink_code:
        print("[shader] **皮肤半透未切档**", file=sys.stderr)
        return 1
    if "MOE_TOON_THRESHOLD" not in s:
        toon_bad2.append("参数块")
    if "ToonEdge" not in s:
        toon_bad2.append("二值化逻辑")
    # **量化对象必须是光照而不是最终颜色**。这一项单独自检：第一版在 Color
    # 上切，编译通过、宏也都在、自检全绿，但反射率被一起量化了 —— 亮皮肤与
    # 深色布料落在阈值两侧，阈值语义不一致，调不出一致的明暗线。
    # 判据取「有没有除掉 _DiffuseColor」，那是这次修复的实质。
    if "ToonAlbedo" not in s or "MaterialProps._DiffuseColor" not in s:
        toon_bad2.append("光照/反射率分离")
    # **两档必须是常量光照，不能是「原光照 × 系数」**（2026-08-25 补）。
    # 上一版写成 `lerp( ToonDiffLight * 0.88, ToonDiffLight * 1.10, edge )`，
    # 两档都还带着原始连续光照、只差 ±11% —— 根本没有二值化。而当时的自检
    # 全绿：宏在、ToonEdge 在、反射率也确实分离了。**判据查了「有没有做」，
    # 没查「做出来的两档是不是真的分开」**，所以带着这个 bug 出了三轮产物，
    # 我还把「看不到硬边」分别误判成贴图腮红、判据不成立、SSAO。
    # 判据：两档的表达式里都不能再出现 ToonDiffLight。
    m_lvl = re.search(r"float3 ToonShadowLight = (.*?);\n.*?"
                      r"float3 ToonLitLight\s*= (.*?);", s, re.S)
    if not m_lvl:
        toon_bad2.append("找不到两档赋值")
    elif ("ToonDiffLight" in m_lvl.group(1)
          or "ToonDiffLight" in m_lvl.group(2)):
        toon_bad2.append(
            "两档还乘着原始光照 ToonDiffLight —— 那不是二值化，只是整体缩放")
    if "MOE_TOON_LIT_LEVEL" not in s or "MOE_TOON_SHADOW_LEVEL" not in s:
        toon_bad2.append("两档常量参数")
    if "MOE_TOON_SSAO_MUL" not in s:
        toon_bad2.append("SSAO 降权")
    # 皮肤不接受投影：逻辑与宏都要在，且**只能**给皮肤/眼睛
    if "LightingProps._ShadowTerm = 1.0f;" not in s:
        toon_bad2.append("皮肤免投影逻辑")
    # **期望集合必须硬编码，不能引用 NO_SHADOW_TARGETS**（2026-08-24）。
    # 我第一版写的是 `want = eff in NO_SHADOW_TARGETS` —— 那是拿配置去核对
    # 由同一个配置生成的产物，等于自己跟自己对账。反向验证一测就露：把宏
    # 也加给 portrait_attachment、甚至把 NO_SHADOW_TARGETS 清空，自检全都
    # 照样通过（退出码 0）。判据的期望值必须独立于被测配置。
    # 2026-09-13 加 portrait_skin：它是皮肤本身（脖子/手臂/腿/眼白），09-11 进了
    # TOON_EFFECTS 却漏了 NO_SHADOW_TARGETS，二值化把帽檐投影放大成硬边色块。
    EXPECT_NO_SHADOW = {
        EFFECT_ALIASES["portrait_face"],
        EFFECT_ALIASES["portrait_eye"],
        EFFECT_ALIASES["portrait_skin"],
    }
    if set(NO_SHADOW_TARGETS) != EXPECT_NO_SHADOW:
        toon_bad2.append(
            f"NO_SHADOW_TARGETS 被改成了 {sorted(NO_SHADOW_TARGETS)}"
            f"（应 {sorted(EXPECT_NO_SHADOW)}）—— 改这个要同步改"
            f"EXPECT_NO_SHADOW 并说明理由")
    for eff in TARGETS:
        m = re.search(r"^Effect[ \t]+" + re.escape(eff)
                      + r"[ \t]*\r?\n\{(.*?)^\}", s, re.S | re.M)
        if not m:
            continue
        has = "MOE_NO_SHADOW" in m.group(1)
        want = eff in EXPECT_NO_SHADOW
        if has != want:
            toon_bad2.append(
                f"{eff} {'不该' if has else '应该'}带 MOE_NO_SHADOW")
    for eff in toon_effects:
        m = re.search(r"^Effect[ \t]+" + re.escape(eff)
                      + r"[ \t]*\r?\n\{(.*?)^\}", s, re.S | re.M)
        if not m or "MOE_TOON" not in m.group(1):
            toon_bad2.append(eff)
    # -------------------------------------------- 面部补光自检
    # 期望集合同样**硬编码**，理由见上面 EXPECT_NO_SHADOW 的注释。
    EXPECT_FACE_FILL = {
        'moe_portrait_face', 'moe_portrait_eye', 'moe_portrait_skin',
        'moe_portrait_attachment', 'moe_portrait_attachment_alpha_to_coverage',
        'moe_portrait_hair', 'moe_portrait_hair_alpha',
    }
    if set(FACE_FILL_TARGETS) != EXPECT_FACE_FILL:
        toon_bad2.append(
            f"FACE_FILL_TARGETS 被改成了 {sorted(FACE_FILL_TARGETS)}"
            f"（应 {sorted(EXPECT_FACE_FILL)}）")
    if "MOE_FACE_FILL_INTENSITY" not in s:
        toon_bad2.append("补光参数块")
    if "float3 FillDir = normalize( CameraPosition" not in s:
        toon_bad2.append("补光逻辑")
    # **补光必须加在二值化之前**：加在之后会在两档之上叠连续渐变、糊掉硬边。
    # 判据是两段代码在文件里的先后位置 —— 这种「顺序对不对」的错误编译能过、
    # 宏也都在，只有位置判据挡得住。同类教训：MOE_NO_SHADOW 必须在所有读
    # _ShadowTerm 的调用之前。
    i_fill = s.find("float3 FillDir = normalize( CameraPosition")
    i_toon = s.find("float3 ToonAlbedo = max( MaterialProps._DiffuseColor")
    if i_toon < 0:
        toon_bad2.append("找不到卡通化逻辑")
    elif i_fill >= 0 and i_fill > i_toon:
        toon_bad2.append(
            "**补光排在二值化之后** —— 会在两档之上叠连续渐变、糊掉硬边")
    # **补光强度必须低于亮档阈值**。补光是加在 DiffuseLight 上、进 ToonKey 的，
    # 强度 ≥ 阈值就意味着不看法线朝向、每个像素都被垫过阈值判进亮档，暗档面积
    # 归零 —— 二值化**静默失效**。2026-09-11 脸部补光 0.26 > 阈值 0.18 就是这样，
    # 宏全在、自检全绿、编译通过，只有实机量脸部亮度直方图才看得出脸是连续渐变
    # （峰距仅 14 级）而腿是真两档（比 1.98~2.61）。这正是
    # [[toon-must-quantize-lighting-not-color]] 里「查了有没有做、没查做出来对不对」
    # 的同一类错误，所以补一条数值判据而不是只写注释。
    def _macro_f(name):
        mm = re.search(r"#define " + name + r"\s+([0-9.]+)f", s)
        return float(mm.group(1)) if mm else None
    _thr = _macro_f("MOE_TOON_THRESHOLD")
    # 平涂模式（MOE_TOON_FLAT=1）下 ToonEdge 与 ToonGain 都钉死，**没有分档可失效**,
    # 所以「补光 < 阈值」这条约束不成立，跳过。不静默跳 —— 打印一行，否则以后关掉
    # FLAT 时容易忘记当前这组数值在二值化下是违规的。
    _flat = re.search(r"#define MOE_TOON_FLAT\s+(\d+)", s)
    _is_flat = bool(_flat and _flat.group(1) != "0")
    if _is_flat:
        print(f"[shader] 平涂模式（MOE_TOON_FLAT=1）：跳过「补光 < 阈值」自检 —— "
              f"没有分档可失效。补光 {_macro_f('MOE_FACE_FILL_INTENSITY')}"
              f" / 阈值 {_thr}，**关掉 FLAT 前必须先把补光降到阈值以下**")
    else:
        for _fill in ("MOE_FACE_FILL_INTENSITY", "MOE_SKIN_FILL_INTENSITY"):
            _v = _macro_f(_fill)
            if _thr is None or _v is None:
                toon_bad2.append(f"读不到 {_fill} 或 MOE_TOON_THRESHOLD 的数值")
            elif _v >= _thr:
                toon_bad2.append(
                    f"**{_fill}={_v} ≥ 阈值 {_thr}** —— 补光单独就垫过阈值，"
                    f"带补光的材质会全部落进亮档、二值化静默失效。"
                    f"要提亮请动 MOE_TOON_LIT_LEVEL 或 MOE_TOON_GAIN_MAX")
    # -------------------------------------------- 三盏灯方向自检
    # **判据先剔掉注释行**再搜代码模式：注释里会引用被废弃的写法作说明，
    # 整文件匹配会误报（踩过四次：布光颜色、饱和度、投影计数、length(P0)）。
    code_only = "\n".join(
        ln for ln in s.splitlines()
        if not ln.lstrip().startswith(("//", "#")))
    for _n in ("MOE_KEY_VANILLA", "MOE_KEY_UP_MIX", "MOE_KEY_SIDE_DEG",
               "MOE_KEY_INTENSITY_MUL"):
        if _n not in s:
            toon_bad2.append(f"缺参数 {_n}")
    # **作用域判据**：三个布光参数不能被关在 `#ifdef MOE_TOON` 里，
    # 因为用它们的 MoeCalculatePortraitLights 在那个块外面。
    #
    # 这个 bug 真实发生过：35 个不带 MOE_TOON 的 Effect 报
    # `undeclared identifier 'MOE_KEY_SIDE_DEG'`，**整份 shader 作废**。
    # 而当时所有文本判据都是绿的 —— 宏在、被引用了、值也对。
    # 文本判据看不见条件编译的作用域，这条必须显式算。
    _spans = []          # (起行, 止行) of every #ifdef MOE_TOON block
    _open = []
    for _i, _ln in enumerate(s.splitlines(), 1):
        _t = _ln.strip()
        if _t.startswith(("#ifdef", "#ifndef", "#if ")):
            _open.append((_i, _t))
        elif _t.startswith("#endif") and _open:
            _st, _tag = _open.pop()
            if _tag.startswith("#ifdef MOE_TOON"):
                _spans.append((_st, _i))
    _lines = s.splitlines()
    for _n in ("MOE_KEY_VANILLA", "MOE_KEY_UP_MIX", "MOE_KEY_SIDE_DEG", "MOE_KEY_INTENSITY_MUL"):
        _def_ln = next((_i for _i, _l in enumerate(_lines, 1)
                        if _l.strip().startswith(f"#define {_n}")), None)
        if _def_ln is None:
            continue
        if any(a <= _def_ln <= b for a, b in _spans):
            toon_bad2.append(
                f"**{_n} 被关在 #ifdef MOE_TOON 里** —— 用它的布光函数在块外，"
                f"不带 MOE_TOON 的 Effect 会报 undeclared identifier、"
                f"整份 shader 作废")
    if "void MoeCalculatePortraitLights" not in s:
        toon_bad2.append("替换版光照函数")
    # 调用点必须真的换掉：只插函数不换调用 = 从没执行、画面毫无变化。
    if "MoeCalculatePortraitLights( Input.WorldSpacePos," not in s:
        toon_bad2.append("调用点没换成 MoeCalculatePortraitLights")
    # **判据要先剔掉注释行**再搜代码模式 —— 注释里会引用被废弃的写法作说明，
    # 整文件匹配会误报。这个坑已踩过四次（布光颜色判据、饱和度判据、
    # affected_by_shadow 计数、length(P0) 的说明文字）。
    if len(re.findall(r"^\t{3}CalculatePortraitLights\( Input",
                      code_only, re.M)) != 1:
        toon_bad2.append("还残留原版 CalculatePortraitLights 调用")
    # 主光方向必须设置，且主光的直接光照计算必须在 if( i == 0 ) 块内。
    # 挪到块外会让轮廓光重复套用主光；这类「作用域/顺序错」只有位置判据
    # 挡得住。同类坑：
    # 补光必须排在二值化之前、MOE_NO_SHADOW 必须在读 _ShadowTerm 之前。
    if "float3 LightDirTo = normalize( float3(" not in s:
        toon_bad2.append("主光方向没设置")
    # 水平偏角必须真的进到 X 分量里。**正反两面都查** —— 只查「正确那行在不在」
    # 挡不住换成 `radians( 10.0f )` 之类的硬编码常量（参数改了不生效），
    # 反向验证会给退出码 0。这个模式已经栽过五次。
    if not re.search(r"-\s*sin\(\s*KeyAz\s*\)\s*\*\s*KeyHoriz", code_only):
        toon_bad2.append("主光偏角没用在 X 分量上，左右不会生效")
    if not re.search(r"cos\(\s*KeyAz\s*\)\s*\*\s*KeyHoriz", code_only):
        toon_bad2.append("主光偏角没用在 Z 分量上")
    if re.search(r"radians\(\s*[0-9]", code_only):
        toon_bad2.append(
            "**偏角写成了硬编码常量** —— 改 MOE_KEY_SIDE_DEG 不会生效")
    # **X 和 Z 必须乘同一个 KeyHoriz**，否则水平模长变了、仰角被悄悄改掉
    # （用户调好的 UP_MIX 会失效）。这类「数值对但比例被破坏」的错误编译能过、
    # 宏也都在，只有这条判据挡得住。
    #
    # **计数必须用 code_only** —— 上面的注释里也写了 KeyHoriz 作说明，
    # 整文件计数会数成 4 而误报。这是同一个坑的第五次（前四次：布光颜色、
    # 饱和度、affected_by_shadow 计数、length(P0) 说明文字）。
    _n_kh = code_only.count("KeyHoriz")
    if _n_kh != 3:
        toon_bad2.append(
            f"KeyHoriz 在代码里出现 {_n_kh} 次，应为 3"
            f"（1 处定义 + X/Z 各 1 处）—— 少一处则仰角被改掉")
    if "float KeyHoriz = 1.0f - MOE_KEY_UP_MIX;" not in s:
        toon_bad2.append("KeyHoriz 不是 1-UP_MIX，仰角与 UP_MIX 脱钩")
    # 主光强度缩放必须在 if( i == 0 ) 块内、且只动 rgb。**正反两面都查**：
    #   · 挪到块外 → 三盏灯一起被压暗，两盏轮廓光跟着变弱
    #   · 写成 `Color_Falloff *= ...`（不带 .rgb）→ alpha 被一起乘，
    #     而 alpha 是 falloff 参数，衰减半径会被改掉
    # 只查「正确那行在不在」挡不住第二种（那行不在、另一种合法写法在），
    # 反向验证会给退出码 0。这个模式已经栽过五次。
    if "Color_Falloff.rgb *= MOE_KEY_INTENSITY_MUL;" not in s:
        toon_bad2.append("主光强度缩放没设置")
    if re.search(r"Color_Falloff\s*\*=", code_only):
        toon_bad2.append(
            "**Color_Falloff 被整体缩放（含 alpha）** —— alpha 是 falloff "
            "参数，会连距离衰减半径一起改掉，必须写 .rgb *=")
    _i_if = s.find("\t\t\t\tif( i == 0 && MOE_KEY_VANILLA == 0 )")
    if _i_if < 0:
        toon_bad2.append("找不到主光分支 if( i == 0 && MOE_KEY_VANILLA == 0 )")
    # 开关必须真的在条件里：写成 `if( i == 0 )` 等于开关不生效、永远走底光方案
    if re.search(r"if\(\s*i\s*==\s*0\s*\)", code_only):
        toon_bad2.append("**主光分支没带 MOE_KEY_VANILLA 开关** —— 恢复原版不会生效")
    else:
        _b0 = s.find("{", _i_if)
        _depth, _b1 = 0, -1
        for _k in range(_b0, len(s)):
            if s[_k] == "{":
                _depth += 1
            elif s[_k] == "}":
                _depth -= 1
                if _depth == 0:
                    _b1 = _k
                    break
        _blk = s[_b0:_b1] if _b1 > 0 else ""
        for _ln in ("KeyLightingProps._ToLightDir = -LightDirTo;",
                    "Color_Falloff.rgb *= MOE_KEY_INTENSITY_MUL;"):
            if _ln not in _blk:
                toon_bad2.append(
                    f"**`{_ln}` 不在 if( i == 0 ) 块内** —— "
                    f"主光计算会污染两盏原版轮廓光")
            if s.count(_ln) != 1:
                toon_bad2.append(f"`{_ln}` 出现 {s.count(_ln)} 次，应恰好 1 次")
        _key_call = ("CalculateLightingFromLight( MaterialProps, "
                     "KeyLightingProps, DiffuseLight, SpecularLight );")
        if _key_call not in _blk:
            toon_bad2.append("主光没有在 i == 0 分支直接计算，Royal Court 会失光")
        if "else if( Light_Direction_Type[ i ].w == LIGHT_TYPE_SPOTLIGHT )" not in s:
            toon_bad2.append("轮廓光分支没有排除 i == 0，主光会重复计算")
    # 反向：次光一旦还有自定义参数/方向，就说明没改回原版。
    # **必须用 code_only** —— 参数块的注释里会提到这些名字，整文件匹配会误报
    # （这个坑已踩过四次）。
    if "MOE_FILL_" in code_only:
        toon_bad2.append("**还残留 MOE_FILL_* 次光参数** —— 次光应完全不动")
    if "SideSign" in code_only or "SpreadRad" in code_only:
        toon_bad2.append("**次光还在被赋方向** —— 应沿用原版轮廓光")
    # 位置若被改回原版世界坐标，就与自定义方向不自洽（衰减/锥角会打架）。
    # 注意这里查的是 `.xyz =` 重新赋值，声明行 `float4 PosRadius = ...` 不算。
    if re.search(r"PosRadius\.xyz\s*=\s*Light_Position_Radius", code_only):
        toon_bad2.append(
            "**位置还用原版世界坐标** —— 与自定义方向不自洽，衰减/锥角会打架")
    # 参数必须真的被读到（写了 #define 却没用 = 没生效）。
    # **code_only 已剔掉 # 开头的行**，所以 #ifndef/#define 两行不在里面，
    # 参数在使用处出现 1 次即正常 —— 阈值写 <2 会把正常情况误报
    # （我第一版就是照「定义+使用=2 次」想的，忘了定义行已被剔除）。
    for _n in ("MOE_KEY_VANILLA", "MOE_KEY_UP_MIX", "MOE_KEY_SIDE_DEG",
               "MOE_KEY_INTENSITY_MUL"):
        if _n not in code_only:
            toon_bad2.append(f"{_n} 只有定义没被使用")
    if re.search(r"MoeRotateY\(", code_only):
        toon_bad2.append(
            "还在用 MoeRotateY 搬光源 —— 这一版改成直接给方向、不搬位置")
    for eff in TARGETS:
        m = re.search(r"^Effect[ \t]+" + re.escape(eff)
                      + r"[ \t]*\r?\n\{(.*?)^\}", s, re.S | re.M)
        if not m:
            continue
        has = "MOE_FACE_FILL" in m.group(1)
        want = eff in EXPECT_FACE_FILL
        if has != want:
            toon_bad2.append(
                f"{eff} {'不该' if has else '应该'}带 MOE_FACE_FILL")
    if toon_bad2:
        print(f"[shader] **卡通化自检失败：{toon_bad2}**", file=sys.stderr)
        return 1
    print(f"[shader] 加了卡通化 {len(toned)} 个：{toned}")
    print(f"[shader] SSAO 降权 {n_ssao} 处")
    print(f"[shader] 皮肤免投影 {len(noshadow)} 个：{noshadow}"
          f"（衣服/头发照常接受投影）")
    if facefill:
        print(f"[shader] 面部补光 {len(facefill)} 个：{facefill}"
              f"（跟随相机的半兰伯特）")
    def _num(nm, dflt="?"):
        mm = re.search(r"#define " + nm + r" (-?[0-9.]+)f", s)
        return mm.group(1) if mm else dflt
    _kv = re.search(r"#define MOE_KEY_VANILLA (\d)", s)
    if _kv and _kv.group(1) == "1":
        print(f"[shader] 主光 **恢复原版**（MOE_KEY_VANILLA 1，2026-09-07）：三盏灯都按引擎传入的位置/类型走原版分支；"
              f"下面的方向/强度参数当前不参与，置 0 才回底光方案")
    else:
        print(f"[shader] 只改主光（次光沿用原版；主光颜色也沿用原版，只改方向与强度）：")
    print(f"[shader]   主光 从下往上 {_num('MOE_KEY_UP_MIX')}"
          f"（0=水平正前 1=正下方，**舞台底光**）、"
          f"水平偏 {_num('MOE_KEY_SIDE_DEG')}°"
          f"（正=角色右侧；符号未经实机单独确认，偏错就取负）、"
          f"强度 ×{_num('MOE_KEY_INTENSITY_MUL')}")
    # 2026-09-11 加 ToonGain 后这句原来的「不改亮部亮度」不再成立：亮档 =
    # LIT_LEVEL × ToonGain，随场景光走。描述必须跟着改 —— 排查下一个问题时
    # 正是靠这几行判断当前状态，留着旧描述等于埋一条假线索。
    print(f"[shader]   注意：主光强度主要改**暗部面积**（ToonHue 已归一化）；"
          f"亮档亮度 = LIT_LEVEL {_num('MOE_TOON_LIT_LEVEL')}"
          f" × ToonGain（sqrt 压缩，夹 {_num('MOE_TOON_GAIN_MIN')}~"
          f"{_num('MOE_TOON_GAIN_MAX')}，基准 REF {_num('MOE_TOON_GAIN_REF')}）"
          f"，随场景光变化；高光会真的变弱")
    print(f"[shader]   两盏次光**完全不动** —— 原版轮廓光在角色背后"
          f"（右 -120° 暖 / 左 -160° 冷），负责勾边")
    print(f"[shader]   不搬位置 —— 世界坐标/look_at 节点/角度符号都不必再标定")
    print(f"[shader] 脸/眼补光 {_num('MOE_FACE_FILL_INTENSITY')}，身体/衣物/头发/附件共用补光 {_num('MOE_SKIN_FILL_INTENSITY')}")
    # 分档范围直接从 TOON_EFFECTS 派生，别写死成「仅脸眼」—— 2026-09-11 把 skin
    # 加进卡通化后这句描述就成了假的，而排查下一个问题时正是靠它判断当前状态。
    print(f"[shader] 卡通两档：{sorted(toon_effects)}；其余走原版连续光照")
    _dbl_miss = []
    for eff in TARGETS:
        _m = effect_block_pattern(eff).search(s)
        if not _m or "DOUBLE_SIDED_ENABLED" not in _m.group(2):
            _dbl_miss.append(eff)
    if s.count("uint3 MoeColorBits = asuint( Color );") != _n_out:
        print("[shader] **双面背面 NaN 守卫自检失败**", file=sys.stderr)
        return 1
    cutout_effects = [eff for eff in TARGETS if "MOE_ALPHA_CUTOUT" in effect_block_pattern(eff).search(s).group(2)]
    if cutout_effects != ["moe_portrait_attachment"]:
        raise RuntimeError(f"unexpected cutout effects: {cutout_effects}")
    if _dbl_miss or s.count("PDX_IsFrontFace ? 1 : -1") < 2:
        print(f"[shader] **双面自检失败：缺宏 {_dbl_miss}，翻转分支 "
              f"{s.count('PDX_IsFrontFace ? 1 : -1')} 处**", file=sys.stderr)
        return 1
    print("[shader] 自检通过：6 个 Effect 全部 CullMode=none，"
          f"{len(toon_effects)} 个带 MOE_TOON，光照/反射率已分离，"
          f"补光位置合规，主光方向已自定义、次光为原版")
    print(f"[shader] Moe effect aliases: {aliases}")
    return build_court_shader(ver)


if __name__ == "__main__":
    sys.exit(main())
