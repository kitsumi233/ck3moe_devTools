"""产物归属的唯一真值：谁写文件，谁当场声明它属于哪个 mod。

## 为什么要有这个模块

原先是「先把所有东西合成进 `mod/moe_florence_poc/`，再用 481 行正则事后
猜归属」。这个结构直接造成了 2026-08-28 这一天的四次静默丢失：

  · 双面 shader / 立绘放大 / 描边  —— `build_moe_mod.py` 开头 `rmtree(MOD)`，
    后续步骤漏跑就没了，且三者都不报错，只是效果消失。
  · 肖像布光覆盖 —— 同上，而且它连流水线都没接进去，从未进过 mod。
  · 换装 `.gui` 被路由进 `big_portrait` —— 因为规则是「`gui/` 整个归放大立绘」。
    只装核心 + 模型包的用户点交互后什么都不弹。
  · 书签底图被当成角色资产 —— 文件名 stem 恰好以 `flo` 结尾。

后两个属于「猜归属」这一类。归属由写文件的人当场声明，这一类就不可能发生。

## 用法

    import moe_out as O
    O.write(O.CORE, "common/scripted_guis/40_moe_cosplay.txt", text)
    O.copy(O.models("gfl2"), "gfx/models/portraits/moe/flo/anims/x.anim", src)
    p = O.path(O.SHADER, "gfx/FX/jomini/portrait.shader")   # 要自己写时取路径

`reset(mod_id)` 只清那一个 mod 的目录 —— 这是与旧结构最关键的差别：
`rmtree` 的作用域从「整个 mod」缩到「本步骤负责的那个 mod」，
所以一个步骤漏跑最多丢它自己那份，不会连带清掉别人的产物。
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 产物根。**就是 `mod/`**（用户 2026-08-29）—— 每个子 mod 一个目录：
#     mod/moe_core/  mod/moe_models_gfl2/  mod/moe_models_endfield/  …
#
# 曾经有两层：`mod/moe_florence_poc/`（单体，合成用）+ `mod_split/`（拆分产物）。
# 那是「先合成再拆分」时代的遗留，同一批文件在磁盘上存两份（各 1.5 GB），
# 而且「哪一份是真的」要看你在流水线的哪一步。直写之后只需要一份。
OUT = ROOT / "mod"
# MOE_OUT_ROOT：把整套生成器的产物改写到隔离目录（2026-10-08 起 mod/ 只手改，
# 新包先在这里生成再挑出来拷进 mod/，见 [[no-more-generators-edit-mod-directly]]）。
import os as _os
if _os.environ.get("MOE_OUT_ROOT"):
    OUT = Path(_os.environ["MOE_OUT_ROOT"])

GROUP = "moe"

CORE = f"{GROUP}_core"
SHADER = f"{GROUP}_shader"
BIG = f"{GROUP}_big_portrait"
COLLECTION = f"{GROUP}_outfit_collection"
UNITS = f"{GROUP}_cultural_units"
TRAVEL = f"{GROUP}_travel_pawns"


def optional_mods() -> list:
    """Optional gameplay/map packages; never auto-enabled by the five-pack installer."""
    return [COLLECTION, UNITS, TRAVEL]

# 角色 → 来源游戏。加角色时**只改这里**（split_mods 与 build_moe_previews
# 都从这里读），别再各存一份。
# **派生自 moe_chars.CHARS**（2026-09-06）。
import moe_chars as _X
GAME_OF = {c["ck"]: c["game"] for c in _X.CHARS}
GAMES = _X.GAMES

# 槽位顺序 = 换装窗口里的按钮顺序。长度必须等于 SLOT_COUNT。
SLOT_ORDER = list(_X.ORDER)


def models(game: str) -> str:
    return f"{GROUP}_models_{game}"


def physics(game: str) -> str:
    """旧独立物理包的名称，仅供迁移与历史定位；不属于当前产物。"""
    return f"{GROUP}_physics_{game}"


def models_of(ck: str) -> str:
    """角色代号 → 它的模型 mod id。"""
    return models(GAME_OF[ck])


# ---------------------------------------------------------------- .asset 命名
# 每个模型包的 `.asset` **必须叫不同的名字**。CK3 的 VFS 按**相对路径**覆盖：
# 两个 mod 里同路径的文件，后加载的那份整份顶掉前一份；`portraits.gui` 仍按此
# 规则覆盖原版。物理动画现已在构建时选入模型包，不再依靠跨包覆盖。
#
# 2026-08-29 实测：两个模型包的 asset 都叫 `moe_portrait.asset` 时，endfield
# 加载在后，gfl2 那份**从未被读取** —— 日志里只有两条
# `Undefined entity 'moe_head_entity' found for moe_flo portrait part head`，
# 芙洛伦的 mesh / 700 条动画全在，却完全不渲染。文件确实各包一份（旧自检只
# 查了「有没有」），但同名 = 只有一份生效。
ASSET_DIR_REL = "gfx/models/portraits/moe"


def asset_name(game: str) -> str:
    return f"{GROUP}_portrait_{game}.asset"


def asset_rel(game: str) -> str:
    return f"{ASSET_DIR_REL}/{asset_name(game)}"


def asset_path(game: str) -> Path:
    return root(models(game)) / asset_rel(game)


def asset_of(ck: str) -> Path:
    """角色代号 → 它的 `.asset` 绝对路径。"""
    return asset_path(GAME_OF[ck])


def all_mods() -> list:
    """当前五个分发包；物理动画包含在对应模型包中。"""
    gs = sorted({GAME_OF[ck] for ck in GAME_OF})
    return [SHADER, BIG, CORE] + [models(g) for g in gs]


def root(mod_id: str) -> Path:
    return OUT / mod_id


def path(mod_id: str, rel) -> Path:
    """取产物绝对路径并建好父目录。`rel` 用 posix 相对路径。"""
    p = root(mod_id) / str(rel).replace("\\", "/")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def write(mod_id: str, rel, text: str) -> Path:
    """写文本产物。**P社文件一律 UTF-8 BOM** —— 没 BOM 的覆盖块会被
    静默忽略（布光覆盖踩过，见 [[ck3-portrait-lighting-is-config-not-shader]]）。
    """
    p = path(mod_id, rel)
    p.write_text(text, encoding="utf-8-sig")
    return p


def write_bytes(mod_id: str, rel, data: bytes) -> Path:
    p = path(mod_id, rel)
    p.write_bytes(data)
    return p


def copy(mod_id: str, rel, src) -> Path:
    p = path(mod_id, rel)
    shutil.copy2(src, p)
    return p


def reset(mod_id: str):
    """只清这一个 mod 的目录。

    与旧结构最关键的差别：`rmtree` 的作用域从「整个 mod」缩到
    「本步骤负责的那个 mod」。所以某个步骤漏跑，最多丢它自己那份，
    不会像以前那样连带清掉 shader / 放大 / 描边 / 布光的产物。
    """
    d = root(mod_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    return d


def find(rel):
    """跨 mod 找一个已产出的文件（渲图/校验工具吃合并视图时用）。

    产物现在按 mod 分散，`mod/<mod>/<rel>` 里 `<mod>` 事先未知。
    找不到返回 None —— 调用方自己决定是报错还是退路径。
    """
    rel = str(rel).replace("\\", "/")
    for m in all_mods():
        p = root(m) / rel
        if p.exists():
            return p
    return None


def find_all(pattern):
    """跨 mod 收集匹配的文件（如所有 `.anim`）。返回 [(mod_id, Path)]。"""
    hits = []
    for m in all_mods():
        d = root(m)
        if d.is_dir():
            hits += [(m, p) for p in sorted(d.rglob(pattern))]
    return hits
