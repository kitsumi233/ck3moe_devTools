# -*- coding: utf-8 -*-
"""从原版 jomini/gui_portrait.shader 生成带黑色描边的 moe 版本。

## 这是 GUI 层的描边，**不是** moe 专属

`shaderfile` 写在 gui 控件上，**按控件选不按角色** —— 和布光配置同一个结构性
限制（见 ck3-portrait-light-give-direction-not-position）。所以主立绘控件换用
这份 shader 后，**全游戏所有 NPC 一起描边**。用户 2026-08-25 明确选了这条路。

要只影响 moe 就只能走 moe_portrait.shader 里的边缘光压暗（不是真描边，
宽度随曲率变、平坦区没有线）。两者互斥，这次做的是前者。

## 为什么是外轮廓而不是内部线条

这一层拿到的是**已经渲好的立绘贴图**，没有深度也没有法线缓冲，只能靠 alpha
找剪影边界。所以鼻梁、下颌线、衣褶那些内部结构线做不出来。

内部描边的三种标准做法在 CK3 都堵着：
  1. 背面外扩（法线外推 + FrontCCW 只画背面）—— 一条 meshsettings 只吃一个
     shader，Effect 里没有多 pass 语法；复制一份放大的 mesh 也不行，我们的
     mesh 是**世界坐标**，scale 会把 83.46 的基准偏移一起乘掉、头飞到身体上方
     （见 moe-mesh-is-world-space-so-scale-breaks-attach）
  2. 深度/法线 Sobel —— 拿不到深度缓冲，立绘渲进独立 RT
  3. 屏幕空间边缘检测 —— 同上

## 描边算法沿用原版 painterly 那份

`game/gfx/FX/gui_painterly_portrait.shader` 的 `AddAlphaOutline()`：在半透明
像素周围采样一圈，命中不透明邻居就涂成描边色。原版只用在传奇小头像
（`portrait_head_small_legend`）那一个控件上，参数是 12 采样 / 宽 4.0 /
阈值 0.9 / 色 (0.18,0.15,0.15)。

**改了两处**：
  · 描边色改成更黑的 (0.06,0.05,0.06)，赛璐璐要的是黑线不是深褐
  · 宽度不走 `PopOutThreshold`。原版 painterly 把那个字段挪用成描边宽度，
    代价是 pop-out 功能被废掉（它自己注释写了 `if( false /* ... */ )`）。
    我们的主立绘控件**要保留 pop-out**（`portrait_character_view_main`
    在用），所以宽度写成常量宏，不抢那个字段。

## 采样必须在 vMask 之前

`vPortrait *= vMask` 会把立绘按遮罩裁掉，裁完再找 alpha 边界，描边就贴着
遮罩边缘画一圈方框，而不是贴着角色剪影。所以描边在乘遮罩**之前**做。
这类「顺序错」编译能过、宏也都在，只有位置判据挡得住 —— 自检里比较了两段
代码在文件里的先后 index 并做反向验证。
"""
import sys as _sys
from pathlib import Path as _P
_sys.path[:0] = [str(_P(__file__).resolve().parents[2] / "tools" / _d) for _d in ("lib", "anim")]   # 子目录化后底座不在脚本同目录
import moe_out as O          # 产物归属：谁写谁声明
import argparse
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VANILLA_SHADER = Path(
    r"E:\SteamLibrary\steamapps\common\Crusader Kings III"
    r"\jomini\gfx\FX\jomini\gui_portrait.shader")
OUT_REL = "gfx/FX/moe/moe_gui_portrait.shader"

# ---------------------------------------------------------------- 参数与函数

PARAMS = """
			// ==================== 黑色描边（MOE_OUTLINE）====================
			// 在立绘剪影外围画一圈黑边。算法沿用原版
			// game/gfx/FX/gui_painterly_portrait.shader 的 AddAlphaOutline。
			//
			// **这一层是 GUI 后处理，按控件生效、不区分角色** —— 换用这份
			// shader 的控件上，所有 NPC 都会描边。只影响 moe 做不到，那是
			// shaderfile 写在控件上的结构决定的。
			//
			// 只有外轮廓，没有内部线条：这一层拿到的是渲好的立绘贴图，
			// 没有深度也没有法线，只能靠 alpha 找剪影边界。

			// 采样圈的方向数。12 是原版值，够圆；调低会让描边出现棱角。
			#ifndef MOE_OUTLINE_SAMPLES
				#define MOE_OUTLINE_SAMPLES 12
			#endif
			// 描边宽度，单位是**立绘贴图的像素**（不是屏幕像素）。
			// 立绘贴图分辨率随控件大小变，所以视觉宽度大体跟着控件缩放。
			//
			// 调整记录：3.0 用户反馈「再淡一些」→ 2.0（用户指定值）。
			// 再往下调 1.0 会开始断线 —— 描边靠邻域采样，半径小于一个
			// 贴图像素时圆周上采不满，边缘会出现虚线感。
			//
			// **不走 PopOutThreshold**。原版 painterly 把那个字段挪用成描边
			// 宽度，代价是 pop-out 功能被废（它自己写了 if( false )）。我们的
			// 主立绘控件要保留 pop-out，所以宽度用常量、不抢那个字段。
			// **宽度单位是「贴图高 MOE_OUTLINE_REF_PX 时的像素」**，实际偏移再乘 PortraitTextureSize.y/REF，
			// 所以线宽占角色高度的比例与立绘分辩率无关。此前单位是当前贴图像素：4K 立绘上 3 像素只占
			// 角色高度 0.1%，用户 2026-09-07「身体、脸部、大腿描边还是很不明显」。09-08 试 6/2 后「黑边太粗」，
			// 换装窗口里发际/衣缝一圈灰晕（内部线 2@2048 = 4K 上 4 像素）→ 剪影 1.5、内部线 1.0（2048 基准）。
			#ifndef MOE_OUTLINE_WIDTH
				#define MOE_OUTLINE_WIDTH 2.0f   // 09-08 晚用户「描边加粗到 2」（此前 1.5）
			#endif
			#ifndef MOE_OUTLINE_REF_PX
				#define MOE_OUTLINE_REF_PX 2048.0f
			#endif
			// alpha 低于这个值的像素才考虑画描边（即「在剪影外面」）。
			#ifndef MOE_OUTLINE_ALPHA_THRESHOLD
				#define MOE_OUTLINE_ALPHA_THRESHOLD 0.9f
			#endif
			// 描边颜色。原版 painterly 用 (0.18,0.15,0.15) 偏深褐，
			// 赛璐璐要的是黑线，所以压到接近纯黑。
			#ifndef MOE_OUTLINE_COLOR
				#define MOE_OUTLINE_COLOR float3( 0.06f, 0.05f, 0.06f )
			#endif

			// 在剪影外围找一圈：当前像素接近透明、但周围有不透明像素，
			// 就把它涂成描边色。alpha 取采到的最大值，让描边边缘自带抗锯齿。
			float4 MoeAddAlphaOutline( float4 Color, float2 PortraitUV, float Lod )
			{
				if( Color.a > MOE_OUTLINE_ALPHA_THRESHOLD )
				{
					return Color;
				}

				float2 SourcePixelRatio = float2( 1.0f / PortraitTextureSize.x,
												  1.0f / PortraitTextureSize.y );
				float RadPerSample = ( ( 360.0f / float( MOE_OUTLINE_SAMPLES ) ) * PI ) / 180.0f;
				float4 OutColor = float4( 0.0f, 0.0f, 0.0f, 0.0f );

				// 固定采样圈展开，让编译器折叠方向并消除循环内 sincos。
				[unroll]
				for( int i = 0; i < MOE_OUTLINE_SAMPLES; ++i )
				{
					float SampleRad = float( i + 1 ) * RadPerSample;
					// 乘 (Y/REF)：偏移按参考分辩率的像素数给，与当前贴图大小无关
					float2 Offset = float2( cos( SampleRad ), -sin( SampleRad ) )
									* SourcePixelRatio * MOE_OUTLINE_WIDTH
									* ( PortraitTextureSize.y / MOE_OUTLINE_REF_PX );
					float4 SampleColor = PdxTex2DLod( Portrait, PortraitUV + Offset, Lod );
					if( SampleColor.a > OutColor.a )
					{
						OutColor.rgb = MOE_OUTLINE_COLOR;
						OutColor.a = SampleColor.a;
					}
				}

				if( OutColor.a > 0.0f )
				{
					return OutColor;
				}
				return Color;
			}

			// ==================== 内部线条（MOE_INNER）====================
			// 剪影内部按**色度差**找材质边界（头发/脸、衣服分块、皮肤/衣领）：取对向一对邻居，
			// 归一化 rgb 的距离 + 大幅明度比 超阈值就把当前像素压向描边色。
			// 卡通两档的明暗交界色度相同、明度比 (0.26-0.11)/0.26≈0.58，LUMA 阈值 0.7 让它**不**出线。
			// 邻居任一半透明时不算（那是剪影，归 MoeAddAlphaOutline）。
			#ifndef MOE_INNER_WIDTH
				#define MOE_INNER_WIDTH 1.0f
			#endif
			#ifndef MOE_INNER_CHROMA
				#define MOE_INNER_CHROMA 0.12f
			#endif
			#ifndef MOE_INNER_LUMA
				#define MOE_INNER_LUMA 0.70f
			#endif
			#ifndef MOE_INNER_STRENGTH
				#define MOE_INNER_STRENGTH 0.85f
			#endif
			float3 MoeChroma( float3 C )
			{
				return C / max( C.r + C.g + C.b, 0.02f );
			}
			float MoeInnerDiff( float2 UV, float2 D, float Lod )
			{
				float4 A = PdxTex2DLod( Portrait, UV + D, Lod );
				float4 B = PdxTex2DLod( Portrait, UV - D, Lod );
				if( A.a < MOE_OUTLINE_ALPHA_THRESHOLD || B.a < MOE_OUTLINE_ALPHA_THRESHOLD )
				{
					return 0.0f;
				}
				float Chroma = length( MoeChroma( A.rgb ) - MoeChroma( B.rgb ) );
				float La = dot( A.rgb, float3( 0.2126f, 0.7152f, 0.0722f ) );
				float Lb = dot( B.rgb, float3( 0.2126f, 0.7152f, 0.0722f ) );
				float Luma = abs( La - Lb ) / max( max( La, Lb ), 0.02f );
				return max( Chroma / MOE_INNER_CHROMA, Luma / MOE_INNER_LUMA );
			}
			float4 MoeAddInnerLine( float4 Color, float2 PortraitUV, float Lod )
			{
				if( Color.a < MOE_OUTLINE_ALPHA_THRESHOLD )
				{
					return Color;
				}
				float2 Px = float2( 1.0f / PortraitTextureSize.x, 1.0f / PortraitTextureSize.y ) * MOE_INNER_WIDTH
							* ( PortraitTextureSize.y / MOE_OUTLINE_REF_PX );   // 同剪影描边：按参考分辩率像素给
				float Edge = MoeInnerDiff( PortraitUV, float2( Px.x, 0.0f ), Lod );
				Edge = max( Edge, MoeInnerDiff( PortraitUV, float2( 0.0f, Px.y ), Lod ) );
				Edge = max( Edge, MoeInnerDiff( PortraitUV, Px * 0.7071f, Lod ) );
				Edge = max( Edge, MoeInnerDiff( PortraitUV, float2( Px.x, -Px.y ) * 0.7071f, Lod ) );
				float Ink = smoothstep( 0.8f, 1.2f, Edge ) * MOE_INNER_STRENGTH;
				Color.rgb = lerp( Color.rgb, MOE_OUTLINE_COLOR, Ink );
				return Color;
			}
"""

# 插入点与调用点都用**正则**定位，不用字面量匹配 —— 原版这两处都带尾随
# tab/空格（`{` 后面 4 个 tab、`vPortrait` 前后空格数不一），字面量对不上。
# 第一版就是这么栽的：报「找不到锚点」，cat -A 才看见尾随空白。
ANCHOR_FN_RE = re.compile(
    r"(\n\t{3}float4 BlendColorPreMultiplied\([^\n]*\n\t{3}\{[ \t]*\n"
    r"\t{4}return Front[^\n]*\n\t{3}\}[ \t]*\n)")

# 调用点：取到 vPortrait 之后立刻描边。**必须在 vPortrait *= vMask 之前** ——
# 乘完遮罩再找边界，描边会贴着遮罩画方框而不是贴着角色剪影。
ANCHOR_CALL_RE = re.compile(
    r"(\n\t{4}float4 vPortrait\s*=\s*PdxTex2DLod\( Portrait,"
    r" PortraitUV, Input\.Lod \);[ \t]*\n)")
CALL_EXTRA = (
    "\t\t\t\t// 描边**必须在乘 vMask 之前**：乘完遮罩再找 alpha 边界，\n"
    "\t\t\t\t// 描边就贴着遮罩边缘画一圈方框，而不是贴着角色剪影。\n"
    "\t\t\t\tvPortrait = MoeAddAlphaOutline( vPortrait, PortraitUV, Input.Lod );\n"
    "\t\t\t\t// 内部线条（色度差边检测）同样要在乘遮罩之前，且在剪影描边之后（描边像素 alpha 已是邻居值）。\n"
    "\t\t\t\tvPortrait = MoeAddInnerLine( vPortrait, PortraitUV, Input.Lod );\n")

HEADER = """######################################################################
#  本文件由 tools/build/build_moe_outline_shader.py 从原版
#  jomini/gfx/FX/jomini/gui_portrait.shader 打补丁生成。不要手改 ——
#  build_moe_mod.py 会 rmtree 整个 mod 目录，改动会丢。
#
#  作用：给立绘剪影加一圈黑色描边。
#  **按控件生效、不区分角色** —— 换用这份 shader 的控件上所有 NPC 都会描边。
######################################################################
"""


def build(text):
    """返回 (新文本, 日志行列表)。"""
    log = []
    n_fn = len(ANCHOR_FN_RE.findall(text))
    if n_fn != 1:
        raise SystemExit(
            f"[outline] 函数插入锚点命中 {n_fn} 次，应为 1"
            f"（BlendColorPreMultiplied 定义）")
    n_call = len(ANCHOR_CALL_RE.findall(text))
    if n_call != 1:
        raise SystemExit(f"[outline] vPortrait 取样锚点命中 {n_call} 次，应为 1")

    text = ANCHOR_FN_RE.sub(lambda m: m.group(1) + PARAMS, text, count=1)
    log.append("插入描边参数与 MoeAddAlphaOutline")
    text = ANCHOR_CALL_RE.sub(lambda m: m.group(1) + CALL_EXTRA, text, count=1)
    log.append("在 vPortrait 取样后、乘遮罩前调用描边")
    return HEADER + text, log


def check(s, vanilla_text):
    """自检。每条「必须这么写」都配一条「不能那样写」—— 只查正确写法在不在
    挡不住换成另一种合法形式，反向验证会给退出码 0（这个模式已栽过五次）。"""
    bad = []

    # 注释里会引用被否决的写法作说明，所以搜代码模式前先剔掉注释行。
    # 这个坑已经踩过五次（布光颜色、饱和度、affected_by_shadow 计数、
    # length(P0) 说明文字、KeyHoriz 计数）。
    code_only = "\n".join(
        ln for ln in s.splitlines()
        if not ln.lstrip().startswith(("//", "#")))

    for name in ("MOE_OUTLINE_SAMPLES", "MOE_OUTLINE_WIDTH", "MOE_OUTLINE_REF_PX",
                 "MOE_OUTLINE_ALPHA_THRESHOLD", "MOE_OUTLINE_COLOR"):
        if name not in s:
            bad.append(f"缺参数 {name}")
        if name not in code_only:
            bad.append(f"{name} 只有定义没被使用")

    for name in ("MOE_INNER_WIDTH", "MOE_INNER_CHROMA", "MOE_INNER_LUMA", "MOE_INNER_STRENGTH"):
        if name not in s:
            bad.append(f"缺参数 {name}")
        if name not in code_only:
            bad.append(f"{name} 只有定义没被使用")
    if "float4 MoeAddInnerLine" not in s or "float MoeInnerDiff" not in s:
        bad.append("缺内部线条函数")
    if len(re.findall(r"vPortrait = MoeAddInnerLine\(", code_only)) != 1:
        bad.append("内部线条调用不是恰好 1 次")
    i_inner = s.find("vPortrait = MoeAddInnerLine(")
    if i_inner > 0 and (i_inner > s.find("vPortrait *= vMask;") or i_inner < s.find("vPortrait = MoeAddAlphaOutline(")):
        bad.append("**内部线条的调用顺序错**：应在剪影描边之后、乘遮罩之前")
    # 反向：对向采样必须一加一减（单侧差分会把线画偏到边界一侧）
    if "UV + D" not in code_only or "UV - D" not in code_only:
        bad.append("内部线条不是对向差分")
    if "Color.rgb = lerp( Color.rgb, MOE_OUTLINE_COLOR, Ink );" not in s:
        bad.append("内部线条颜色没用 MOE_OUTLINE_COLOR 宏")
    if "float4 MoeAddAlphaOutline" not in s:
        bad.append("缺描边函数")
    # 函数存在但没被调用 = 从没执行、画面毫无变化。
    n_call = len(re.findall(r"vPortrait = MoeAddAlphaOutline\(", code_only))
    if n_call != 1:
        bad.append(f"描边调用 {n_call} 次，应恰好 1 次")

    # **位置判据**：描边必须在乘遮罩之前。顺序错编译能过、宏也都在，
    # 只有比较先后 index 挡得住。同类坑：补光必须在二值化之前、
    # MOE_NO_SHADOW 必须在读 _ShadowTerm 之前。
    i_outline = s.find("vPortrait = MoeAddAlphaOutline(")
    i_mask = s.find("vPortrait *= vMask;")
    if i_outline < 0:
        bad.append("找不到描边调用")
    elif i_mask < 0:
        bad.append("找不到 vPortrait *= vMask（原版结构变了）")
    elif i_outline > i_mask:
        bad.append(
            "**描边排在乘遮罩之后** —— 会贴着遮罩边缘画方框，不是角色剪影")

    # 反向：宽度不能抢 PopOutThreshold。原版 painterly 那么做的代价是
    # pop-out 功能被废，而我们的主立绘控件在用 pop-out。
    if re.search(r"MOE_OUTLINE_WIDTH\s+PopOutThreshold", s) or \
            re.search(r"\*\s*PopOutThreshold", code_only):
        bad.append(
            "**描边宽度抢用了 PopOutThreshold** —— 会废掉主立绘的 pop-out")

    # 反向：描边色不能是硬编码字面量（改宏不生效）。
    if re.search(r"OutColor\.rgb\s*=\s*float3\(", code_only):
        bad.append("**描边色写成了字面量** —— 改 MOE_OUTLINE_COLOR 不生效")
    if "OutColor.rgb = MOE_OUTLINE_COLOR;" not in s:
        bad.append("描边色没用宏")

    # 反向：alpha 阈值的比较方向不能反。写成 `< THRESHOLD` 就变成「只给
    # 不透明像素描边」，整个角色会被涂成黑块。
    if "if( Color.a > MOE_OUTLINE_ALPHA_THRESHOLD )" not in s:
        bad.append("alpha 阈值的早退判断写法不对（方向反了会把角色涂成黑块）")

    # 采样循环必须真的用上 SAMPLES，且圈上每个方向都要采（cos/sin 都在）。
    for pat, msg in [
        (r"i\s*<\s*MOE_OUTLINE_SAMPLES", "采样数没用在循环条件上"),
        (r"cos\(\s*SampleRad\s*\)", "采样方向缺 cos 分量"),
        (r"-\s*sin\(\s*SampleRad\s*\)", "采样方向缺 sin 分量"),
        (r"\*\s*MOE_OUTLINE_WIDTH", "宽度没乘进偏移量"),
        (r"SourcePixelRatio", "偏移没换算成贴图像素比例"),
    ]:
        if not re.search(pat, code_only):
            bad.append(msg)
    # 宽度必须与贴图分辩率无关：两处偏移都要乘 (PortraitTextureSize.y / MOE_OUTLINE_REF_PX)
    if code_only.count("PortraitTextureSize.y / MOE_OUTLINE_REF_PX") != 2:
        bad.append("**描边/内部线宽度没按参考分辩率换算** —— 4K 立绘上线会变细一半")
    for pat, msg in []:
        if not re.search(pat, code_only):
            bad.append(msg)

    # 原版的每个 Effect 必须一个不少 —— 少一个，对应的控件状态
    # （Over/Down/Disabled…）会静默丢 shader。
    #
    # **期望值从原版源文件实测，不写死数字**：我第一版写死 10，原版实际是 9，
    # 正确的产物被判失败。但也**不能只比计数** —— 那样名字被改错就查不出来。
    # 所以比**名字集合**：块被吃掉、名字写错、意外多出一个，三种都拦得住。
    #
    # 拿源文件当期望值不构成自我印证：它是原版只读文件，不是我们的产物。
    # （自我印证的反面教材：`want = eff in NO_SHADOW_TARGETS`，
    # 拿被测配置本身当期望值，判据永远绿。）
    want_eff = set(re.findall(r"^Effect\s+(\w+)", vanilla_text, re.M))
    got_eff = set(re.findall(r"^Effect\s+(\w+)", s, re.M))
    if got_eff != want_eff:
        miss = sorted(want_eff - got_eff)
        extra = sorted(got_eff - want_eff)
        bad.append(f"Effect 集合不符：缺 {miss}、多 {extra}"
                   f"（原版 {len(want_eff)} 个）")

    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()

    src = io.open(VANILLA_SHADER, encoding="utf-8-sig").read()
    out, log = build(src)

    bad = check(out, src)
    if bad:
        print(f"[outline] **自检失败：{bad}**", file=sys.stderr)
        return 1

    dst = O.root(O.BIG) / OUT_REL
    if not a.check_only:
        dst.parent.mkdir(parents=True, exist_ok=True)
        io.open(dst, "w", encoding="utf-8", newline="").write(out)

    def _num(nm):
        # 取到行尾而不是第一个 token —— `float3( 0.06f, 0.05f, 0.06f )` 里有
        # 空格，用 (\S+) 只会抓到 "float3(" 而让人以为色值没写对。
        m = re.search(r"#define " + nm + r"[ \t]+(.+)", out)
        return m.group(1).strip() if m else "?"

    print(f"[outline] 源 {VANILLA_SHADER}")
    print(f"[outline] 出 {dst}  ({len(out.splitlines())} 行)")
    for ln in log:
        print(f"[outline]   {ln}")
    print(f"[outline] 描边 {_num('MOE_OUTLINE_SAMPLES')} 采样 / "
          f"宽 {_num('MOE_OUTLINE_WIDTH')} 像素@贴图高 {_num('MOE_OUTLINE_REF_PX')}（按分辩率换算） / "
          f"色 {_num('MOE_OUTLINE_COLOR')}")
    print(f"[outline] **按控件生效、不区分角色** —— 用这份 shader 的控件上"
          f"所有 NPC 都会描边")
    n_eff = len(re.findall(r"^Effect\s+\w+", out, re.M))
    print(f"[outline] 自检通过：描边在乘遮罩之前、{n_eff} 个 Effect 与原版一致、"
          f"未抢用 PopOutThreshold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
