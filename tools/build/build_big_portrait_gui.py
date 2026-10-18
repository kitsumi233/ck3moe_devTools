"""把「放大立绘」的能力合并进我们自己的 mod，从**原版文件**重新打补丁。

## 为什么不直接复制 Big Portraits 的文件

`Big Portraits` 的 `gui/window_character.gui` 是一份**缝合了别家 mod 挂钩的
脏版本**（BUI / Carnalitas / Factions Explained）：里面有 `carn_lactation_gui`、
`SEX_TT`、`fe_calculate_faction_score`、`hbox_mod_renamer`、`button_arrow_up`、
`gfx/marry_ring.png` 等等。那些 mod 没开时，实测刷出 206 条
`marry_ring.png not found` + 6 条 `button_arrow_up is not a valid widget`
+ 若干 `Unlocalized text 'SEX_TT'`。功能不受影响，但日志被淹掉，真问题看不见。

而它为「放大」真正做的改动只有下面 PATCHES 里那几条。所以本脚本从原版
`game/gui/` 读原文、只打这几个补丁，得到干净版本。

## 另一个取舍：Big Portraits 删掉的原版功能我们不跟

它顺手删了三样东西，我们保留：

  - 配偶框的 `portrait_status_icons`（状态图标）
  - 配偶框的 `coa_realm_small`（纹章）
  - `not_at_home_icon`（「离家在外」图标）

## 为什么必须是脚本而不是手写文件

`build_moe_mod.py` 会 `shutil.rmtree(MOD)` 清空整个 mod 目录再重建，
手写进去的 gui 文件会被冲掉。见 [[moe-mod-build-order-matters]]。

## 相机为什么走新文件追加

`gfx/portraits/cameras/` 下的相机定义**可以新文件追加**，引擎会合并。
绝不要整体覆盖原版 `portrait_cameras.txt` —— `MuchBiggerPortraits-ZM`
（显示名「Bigger Lower Portrait」）就是那么做的，它那份只有 71 个相机而
原版 1.18.2 有 149 个，丢了 78 个，其中 72 个名字散落在 187 处事件文件里
（加冕/狩猎/科举/庆典的演出相机），另有 `camera_hud`、`camera_torso_left/right`
被原版 gui 引用。

用法:
  python tools/build/build_big_portrait_gui.py            # 写入 mod 目录
  python tools/build/build_big_portrait_gui.py --check     # 只校验，不写
"""
import sys as _sys
from pathlib import Path as _P
_sys.path[:0] = [str(_P(__file__).resolve().parents[2] / "tools" / _d) for _d in ("lib", "anim")]   # 子目录化后底座不在脚本同目录
import moe_out as O          # 产物归属：谁写谁声明
import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VANILLA = Path(r"E:\SteamLibrary\steamapps\common\Crusader Kings III\game")
# 放大立绘的产物（相机 + 打补丁的 gui）归 moe_big_portrait。
MOD = O.root(O.BIG)

# ---------------------------------------------------------------- 相机

# 三个新相机。**新名字、新文件**，不碰原版任何相机。
#
# 放大的原理是「拉远 + 收窄 fov」的组合：可见范围 r·tan(fov/2) 几乎不变，
# 但控件被同步放大，同样内容铺到更多像素上 —— 看起来就是立绘变大，
# 且长焦畸变比原版的 fov 30 更小，脸不外凸。
#
#   原版 camera_character_view_main  r=190 fov=30  可见半高 50.9
#   本文件 _main_big                 r=335 fov=20  可见半高 59.1
#   原版 camera_hud                  r=280 fov=20  可见半高 49.4
#   本文件 _hud_big                  r=400 fov=20  可见半高 70.5
#
# `_hud_big` 的参数对齐 Bigger Lower Portrait 的 camera_body_mod_bigger
# （r 400 / h -60 / look_at -30），用户 2026-08-18 指定「大小放到它那么大」。
#
# look_at 的 U/D 往下压是为了让画面露出躯干而不是只有头。
CAMERAS = """\
#########################################################################
#																		#
#				MOE 放大立绘相机（追加，不覆盖原版）					#
#																		#
#	由 tools/build/build_big_portrait_gui.py 生成，不要手改。					#
#	思路来自 Big Portraits / Bigger Lower Portrait，但从原版重打补丁。	#
#																		#
#########################################################################

# 角色面板主立绘。r 190→335、fov 30→20、look_at 5→-22。
camera_moe_character_view_main_big = {
	camera = {
		position = cylindrical{ 335 5 0 } # Radius, height, angle
		position_node = {
			default = camera_torso_look_at
		}

		look_at = {	-5 -22 0 } # L/R, U/D, F/B
		look_at_node = {
			default = camera_torso_look_at
		}
		fov = 20
		camera_near_far = { 10 800 }
	}

	unknown = camera_torso
}

# 配偶框。与 main 同参数，只是角度偏 -15 保持原版的侧身取景。
camera_moe_character_view_spouse_big = {
	camera = {
		position = cylindrical{ 335 0 -15 } # Radius, height, angle
		position_node = {
			default = camera_torso_look_at
		}

		look_at = {	-5 -22 0 } # L/R, U/D, F/B
		look_at_node = {
			default = camera_torso_look_at
		}
		fov = 20
		camera_near_far = { 10 800 }
	}

	unknown = camera_torso
}

# 大地图左下角 HUD 头像。对齐 Bigger Lower Portrait 的尺度：
# r 400 / h -60 / look_at -30，可见半高 70.5，配合 580x1080 的控件
# 露出接近全身而不是半身。
camera_moe_character_view_hud_big = {
	camera = {
		position = cylindrical{ 400 -60 0 } # Radius, height, angle
		position_node = {
			default = camera_torso_look_at
		}

		look_at = {	-5 -30 0 } # L/R, U/D, F/B
		look_at_node = {
			default = camera_torso_look_at
		}
		fov = 20
		camera_near_far = { 10 800 }
	}

	unknown = camera_torso
}
"""

# ------------------------------------------------------- gui 补丁清单

# 每条 (文件, 说明, old, new, 期望命中次数)。
# old 必须在原版里**恰好**命中期望次数 —— 命中数不对就报错停下，
# 因为那意味着原版改了版式，闭眼替换会写出坏文件。
PATCHES = [
    # ---- gui/shared/portraits.gui ----
    # ---- 黑色描边（必须排在改 size 的补丁之前：锚点用的就是原版 size）----
    # 锚点带各控件独有的 size，因为 `parentanchor + using = portrait_base`
    # 在原版出现十几次、不唯一（第一版这么写，被命中次数校验挡住了）。
    (
        "gui/shared/portraits.gui",
        "角色面板主立绘（普通分支 Saint 版，1.20 新增）加黑色描边",
        '			size = { 380 421 }\n'
        '			position = { -30 10 }\n'
        '			parentanchor = bottom|hcenter\n'
        '			using = portrait_base\n',
        '			size = { 380 421 }\n'
        '			position = { -30 10 }\n'
        '			parentanchor = bottom|hcenter\n'
        '			using = portrait_base\n'
        '			# 黑色描边（覆盖 portrait_base 继承来的 shaderfile）。\n'
        '			# **按控件生效、不区分角色** —— 所有 NPC 都会描边。\n'
        '			shaderfile = "gfx/FX/moe/moe_gui_portrait.shader"\n',
        1,
    ),
    (
        "gui/shared/portraits.gui",
        "角色面板主立绘（普通分支，type 定义）加黑色描边",
        '	type portrait_character_view_main_button = portrait_button {\n'
        '		size = { 380 421 }\n'
        '		position = { -30 10 }\n'
        '		parentanchor = bottom|hcenter\n'
        '		using = portrait_base\n',
        '	type portrait_character_view_main_button = portrait_button {\n'
        '		size = { 380 421 }\n'
        '		position = { -30 10 }\n'
        '		parentanchor = bottom|hcenter\n'
        '		using = portrait_base\n'
        '		# 黑色描边（1.20 起普通主立绘是 type 定义，缩进少一层）。\n'
        '		shaderfile = "gfx/FX/moe/moe_gui_portrait.shader"\n',
        1,
    ),
    (
        "gui/shared/portraits.gui",
        "角色面板主立绘（herder 分支）加黑色描边",
        '			size = { 1000 421 }\n'
        '			position = { -30 10 }\n'
        '			parentanchor = bottom|hcenter\n'
        '			using = portrait_base\n',
        '			size = { 1000 421 }\n'
        '			position = { -30 10 }\n'
        '			parentanchor = bottom|hcenter\n'
        '			using = portrait_base\n'
        '			# 黑色描边，同上。\n'
        '			shaderfile = "gfx/FX/moe/moe_gui_portrait.shader"\n',
        # 1.20 起 herder 分支多了一个 Saint 版（同样的头 4 行），两处都要描边
        2,
    ),
    (
        "gui/shared/portraits.gui",
        "配偶立绘加黑色描边",
        '			size = { 350 338 }\n'
        '			position = { 0 10 }\n'
        '			parentanchor = bottom|hcenter\n'
        '			using = portrait_base\n',
        '			size = { 350 338 }\n'
        '			position = { 0 10 }\n'
        '			parentanchor = bottom|hcenter\n'
        '			using = portrait_base\n'
        '			# 黑色描边，同上。\n'
        '			shaderfile = "gfx/FX/moe/moe_gui_portrait.shader"\n',
        1,
    ),
    (
        "gui/shared/portraits.gui",
        "地图 HUD 立绘加黑色描边（**用户实际在看的那个立绘**）",
        '		visible = "[Not(GetVariableSystem.Exists( \'lifestyle_open\' ))]"\n'
        '		size = { 350 350 }\n'
        '\n'
        '		gfxtype = portraitbuttongfx\n'
        '		shaderfile = "gfx/FX/jomini/gui_portrait.shader"\n',
        '		visible = "[Not(GetVariableSystem.Exists( \'lifestyle_open\' ))]"\n'
        '		size = { 350 350 }\n'
        '\n'
        '		gfxtype = portraitbuttongfx\n'
        '		# 黑色描边。portrait_hud 自己写了 shaderfile（不是从\n'
        '		# portrait_base 继承），所以直接替换、不用追加覆盖。\n'
        '		shaderfile = "gfx/FX/moe/moe_gui_portrait.shader"\n',
        1,
    ),
    (
        "gui/shared/portraits.gui",
        "角色面板主立绘控件加高 300→400",
        '	type portrait_character_view_main = margin_widget {\n'
        '		size = { 300 300 }\n',
        '	type portrait_character_view_main = margin_widget {\n'
        '		size = { 300 400 }\n',
        1,
    ),
    (
        "gui/shared/portraits.gui",
        "主立绘换用放大相机（2 处：普通 / herder 政体）",
        "'camera_character_view_main', 'idle'",
        "'camera_moe_character_view_main_big', 'idle'",
        2,
    ),
    (
        "gui/shared/portraits.gui",
        "Saint 版主立绘也换用放大相机（1.20 新增 2 处：普通 / herder）",
        "'camera_character_view_main', Select_CString( Character.IsAlive, 'idle', 'dead' )",
        "'camera_moe_character_view_main_big', Select_CString( Character.IsAlive, 'idle', 'dead' )",
        2,
    ),
    (
        "gui/shared/portraits.gui",
        "好感度条随立绘加高下移 5px",
        '			portrait_opinion = {\n'
        '				position = { -20 0 }\n',
        '			portrait_opinion = {\n'
        '				position = { -20 -5 }\n',
        1,
    ),
    (
        "gui/shared/portraits.gui",
        "配偶框控件加高 320x300→300x400",
        '	type portrait_character_view_spouse = margin_widget {\n'
        '		size = { 320 300 }\n',
        '	type portrait_character_view_spouse = margin_widget {\n'
        '		size = { 300 400 }\n',
        1,
    ),
    (
        "gui/shared/portraits.gui",
        "配偶立绘按钮加高 338→388",
        '			size = { 350 338 }\n',
        '			size = { 350 388 }\n',
        1,
    ),
    (
        "gui/shared/portraits.gui",
        "配偶立绘换用放大相机",
        "'camera_character_view_spouse', Select_CString",
        "'camera_moe_character_view_spouse_big', Select_CString",
        1,
    ),
    (
        "gui/shared/portraits.gui",
        "大地图 HUD 头像控件 350x350→580x1080（对齐 Bigger Lower Portrait）",
        '		visible = "[Not(GetVariableSystem.Exists( \'lifestyle_open\' ))]"\n'
        '		size = { 350 350 }\n',
        '		visible = "[Not(GetVariableSystem.Exists( \'lifestyle_open\' ))]"\n'
        '		# 对齐 Bigger Lower Portrait 的尺度（原版 350 350）\n'
        '		size = { 580 1080 }\n',
        1,
    ),
    (
        "gui/shared/portraits.gui",
        "HUD 悬停弹跳关掉：1080 高的控件再放大 35% 会顶出屏幕",
        '			on_start = "[Character.OnMouseEnter]"\n'
        '			scale = 1.35\n',
        '			on_start = "[Character.OnMouseEnter]"\n'
        '			scale = 1.05\n',
        1,
    ),
    (
        "gui/shared/portraits.gui",
        "HUD 悬停离开也关掉弹跳",
        '			on_start = "[Character.OnMouseLeave]"\n'
        '			using = Animation_Curve_Default\n'
        '			duration = 0.05\n'
        '			scale = 1.3\n',
        '			on_start = "[Character.OnMouseLeave]"\n'
        '			using = Animation_Curve_Default\n'
        '			duration = 0.05\n'
        '			scale = 1\n',
        1,
    ),
    (
        "gui/shared/portraits.gui",
        "HUD 头像换用放大相机",
        "'environment_hud', 'camera_hud', 'idle'",
        "'environment_hud', 'camera_moe_character_view_hud_big', 'idle'",
        1,
    ),
    # ---- gui/window_character.gui ----
    # Big Portraits 在这个文件里改了一大堆（scrollbox / 字号 / 别家 mod 挂钩），
    # 但与放大真正相关的**只有这一条**。其余一律不跟。
    (
        "gui/window_character.gui",
        # 1.19.0.6（2026-09-26 更新）原版把这里从 305 改成了 310，补丁锚点跟着改。
        "角色窗口的立绘区加高 310→400（放大后原版版面装不下）",
        '			name = "main_characters"\n'
        '			datacontext = "[GetIllustration( \'character_view_bg\' )]"\n'
        '			layoutpolicy_horizontal = expanding\n'
        '			size = { 0 310 }\n',
        '			name = "main_characters"\n'
        '			datacontext = "[GetIllustration( \'character_view_bg\' )]"\n'
        '			layoutpolicy_horizontal = expanding\n'
        '			size = { 0 400 }\n',
        1,
    ),
]

HEADER = """\
######################################################################
#  本文件由 tools/build/build_big_portrait_gui.py 从原版 {src} 打补丁生成。
#  不要手改 —— build_moe_mod.py 会 rmtree 整个 mod 目录，改动会丢。
#  .gui 只能整文件覆盖、不支持片段合并，所以我们必须接管整份原版文件。
#  副作用：与任何同样覆盖此文件的 mod（Big Portraits 等）互斥，只有
#  加载顺序最后那个生效。这是 CK3 的限制，不是我们的选择。
######################################################################
"""


def patch_file(rel, text, log):
    """对一份原版文件依次打补丁。命中次数不符就抛错。"""
    for pfile, desc, old, new, want in PATCHES:
        if pfile != rel:
            continue
        got = text.count(old)
        if got != want:
            raise SystemExit(
                f"[FAIL] {rel}\n"
                f"  补丁: {desc}\n"
                f"  期望命中 {want} 次，实际 {got} 次。\n"
                f"  原版版式可能变了 —— 请人工核对后更新 PATCHES，"
                f"不要闭眼替换。\n"
                f"  待查片段: {old[:120]!r}"
            )
        text = text.replace(old, new)
        log.append(f"  ✓ {desc}（{got} 处）")
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验补丁能否命中，不写文件")
    a = ap.parse_args()

    targets = sorted({p[0] for p in PATCHES})
    outputs = {}
    log = []

    for rel in targets:
        src = VANILLA / rel
        if not src.is_file():
            raise SystemExit(f"[FAIL] 原版文件不存在: {src}")
        # 原版 gui 有的带 BOM（window_character.gui 就是），用 utf-8-sig 读，
        # 写回时不带 BOM —— 引擎两种都吃。
        text = src.read_text(encoding="utf-8-sig")
        log.append(f"{rel}  ({len(text.splitlines())} 行)")
        outputs[rel] = HEADER.format(src=rel) + patch_file(rel, text, log)

    print("\n".join(log))

    if a.check:
        print("\n--check：补丁全部命中，未写文件。")
        return

    cam_rel = "gfx/portraits/cameras/30_moe_big_portrait_cameras.txt"
    dst = MOD / cam_rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    # **相机文件必须带 BOM**，否则引擎报
    #   File gfx/portraits/cameras/30_moe_big_portrait_cameras.txt should be
    #   in utf8-bom encoding
    # 只有 common/ 与 gfx/ 下的**数据**文件有这条要求；.gui 与 .shader 不报。
    dst.write_text(CAMERAS, encoding="utf-8-sig")
    print(f"\n写入 {cam_rel}  ({len(CAMERAS.splitlines())} 行, 3 个相机)")

    for rel, text in outputs.items():
        dst = MOD / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8")
        print(f"写入 {rel}  ({len(text.splitlines())} 行)")

    # 自检：确认写出的文件里没有别家 mod 的挂钩（那是复制 Big Portraits
    # 会带进来的东西，从原版打补丁不该出现）。
    dirty = ["carn_lactation", "SEX_TT", "fe_calculate_faction_score",
             "hbox_mod_renamer", "marry_ring", "widget_top_character_bui",
             "button_arrow_up", "cse_show_social_relationship"]
    bad = []
    for rel in outputs:
        t = (MOD / rel).read_text(encoding="utf-8")
        bad += [f"{rel}: {d}" for d in dirty if d in t]
    if bad:
        raise SystemExit("[FAIL] 写出的文件含别家 mod 挂钩:\n  " + "\n  ".join(bad))
    print("\n自检: 无别家 mod 挂钩 ✓")


if __name__ == "__main__":
    main()
