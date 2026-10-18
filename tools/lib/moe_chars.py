# -*- coding: utf-8 -*-
"""角色登记表 —— **加角色只改这个文件**，其余一切派生。

2026-09-06 加艾尔黛拉时角色配置散在 12 处（moe_paths.CHARACTERS、moe_out.GAME_OF /
SLOT_ORDER、build_moe_mod 的 GROUPS / TYPES / MOE_CHARACTERS / MESH_SRC / REPORTS /
SLOT_COUNT / _DESC_*、build_all.CHARS / ANIM_CHARS / check_products、build_moe_props.TYPES、
build_moe_previews.PREFIX_OF、moe_bullet_bake.REPORTS、贴图脚本写死芙洛伦源目录……），
漏了四处才在 finalize 自检抓到。同日要批量接 20 个少前 2 角色，不可能每人改 12 处。

两类角色：
  · **模型角色**（所有条目）：portrait group / type / ethnicity / DNA / 基因 / 动画集 /
    道具 / 模型池注册 / 换装槽位。这是「能在游戏里用这套模型」的最小集合。
  · **可玩开局角色**（`playable` 非空）：另加书签卡片、history 角色、王朝/家族、
    伯爵领。用户 2026-09-06 定：批量导入的角色**只进模型池与换装槽位**，不做开局角色。

字段：
  ck        短键（ASCII，文件名/变量名/事件号都用它；**不能互为前缀**，char_of 按最长前缀匹配）
  slug      盘点报告名 = work/pmx_skeleton_inventory/<slug>.json = 建网格产物目录名
  game      来源游戏（决定落进哪个模型子 mod）
  prefix    产物文件名前缀（moe_<ck>_；芙洛伦的 `moe_` 无角色段是历史遗留，别学）
  tex       该角色贴图基名的正则（用**贴图里**的角色名，可能与 slug 不同）
  tex_src   {PMX 引用的贴图名: 源包内实际文件名}——源包把图改了名、PMX 没跟着改时用；导入时先把图重定位到真实文件
  tex_alias {源文件名: 写进 mesh 的别名}——同名不同内容、或没角色前缀的图，起一个带自己前缀的名
  phys_erp_skip 物理烘焙时不参与按轴限位 ERP 软化的骨名子串（小写匹配 bn_m_* 名）；链式挂件软化后会整链折过去时登记
  phys_wall_fit 运动学墙组 (g, ...)，物理烘焙时按 rest 穿透量收缩该组球/胶囊墙半径（碰撞保留，坐姿仍有腿撑裙）
  phys_ang_damp_cap 动态刚体角阻尼上限（None = 用 PMX 原值）。坐姿被腿墙静态挤压时压极限循环，不吃正常摆幅
  phys_erp2 接触约束 ERP（None = CLI 默认 0.1）。0.02 让坐姿指纹从连续颤动转成间歇跳变；0 会把裙子冻死
  phys_warmup 循环动画预热圈数（None = CLI 默认 1）。坐姿要 3 圈压入场瞬变，代价是解算时间同比例涨
  phys_nocollide PMX 碰撞组对 ((gA, gB), ...)，物理烘焙时双向免碰；挂件 rest 下嵌进墙里超过自身厚度（踢击而非颤动、求解参数全无效）时登记
  phys_chain_nocollide ((刚体名前缀元组, 禁碰组元组), ...)；仅作用于匹配的动态链，包含未蒙皮末端刚体，先于手臂移组执行
  phys_follow_bone 刚体名前缀元组；饰品跟随基础动画骨、不参与动态解算/碰撞/关节写回
  phys_degenerate_extent 仅将源碰撞形状恰好为 0 的有效尺寸扩至此 PMX 值；不改正尺寸、碰撞组或关节
  src       源模型解压目录（glob，必须恰好命中一个目录；贴图在其下递归找）
  work      建网格产物目录（相对 ROOT）；report = work/<report>
  bake      表情烘焙产物目录（相对 ROOT）= build_moe_mod 的 mesh 拷贝来源
  char_id   history / 书签肖像用的角色 id（9900001 起，加角色顺延，别改旧的）
  slot_char_id（派生）换装格左栏肖像用的槽位角色 id：可玩者 9900500+槽号（开局即死亡的占位），其余 = char_id
  playable  None 或 dict(title, birth, death, position, house, dynasty, dyn_name,
            bm_weight, stats, emblem, coa_color, desc_en, desc_cn)

**列表顺序 = 换装窗口槽位顺序 = 模型池注册顺序**。往后追加，别插中间（槽位号写进
预览图文件名与玩家变量）。
"""
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[2]
GROUP = "moe"

# 少前 2 系列的贴图前缀 = `c_<角色英文名>`；同一角色的换装皮肤会出现 `c_<名>ssr01xx`
# 之类的后缀，`^c_<名>` 一并吃掉。**`^c_nagant` 不会吃到 `c_mosinnagant`**（锚定开头）。
_GFL2_SRC = "assets/GFL2/【少女前线2：追放】{cn}_by_*"


def _gfl2(ck, slug, cn, en, tex, src=None, skin_of=None, tex_alias=None, tex_src=None, **extra):
    return dict(
        ck=ck, slug=slug, game="gfl2", name_cn=cn, name_en=en, skin_of=skin_of, tex_alias=dict(tex_alias or {}),
        tex_src=dict(tex_src or {}),
        prefix=f"{GROUP}_{ck}_", tex=tuple(tex),
        src=src or _GFL2_SRC.format(cn=cn),
        work=f"work/{slug}", report=f"{slug}_report.json",
        bake=f"work/{ck}_anim_bake", playable=None, **extra,
    )


def _candidate_src(slug):
    """Resolve a surveyed candidate's PMX folder from its frozen inventory record."""
    inventory = ROOT / "work" / "pmx_skeleton_inventory" / f"{slug}.json"
    if not inventory.is_file():
        raise FileNotFoundError(f"Missing candidate inventory {inventory}")
    source = Path(json.loads(inventory.read_text(encoding="utf-8"))["source_file"])
    return source.parent.relative_to(ROOT).as_posix()


def _candidate(ck, slug, cn, en, game, skin_of=None, **extra):
    """Add a surveyed second-wave model with isolated diffuse names."""
    return dict(
        ck=ck, slug=slug, game=game, name_cn=cn, name_en=en, skin_of=skin_of,
        prefix=f"{GROUP}_{ck}_", tex=(rf"^moe_{ck}_",),
        src=_candidate_src(slug), work=f"work/{slug}", report=f"{slug}_report.json",
        bake=f"work/{ck}_anim_bake", playable=None,
        texture_namespace=True, release_managed=False, tex_alias={}, tex_src={},
        **extra,
    )


CHARS = [
    # ---- 四个先行角色（2026-08/09 逐个手工接入；可玩开局角色，书签都在西班牙东北）
    dict(ck="flo", slug="gfl2_florence", game="gfl2", name_cn="芙洛伦", name_en="Florence",
         prefix="moe_", tex=(r"^c_Florence", r"^c_FlorenceSSR01", r"^cw_Florence"),
         src="assets/source_models/芙洛伦原皮_by_少女前线2：追放_*/芙洛伦原皮",
         work="work/florence_poc", report="florence_poc_report.json",
         bake="work/moe_anim_bake",
         playable=dict(
             title="c_barcelona", birth="1040.3.1", death="1100.1.1",
             position="700 480", house="house_moe_florence",
             dynasty="moe_dynasty_florence", dyn_name="dynn_Florence", bm_weight=100,
             stats={"martial": 8, "diplomacy": 6, "intrigue": 5,
                    "stewardship": 6, "learning": 5, "prowess": 9},
             emblem="ce_fleur_florence.dds", coa_color="red",
             desc_en="A playable start for the anime portrait project. Her portrait uses "
                     "the independent #bold moe#! portrait group: custom skeleton, custom "
                     "animation, custom face system, with zero changes to the vanilla "
                     "#bold human#! group.",
             desc_cn="二次元肖像项目的可玩开局。她的肖像走独立的 #bold moe#! "
                     "portrait group：自带骨架、自带动画、自带捏脸系统，"
                     "原版 #bold human#! group 零改动。")),
    dict(ck="lau", slug="endfield_laurentina", game="endfield", name_cn="洛茜", name_en="Laurentina",
         prefix="moe_lau_", tex=(r"^T_actor_wulfa",),
         src="assets/source_models/【明日方舟：终末地】洛茜_by_*",
         work="work/endfield_laurentina", report="endfield_laurentina_report.json",
         bake="work/lau_anim_bake",
         playable=dict(
             title="c_girona", birth="1042.7.1", death="1102.1.1",
             position="940 250", house="house_moe_laurentina",
             dynasty="moe_dynasty_laurentina", dyn_name="dynn_Laurentina", bm_weight=99,
             stats={"martial": 9, "diplomacy": 5, "intrigue": 6,
                    "stewardship": 5, "learning": 6, "prowess": 10},
             emblem="ce_wolf.dds", coa_color="blue",
             desc_en="A playable start for the second anime model. She has her own "
                     "portrait group, her own 473-bone skeleton, her own animation set, "
                     "and Bullet-baked hair and skirt physics.",
             desc_cn="第二个二次元模型的可玩开局。她有自己的 portrait group、"
                     "自己的骨架（473 骨）、自己的动作集，以及 Bullet 烘焙的"
                     "头发与裙子物理。")),
    dict(ck="per", slug="endfield_perlica", game="endfield", name_cn="洁尔佩塔", name_en="Aglina",
         prefix="moe_per_", tex=(r"^T_actor_aglina",),
         # 挂在上脊椎的 QB/QJ 飘带（4 节、各 ±10°，增益 1）不参与按轴限位 ERP 软化：软化后整链折过去
         # （摆角 60°→180°、抖动 6→18，开关对照可复现、无关微扰不复现，是真回归不是刀锋态）。
         phys_erp_skip=("_qb_", "_qj_"),
         src="assets/source_models/【明日方舟：终末地】洁尔佩塔_by_*",
         work="work/endfield_perlica", report="endfield_perlica_report.json",
         bake="work/per_anim_bake",
         playable=dict(
             title="c_urgell", birth="1044.4.1", death="1104.1.1",
             position="700 250", house="house_moe_aglina",
             dynasty="moe_dynasty_aglina", dyn_name="dynn_Aglina", bm_weight=98,
             stats={"martial": 7, "diplomacy": 8, "intrigue": 7,
                    "stewardship": 7, "learning": 6, "prowess": 8},
             emblem="ce_lion_passant.dds", coa_color="green",
             desc_en="A playable start for the third anime model. She has her own "
                     "portrait group, her own 594-bone skeleton, her own animation set, "
                     "and Bullet-baked hair and skirt physics whose collision bodies "
                     "are scaled to 50%.",
             desc_cn="第三个二次元模型的可玩开局。自带 portrait group、594 骨骨架、"
                     "独立动作集，头发与裙子物理由 Bullet 烘焙，碰撞体缩到 50%。")),
    dict(ck="eld", slug="endfield_eldera", game="endfield", name_cn="艾尔黛拉", name_en="Eldera",
         prefix="moe_eld_", tex=(r"^T_actor_ardelia",),
         src="assets/source_models/【明日方舟：终末地】艾尔黛拉_by_*",
         work="work/endfield_eldera", report="endfield_eldera_report.json",
         bake="work/eld_anim_bake",
         playable=dict(
             title="c_rossello", birth="1046.1.1", death="1106.1.1",
             position="940 480", house="house_moe_eldera",
             dynasty="moe_dynasty_eldera", dyn_name="dynn_Eldera", bm_weight=97,
             stats={"martial": 6, "diplomacy": 7, "intrigue": 8,
                    "stewardship": 6, "learning": 9, "prowess": 6},
             emblem="ce_harp.dds", coa_color="purple",
             desc_en="A playable start for the fourth anime model. Her own portrait group, "
                     "skeleton and animation set, with skirt and hair physics baked by the "
                     "native Bullet solver straight from the source model's rigid bodies.",
             desc_cn="第四个二次元模型的可玩开局。自带 portrait group、骨架与动作集，"
                     "裙子与头发物理由原生 Bullet 直接按源模型刚体烘焙。")),

    # ---- 少前 2 批量（2026-09-06 起）：默认造型 20 个，只进模型池与换装槽位，不烘物理。
    # ck 与 per（洁尔佩塔）撞的 佩里缇亚 用 pri。莱娜 的贴图有 c_Lanna 拼写变体。
    _gfl2("len", "gfl2_lennadefault", "莱娜", "Lenna", (r"^c_lenna", r"^c_lanna"),
          src="assets/GFL2/lenna_by_*",
          phys_nocollide=((2, 7),),   # 裙 rest 嵌腿/躯干墙(g2×g7)，idle 踢击 6.9%→0.03%（2026-09-09 A/B）
          phys_degenerate_extent=0.001  # 源上半身胶囊半径 0；保留轴线，扩成有限细胶囊（09-18）
    ),
    _gfl2("pri", "gfl2_perityadefault", "佩里缇亚", "Peritya", (r"^c_peritya",)),
    _gfl2("cha", "gfl2_charolicdefault", "克罗丽科", "Charolic", (r"^c_charolic",)),
    _gfl2("sab", "gfl2_sabrinadefault", "塞布丽娜", "Sabrina", (r"^c_sabrina",)),
    _gfl2("sha", "gfl2_sharkrydefault", "夏克里", "Sharkry", (r"^c_sharkry",)),
    _gfl2("che", "gfl2_cheetadefault", "奇塔", "Cheeta", (r"^c_cheeta",),
          # 裙 rest 嵌腿墙(g2×g7) idle 踢击 5.2%→0；发链根节 HairK1/H1 球嵌 頭A/首(g0)、頭(g1) + 全轴锁死关节拉锯 13%→0（2026-09-09 A/B）。
          phys_nocollide=((2, 7), (4, 0), (4, 1)),
          phys_follow_bone=('RibbonA', 'RibbonB', 'CapeB', 'CapeC')),
    _gfl2("col", "gfl2_colphnedefault", "寇尔芙", "Colphne", (r"^c_colphne",),
          expression_tooth_depth_weight=1024.),
    _gfl2("tol", "gfl2_tololodefault", "托洛洛", "Tololo", (r"^c_tololo",),
          expression_tooth_depth_weight=1024., expression_tooth_coupling_weight=128.),
    _gfl2("cen", "gfl2_centaureissidefault", "桑朵莱希", "Centaureissi", (r"^c_centaureissi",)),
    _gfl2("mac", "gfl2_macqiatodefault", "玛绮朵", "Macqiato", (r"^c_macqiato",)),
    _gfl2("qio", "gfl2_qiongjiudefault", "琼玖", "QiongJiu", (r"^c_qiongjiu",)),
    _gfl2("kse", "gfl2_kseniadefault", "科谢尼娅", "Ksenia", (r"^c_ksenia",),
          phys_degenerate_extent=0.001),  # 源 Bag1 盒 z 半厚度 0；有限薄片，不关闭碰撞/锁定动态
    _gfl2("nag", "gfl2_nagantdefault", "纳甘", "Nagant", (r"^c_nagant",)),
    _gfl2("nem", "gfl2_nemesisdefault", "纳美西丝", "Nemesis", (r"^c_nemesis",)),
    _gfl2("jia", "gfl2_jiangyudefault", "绛雨", "JiangYu", (r"^c_jiangyu",),
          # 袖口布片 Lf/Rt_cloth_loft 各 5 面碰撞板 + 3 动态体，与裙子(g2)互碰是裙抖主因：
          # idle kick 33.5%→15.8% max 25.53→10.92、dance_spin 40.7→30.5，指纹连续颤动→间歇跳变，
          # 与「只留裙子」基线(16.5%)持平而只改 16 条刚体。|r| 0.250→0.361 上升 = 压掉的是解算
          # 噪声不是如实响应（对比摩擦力那次 0.859→0.416 塌陷）。cloth_loft 掩码 0xff6d/0xff6f
          # 与同组头发 0xffef 本就不同，PMX 作者已显式区分，免碰 g2 不牵连头发。（2026-09-14 A/B）
          phys_chain_nocollide=((("Lf_cloth_loft", "Rt_cloth_loft"), (2,)),)),
          # 2026-09-15 曾登记 phys_wall_fit=(6,7) / phys_ang_damp_cap=0.9 / phys_erp2=0.02
          # / phys_warmup=3 压坐姿抖动，**已全部撤除**：那四档是在「裙子还没落到腿上」的
          # 基线上 A/B 的（预热当时是 1 圈），压小的是悬空乱弹而不是搭在腿上的稳态。
          # 用户「如果你不加预热，裙子甚至都不正确在腿上，回退所有其他参数」。
          # 预热改成全员默认（warmup 3 / settle 180），要重新定参先在新基线上量。
    _gfl2("vep", "gfl2_vepleydefault", "维普蕾", "Vepley", (r"^c_vepley",)),
    _gfl2("lit", "gfl2_littaradefault", "莉塔拉", "Littara", (r"^c_littara",),
          # 内裙 64 盒里 39 盒 rest 下嵌进腿/躯干胶囊(g7) 1.5~2 倍厚度，Bullet 每步往外踢 → 58% 踢击；
          # 免碰后 0.56%（2026-09-08 A/B）。代价：大动作时内裙可能穿腿，实机看。
          phys_nocollide=((2, 7),)),
    _gfl2("mos", "gfl2_mosinnagantdefault", "莫辛纳甘", "Mosinnagant", (r"^c_mosinnagant",)),
    _gfl2("gro", "gfl2_grozadefault", "闪电", "Groza", (r"^c_groza",)),
    _gfl2("dai", "gfl2_daiyandefault", "黛烟", "DaiYan", (r"^c_daiyan",),
          phys_nocollide=((2, 7),),
          phys_chain_nocollide=((('Sleeve',), (0, 1, 2, 7)),),
          phys_follow_bone=('ArmRCard', 'HandLLace', 'HandRLace', 'Earring', 'ShoeLace', 'Tie', 'Tassel')
    ),

    # ---- 少前 2 换装皮肤（2026-09-06 晚，用户「把 7 个皮肤也导入进来」）。骨表与本体差异大
    # （同名骨 194~350 根、序全不同），每套按新角色走完整管线（不烘物理）；`skin_of` 指向本体 ck，
    # 换装窗口不给皮肤单独开格，挂在本体格的轮换按钮上切（0001/0003 写 moe_skins_<本体槽>）。
    # 皮肤与本体共用的同名同内容贴图按 tex 正则落进本体目录（引擎按裸基名平表，同名只能一份）。
    _gfl2("chs", "gfl2_charolicskin001", "克罗丽科·跃行脱兔", "CharolicRabbit", (r"^c_charolic",),
          src="assets/GFL2/【少女前线2：追放】克罗丽科（跃行脱兔）_by_*", skin_of="cha"),
    # 身体图叫 B_body_d.png（不带角色前缀；全库只有她有 B_body*）：不加这条 dest() 归属不了，v7 装机就栽在这。
    _gfl2("mab", "gfl2_macqiatoballgrown", "玛绮朵·舞会休止符", "MacqiatoBallgown", (r"^c_macqiato", r"^B_body"),
          src="assets/GFL2/【少女前线2：追放】玛绮朵 · 衣装 · 舞会休止符_by_*", skin_of="mac",
          phys_nocollide=((2, 7),)   # 裙 rest 嵌腿/躯干墙(g2×g7)，idle 踢击 5.4%→0.40%（2026-09-09 A/B）
    ),
    # 毛领（Furs）在 PMX 里引用 c_MosinnagantSR01_slg_plush_dt.png，源包里被改名成 c_SR01_slg_plush_dt.png：
    # 不重定位就是尺寸 0 的图被过滤、退回球面辅助图 SSShine1 当 diffuse（v7 实际就写进了 mesh）。
    # 重定位后再起一个带自己前缀的别名，路由（^c_vepley）与贴图反查都走现成机制。
    _gfl2("vec", "gfl2_vepleycommanderskin", "维普蕾·闪耀心愿", "VepleyWish", (r"^c_vepley",),
          src="assets/GFL2/【少女前线2：追放】维普蕾（闪耀心愿）_by_*", skin_of="vep",
          tex_src={"c_MosinnagantSR01_slg_plush_dt.png": "c_SR01_slg_plush_dt.png"},
          tex_alias={"c_SR01_slg_plush_dt.png": "c_VepleyWish_slg_plush_dt.png"}),
    _gfl2("grs", "gfl2_grozaskin001", "闪电·黎明战役", "GrozaDawn", (r"^c_groza",),
          src="assets/GFL2/【少女前线2：追放】闪电（作战服·黎明战役）_by_*", skin_of="gro"),
    _gfl2("grz", "gfl2_groza_2024springfestival", "闪电·紫雨心", "GrozaVioletRain", (r"^c_groza", r"^cw_ksenia"),   # 她的武器贴图借科谢尼娅的 cw_KseniaSR01_WL
          src="assets/GFL2/【少女前线2：追放】闪电（紫雨心）_by_*", skin_of="gro",
          expression_head_budget_floor=.5),  # Pure-head mouth targets need nonzero shared FX budget.
    # 下面两套各有一张与别人**同名不同内容**的贴图（引擎按裸基名建全局平表，同名只能存一份）：
    # 导入时按 tex_alias 把源图改名（build_florence_poc），贴图脚本按别名反查源文件（build_char_textures）。
    _gfl2("das", "gfl2_daiyan_2024springfestival", "黛烟·玉玲珑", "DaiYanJade", (r"^c_daiyan", r"^cw_fayee"),
          src="assets/GFL2/【少女前线2：追放】黛烟（玉玲珑）_by_*", skin_of="dai",
          tex_alias={"c_Daiyan_slg_face_d.png": "c_DaiyanSSR0103_slg_face_d.png"}),     # 与本体脸图同名不同内容
    _gfl2("lec", "gfl2_lenna_commander", "莱娜·元气魔法", "LennaMagic", (r"^c_lenna", r"^c_lanna"),
          src="assets/GFL2/莱娜·作战服·元气魔法_by_*", skin_of="len",
          tex_alias={"c_GrozaSR0105_slg_body_d.png": "c_LennaSSR0105_slg_body_d.png"},
          phys_nocollide=((2, 7),)   # 裙 rest 嵌腿/躯干墙(g2×g7)，idle 踢击 2.5%→0.17%（2026-09-09 A/B）
    ),  # 借了闪电紫雨心的身体图名，内容不同
]

# ---- GFL2 第二批、终末地女孩与男孩（2026-09-24 调查完成，用户授权导入）
# 数据源与选择结论见 work/evidence/hf_import_20260923/summary.json：GFL2 30 个 PMX
# 全部导入；终末地女孩的 4 个旧源文件重复模型中，3 个已发布，不重复注册；
# 萤石只有旧源盘点，在本段末补入，其余 19 个先登记；男孩批次
# 9 个压缩包里有 11 个 PMX 形态。第二波模型分别进入 gfl2_2 / endfield_2 子 Mod。
# 新候选的 diffuse 在 mesh 引用里加 moe_<ck>_ 前缀，避免跨包/跨角色裸贴图基名撞名；
# extra.png 与 T_actor_common_* 仍复用各自原系列模型包里的共享贴图。
_SECOND_WAVE_ROOTS = [
    # GFL2 第二批中新增的默认造型。
    _candidate("clu", "gfl2_2_002_efe68546", "可露凯", "Klukai", "gfl2_2"),
    _candidate("biy", "gfl2_2_007_252973e7", "比悠卡", "Biyoca", "gfl2_2"),
    _candidate("lot", "gfl2_2_009_f2da134d", "洛塔", "Lotta", "gfl2_2"),
    _candidate("qiu", "gfl2_2_010_e4fe174c", "秋桦", "Qiuhua", "gfl2_2"),
    _candidate("mis", "gfl2_2_012_4cc23c77", "米什缇", "Mishty", "gfl2_2"),
    _candidate("ull", "gfl2_2_013_be98f9b7", "乌尔丽德", "Ullrid", "gfl2_2"),
    _candidate("and", "gfl2_2_016_ed5cc685", "安朵丝", "Andoris", "gfl2_2"),
    _candidate("spr", "gfl2_2_019_a030b24c", "春田", "Springfield", "gfl2_2"),
    _candidate("zho", "gfl2_2_020_c16ba4dc", "朝晖", "Zhaohui", "gfl2_2"),
    _candidate("dus", "gfl2_2_022_d7f5e468", "杜莎妮", "Dusevnyj", "gfl2_2"),
    _candidate("suo", "gfl2_2_026_4019c945", "索米", "Suomi", "gfl2_2"),
    _candidate("fay", "gfl2_2_027_52714405", "绯", "Faye", "gfl2_2"),
    _candidate("vct", "gfl2_2_028_4f5d642f", "维克托", "Vector", "gfl2_2"),

    # 终末地女孩包里除四个重复模型之外的 19 个新增角色。
    _candidate("dea", "endfield_001_40efd214", "Death", "Death", "endfield"),
    _candidate("yvo", "endfield_002_75a2edd0", "伊冯", "Yvonne", "endfield"),
    _candidate("emb", "endfield_003_f18550f2", "余烬", "Ember", "endfield"),
    _candidate("aet", "endfield_005_e8d65557", "埃特拉", "Aethera", "endfield"),
    _candidate("fad", "endfield_006_eaff3d48", "女管理员", "FemaleOperator", "endfield"),
    _candidate("zfy", "endfield_007_78785330", "庄方宜", "ZhuangFangyi", "endfield"),
    _candidate("mif", "endfield_008_112b8397", "弭弗", "Mif", "endfield"),
    _candidate("tep", "endfield_009_d91bb553", "提弗洛斯", "Tephros", "endfield"),
    _candidate("zxu", "endfield_010_67ba02db", "昼雪", "Zhouxue", "endfield"),
    _candidate("lzy", "endfield_011_d4d20d71", "李织烟", "Liziyan", "endfield"),
    _candidate("lin", "endfield_012_ff8257a8", "梨诺", "Lino", "endfield"),
    _candidate("tan", "endfield_013_7829e7b5", "汤汤", "Tangtang", "endfield"),
    _candidate("shi", "endfield_016_77ce5015", "祀", "Si", "endfield"),
    _candidate("qil", "endfield_017_a2c1b8e4", "秋栗", "Qiuli", "endfield"),
    _candidate("avi", "endfield_019_a28d4daa", "艾维文娜", "Avywenna", "endfield"),
    _candidate("lav", "endfield_020_5f9643df", "莱万汀", "Laevatain", "endfield"),
    _candidate("sei", "endfield_022_63f64fcf", "赛希", "Seth", "endfield"),
    _candidate("cyu", "endfield_023_3d44ab42", "陈千语", "ChenQianyu", "endfield"),

    # 终末地男孩第二模型包中的九个基础模型。
    _candidate("kac", "endfield_2_001_c47bf814", "卡契尔", "Kachel", "endfield_2", sex="male"),
    _candidate("kam", "endfield_2_002_ca7bc3b1", "卡缪", "Kamu", "endfield_2", sex="male"),
    _candidate("pan", "endfield_2_005_e3a553c2", "大潘", "Pan", "endfield_2", sex="male"),
    _candidate("ant", "endfield_2_006_6bb0d86d", "安塔尔", "Antal", "endfield_2", sex="male"),
    _candidate("wol", "endfield_2_007_f1694607", "狼卫", "WolfGuard", "endfield_2", sex="male"),
    _candidate("mad", "endfield_2_008_cb78e97c", "男管理员", "MaleOperator", "endfield_2", sex="male"),
    _candidate("ale", "endfield_2_009_a2024d8a", "阿列什", "Alesh", "endfield_2", sex="male"),
    _candidate("jun", "endfield_2_010_010905ce", "骏卫", "Junwei", "endfield_2", sex="male"),
    _candidate("ada", "endfield_2_011_655daf82", "阿达希尔", "Adashir", "endfield_2", sex="male"),
]

# 换装形态排在新基础模型之后，保持本批次的实际槽位连续。已有 GFL2 角色可跨模型
# 子 Mod 挂载新换装；男孩同一压缩包里的卡缪形态也走同一轮换入口。
_SECOND_WAVE_SKINS = [
    _candidate("prf", "gfl2_2_001_f0da1b84", "佩里缇亚（修正版）", "PerityaRepaired", "gfl2_2", skin_of="pri"),
    _candidate("clr", "gfl2_2_003_a0790aac", "可露凯·新机车", "KlukaiRacing", "gfl2_2", skin_of="clu"),
    _candidate("clb", "gfl2_2_004_6826e4cc", "可露凯·速度之星", "KlukaiBunny", "gfl2_2", skin_of="clu"),
    _candidate("chr", "gfl2_2_005_9c9020cd", "奇塔·赛场火花", "CheetaRacing", "gfl2_2", skin_of="che"),
    _candidate("tos", "gfl2_2_006_63f08b2e", "托洛洛·暮夜天河", "TololoSummerNight", "gfl2_2", skin_of="tol"),
    _candidate("byp", "gfl2_2_008_009c2d6b", "比悠卡·魅影幽梦", "BiyocaPhantom", "gfl2_2", skin_of="biy"),
    _candidate("lom", "gfl2_2_023_67aa09d4", "洛塔·竞技萌新", "LottaMarathon", "gfl2_2", skin_of="lot"),
    _candidate("mir", "gfl2_2_011_3a832903", "米什缇·休息室", "MishtyRestRoom", "gfl2_2", skin_of="mis"),
    _candidate("prh", "gfl2_2_014_f875dc9d", "佩里缇亚·天生猎手", "PerityaHunter", "gfl2_2", skin_of="pri"),
    _candidate("sbs", "gfl2_2_015_0f0c8a42", "塞布丽娜·泳装", "SabrinaSummer", "gfl2_2", skin_of="sab"),
    _candidate("cpx", "gfl2_2_017_bf49e872", "寇尔芙·SSR01", "ColphneSSR01", "gfl2_2", skin_of="col"),
    _candidate("spb", "gfl2_2_018_cc787cf2", "春田·灯火下的女王", "SpringfieldBallgown", "gfl2_2", skin_of="spr"),
    _candidate("zhr", "gfl2_2_021_8eeea7a7", "朝晖·赛车", "ZhaohuiRacing", "gfl2_2", skin_of="zho"),
    _candidate("mas", "gfl2_2_024_79e7c6a0", "玛绮朵·新春", "MacqiatoSpring", "gfl2_2", skin_of="mac"),
    _candidate("sws", "gfl2_2_025_43ef6f59", "索米·灵海游光", "SuomiSwimwear", "gfl2_2", skin_of="suo"),
    _candidate("vps", "gfl2_2_029_99c97d06", "维普蕾·晴夏回响", "VepleySummer", "gfl2_2", skin_of="vep"),
    _candidate("lnc", "gfl2_2_030_b8a56876", "莱娜·秘夜猎影", "LennaNightHunt", "gfl2_2", skin_of="len"),
    _candidate("kfm", "endfield_2_003_095bf39f", "卡缪·形态二", "KamuForm2", "endfield_2", skin_of="kam", sex="male"),
    _candidate("kfw", "endfield_2_004_6353c1fa", "卡缪·形态二（带翅膀）", "KamuForm2Wings", "endfield_2", skin_of="kam", sex="male"),
    _candidate("pea", "endfield_004_5e58f0a0", "佩丽卡·另一造型", "PerlicaAlt", "endfield", skin_of="per"),
]
CHARS.extend(_SECOND_WAVE_ROOTS + _SECOND_WAVE_SKINS)

# 萤石的 PMX 与旧 source_models 重复，但旧库只有盘点，没有已导入角色。
# 放在已登记槽位之后补入，避免本批次现有槽位号移动。
_SECOND_WAVE_EXISTING_SOURCES = [
    _candidate("flu", "endfield_fluorite", "萤石", "Fluorite", "endfield"),
]
CHARS.extend(_SECOND_WAVE_EXISTING_SOURCES)

# 鸣潮（2026-10-08）：一包一模型，按性别分 wuwa（女）/ wuwa_2（男）。只能往后追加。
_WUWA = [
    _candidate("wvr", "wuwa_001_6eef8d81", "维里奈", "Verina", "wuwa"),
    _candidate("wjx", "wuwa_002_041dc033", "今汐", "Jinhsi", "wuwa"),
    _candidate("wjt", "wuwa_003_3968cf78", "今汐·桃夭灼灼", "JinhsiPeach", "wuwa"),
    _candidate("wcs", "wuwa_004_adbff656", "千咲·泳装", "ChisaSwim", "wuwa"),
    _candidate("wbl", "wuwa_005_0ff2d71c", "卜灵", "Buling", "wuwa"),
    _candidate("wct", "wuwa_006_64969f8f", "卡提希娅", "Cartethyia", "wuwa",
               phys_degenerate_extent=0.001),  # 源 ArmRing/LegRing/Bhair_17 球半径 0（10-09）
    _candidate("wsa", "wuwa_007_b4dd5d8c", "守岸人", "Shorekeeper", "wuwa"),
    _candidate("wcn", "wuwa_008_39312472", "坎特蕾拉（少女）", "Cantarella", "wuwa"),
    _candidate("wyn", "wuwa_009_323e61f8", "尤诺", "Yuno", "wuwa"),
    _candidate("wfl", "wuwa_010_eda96b7c", "弗洛洛", "Phrolova", "wuwa"),
    _candidate("wzz", "wuwa_011_b1978d1b", "折枝", "Zhezhi", "wuwa"),
    _candidate("wcu", "wuwa_012_c23eeb92", "椿", "Camellya", "wuwa"),
    _candidate("wrc", "wuwa_013_6fc32f64", "洛可可", "Roccia", "wuwa"),
    _candidate("wdd", "wuwa_014_e3d9517a", "灯灯", "Lumi", "wuwa"),
    _candidate("wcr", "wuwa_015_d07d41db", "珂莱塔", "Carlotta", "wuwa"),
    _candidate("wpb", "wuwa_016_f107fdd6", "菲比", "Phoebe", "wuwa"),
    _candidate("wzn", "wuwa_017_358b77ea", "赞妮", "Zani", "wuwa"),
    _candidate("wyh", "wuwa_018_62bb1d47", "釉瑚", "Youhu", "wuwa"),
    _candidate("wcl", "wuwa_019_0b4f3289", "长离", "Changli", "wuwa"),
    _candidate("wdj", "wuwa_020_2e376a40", "丹瑾", "Danjin", "wuwa"),
    _candidate("wcq", "wuwa_021_bd2ef352", "千咲", "Chisa", "wuwa"),
    _candidate("wyl", "wuwa_022_45518670", "吟霖", "Yinlin", "wuwa",
               phys_degenerate_extent=0.001),  # 源上半身2 球半径 0（10-09）
    _candidate("wrf", "wuwa_023_c6315bb0", "女漂泊者", "RoverFemale", "wuwa"),
    _candidate("wec", "wuwa_024_8b3be221", "安可", "Encore", "wuwa"),
    _candidate("wsh", "wuwa_025_d781b255", "散华", "Sanhua", "wuwa"),
    _candidate("wtq", "wuwa_026_6a24b897", "桃祈", "Taoqi", "wuwa"),
    _candidate("wcx", "wuwa_027_476c3cb3", "炽霞", "Chixia", "wuwa"),
    _candidate("wbz", "wuwa_028_3b31d7f5", "白芷", "Baizhi", "wuwa",
               phys_degenerate_extent=0.001),  # 源 Piao_*_Arm01_001 球半径 0（10-09）
    _candidate("wyy", "wuwa_029_6005bda5", "秧秧", "Yangyang", "wuwa"),
    _candidate("wjn", "wuwa_030_ac07f88e", "鉴心", "Jianxin", "wuwa"),
    _candidate("wsm", "wuwa_031_ad7c02e9", "锁暝", "Suoming", "wuwa"),
    _candidate("wly", "wuwa_2_001_b462b0fc", "凌阳", "Lingyang", "wuwa_2", sex="male"),
    _candidate("wsc", "wuwa_2_002_3c09b74d", "伤痕", "Scar", "wuwa_2", sex="male"),
    _candidate("wkf", "wuwa_2_003_5e7cf7cc", "克里斯托弗", "Christoph", "wuwa_2", sex="male"),
    _candidate("wkk", "wuwa_2_004_c286df89", "卡卡罗", "Calcharo", "wuwa_2", sex="male"),
    _candidate("wbt", "wuwa_2_005_5553a57c", "布兰特", "Brant", "wuwa_2", sex="male"),
    _candidate("wxy", "wuwa_2_006_fb20aaaf", "相里要", "Xiangliyao", "wuwa_2", sex="male"),
    _candidate("wyv", "wuwa_2_007_22009c81", "严彦", "Yanyan", "wuwa_2", sex="male"),
    _candidate("wjy", "wuwa_2_008_d5940446", "忌炎", "Jiyan", "wuwa_2", sex="male"),
    _candidate("wyw", "wuwa_2_009_16b7b5d3", "渊武", "Yuanwu", "wuwa_2", sex="male"),
    _candidate("wrm", "wuwa_2_010_7d1fbcc9", "男漂泊者", "RoverMale", "wuwa_2", sex="male"),
    _candidate("wqs", "wuwa_2_011_e6e63103", "秋水", "Aalto", "wuwa_2", sex="male"),
    _candidate("wmf", "wuwa_2_012_1e27c3bf", "莫特斐", "Mortefi", "wuwa_2", sex="male"),
    _candidate("wab", "wuwa_2_013_c10c81d9", "阿布", "Abby", "wuwa_2", sex="male"),
]
CHARS.extend(_WUWA)

# 各游戏系列**跨角色共用**的贴图（引擎按裸基名建全局平表，同名文件只能存在一份，
# 所以不能每个角色复制一张）。落进该游戏模型包的 shared_tex/。
#   endfield: T_actor_common_* 通用表情图集（三件 head mesh 都引用）
#   gfl2:     extra.png 表情贴花图集（27 个模型 md5 全同，含芙洛伦）
SHARED_TEX_BY_GAME = {
    "endfield": (r"^T_actor_common",),
    "gfl2": (r"^extra",),
}

GAMES = {
    "gfl2":     {"cn": "少女前线2：追放", "en": "Girls' Frontline 2"},
    "endfield": {"cn": "明日方舟：终末地", "en": "Arknights: Endfield"},
    "gfl2_2":   {"cn": "少女前线2：追放（第二模型包）", "en": "Girls' Frontline 2: Batch 2",
                 "deps": ["Moe Portraits - Models: Girls' Frontline 2"]},
    "endfield_2": {"cn": "明日方舟：终末地（男孩模型包）", "en": "Arknights: Endfield: Batch 2",
                   "deps": ["Moe Portraits - Models: Arknights: Endfield"]},
    "wuwa":     {"cn": "鸣潮", "en": "Wuthering Waves"},
    "wuwa_2":   {"cn": "鸣潮（男性模型包）", "en": "Wuthering Waves: Batch 2",
                 "deps": ["Moe Portraits - Models: Wuthering Waves"]},
}

# ---------------------------------------------------------------- 派生
BY_CK = {c["ck"]: c for c in CHARS}
ORDER = [c["ck"] for c in CHARS]                    # 槽位顺序
# 换装窗口模型网格每页格数（用户 2026-09-06：20 个一页）。页数 = ceil(len(CHARS)/它)。
SLOT_PAGE_SIZE = 20
for _i, _c in enumerate(CHARS, start=1):
    _c.setdefault("char_id", 9900000 + _i)          # flo=9900001 … 与历史值一致
    _c["type"] = f"{GROUP}_{_c['ck']}"
    _c["group"] = f"{GROUP}_{_c['ck']}_g"
    _c["head_entity"] = f"{_c['prefix']}head_entity"
    _c["torso_entity"] = f"{_c['prefix']}torso_entity"
    _c["slot"] = _i
    _c.setdefault("skin_of", None)
    # 换装格左栏肖像用的槽位角色：可玩者另派一个专用 id（2026-09-07，用户：格子跟着本尊的模型/状态变），
    # 其余角色本来就是「开局即死亡」的专用角色，直接用 char_id。
    _c["slot_char_id"] = (9900500 + _i) if _c["playable"] else _c["char_id"]
    _c.setdefault("tex_alias", {})
    _c.setdefault("tex_src", {})
    _c.setdefault("phys_erp_skip", ())              # 物理：不参与 --erp-limit 软化的骨名子串（moe_bullet_bake 默认读它）
    _c.setdefault("phys_nocollide", ())             # 物理：双向免碰的 PMX 碰撞组对（moe_bullet_bake 默认读它）
    _c.setdefault("phys_wall_fit", ())              # 物理：按 rest 穿透量收缩半径的墙组（替代免碰，坐姿仍有腿撑裙）
    _c.setdefault("phys_ang_damp_cap", None)        # 物理：动态刚体角阻尼上限（PMX 原值 ≥1 被 Bullet 夹成 1 = 每子步清零角速度）
    _c.setdefault("phys_erp2", None)                # 物理：接触约束 ERP（None = CLI 默认 0.1；小 = 推出更软，打断静态挤压的极限循环）
    _c.setdefault("phys_warmup", None)              # 物理：循环动画预热圈数（None = CLI 默认 1；直接乘解算时间）

# 自检：ck 不能互为前缀（char_of 按最长前缀匹配文件名）、slug/prefix/char_id 唯一
_pfx = [c["prefix"] for c in CHARS]
for _a in _pfx:
    for _b in _pfx:
        if _a != _b and _a != "moe_" and _b.startswith(_a):
            raise SystemExit(f"moe_chars: 前缀 {_a} 是 {_b} 的前缀，char_of 会串")
for _f in ("ck", "slug", "prefix", "char_id", "slot_char_id"):
    _vals = [c[_f] for c in CHARS]
    if len(set(_vals)) != len(_vals):
        raise SystemExit(f"moe_chars: 字段 {_f} 有重复")


def playable():
    """可玩开局角色（书签 / history / 王朝）。"""
    return [c for c in CHARS if c["playable"]]


def of_game(game):
    return [c for c in CHARS if c["game"] == game]


def game_of(ck):
    return BY_CK[ck]["game"]


def prefix_of(ck):
    return BY_CK[ck]["prefix"]


def report_path(ck):
    c = BY_CK[ck]
    return ROOT / c["work"] / c["report"]


def bake_dir(ck):
    from moe_release_sources import role
    selected=role(ck)
    if selected is not None:return selected['canonical']
    return ROOT / BY_CK[ck]["bake"]


def work_dir(ck):
    return ROOT / BY_CK[ck]["work"]


def src_dir(ck):
    """源模型解压目录。glob 必须恰好命中一个 —— 命中多个多半是皮肤目录同名前缀。"""
    pat = BY_CK[ck]["src"]
    hits = sorted(p for p in ROOT.glob(pat) if p.is_dir())
    if len(hits) != 1:
        raise FileNotFoundError(f"{ck} 的源目录 glob {pat!r} 命中 {len(hits)} 个：{hits}")
    return hits[0]


def ready(ck):
    """该角色是否已有可部署的 mesh（表情烘焙产物目录里有 torso.mesh）。

    只有就绪的角色才进 TYPES / .asset / DNA / 池注册；没就绪的仍占着**登记表里的
    槽位号**（预览图与玩家变量按 slot 命名，不能随就绪集合的增减而漂移）。
    """
    d = bake_dir(ck); pfx = BY_CK[ck]["prefix"]
    has_mesh = (d / f"{pfx}torso.mesh").is_file() or (d / "moe_torso.mesh").is_file()
    # 动画也要齐：只有 mesh 没有 345 body / 357 head 时进 .asset 会登记一堆不存在的
    # animation（纳甘 2026-09-06 试点：表情烘完、动画未生成时就被当就绪）。
    n_body = sum(1 for _ in d.glob("*body_*.anim")); n_head = sum(1 for _ in d.glob("*head_*.anim"))
    return has_mesh and n_body >= 340 and n_head >= 350


def active():
    return [c for c in CHARS if ready(c["ck"])]


def portrait_types(c):
    """该角色 group 的全部 portrait type：[(type 名, sex, 'adult'|'child')]，本性别成人在首位。

    引擎按 sex + age 实时重挑 type（见 ck3-portrait-type-is-recomputed），所以「忽略性别」
    手动换装要真正渲染，每个 group 必须四个 sex×age 槽位都有 type，否则异性角色在本
    group 里挑不到 type。四个 type 共用同一套 head/torso 实体，只有 sex 取反。
    命名：本性别成人 = 登记表的 type（DNA 用它定位 group）；儿童 = `<type>_girl|_boy`；
    异性成人 = `<type>_as_<异性>`。
    """
    base, sex = c["type"], c.get("sex", "female")
    other = "male" if sex == "female" else "female"
    child = {"female": "girl", "male": "boy"}
    return [(base, sex, "adult"), (f"{base}_{child[sex]}", sex, "child"),
            (f"{base}_as_{other}", other, "adult"), (f"{base}_{child[other]}", other, "child")]


def by_slug(slug):
    return next((c for c in CHARS if c["slug"] == slug), None)


def by_type(t):
    return next((c for c in CHARS if c["type"] == t), None)
