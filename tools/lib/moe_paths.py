# -*- coding: utf-8 -*-
"""moe mod 的资源路径集中定义。**改目录结构只改这一个文件。**

## 为什么要有这个模块

2026-08-26 重构之前，21 个脚本各自硬编码同一条
`ROOT/"mod"/"moe_florence_poc"/"gfx"/"models"/"portraits"/"moe"`，
没有单点可以拦。改一次目录要动 21 处，漏一处就是静默失败。

## 目录结构（角色优先，用户 2026-08-26 选定）

    mod/moe_models_gfl2/gfx/models/portraits/moe/
    ├── moe_portrait_gfl2.asset      **每包一份，名字带游戏后缀**
    ├── models/  moe_head.mesh, moe_torso.mesh, flo_tex/
    └── flo/anims/   moe_*.anim

    mod/moe_models_endfield/gfx/models/portraits/moe/
    ├── moe_portrait_endfield.asset
    ├── models/  moe_{lau,per,eld}_{head,torso}.mesh, *_tex/
    └── {lau,per,eld}/anims/   moe_<角色>_*.anim

    work/base_animation_inputs/<角色>/anims/   未解算输入（不发行）
    work/physics_release/<角色>/anims/          已验证物理发布输入（不发行）

2026-09-18 起物理并入两个模型包；模型 anims/ 只保留最终播放的一套。
旧 moe_physics_<游戏> 独立包已退役，不能作为缺失资产的兜底。

**`.asset` 的文件名必须跨包不同**：VFS 按相对路径覆盖，同名时只有最后加载
的那份生效（见 `moe_out.asset_name`）。用 `MP.assets()` / `MP.asset_of(角色)`
定位，别拼固定文件名。

`.asset` 在根是硬约束：引擎按「与 .asset 同目录」为基准解析相对路径，
而这一份 asset 同时描述两个角色（1408 个 anim 引用 = 芙洛伦 704 + 洛茜 704）。

## 引擎的三条实测约束

1. **相对子目录可用**。原版先例：`blendshapes/female_bs_*.mesh`、
   `type = "../animations/xxx.anim"`（连 `../` 上跳都支持）。
   `.dds` 原版没有先例，2026-08-26 实测确认可用（挪进子目录后零报错）。

2. **引擎按裸文件名建全局贴图索引，同名文件不能存在于两个位置**。
   实测报错：
       Duplicate texture 'moe_invisible.dds' found
       (current path 'gfx/models/portraits/moe/_test_tex/moe_invisible.dds')
   所以迁移必须是**移动**而不是复制，旧位置不能留副本。

3. `.asset` 里的引用是**相对 .asset 所在目录**，不是相对 gfx/ 根。
   （`shader_file` 是例外，它写从 gfx/ 根算的完整路径。）
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 曾有一个 `MOD = ROOT/"mod"/"moe_florence_poc"` 常量。单体 mod 于
# 2026-08-29 退役（目录进 trash/20260829_monolithic_mod_retired/），常量一并
# 删掉 —— 留着它只会让人继续拼出指向空目录的路径，而**那种路径读不到文件却
# 不报错**（find() 返回 None、rglob 返回空），表现是产物静默残缺。
# 要资源路径就用 base_of()/models_dir()/anims_dir()/tex_dir()，它们按角色
# 解析到对应子 mod；要产物落点就用 moe_out.root()/path()。

# 相对 mod 根的资源目录（写进构建脚本时用这个字符串拼）
ASSET_REL = "gfx/models/portraits/moe"

# ---------------------------------------------------------------- 基址
# 产物现在**直接写进各子 mod**（`mod/<mod>/`，见 moe_out.py），
# 不再先合成到单体目录。但模型资产按来源游戏分散在多个 mod 里：
#   flo 的 mesh/anim  → mod/moe_models_gfl2/...
#   lau/per/eld 的    → mod/moe_models_endfield/...
#   物理版 .anim      → mod/moe_physics_<游戏>/...
# 所以「基址」不再是一个目录，而是**一组按序搜索的目录**。
#
# `BASE` 仍保留为单值，指向第一个存在的候选 —— 只用于拼相对引用字符串
# 和兼容老代码；**定位具体文件一律走 find()**，它会遍历全部候选。
def _base_candidates():
    """按搜索优先级返回资源根候选。

    模型包直接保存最终物理动画；基础输入和物理发布缓存都不参与搜索。
    2026-09-18 退役的两个独立物理包也不再作为候选。
    shader 包也要在列 —— 全透明工具贴图放在那里（见 util_tex_dir）。

    单体目录 `mod/moe_florence_poc/` 已退役（2026-08-29），不再是候选 ——
    留着它做兜底反而危险：新位置缺文件时会静默命中老目录里的旧版本，
    而旧版本可能是几轮前的产物（例如 167 骨的 mesh）。
    """
    import moe_out as _O
    out = []
    for m in (*(_O.models(game) for game in _O.GAMES), _O.SHADER):
        out.append(_O.root(m) / ASSET_REL)
    return out


BASE_DIRS = _base_candidates()
BASE = next((d for d in BASE_DIRS if d.is_dir()), BASE_DIRS[-1])

# 所有 .mesh 的落地目录。贴图子目录挂在它下面（见 tex_dir 的注释）。
MODELS = BASE / "models"


def rel_of(p):
    """把绝对路径化成**相对资源根**的 posix 串（写进 `.asset` 用的那种）。

    基址不再唯一（flo 在 gfl2 包、lau/per 在 endfield 包），所以不能用
    `p.relative_to(BASE)` —— 那样只有恰好落在 BASE 那个 mod 里的路径能过，
    其余抛 ValueError。这里对所有候选基址试一遍。
    """
    p = Path(p)
    for b in BASE_DIRS:
        try:
            return p.relative_to(b).as_posix()
        except ValueError:
            continue
    # 兜底：找 ASSET_REL 段之后的部分
    parts = p.as_posix().split(ASSET_REL + "/")
    if len(parts) > 1:
        return parts[-1]
    raise ValueError(f"{p} 不在任何资源根下（试过 {len(BASE_DIRS)} 个）")

# ---------------------------------------------------------------- .asset
# **每个模型包一份，文件名按游戏区分**（`moe_out.asset_name`）。曾经是
# 「共用一份 moe_portrait.asset」，拆包后同名会被 VFS 整份覆盖 —— 见
# `moe_out.asset_name` 的注释。


def asset_of(char):
    """角色代号 → 它的 `.asset` 路径。"""
    import moe_out as _O
    return _O.asset_of(char)


def assets():
    """全部模型包的 `.asset` 路径（存在的才返回）。"""
    import moe_out as _O
    return [p for p in (_O.asset_path(g) for g in _O.GAMES) if p.is_file()]


def asset_containing(text):
    """返回内容里含 `text` 的那份 `.asset`（找不到返回 None）。

    用于「已知 animation set / entity 名，要找它在哪份 asset 里」——
    包名事先未知，硬编码一个包名会在另一个包上静默失效。
    """
    for p in assets():
        if text in p.read_text(encoding="utf-8-sig", errors="replace"):
            return p
    return None

# ---------------------------------------------------------------- 角色定义
#
# `prefix` 是文件名前缀，也是区分两个角色产物的唯一依据（.anim 全靠它）。
# **注意 flo 的前缀是 "moe_" 而 lau 是 "moe_lau_"** —— 前者是后者的前缀，
# 所以判断归属必须**先测 lau**，否则 moe_lau_xxx.anim 会被当成芙洛伦的。
# 这个顺序错了不报错，只是 708 个文件进错目录。
# 角色表**派生自 moe_chars.CHARS**（2026-09-06）—— 加角色只改那一个文件。
# 形状保持 {ck: {"prefix", "name", "tex_patterns"}} 不变，调用方无需改动。
import moe_chars as _X
CHARACTERS = {c["ck"]: {"prefix": c["prefix"], "name": c["name_cn"],
                        "tex_patterns": tuple(c["tex"])} for c in _X.CHARS}

# 各角色共用的贴图。**两类性质不同，去处也不同**（见 base_of / util_tex_dir）：
#
#   moe_invisible*   —— 渲染工具贴图（192 字节全透明），给 no_render 层用。
#                       **两个模型包都引用它**，所以只能放在双方都依赖的
#                       mod 里 —— 即 shader 包。复制成两份会报
#                       `Duplicate texture`（引擎按基名建全局平表）。
#   按游戏共用       —— 真正的角色贴图内容，但同一系列多个角色引用同一张
#                       （endfield 的 T_actor_common_* 表情图集；gfl2 的 extra.png
#                       表情贴花图集，27 个模型 md5 全同）。落进**该游戏**模型包的
#                       models/shared_tex/。规则表在 moe_chars.SHARED_TEX_BY_GAME。
SHARED_TEX_BY_GAME = _X.SHARED_TEX_BY_GAME
SHARED_TEX_PATTERNS = (r"^moe_invisible",) + tuple(
    pat for pats in SHARED_TEX_BY_GAME.values() for pat in pats)
UTIL_TEX_PATTERNS = (r"^moe_invisible",)


def shared_game_of(filename):
    """这张共用贴图属于哪个游戏（按 SHARED_TEX_BY_GAME 的正则），不是共用图返回 None。"""
    import re as _re
    for g, pats in SHARED_TEX_BY_GAME.items():
        if any(_re.match(pt, filename, _re.I) for pt in pats):
            return g
    return None


def char_of(filename):
    """按文件名判断角色，返回 CHARACTERS 的 key，或 None。

    **必须先测最长前缀**：`moe_` 是 `moe_lau_` 的前缀，反过来测会把
    洛茜的 708 个文件全判成芙洛伦的。
    """
    for key in sorted(CHARACTERS, key=lambda k: -len(CHARACTERS[k]["prefix"])):
        if filename.startswith(CHARACTERS[key]["prefix"]):
            return key
    return None


def models_dir(char):
    """某角色的 .mesh 目录 —— **所有角色共用一个 `models/`**（2026-08-26 修）。

    参数 char 保留是为了调用方无需改动；返回值与它无关。mesh 靠 `<pfx>` 前缀
    区分（moe_head.mesh / moe_lau_head.mesh / moe_eld_head.mesh …）。

    为什么不分角色子目录：`.asset` 的 `file = "models/x.mesh"` 相对 **.asset
    目录**解析，而贴图相对 **.mesh 目录**解析。mesh 集中放，所有贴图就共享
    同一个基准（`models/<char>_tex/`）；mesh 分在 `<char>/models/` 时基准会
    变成 `<char>/models/<char>_tex/`，多一层且每个角色都不同。

    这里曾返回 `BASE/<char>/models` —— 与 tex_dir 同一个根因（目录整理时
    没跟着改写路径）。芙洛伦/洛茜没暴露是因为 find() 的遍历分支先命中了，
    属于「候选路径错了但被别的分支救回来」的假通过。
    """
    return base_of(char) / "models"


def base_of(char):
    """该角色的资源根 —— 即拥有它的那个模型子 mod 的 `gfx/models/portraits/moe`。

    产物直写各子 mod 后，「BASE」不再唯一：flo 在 gfl2 包里，
    lau/per/eld 在 endfield 包里。写路径必须按角色定，否则会把
    endfield 的 mesh 写进 gfl2 包（不报错，只是那个角色整体不渲染）。

    `char="shared"` 的去处按**谁真的引用它**定，不按「共用」这个名字定：
    实测只有 endfield 系的三件 head mesh 引用 `T_actor_common_*`（通用表情
    图集），gfl2 的 asset 与 mesh 一次都没引用。所以它归 endfield 包。

    放核心不行 —— 核心不该有任何模型资产（用户 2026-08-28 的要求）。
    复制进每个模型包也不行 —— 引擎按裸文件名建全局贴图索引，同名文件存在
    于两个位置会报 `Duplicate texture`
    （[[ck3-texture-refs-must-be-bare-basenames]]）。

    以后若 gfl2 的角色也要用这张图集，那就是**真的跨包依赖**，
    得给 gfl2 模型包加一条 `dependencies`，不能靠复制解决。
    """
    import moe_out as _O
    if char == "shared":                       # 旧调用方：endfield 的共用目录
        return _O.root(_O.models("endfield")) / ASSET_REL
    if char in _O.GAMES:                       # 直接传游戏名
        return _O.root(_O.models(char)) / ASSET_REL
    g = _O.GAME_OF.get(char)
    if g is None:
        return BASE
    return _O.root(_O.models(g)) / ASSET_REL


def shared_tex_dir(game):
    """某游戏系列共用贴图（表情图集等）的目录：models/shared_tex/，在该游戏的模型包里。"""
    return base_of(game) / "models" / "shared_tex"


def shared_tex_dirs():
    import moe_out as _O
    return [shared_tex_dir(g) for g in _O.GAMES]


def tex_dir(char):
    """某角色的 .dds 目录。char 传 "shared" 取共用贴图目录。

    **必须是 models/<char>_tex/ 而不是 <char>/tex/**（2026-08-26 修）：
    贴图路径在 .asset 里**相对 .mesh 所在目录**解析，而所有 .mesh 都在
    models/ 下。`.asset` 里写的就是 `flo_tex/x.dds`，所以物理位置只能是
    models/flo_tex/x.dds。

    这里曾返回 BASE/<char>/tex —— 一个不存在的路径。`find()` 靠遍历
    all_dirs() 找文件所以不受影响，只有 `dest()`（写路径）会用到它，
    而目录整理后一直没写过贴图，于是潜伏至今。
    """
    return base_of(char) / "models" / f"{char}_tex"


def anims_dir(char):
    """某角色最终播放的 .anim 目录，包含已物化的物理动画。"""
    return base_of(char) / char / "anims"



def base_anims_dir(char):
    """Unsimulated, character-prefixed work inputs; never the shipped animations.

    Populate from canonical bake output with sync_base_animation_inputs.py before
    baking physics or comparing base/current. A missing cache never falls back to
    the final model package, which now contains physical motion.
    """
    if char not in _X.BY_CK:
        raise ValueError(f"Unknown base-animation character: {char}")
    from moe_release_sources import role
    selected=role(char)
    if selected is not None:return selected['base']
    return ROOT / "work" / "base_animation_inputs" / char / "anims"


def base_anim_path(char, deployed_filename, missing_ok=False):
    """Resolve a base input using its exported character-prefixed filename."""
    name = str(deployed_filename)
    if Path(name).name != name or not name.endswith('.anim') or char_of(name) != char:
        raise ValueError(f"Invalid base-animation name for {char}: {name}")
    result = base_anims_dir(char) / name
    if not missing_ok and not result.is_file():
        raise FileNotFoundError(f"Missing unsimulated input {result}; run tools/build/sync_base_animation_inputs.py")
    return result


def require_base_anims(char, verify_hashes=False):
    """Validate canonical/cache provenance; full hashes are required before solving."""
    from moe_base_inputs import require_base_animation_inputs
    return require_base_animation_inputs(char, verify_hashes=verify_hashes)


def sync_base_animation_inputs(char, jobs=8):
    """Refresh unsimulated work inputs from canonical bake output, never mod/."""
    from moe_base_inputs import sync_base_animation_inputs as sync
    return sync(char, jobs=jobs)


def phys_anims_dir(char):
    """当前发布物理动画目录；保留旧 API 名，返回最终模型目录。

    2026-09-18 物理独立包并入模型包。物理解算必须读 base_anims_dir，
    已验证发布缓存则由 moe_physics_release.source_dir 单独管理。
    """
    return anims_dir(char)


def util_tex_dir():
    """渲染工具贴图（全透明贴图）的目录 —— 在 **shader 包**里。

    为什么不能跟着某个模型包走：两个模型包的 `no_render` 层都引用
    `moe_invisible*.dds`，放在其中一个包里，另一个单独装就缺贴图；
    两个包各放一份则报 `Duplicate texture`（引擎按基名建全局平表，
    见 [[ck3-texture-refs-must-be-bare-basenames]]）。
    shader 包是两个模型包**本来就依赖**的那个，所以放这里不引入新依赖。

    它也确实不是角色资产 —— 192 字节全透明，属于渲染工具。
    """
    import moe_out as _O
    return _O.root(_O.SHADER) / ASSET_REL / "models" / "shared_tex"


# 同 tex_dir 的理由：贴图相对 .mesh 目录解析，.asset 里写基名（平表查基名，
# 目录只影响物理位置）。表情图集归 endfield（见 base_of 的说明）。
SHARED_TEX = base_of("shared") / "models" / "shared_tex"


# ---------------------------------------------------------------- asset 内引用
#
# .asset 里写的是**相对 .asset 所在目录**的路径。asset 在 BASE 根，
# 所以引用形如 "flo/anims/moe_idle_calm.anim"。







def all_dirs():
    """迁移与构建时需要预先创建的全部目录。"""
    out = list(shared_tex_dirs())
    for c in CHARACTERS:
        out += [models_dir(c), tex_dir(c), anims_dir(c)]
    return out


# ---------------------------------------------------------------- 文件定位
#
# 21 个既有脚本都在写 `BASE / "moe_head.mesh"` 这类**拼裸文件名**的代码。
# 分层后这些路径全部失效，但逐处改成 `BASE/"flo"/"models"/...` 既啰嗦
# 又要求每处都判断角色与类型 —— 判断规则重复 21 遍，改一次结构又要全改。
#
# 所以给一个按文件名自动定位的解析函数：`find("moe_head.mesh")`。
# 既有代码只要把 `BASE / name` 换成 `MP.find(name)` 就行。

def find(filename, missing_ok=False):
    """按文件名在分层目录里定位实际路径。

    规则由扩展名与文件名前缀推导（与 migrate_moe_dirs.py 的分类一致）：
        *.anim / *.mesh  →  {char}/{anims|models}/
        *.dds / *.png    →  {char|shared}/tex/
        *.asset          →  就在 BASE 根

    找不到时抛错（除非 missing_ok），**不静默回退到 BASE 根** ——
    静默回退会让「文件真的缺失」和「分类规则漏了一种命名」看起来一样，
    而后者是这次重构最可能出的错。
    """
    import re as _re
    name = str(filename).replace("\\", "/")

    # **已经带目录的输入直接用** —— .asset 里的引用现在写的就是
    # "flo/anims/xxx.anim"，把它再喂给下面的分类逻辑会拼成
    # "flo/anims/flo/anims/xxx.anim"。校验脚本读 .asset 再来查文件，
    # 走的正是这条路，第一版忘了这点，1405 个引用全报「文件不存在」。
    if "/" in name:
        # 产物分散在多个子 mod 里，所以要遍历全部基址候选。
        # 只试 BASE 一个的话，lau/per 的资源（在 endfield 包里）会全部报缺。
        for b in BASE_DIRS:
            p = b / name
            if p.is_file():
                return p
        if missing_ok:
            return BASE / name
        raise FileNotFoundError(
            f"找不到 {name}（找过 {len(BASE_DIRS)} 个基址）")

    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""

    cands = []
    if ext == "asset":
        cands = [b / name for b in BASE_DIRS]
    elif ext in ("anim", "mesh"):
        # **必须调 models_dir/anims_dir，不要自己拼** —— 路径规则只能有一处
        # 定义。这里曾写死 `BASE / k / sub / name`，于是 models_dir 改成
        # 共用 models/ 之后本函数仍去 <char>/models/ 找，八件 mesh 全抛
        # FileNotFoundError（2026-08-26）。
        dirf = anims_dir if ext == "anim" else models_dir
        c = char_of(name)
        # 认不出角色时所有角色都试 —— 比直接失败好，命名可能有例外
        keys = [c] if c else list(CHARACTERS)
        # models_dir 对所有角色返回同一个目录，去重避免候选表出现重复项
        cands = []
        for k in keys:
            q = dirf(k) / name
            if q not in cands:
                cands.append(q)
    elif ext in ("dds", "png"):
        # 工具贴图（全透明）在 shader 包，先判 —— 它也匹配
        # SHARED_TEX_PATTERNS，顺序反了会去 endfield 包找。
        for pat in UTIL_TEX_PATTERNS:
            if _re.match(pat, name, _re.I):
                cands = [util_tex_dir() / name]
                break
        if not cands:
            _sg = shared_game_of(name)
            if _sg:
                cands = [shared_tex_dir(_sg) / name]
        if not cands:
            for k, spec in CHARACTERS.items():
                if any(_re.match(p, name, _re.I) for p in spec["tex_patterns"]):
                    cands = [tex_dir(k) / name]
                    break
        if not cands:
            cands = ([tex_dir(k) / name for k in CHARACTERS]
                     + [d / name for d in shared_tex_dirs()] + [util_tex_dir() / name])
    else:
        cands = [BASE / name]

    for p in cands:
        if p.is_file():
            return p
    if missing_ok:
        return cands[0]
    raise FileNotFoundError(
        f"在分层目录里找不到 {name}；试过 "
        # 候选可能在别的模型包里（不在 BASE 下），relative_to(BASE) 会抛 ValueError
        # 把真正的「找不到」盖住（2026-09-05 全量烘焙就这样崩过）。用绝对路径。
        f"{[str(c) for c in cands]}。"
        f"若是新增的命名规则，把它加进 moe_paths 的 CHARACTERS/"
        f"SHARED_TEX_PATTERNS 而不是绕过本函数")






def dest(filename):
    """**写入**时该放的路径（目标目录会被创建）。

    与 `find()` 的区别：find 要求文件已存在（找不到就抛错，避免静默回退
    掩盖分类规则的漏洞）；dest 用于产出新文件，按同一套规则算出目标位置
    并建好目录。

    分类认不出角色时**直接抛错**而不是塞进某个默认目录 —— 一个分不清
    归属的产物放错地方，后续 find 找不到它，报错点会离真正的原因很远。
    """
    import re as _re
    name = str(filename).replace("\\", "/")

    # 已带目录的直接用（同 find 的理由）
    if "/" in name:
        p = BASE / name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""

    if ext == "asset":
        d = BASE
    elif ext in ("anim", "mesh"):
        c = char_of(name)
        if not c:
            raise ValueError(
                f"无法判断 {name} 属于哪个角色（前缀不匹配 "
                f"{[s['prefix'] for s in CHARACTERS.values()]}）")
        d = anims_dir(c) if ext == "anim" else models_dir(c)
    elif ext in ("dds", "png"):
        d = None
        # 工具贴图先判 —— 它也匹配 SHARED_TEX_PATTERNS，顺序反了会被
        # 归进 endfield 包，而两个模型包都要引用它。
        for pat in UTIL_TEX_PATTERNS:
            if _re.match(pat, name, _re.I):
                d = util_tex_dir()
                break
        if d is None:
            _sg = shared_game_of(name)
            if _sg:
                d = shared_tex_dir(_sg)
        if d is None:
            for k, spec in CHARACTERS.items():
                if any(_re.match(p, name, _re.I) for p in spec["tex_patterns"]):
                    d = tex_dir(k)
                    break
        if d is None:
            raise ValueError(
                f"无法判断贴图 {name} 属于哪个角色。把它的命名规则加进 "
                f"moe_paths 的 CHARACTERS[...]['tex_patterns'] 或 "
                f"SHARED_TEX_PATTERNS")
    else:
        d = BASE

    d.mkdir(parents=True, exist_ok=True)
    return d / name
