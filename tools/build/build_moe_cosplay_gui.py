# -*- coding: utf-8 -*-
"""生成换 cos 服的大窗口（预览模型 + 姿势下拉）。

用户 2026-08-28 第 4 项：「右键菜单我喜欢出现一个更大的窗口，里面可以预览所有
可选模型，所有姿势也在一个下拉菜单里可以选择预览」。

## 为什么是「固定槽位」而不是动态列表

理想做法是 GUI 遍历一个由子 mod 注册的列表。**做不到**：原版 48 处
`datamodel` 全部用 C++ 提供的访问器（`CharacterSelectionList.GetList` 之类），
没有一处遍历脚本侧的变量列表。`GetGlobalList` 在 exe 里存在但原版 `.gui`
**零使用** —— 而 gui 出错不进 `error.log`、窗口直接打不开且没有任何提示，
不能赌未验证的 API。

所以用固定 N 个槽位。每个槽位：
  · 预览图路径写死 `gfx/interface/moe_preview/moe_slot_<i>.dds`
    → **图由子 mod 提供**（用户要求预览图不能放核心），核心只有窗口骨架
  · 显隐读玩家角色的变量 `moe_slot_<i>`
    → 用的是**原版验证过的**写法 `GetPlayer.MakeScope.Var('名').GetValue`
      （`hud.gui` 里 `hide_ui_*` 就是这么读的）
  · 点击 → `GetScriptedGui('moe_pick_slot_<i>').Execute(...)`
    → 照原版 `window_faith.gui` 的 `create_head_of_faith` 按钮

子 mod 在开局把自己占的槽位号写进玩家变量并放好预览图，核心不需要知道装了
哪些模型 —— 与模型池的 on_action 自注册是同一个思路。

## 为什么窗口必须挂进原版 `hud_sidebars.gui`（2026-08-29 修）

**CK3 不会实例化 mod 新增的顶层 `window`。** 顶层 widget 是 C++ 按名字查出来
创建的：`meta_info` / `ingame_topbar` / `character_window` / `hud_sidebars`
在 `ck3.exe` 里都能 grep 到，而我们自造的 `moe_cosplay_window` 出现 **0 次**。
没人创建它，它的 `visible` 表达式**永远不会被求值** —— 表现就是用户报的
「右键点了选项，没有任何窗口弹出」，且 `error.log` 里一条报错都没有。

所以窗口要作为**子 widget** 挂进一个已被实例化的容器。选 `hud_sidebars`：

  · 全屏 `size = { 100% 100% }`，坐标系就是屏幕，`parentanchor = center` 直接可用
  · `visible = "[Or( '(bool)yes', '(bool)yes' )]"` —— 原版写死为真，
    不像 `in_front_topbar`（只在自动存档时显示）会把子控件一起藏掉
  · 整个文件只 171 行，覆盖的代价和与其他 mod 撞车的面都最小
    （`hud.gui` 是 8575 行）

宿主在 `layer = bottom_bottom`，所以窗口自己写 `layer = top` 往上提（层级表在 gui/shared/windows.gui）。
嵌套 widget 自设 layer 的先例是 `hud.gui:181`（`ingame_topbar` 的子 widget 写 `layer = hud_layer`），
但**左下 3D 立绘不受它约束**：windows_layer / top 两轮实机都被遮（2026-09-06 晚）。左下立绘只能像
原版角色窗那样在 _show/_hide state 里置 / 清 gui 变量 `hide_bottom_left_HUD` 藏掉。

覆盖文件是**从原版实读后注入**，不手工维护副本 —— 游戏更新后重跑构建即可。

## 姿势下拉

姿势清单归核心（`animations.txt` 单文件覆盖决定的，见 README），所以可以直接
静态展开成一个 `dropdown` 的选项列表，不需要注册机制。
"""
import sys as _sys
from pathlib import Path as _P
_sys.path[:0] = [str(_P(__file__).resolve().parents[2] / "tools" / _d) for _d in ("lib", "anim")]   # 子目录化后底座不在脚本同目录
import moe_out as O          # 产物归属：谁写谁声明
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# 换装窗口是机制，归核心 —— 不能跟着放大立绘走，否则只装核心 +
# 模型包的用户点交互后什么都不弹。
MOD = O.root(O.CORE)

GROUP = "moe"
ART_REL = "gfx/interface/moe_cosplay"
ART_SOURCE = ROOT / "assets/ui/cosplay_gui"
ART_FILES = ("window_backdrop.dds", "portrait_stage.dds", "title_crest.dds")


def _slot_count():
    """槽位数 = 登记表 moe_chars.CHARS 的长度（含未就绪角色 —— 槽位号是登记表序号，
    不随就绪集合漂移；未就绪的槽位没有子 mod 写玩家变量，按钮自然隐藏）。
    以前从 build_moe_mod.py 源码里正则抠 `SLOT_COUNT = N`，现在 N 本身派生自登记表。
    """
    import moe_chars as X
    return len(X.CHARS)

SLOTS = _slot_count()

# 姿势清单从 tools/lib/moe_pose_catalog.py 派生（原版 animations.txt 全部顶层块，252 个），
# 与 build_moe_mod.py 的 POSES 是同一个来源。键 = 块名，显示文字走本地化
# `moe_cosplay.0002.<键>`、分组标题走 `moe_cos_group_<组键>`（那边已经生成了两种语言）。
import moe_pose_catalog as _PC
_POSE_CAT = _PC.load()
POSE_KEYS = [q["key"] for q in _POSE_CAT]
POSE_GROUPS = _PC.groups(_POSE_CAT)

WIN = f"{GROUP}_cosplay_window"
VAR_TARGET = f"{GROUP}_cos_target"     # 玩家变量：被换装的角色（交互 on_accept 写）
VAR_POSE_PREV = f"{GROUP}_cos_pose_prev"  # gui 变量：右侧预览当前用哪个姿势
VAR_PAGE = f"{GROUP}_cos_page"         # gui 变量：模型网格当前页（值是页号字串，没设=第 1 页）

# 实时预览（用户 2026-09-05：要像 portrait editor 那样实时渲染，不接受缩略图）。
#
# **姿势预览的第三个参数是 animations.txt 的块名，不是 entity state 名**
# （[[ck3-portrait-animation-four-name-layers]]）。原版 `Character.GetAnimatedPortrait`
# 的第三个参数就是字面块名（interaction_templates.gui 传 'idle'/'dead'）。姿势键本身
# 就是块名，所以不需要再映射一层。
PREVIEW_ENV = "environment_body"
PREVIEW_CAM = "camera_body"
# 全身相机配全身遮罩（原版 portrait_body 类型就是这一对）。遮罩边缘羽化，
# 角落 alpha=0，正中 255 —— 肖像在中间、四角淡出。
PREVIEW_MASK = "gfx/portraits/portrait_mask_body.dds"
VAR_OPEN = f"{GROUP}_cos_open"        # gui 变量：窗口是否显示

# ---- 模型网格分页（用户 2026-09-06：「槽位多之后可以也需要换页，20 个一页吧」）
#
# 页大小是槽位表的事实源 moe_chars 的常量；页数由槽位总数派生。翻页是纯 gui 变量
# （`GetVariableSystem.Set('页变量', '2')` + 每格 visible 多 AND 一个页判定），不进脚本。
# 网格 5 列 × 4 行正好一页，格 140×210，显式 hbox 行，scrollarea 固定尺寸；窗口 1620×1080。
import moe_chars as _X
PAGE_SIZE = _X.SLOT_PAGE_SIZE
# 皮肤不单独开格（2026-09-06）：挂在本体格的轮换按钮上。网格只排本体槽位；页/行位置按本表下标，不按槽号
# （皮肤的槽号夹在中间会留空格，且 gui 会读它们的 moe_skins_N 报「被读未设」）。
BODY_SLOTS = [c["slot"] for c in _X.CHARS if not c.get("skin_of")]
PAGES = (len(BODY_SLOTS) + PAGE_SIZE - 1) // PAGE_SIZE
# 页签宽：6 页（本体 101 人，2026-10-09 鸣潮入包）时 44 会让页签行 636 > 600 越界；38 → 600 正好放下
PAGE_TAB_W = 44 if PAGES <= 5 else 38
GRID_COLS = 5
PAGE_ROWS = -(-PAGE_SIZE // GRID_COLS)          # 20/5 = 4 行

# ---- 窗口布局（2026-10-01 用户「gui 的页面布局好像有点超边框」）
#
# 旧版 1620×1080 的三处越界：
#   · 左栏页签行一行塞了 2 个标签页 + 4 页签 + 忽略性别(200) + 关闭(176) ≈ 924 宽，比网格 760 还宽，
#     把左栏撑宽后三栏合计 ≈ 924+420+300+32 = 1676 > 内宽 1580；
#   · 左栏模型 / 衣橱两个 scrollarea 各 880 高、vbox 没 ignoreinvisible，隐藏的那个照样占位
#     → 左栏 ≈ 25+25+880+880 远超三栏可用高 ≈ 940；
#   · 窗口 1080 高 = 1080p 整屏，边框贴屏幕边、被顶栏压住。
# 现在所有尺寸从下面的常量算出，_layout_check() 在生成时逐容器断言「子矩形装得进父矩形」，
# 超了直接中断构建（[[build-failure-must-stop-install-chain]]）。
WIN_W, WIN_H, WIN_MARGIN = 1560, 940, 20       # 1080p、UI 缩放 1.0：左右各留 180，上下各留 70
HEADER_H, FOOTER_H, VSPACE = 46, 33, 10         # 标题栏（crest / 关闭钮 46 高）、底栏（关闭钮 33 高）
COL_SPACE = 16
TEXT_H = 25                                     # Font_Size_Medium 单行标题
SLOT_W, SLOT_H, SLOT_GAP = 108, 162, 8          # 2:3，与原 140×210 同比例
TAB_ROW1_H, TAB_ROW2_H, TAB_GAP = 25, 36, 6     # 页签拆两行：标签页+页码 / 忽略性别+关闭（TAB_GAP = 左栏 vbox spacing）
PREVIEW_W, PREVIEW_H = 350, 750                 # 中栏实时预览（原 420×900，同比 ≈0.83）
POSE_COLS, POSE_BTN_W, POSE_BTN_H, POSE_GAP = 2, 245, 30, 6
SCROLLBAR_W = 20


def _page_of(i):
    return BODY_SLOTS.index(i) // PAGE_SIZE + 1


def _page_visible(p):
    """第 p 页的显示条件。第 1 页在没翻过页时也显示。"""
    if p == 1:
        return (f"Or( Not( GetVariableSystem.Exists( '{VAR_PAGE}' ) ), "
                f"GetVariableSystem.HasValue( '{VAR_PAGE}', '1' ) )")
    return f"GetVariableSystem.HasValue( '{VAR_PAGE}', '{p}' )"


def _close_button(name, S="GetPlayer", width=176, height=40):
    """Readable, filled Close buttons using vanilla art, without a confirm shortcut."""
    return f'''
                    button_standard_big = {{
                        name = "{name}"
                        size = {{ {width} {height} }}
                        text = "{GROUP}_cos_close"
                        tooltip = "{GROUP}_cos_close"
                        # Filled vanilla primary-button art; keep the standard click behavior.
                        background = {{
                            texture = "gfx/interface/buttons/button_standard.dds"
                            framesize = {{ 252 80 }}
                            frame = 4
                        }}
                        blockoverride "button_pattern" {{}}
                        buttonText = {{
                            text_single = {{
                                size = {{ 100% 100% }}
                                autoresize = no
                                margin = {{ 10 0 }}
                                align = center|nobaseline
                                default_format = "#high"
                                using = Font_Size_Medium
                                alwaystransparent = yes
                            }}
                        }}
                        datacontext = "[GetScriptedGui( '{GROUP}_close_cosplay' )]"
                        onclick = "[GetVariableSystem.Clear( '{VAR_POSE_PREV}' )]"
                        onclick = "[GetVariableSystem.Clear( '{VAR_PAGE}' )]"
                        onclick = "[ScriptedGui.Execute( GuiScope.SetRoot( {S}.MakeScope ).End )]"
                    }}'''


def _cancel_button(name, S="GetPlayer", width=200, height=33):
    """「取消cos」：目标角色打上 opt-out 并回到所属文化的原版随机外观（用户 2026-10-07）。
    脚本回调 moe_cancel_cosplay 由 moe_cosplay_rules 生成；它同时关窗，所以也清掉两个 gui 变量。"""
    return f'''
                    button_standard = {{
                        name = "{name}"
                        size = {{ {width} {height} }}
                        text = "{GROUP}_cos_cancel"
                        tooltip = "{GROUP}_cos_cancel_tt"
                        datacontext = "[GetScriptedGui( 'moe_cancel_cosplay' )]"
                        onclick = "[GetVariableSystem.Clear( '{VAR_POSE_PREV}' )]"
                        onclick = "[GetVariableSystem.Clear( '{VAR_PAGE}' )]"
                        onclick = "[ScriptedGui.Execute( GuiScope.SetRoot( {S}.MakeScope ).End )]"
                    }}'''


IGNORE_GENDER_TOGGLE = "moe_toggle_ignore_gender"   # scripted_guis 由 moe_cosplay_rules 生成
IGNORE_GENDER_STATE = "moe_ignore_gender_state"     # is_shown = 全局变量 moe_ignore_gender 存在


def _ignore_gender_button(S="GetPlayer"):
    """「忽略性别」全局开关（用户 2026-09-27）。状态读 scripted_gui 的 IsShown，
    游戏内与大厅两份窗口都用：大厅期没有 GetPlayer，root 取窗口的 S（选中角色）。
    点击后 `down` 与开/关文字即时跟随全局变量；槽位按钮的 IsValid 同步重新求值。"""
    scope = f"GuiScope.SetRoot( {S}.MakeScope ).End"
    state = f"GetScriptedGui( '{IGNORE_GENDER_STATE}' ).IsShown( {scope} )"
    return f'''
					button_standard = {{
						name = "{GROUP}_cos_ignore_gender"
						size = {{ 200 30 }}
						datacontext = "[GetScriptedGui( '{IGNORE_GENDER_TOGGLE}' )]"
						down = "[{state}]"
						onclick = "[ScriptedGui.Execute( {scope} )]"
						tooltip = "{GROUP}_cos_ignore_gender_tt"
						# 整句两个 key 由 SelectLocalization 选（原版 CreditsWindow 同款），状态可见。
						text = "[SelectLocalization( {state}, '{GROUP}_cos_ignore_gender_on', '{GROUP}_cos_ignore_gender_off' )]"
					}}'''


def _layout_rects():
    # → 布局数字 + 每个容器 (名字, 宽, 高, 父给它的宽, 父给它的高)。父份额 = 父尺寸扣掉 margin/spacing/兄弟。
    inner_w, inner_h = WIN_W - 2 * WIN_MARGIN, WIN_H - 2 * WIN_MARGIN
    main_h = inner_h - HEADER_H - FOOTER_H - 2 * VSPACE
    grid_w = GRID_COLS * (SLOT_W + SLOT_GAP) + SCROLLBAR_W           # 5 格 + 4 间距 + 余量 8 + 滚动条
    grid_h = PAGE_ROWS * (SLOT_H + SLOT_GAP) + SLOT_GAP
    grid_row_w = GRID_COLS * SLOT_W + (GRID_COLS - 1) * SLOT_GAP
    grid_rows_h = PAGE_ROWS * SLOT_H + (PAGE_ROWS - 1) * SLOT_GAP
    tab1_w = 2 * 140 + 60 + PAGES * PAGE_TAB_W + (2 + PAGES) * 4           # 两个 small 标签 + 「页」字 + 页签 + spacing
    tab2_w = 200 + 4 + 176                                          # 忽略性别 + spacing + 关闭
    left_h = TEXT_H + TAB_ROW1_H + TAB_ROW2_H + grid_h + 3 * TAB_GAP
    mid_h = TEXT_H + 6 + PREVIEW_H
    pose_row_w = POSE_COLS * POSE_BTN_W + (POSE_COLS - 1) * POSE_GAP + 12   # + margin_right
    pose_w = inner_w - grid_w - PREVIEW_W - 2 * COL_SPACE          # 右栏吃掉剩余宽度
    pose_h = main_h - TEXT_H - 6
    header_w = 46 + 20 + 260 + 12 + 200 + 180                       # crest / 移动图标 / 标题 / 提示 / 关闭（中文字宽估）
    return dict(inner_w=inner_w, inner_h=inner_h, main_h=main_h, grid_w=grid_w, grid_h=grid_h,
                pose_w=pose_w, pose_h=pose_h, rects=[
        ("vertical(标题+三栏+底栏)", inner_w, HEADER_H + main_h + FOOTER_H + 2 * VSPACE, inner_w, inner_h),
        ("header", header_w, HEADER_H, inner_w, HEADER_H),
        ("main(三栏)", grid_w + PREVIEW_W + pose_w + 2 * COL_SPACE, main_h, inner_w, main_h),
        ("left(标题+页签2行+网格)", grid_w, left_h, grid_w, main_h),
        ("left.tab_row1", tab1_w, TAB_ROW1_H, grid_w, TAB_ROW1_H),
        ("left.tab_row2", tab2_w, TAB_ROW2_H, grid_w, TAB_ROW2_H),
        ("left.grid 一页", grid_row_w, grid_rows_h, grid_w - SCROLLBAR_W, grid_h),
        ("mid(名字+预览)", PREVIEW_W, mid_h, PREVIEW_W, main_h),
        ("right(标题+列表)", pose_w, TEXT_H + 6 + pose_h, pose_w, main_h),
        ("right.pose 行", pose_row_w, POSE_BTN_H, pose_w - SCROLLBAR_W, POSE_BTN_H),
    ])


def _layout_check(verbose=False):
    L = _layout_rects()
    bad = [r for r in L["rects"] if r[1] > r[3] or r[2] > r[4]]
    if verbose or bad:
        print(f"  布局：窗口 {WIN_W}×{WIN_H}，内框 {L['inner_w']}×{L['inner_h']}，三栏高 {L['main_h']}")
        for n, w, h, pw, ph in L["rects"]:
            print(f"    {'!!' if (w > pw or h > ph) else 'ok'} {n:<24} {w:>4}×{h:<4} ⊂ {pw}×{ph}")
    if bad:
        raise SystemExit(f"**换装窗口布局越界**：{[r[0] for r in bad]}")
    return L


def _page_tabs(S="GetPlayer"):
    """页签行：每页一个小按钮，当前页按下态，行尾一个「关闭」（用户 2026-09-08「page 上面也放个 exit 按钮」，
    与右上 X / 底部退出同一条关窗路径）。只有一页时不渲染页签，但关闭按钮行照样渲染。"""
    tabs = ""
    if PAGES > 1:
        tabs = "".join(f'''
					button_standard_small = {{
						name = "{GROUP}_page_{p}"
						size = {{ {PAGE_TAB_W} 25 }}
						raw_text = "{p}"   # 字面数字用 raw_text（原版 908 处）；写 text 会报 Unlocalized text（2026-09-06 实机 4 条）
						down = "[{_page_visible(p)}]"
						onclick = "[GetVariableSystem.Set( '{VAR_PAGE}', '{p}' )]"
					}}''' for p in range(1, PAGES + 1))
    # 拆两行（2026-10-01）：一行 ≈924 宽，把左栏撑得比网格还宽，三栏合计超出窗口内框。
    gw = _layout_rects()["grid_w"]
    return f'''
				hbox = {{
					size = {{ {gw} {TAB_ROW1_H} }}
					spacing = 4
					button_standard_small = {{
						text = "moe_models_tab"
						onclick = "[GetVariableSystem.Clear('moe_wardrobe_panel')]"
					}}
					button_standard_small = {{
						text = "moe_wardrobe_tab"
						onclick = "[GetVariableSystem.Set('moe_wardrobe_panel', 'yes')]"
					}}
					text_single = {{ text = "{GROUP}_cos_page" }}{tabs}
					expand = {{}}
				}}
				hbox = {{
					size = {{ {gw} {TAB_ROW2_H} }}
					spacing = 4{_ignore_gender_button(S)}
					expand = {{}}
{_close_button(f'{GROUP}_cos_exit_top', S, height=TAB_ROW2_H)}
				}}'''


def _wardrobe_list(S="GetPlayer"):
    """Explicit forms reuse the registry; no inventory objects or new textures."""
    rows = []
    for base in (c for c in _X.CHARS if not c.get('skin_of')):
        for c in (x for x in _X.CHARS if x['ck'] == base['ck'] or x.get('skin_of') == base['ck']):
            ck = c['ck']
            scope = f'GuiScope.SetRoot( {S}.MakeScope ).End'
            tailor = '' if S != 'GetPlayer' else f'''
                widget = {{
                  datacontext = "[GetScriptedGui('moe_collection_enabled')]"
                  visible = "[ScriptedGui.IsShown({scope})]"
                  size = {{ 160 35 }}
                  button_standard_small = {{
                    text = "moe_wardrobe_tailor"
                    datacontext = "[GetScriptedGui('moe_wardrobe_tailor_{ck}')]"
                    enabled = "[ScriptedGui.IsValid({scope})]"
                    onclick = "[ScriptedGui.Execute({scope})]"
                    tooltip = "[ScriptedGui.BuildTooltip({scope})]"
                  }}
                }}'''
            rows.append(f'''
            vbox = {{
                visible = "[GreaterThan_CFixedPoint({S}.MakeScope.Var('moe_outfit_available_{ck}').GetValue, '(CFixedPoint)0')]"
                background = {{ using = Background_Area_Dark }}
                margin = {{ 6 5 }}
                hbox = {{
                    spacing = 8
                    text_single = {{ text = "moe_wardrobe_name_{ck}" using = Font_Size_Medium }}
                    text_single = {{
                        text = "moe_wardrobe_owned"
                        datacontext = "[GetScriptedGui('moe_wardrobe_owned_{ck}')]"
                        visible = "[ScriptedGui.IsShown({scope})]"
                    }}
                }}
                hbox = {{
                    spacing = 8
                    button_standard_small = {{
                        text = "moe_wardrobe_wear"
                        datacontext = "[GetScriptedGui('moe_wardrobe_wear_{ck}')]"
                        enabled = "[ScriptedGui.IsValid({scope})]"
                        onclick = "[ScriptedGui.Execute({scope})]"
                        tooltip = "[ScriptedGui.BuildTooltip({scope})]"
                    }}{tailor}
                }}
            }}''')
    return '\n'.join(rows)


def _slot_row(r0, S="GetPlayer", charS=None):
    """一行槽位（r0 是 0 基起始下标）：hbox 装 GRID_COLS 个按钮，visible 绑该行所在页。"""
    cells = "".join(_slot_button(i, S, charS) for i in BODY_SLOTS[r0:r0 + GRID_COLS])
    page = _page_of(BODY_SLOTS[r0])
    return f'''
\t\t\t\thbox = {{
\t\t\t\t\tspacing = {SLOT_GAP}
\t\t\t\t\tignoreinvisible = yes
\t\t\t\t\tvisible = "[{_page_visible(page)}]"{cells}
\t\t\t\t}}'''

def _slot_button(i, S="GetPlayer", charS=None):
    """一个模型槽位。显隐由子 mod 写的玩家变量驱动；实时肖像的 datacontext 是槽位角色本人。

    charS：从谁身上读 `moe_slot_char_<i>` 变量。游戏内 = S（GetPlayer）；大厅副本窗口的
    datacontext 已是 Character，再写 `Character.MakeScope.Var(..).Char` 会被引擎拒绝
    （pdx_gui_data_context.cpp: reading and writing the same context），改传
    `LobbyView.GetSelectedPlayable.GetCharacter`（输入类型 LobbyView，输出 Character）。**大厅侧未验证**。
    """
    charS = charS or S
    return f'''
\t\t\t\tbutton = {{
\t\t\t\t\tname = "{GROUP}_slot_{i}"
\t\t\t\t\tsize = {{ {SLOT_W} {SLOT_H} }}
\t\t\t\t\t# Quiet native card material behind the live portrait, not a character thumbnail.
\t\t\t\t\ticon = {{
\t\t\t\t\t\tname = "{GROUP}_slot_{i}_card"
\t\t\t\t\t\tsize = {{ 100% 100% }}
\t\t\t\t\t\tusing = Background_Area_Dark_Border
\t\t\t\t\t\talpha = 0.72
\t\t\t\t\t\talwaystransparent = yes
\t\t\t\t\t}}
\t\t\t\t\t# 悬停反馈（2026-09-06）：整格放大 1.05（scale 带子件），自身 texture 是金色边框
\t\t\t\t\t# （backgrounds.gui Background_Frame_Gold 的 mask_frame），平时 alpha 0，悬停 0.9；color 不传给子件。
\t\t\t\t\t# 2026-09-08 晚：state 不支持 color（error.log 三条 Unsupported property），金框挪成子件 icon
\t\t\t\t\t# 用 alpha 做状态；子件的 _mouse_hierarchy_enter 由本 button 悬停触发。button 只保留 scale。
\t\t\t\t\tstate = {{
\t\t\t\t\t\tname = _mouse_hierarchy_enter
\t\t\t\t\t\tduration = 0.1
\t\t\t\t\t\tusing = Animation_Curve_Default
\t\t\t\t\t\tscale = 1.05
\t\t\t\t\t}}
\t\t\t\t\tstate = {{
\t\t\t\t\t\tname = _mouse_hierarchy_leave
\t\t\t\t\t\tduration = 0.15
\t\t\t\t\t\tusing = Animation_Curve_Default
\t\t\t\t\t\tscale = 1
\t\t\t\t\t}}
\t\t\t\t\ticon = {{
\t\t\t\t\t\tname = "{GROUP}_slot_{i}_frame"
\t\t\t\t\t\tsize = {{ 100% 100% }}
\t\t\t\t\t\ttexture = "gfx/interface/component_masks/mask_frame.dds"
\t\t\t\t\t\tspriteType = Corneredstretched
\t\t\t\t\t\tspriteborder = {{ 10 10 }}
\t\t\t\t\t\tcolor = {{ 0.9 0.7 0.5 1 }}
\t\t\t\t\t\talpha = 0
\t\t\t\t\t\talwaystransparent = yes
\t\t\t\t\t\tstate = {{
\t\t\t\t\t\t\tname = _mouse_hierarchy_enter
\t\t\t\t\t\t\tduration = 0.1
\t\t\t\t\t\t\tusing = Animation_Curve_Default
\t\t\t\t\t\t\talpha = 0.9
\t\t\t\t\t\t}}
\t\t\t\t\t\tstate = {{
\t\t\t\t\t\t\tname = _mouse_hierarchy_leave
\t\t\t\t\t\t\tduration = 0.15
\t\t\t\t\t\t\tusing = Animation_Curve_Default
\t\t\t\t\t\t\talpha = 0
\t\t\t\t\t\t}}
\t\t\t\t\t}}
\t\t\t\t\t# 显隐读**玩家角色的变量**。子 mod 在开局把自己占的槽位号写进来（set_variable），
\t\t\t\t\t# 核心不知道有哪些模型。读法是原版验证过的（hud.gui 的 hide_ui_* 同款）。
\t\t\t\t\t# 再 AND 一个「在当前页」（第 {_page_of(i)} 页）。
\t\t\t\t\tvisible = "[And( GreaterThan_CFixedPoint( {S}.MakeScope.Var('{GROUP}_slot_{i}').GetValue, '(CFixedPoint)0' ), {_page_visible(_page_of(i))} )]"

\t\t\t\t\t# 不再放缩略图底图：它会从实时肖像的羽化边缘透出来（用户 2026-09-06「老的图还在」）。
\t\t\t\t\t# **实时渲染的模型肖像**，盖在缩略图上。datacontext 是槽位角色本人 —— 子 mod 的槽位
\t\t\t\t\t# 事件把 character:<id> 存进玩家变量 moe_slot_char_{i}（不可玩的角色在 history 里
\t\t\t\t\t# 生成即死亡，只为这里存在）。与中栏同一条 Character.GetAnimatedPortrait 路径，
\t\t\t\t\t# 2026-09-05 实机验证过；旧的 GetAnimatedBookmarkPortrait + AnimationTestWindow 实机不出图。
\t\t\t\t\tportrait_button = {{
\t\t\t\t\t\tname = "{GROUP}_slot_{i}_live"
\t\t\t\t\t\tdatacontext = "[{charS}.MakeScope.Var('{GROUP}_slot_char_{i}').Char]"
\t\t\t\t\t\tvisible = "[Character.IsValid]"
\t\t\t\t\t\tsize = {{ 100% 100% }}
\t\t\t\t\t\tusing = portrait_base
\t\t\t\t\t\t# portrait_base 把 grayscale 绑成 Not(Character.IsAlive)，非可玩槽位角色是死者会整格变灰
\t\t\t\t\t\t# （2026-09-06 实机）。覆盖成空块，原版 49 处这么写。
\t\t\t\t\t\tblockoverride "grayscale" {{}}
\t\t\t\t\t\talwaystransparent = yes
\t\t\t\t\t\ttooltip_visible = no
\t\t\t\t\t\t# mask 不能省：gui_portrait.shader 把肖像乘 Mask.a，没绑就整张乘 0（2026-09-05）。
\t\t\t\t\t\tmask = "{PREVIEW_MASK}"
\t\t\t\t\t\teffectname = "NoHighlight"
\t\t\t\t\t\tportrait_texture = "[Character.GetAnimatedPortrait('{PREVIEW_ENV}', '{PREVIEW_CAM}', 'idle', PdxGetWidgetScreenSize(PdxGuiWidget.Self))]"
\t\t\t\t\t}}

\t\t\t\t\t# 皮肤轮询小按钮（2026-09-06，接口）：子 mod 该角色有 N>1 套皮肤时把 N 写进玩家变量
\t\t\t\t\t# {GROUP}_skins_{i}（0001/0003 与 {GROUP}_slot_{i} 同处），按钮才显示；点它走核心的
\t\t\t\t\t# {GROUP}_cycle_skin_{i}（广播 on_action {GROUP}_cycle_skin，子 mod 认领后自己轮换）。
\t\t\t\t\tbutton_icon = {{
\t\t\t\t\t\tname = "{GROUP}_slot_{i}_skin"
\t\t\t\t\t\tsize = {{ 28 28 }}
\t\t\t\t\t\tparentanchor = bottom|right
\t\t\t\t\t\tposition = {{ -4 -4 }}
\t\t\t\t\t\ttexture = "gfx/interface/icons/flat_icons/randomize.dds"
\t\t\t\t\t\tvisible = "[GreaterThan_CFixedPoint( {S}.MakeScope.Var('{GROUP}_skins_{i}').GetValue, '(CFixedPoint)1' )]"
\t\t\t\t\t\tdatacontext = "[GetScriptedGui( '{GROUP}_cycle_skin_{i}' )]"
\t\t\t\t\t\tenabled = "[ScriptedGui.IsValid( GuiScope.SetRoot( {S}.MakeScope ).End )]"
\t\t\t\t\t\tonclick = "[ScriptedGui.Execute( GuiScope.SetRoot( {S}.MakeScope ).End )]"
\t\t\t\t\t\ttooltip = "{GROUP}_cos_skin_tt"
\t\t\t\t\t}}

\t\t\t\t\tdatacontext = "[GetScriptedGui( '{GROUP}_pick_slot_{i}' )]"
\t\t\t\t\tenabled = "[ScriptedGui.IsValid( GuiScope.SetRoot( {S}.MakeScope ).End )]"
\t\t\t\t\tonclick = "[ScriptedGui.Execute( GuiScope.SetRoot( {S}.MakeScope ).End )]"
\t\t\t\t\ttooltip = "[ScriptedGui.BuildTooltip( GuiScope.SetRoot( {S}.MakeScope ).End )]"
\t\t\t\t}}'''


def _pose_button(k, S="GetPlayer"):
    """一个姿势按钮：点下去先切中栏预览变量，再执行 scripted_gui 提交 flag。
    `down` 显示当前选中项（原版 button 的 down 属性 93 处，如 window_component_library.gui）。"""
    if k == "idle":
        down = (f"Or( Not( GetVariableSystem.Exists( '{VAR_POSE_PREV}' ) ), "
                f"GetVariableSystem.HasValue( '{VAR_POSE_PREV}', 'idle' ) )")
    else:
        down = f"GetVariableSystem.HasValue( '{VAR_POSE_PREV}', '{k}' )"
    return f'''
							button_standard = {{
								name = "{GROUP}_pose_{k}"
								size = {{ {POSE_BTN_W} {POSE_BTN_H} }}
								text = "{GROUP}_cosplay.0002.{k}"
								tooltip = "{GROUP}_cosplay.0002.{k}"   # 固定宽度下长名字会被截，悬停看全名
								down = "[{down}]"
								datacontext = "[GetScriptedGui( '{GROUP}_set_pose_{k}' )]"
								enabled = "[ScriptedGui.IsValid( GuiScope.SetRoot( {S}.MakeScope ).End )]"
								onclick = "[GetVariableSystem.Set( '{VAR_POSE_PREV}', '{k}' )]"
								onclick = "[ScriptedGui.Execute( GuiScope.SetRoot( {S}.MakeScope ).End )]"
							}}'''


def _pose_list(S="GetPlayer"):
    """右栏：按原版 animations.txt 的横幅分组，每组一个小标题 + 该组全部姿势按钮。
    整体放进 scrollarea（用户 2026-09-06 要滚动条）。"""
    out = []
    for gcn, gen, items in POSE_GROUPS:
        out.append(f'''
							hbox = {{
								layoutpolicy_horizontal = expanding
								margin_top = 10
								background = {{ using = Background_Area_Dark }}
								text_single = {{
									text = "{GROUP}_cos_group_{_PC.group_key(gen)}"
									using = Font_Size_Medium
									margin = {{ 8 5 }}
								}}
							}}''')
        # 每 POSE_COLS 个一行的显式 hbox —— 横向 flowcontainer 不折行
        # （[[ck3-gui-flowcontainer-no-wrap-and-3d-portrait-ignores-layer]]）。
        for r0 in range(0, len(items), POSE_COLS):
            cells = "".join(_pose_button(q["key"], S) for q in items[r0:r0 + POSE_COLS])
            out.append(f'''
							hbox = {{
								spacing = {POSE_GAP}{cells}
								expand = {{}}
							}}''')
    return "".join(out)


def _pose_preview(k):
    """中栏实时预览：每个姿势一个肖像 widget，只显示当前选中的那个。

    `Character.GetAnimatedPortrait(env, cam, '块名', size)` 的第三个参数只能是
    字面量（原版 8 处全是），所以不能把 gui 变量拼进去 —— 改成 N 个 widget
    按 gui 变量显隐。idle 那个在没选过姿势时也显示。
    **未验证**：252 个 widget 里只有 1 个 visible，其余是否仍被求值/渲染只能进游戏看
    帧率与显存；原版 hud 也是靠 visible 挡掉不显示的 portrait_button（同一套机制）。
    """
    if k == "idle":
        vis = (f"[Or( Not( GetVariableSystem.Exists( '{VAR_POSE_PREV}' ) ), "
               f"GetVariableSystem.HasValue( '{VAR_POSE_PREV}', 'idle' ) )]")
    else:
        vis = f"[GetVariableSystem.HasValue( '{VAR_POSE_PREV}', '{k}' )]"
    return f'''
					portrait_button = {{
						name = "{GROUP}_target_preview_{k}"
						size = {{ 100% 100% }}
						visible = "{vis}"
						# datacontext 是 Character，可以直接套原版模板（gfxtype /
						# shaderfile / 透明底图 / 悬停状态都在里面），与
						# window_legend_chronicle.gui:442 的实时肖像同款写法。
						using = portrait_base
						alwaystransparent = yes
						tooltip_visible = no
						# mask 必须给，理由见槽位那份注释。
						mask = "{PREVIEW_MASK}"
						effectname = "NoHighlight"
						portrait_texture = "[Character.GetAnimatedPortrait('{PREVIEW_ENV}', '{PREVIEW_CAM}', '{k}', PdxGetWidgetScreenSize(PdxGuiWidget.Self))]"
					}}'''


def gui_text(S="GetPlayer", win=WIN, dc=None, charS=None):
    """窗口正文。

    S 是「变量挂在谁身上」的 gui 对象表达式：游戏内是 `GetPlayer`（交互把目标与
    显示标志写在玩家身上）；大厅里 GetPlayer 在原版大厅 gui **零使用**，改传
    `Character` —— 窗口 datacontext（dc）设成大厅选中的角色，变量就写在她自己
    身上（开窗的 scripted_gui 把目标设成 root）。win 是窗口 name，两份副本不能同名。
    """
    # 槽位按行分组：每 GRID_COLS 个一个 hbox，行只属于一页（PAGE_SIZE 必须是 GRID_COLS 的倍数），
    # 行的 visible 绑本页判定，别页的行被 vbox 的 ignoreinvisible 折成 0 高。
    # 不用 flowcontainer + max_width：原版 1231 处 max_width 全在 text 件上，横向 flowcontainer
    # 没有折行先例，实机 20 格排成一长行（2026-09-06 晚）。
    assert PAGE_SIZE % GRID_COLS == 0, "一行不能跨页"
    slots = "".join(_slot_row(r0, S, charS) for r0 in range(0, len(BODY_SLOTS), GRID_COLS))
    poses = _pose_list(S)
    previews = "".join(_pose_preview(k) for k in POSE_KEYS)
    dc_line = f'\t\tdatacontext = "[{dc}]"\n' if dc else ""
    # 中栏被换装角色的 datacontext。游戏内从玩家变量里取目标；大厅副本的根**就是**
    # 目标本人，这一行必须省 —— 写成 `Character.MakeScope.Var(...).Char` 会被引擎拒绝：
    #   pdx_gui_data_context.cpp: datacontext statement ... is reading and writing
    #   the same context (Character) this is not allowed        （2026-09-05 实机报错）
    target_dc = ("" if dc else
                 f"\t\t\t\tdatacontext = \"[{S}.MakeScope.Var('{VAR_TARGET}').Char]\"\n")
    L = _layout_check()
    grid_w, grid_h = L["grid_w"], L["grid_h"]
    return f'''	### 换 cos 服的大窗口。生成自 tools/build/build_moe_cosplay_gui.py，不要手改。
	###
	### 打开方式：交互 `{GROUP}_cosplay_interaction` 的 on_accept 里
	###   set_variable = {{ name = {VAR_OPEN} value = yes }}
	### 然后这个窗口的 visible 读到就显示。**没用 open_view** —— `open_view` 的
	### view 名是 C++ 侧注册的，mod 加不了新 view。
	###
	### **这是 hud_sidebars 的子 widget，不是顶层 window** —— 顶层 window 由
	### C++ 按名字创建，mod 新增的名字没人创建，visible 永不求值（见模块 docstring）。
	###
	### 模型槽位是实时肖像（datacontext 为槽位角色），2026-09-06 起不再有缩略图底图；
	### 槽位显隐由子 mod 写玩家变量驱动。核心不含任何模型资产。
	### 模型网格 {PAGE_SIZE} 格一页共 {PAGES} 页；姿势 {len(POSE_KEYS)} 个分 {len(POSE_GROUPS)} 组滚动。
	window = {{
		name = "{win}"
{dc_line}		# 宿主 hud_sidebars 在 bottom_bottom，这里往上提到窗口层。
		# 嵌套 widget 自设 layer 是原版验证过的（hud.gui:181）。
		layer = top   # 盖住其余 2D HUD；左下 3D 立绘不吃它，靠下面的 hide_bottom_left_HUD。原：windows_layer(优先级 3) 被 hud 的 3D 立绘压住，top(8) 在 events(7)/hud_layer(4) 之上，原版 26 处
		movable = yes
		size = {{ {WIN_W} {WIN_H} }}
		parentanchor = center
		# 宿主 alwaystransparent 会让子控件收不到点击，这里显式收鼠标。
		alwaystransparent = no
		filter_mouse = all

		# 显隐读**脚本侧的玩家变量**，写法同 hud.gui 的 hide_ui_*。
		visible = "[GreaterThan_CFixedPoint( {S}.MakeScope.Var('{VAR_OPEN}').GetValue, '(CFixedPoint)0' )]"

		# 左下 3D 立绘**不吃 layer**：嵌套件写 windows_layer / top 两轮都被它遮（2026-09-06 晚），
		# 而它所在的 bottom_bar 只是 layer bottom。原版角色窗/军队窗/交易窗的做法是开窗时置 gui 变量
		# hide_bottom_left_HUD 把它藏掉、关窗清掉（window_character.gui:23/32），bottom_left_portrait 的
		# visible 读它。大厅副本里没有左下立绘，这两句无害。
		state = {{
			name = _show
			on_start = "[GetVariableSystem.Set( 'hide_bottom_left_HUD', 'true' )]"
		}}
		state = {{
			name = _hide
			on_start = "[GetVariableSystem.Clear( 'hide_bottom_left_HUD' )]"
		}}

	using = Window_Background
	# Decorative art is behind all controls and never receives mouse input.
	icon = {{
		name = "{win}_painted_background"
		size = {{ 100% 100% }}
		texture = "{ART_REL}/window_backdrop.dds"
		alpha = 0.86
		alwaystransparent = yes
	}}
	using = Window_Decoration

	vbox = {{
		margin = {{ {WIN_MARGIN} {WIN_MARGIN} }}
		spacing = {VSPACE}

		# ---- 标题栏：整条可拖（window movable），加移动图标 + tooltip 提示（2026-09-06 用户要明确视觉提示）
			hbox = {{
			layoutpolicy_horizontal = expanding
			tooltip = "{GROUP}_cos_drag_tt"
			icon = {{
				name = "{win}_title_crest"
				size = {{ 46 46 }}
				texture = "{ART_REL}/title_crest.dds"
				alwaystransparent = yes
			}}
			icon = {{
				size = {{ 20 20 }}
				texture = "gfx/interface/icons/flat_icons/move_capital.dds"
				alpha = 0.7
				alwaystransparent = yes
			}}
			text_single = {{
				text = "{GROUP}_cosplay.0001.t"
				using = Font_Size_Medium
			}}
			text_single = {{
				text = "{GROUP}_cos_drag_hint"
				alpha = 0.6
				margin_left = 12
			}}
			expand = {{}}
{_close_button(f'{GROUP}_cos_exit_header', S, width=180, height=46)}
		}}

		hbox = {{
			size = {{ {L["inner_w"]} {L["main_h"]} }}
			spacing = {COL_SPACE}

			# ---- 左：模型预览网格（{GRID_COLS} 列，{PAGE_SIZE} 格一页，页签翻页）
			vbox = {{
				layoutpolicy_vertical = expanding
				spacing = {TAB_GAP}
				# 模型 / 衣橱两个 scrollarea 同位互斥；不写 ignoreinvisible 时隐藏的那个照样占 {grid_h} 高（2026-10-01）
				ignoreinvisible = yes
				text_single = {{ text = "{GROUP}_cos_models" using = Font_Size_Medium }}{_page_tabs(S)}
				# scrollarea 的两层包装都是必须的（照原版
				# gui/event_windows/big_event_window.gui:445）：
				#   - `using = Scrollbar_Vertical` 要包在 `scrollbar_vertical = {{}}`
				#     里。直接写在 scrollarea 下会报
				#     `No vertical scrollbar found for scrollarea ''`。
				#   - 内容要包在 `scrollwidget = {{}}` 里，否则滚动区没有可滚的子件。
				# 一页正好装满网格时不需要滚；留着是给小分辩率兜底。
				# **固定尺寸**：layoutpolicy expanding 在这个 vbox 里实机被压成一行高（2026-09-06 只见 5 格），
				# 直接按 {GRID_COLS} 列 × {PAGE_ROWS} 行算死。
				scrollarea = {{
					size = {{ {grid_w} {grid_h} }}
					visible = "[Not(GetVariableSystem.Exists('moe_wardrobe_panel'))]"
					scrollbarpolicy_horizontal = always_off

					scrollbar_vertical = {{
						using = Scrollbar_Vertical
					}}

					scrollwidget = {{
						# 显式行布局（每行一个 hbox，见 _slot_row）。flowcontainer + max_width 实机不折行。
						vbox = {{
							spacing = {SLOT_GAP}
							ignoreinvisible = yes
{slots}
						}}
					}}
				}}
				scrollarea = {{
					size = {{ {grid_w} {grid_h} }}
					visible = "[GetVariableSystem.Exists('moe_wardrobe_panel')]"
					scrollbarpolicy_horizontal = always_off
					scrollbar_vertical = {{ using = Scrollbar_Vertical }}
					scrollwidget = {{
						vbox = {{ spacing = 6 ignoreinvisible = yes {_wardrobe_list(S)} }}
					}}
				}}
			}}

			# ---- 中：被换装角色的实时预览（跟着右侧选姿势立刻换动画）
			#
			# datacontext 取玩家变量里存的角色：`{S}.MakeScope.Var('x').Char`
			# 是原版 17 处用过的读法（chariot_race 的 wager_target）；作为
			# datacontext 的同构写法见 funeral_deceased_portrait.gui
			# （`Activity.MakeScope().Var('body_to_bury').Char`）。
			vbox = {{
				layoutpolicy_vertical = expanding
				spacing = 6
{target_dc}				text_single = {{ text = "[Character.GetUIName]" using = Font_Size_Medium }}
				widget = {{
					size = {{ {PREVIEW_W} {PREVIEW_H} }}
					visible = "[Character.IsValid]"
					icon = {{
						name = "{win}_portrait_stage"
						size = {{ 100% 100% }}
						texture = "{ART_REL}/portrait_stage.dds"
						alwaystransparent = yes
					}}
{previews}
					icon = {{
						name = "{win}_portrait_frame"
						size = {{ 100% 100% }}
						using = Background_Frame_Gold
						alwaystransparent = yes
					}}
				}}
				expand = {{}}
			}}

			# ---- 右：姿势列表（分组标题 + 滚动条）
			#
			# **不是 dropdown**：`dropdown_menu_standard` 要一个 C++ 提供的
			# `datamodel`（原版全部 18 处都是），脚本喂不进去。252 个姿势静态展开进
			# scrollarea，按 animations.txt 的横幅分组给小标题。
			vbox = {{
				layoutpolicy_vertical = expanding
				spacing = 6
				text_single = {{ text = "{GROUP}_cos_pose_header" using = Font_Size_Medium }}
				scrollarea = {{
					# 固定尺寸（与左栏同理：expanding 在嵌套 vbox 里实机不可靠）；{len(POSE_KEYS)} 个姿势 {POSE_COLS} 列
					size = {{ {L["pose_w"]} {L["pose_h"]} }}
					scrollbarpolicy_horizontal = always_off

					scrollbar_vertical = {{
						using = Scrollbar_Vertical
					}}

					scrollwidget = {{
						vbox = {{
							spacing = 3
							margin_right = 12
{poses}
						}}
					}}
				}}
			}}
		}}

		# ---- 底部：明确的退出按钮（用户 2026-09-06）。与右上 X 同一条关窗路径。
		hbox = {{
			layoutpolicy_horizontal = expanding
			text_single = {{ text = "moe_wardrobe_hint" }}
			expand = {{}}
{_cancel_button(f'{GROUP}_cos_cancel', S)}
{_close_button(f'{GROUP}_cos_exit', S, width=180, height=33)}
		}}
	}}
	}}
'''

# 宿主：原版这个文件的顶层 widget 是 `hud_sidebars`，全屏且 visible 恒为真。
HOST_REL = "gui/hud_sidebars.gui"
HOST_NAME = "hud_sidebars"


def _game_dir():
    """CK3 的 game 目录。**从 moe_anim_catalog 抠常量**，不另立一份路径。"""
    import re
    src = (ROOT / "tools" / "lib" / "moe_anim_catalog.py").read_text(encoding="utf-8")
    m = re.search(r'^GAME = Path\(r"([^"]+)"', src, re.M)
    if not m:
        raise SystemExit("**读不到 moe_anim_catalog.GAME** —— 拒绝猜游戏路径")
    return Path(m.group(1))


def host_text(payload):
    """原版 hud_sidebars.gui + 注入我们的窗口。

    在顶层 widget 的**最后一个右花括号之前**插入。用 rfind 而不是正则：
    原版文件里 `}` 有 40 多个，只有文件末尾那个是顶层的闭合。
    """
    src = _game_dir() / HOST_REL
    if not src.is_file():
        raise SystemExit(f"**找不到原版 {HOST_REL}**（{src}）—— "
                         f"覆盖必须基于实读的原版，不能手工维护副本")
    t = src.read_text(encoding="utf-8-sig")
    i = t.rfind("}")
    if i < 0 or f'name = "{HOST_NAME}"' not in t:
        raise SystemExit(f"**{HOST_REL} 结构不认识**（顶层应是 {HOST_NAME}）—— "
                         f"游戏可能更新了，先看一眼再改注入点")
    head = (f"\n\t### ↓↓↓ 以下由 tools/build/build_moe_cosplay_gui.py 注入 ↓↓↓\n"
            f"\t### 上面全部是原版 {HOST_REL} 的原文，不要手改本文件。\n")
    tail = f"\t### ↑↑↑ 注入结束 ↑↑↑\n"
    return t[:i] + head + payload + tail + t[i:]


# ---------------------------------------------------------------- 大厅入口
# 用户 2026-09-05：「自定义完角色后，如果角色满足特质/文化要求，在选 game rule /
# start / difficulty 的那个页面新建个按钮，进入我们的 gui 选模型」。
#
# 大厅是 `gui/multiplayer_lobby.gui` 的第一个顶层块 `JominiMultiplayerLobby`（单人也
# 用它）。选中角色的面板是 `blockoverride "game_rules"` 里 name 为
# `select_and_customize_character` 的 vbox，datacontext 已是选中角色
# （`LobbyView.GetSelectedPlayable.GetCharacter`）。按钮插进这个 vbox 顶部。
#
# 与游戏内窗口不同的三点：
#   · **不能用 GetPlayer**：原版大厅 gui 里 GetPlayer 出现 0 次，大厅期没有「玩家
#     角色」。所有变量改挂在选中角色自己身上（S = Character）。
#   · 槽位变量 moe_slot_<i> 原本由子 mod 在 on_game_start_after_lobby 写到玩家身上，
#     大厅期还没发生 —— 开窗的 scripted_gui 顺带触发核心的空 on_action
#     `moe_register_slots`，子 mod 把自己的槽号写到这个角色身上。
#   · 窗口副本挂进大厅这个顶层块（hud_sidebars 只在游戏内实例化）。
#     `windows_layer` 在前端可用（frontend_load.gui 用它）。
#
# **未经验证、只能进大厅看**：ScriptedGui.IsShown/Execute 在前端期是否求值（原版
# 前端 gui 零 ScriptedGui）；Character.GetAnimatedPortrait 在大厅是否出图（原版大厅
# 用的是 Character.GetPortrait）。判据：按钮对合格角色可见、点开有窗、debug.log
# 有 `MOE_COS lobby_open` 与 `MOE_SLOT register`。gui 侧失败不进 error.log。
LOBBY_REL = "gui/multiplayer_lobby.gui"
LOBBY_TOP = "JominiMultiplayerLobby"
LOBBY_HOOK = 'name = "select_and_customize_character"'
LOBBY_CHAR = "LobbyView.GetSelectedPlayable.GetCharacter"
LOBBY_WIN = f"{WIN}_lobby"
OPEN_SG = f"{GROUP}_open_cosplay_self"   # 定义在 build_moe_mod.py 的 scripted_guis


def lobby_button_text():
    return f'''
				### ↓ 由 tools/build/build_moe_cosplay_gui.py 注入：合格角色的「更换cos服」入口 ↓
				# 外包一层：未选中角色时 GetCharacter 是空角色，And(...) 不短路，内层 ScriptedGui.IsShown 仍会
				# 求值并刷 125 条 `Scoped object of type character is not valid`（2026-09-06 实机）。父不可见则子不求值。
				widget = {{
					visible = "[Character.IsValid]"
					size = {{ 380 45 }}
				button_standard = {{
					name = "{GROUP}_cos_lobby_open"
					datacontext = "[GetScriptedGui( \'{OPEN_SG}\' )]"
					# 资格判定在 scripted_gui 里（cosplay认同传统 + 绝世美人，不限性别与年龄），
					# 与模型指派用同一条 _trig，不在 gui 里再抄一份。
					visible = "[And( Character.IsValid, ScriptedGui.IsShown( GuiScope.SetRoot( Character.MakeScope ).End ) )]"
					enabled = "[ScriptedGui.IsValid( GuiScope.SetRoot( Character.MakeScope ).End )]"
					onclick = "[ScriptedGui.Execute( GuiScope.SetRoot( Character.MakeScope ).End )]"
					tooltip = "{GROUP}_cos_lobby_open_tt"
					size = {{ 380 45 }}
					text = "{GROUP}_cos_lobby_open"
				}}
				}}
				### ↑ 注入结束 ↑
'''


def lobby_host_text(win_payload, btn_payload):
    """原版 multiplayer_lobby.gui + 按钮 + 窗口副本。

    窗口插在**第一个顶层块**（JominiMultiplayerLobby）的闭合花括号之前 —— 用配平算，
    不是 rfind：这个文件后面还有 JominiMultiplayerOosMessage 等别的顶层块。
    按钮插在 select_and_customize_character vbox 的 `spacing = 5` 行之后。
    """
    src = _game_dir() / LOBBY_REL
    if not src.is_file():
        raise SystemExit(f"**找不到原版 {LOBBY_REL}**（{src}）")
    t = src.read_text(encoding="utf-8-sig")
    if not t.lstrip().startswith(LOBBY_TOP + " = {"):
        raise SystemExit(f"**{LOBBY_REL} 第一个顶层块不是 {LOBBY_TOP}** —— 游戏更新了，先看再改")
    h = t.find(LOBBY_HOOK)
    j = t.find("spacing = 5", h) if h >= 0 else -1
    if h < 0 or j < 0 or j - h > 200:
        raise SystemExit(f"**{LOBBY_REL} 里找不到 {LOBBY_HOOK} + spacing 锚点** —— 结构变了")
    k = t.index("\n", j) + 1
    t = t[:k] + btn_payload + t[k:]
    d, end = 0, -1
    for pos in range(t.index("{"), len(t)):
        c = t[pos]
        if c == "{":
            d += 1
        elif c == "}":
            d -= 1
            if d == 0:
                end = pos
                break
    if end < 0:
        raise SystemExit(f"**{LOBBY_REL} 花括号不配平**")
    head = ("\n\t### ↓↓↓ 以下由 tools/build/build_moe_cosplay_gui.py 注入 ↓↓↓\n"
            f"\t### 上面全部是原版 {LOBBY_REL} 的原文（只多插了一个按钮），不要手改本文件。\n")
    tail = "\t### ↑↑↑ 注入结束 ↑↑↑\n"
    return t[:end] + head + win_payload + tail + t[end:]


def main():
    # Preflight the reviewed bitmap assets before updating either window.
    _layout_check(verbose=True)
    art = {f"{ART_REL}/{name}": (ART_SOURCE/name).read_bytes() for name in ART_FILES}
    assert all(data.startswith(b"DDS ") for data in art.values())
    payload = gui_text()
    p = MOD / HOST_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    txt = host_text(payload)
    _write_generated(p, txt.encode("utf-8-sig"))
    print(f"  写入 {HOST_REL}（覆盖原版 {HOST_NAME}）"
          f"  {len(txt.splitlines())} 行，其中注入 {len(payload.splitlines())} 行")
    print(f"    {len(BODY_SLOTS)} 个本体槽位（登记 {SLOTS}，皮肤不开格；实时肖像，{PAGE_SIZE} 格一页共 {PAGES} 页）"
          f"+ {len(POSE_KEYS)} 个姿势分 {len(POSE_GROUPS)} 组（滚动列表，实时预览）")
    # 大厅入口：按钮 + 以选中角色为根的窗口副本
    lw = gui_text(S="Character", win=LOBBY_WIN, dc=LOBBY_CHAR, charS=LOBBY_CHAR)
    lb = lobby_button_text()
    lt = lobby_host_text(lw, lb)
    _write_generated(MOD / LOBBY_REL, lt.encode("utf-8-sig"))
    print(f"  写入 {LOBBY_REL}（覆盖原版 {LOBBY_TOP}）  {len(lt.splitlines())} 行，"
          f"其中注入按钮 {len(lb.splitlines())} 行 + 窗口 {len(lw.splitlines())} 行")
    for relative, data in art.items():
        _write_generated(MOD / relative, data)
    print(f"  复制 {len(art)} 张换装界面素材（{sum(map(len, art.values())):,} bytes）至核心包")
    # 旧产物：曾经写成独立顶层 window，引擎从不实例化它。留着只会让人以为
    # 窗口已经在了，删掉。
    old = MOD / "gui" / f"{WIN}.gui"
    if old.is_file():
        old.unlink()
        print(f"    删掉旧的独立 gui/{WIN}.gui（顶层 window 从不被实例化）")
    # 缩略图（gfx/interface/moe_preview/）2026-09-06 退役：左栏改成实时肖像后没有任何 gui 引用它。


def _write_generated(path, data):
    """Replace a core-owned output atomically, preserving hard-linked snapshots."""
    assert path.resolve().is_relative_to(MOD.resolve())
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.generating')
    temporary.write_bytes(data)
    temporary.replace(path)


if __name__ == "__main__":
    main()
