"""收尾：给直写出来的各子 mod 补 descriptor、自检、装进隔离 userdir。

## 它取代了什么

原先是 `split_mods.py`（481 行）+ `sync_split_mods.py`。前者做三件事：
**按正则事后推断每个文件归哪个 mod**、剥离物理通道、写 descriptor。
前两件现在都不需要了：

  · **归属**由写文件的人当场声明（`moe_out.py`）。事后推断制造过两类真 bug：
    换装 `.gui` 被规则「`gui/` 整个归放大立绘」吞掉，只装核心 + 模型包的
    用户点交互后什么都不弹；书签底图因为文件名恰好以 `flo` 结尾被当成
    角色资产。
  · **物理版与基础版分开生成、统一发布**：物理缓存与基础 canonical 保留在工作
    目录，`moe_physics_release.materialize` 全量校验后选取完整动画进入模型包。
    不剥离或改写导出通道；未被物理覆盖的基础动作仍保留。

这里负责「物化最终动画 + 补 descriptor + 自检 + 全部必需模型包安装」。

    python tools/build/finalize_mods.py            # 写 descriptor + 自检 + 装机
    python tools/build/finalize_mods.py --no-sync  # 只到自检，不装机
    python tools/build/finalize_mods.py --no-physics  # 基础动画对照，恢复 canonical
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import re as _re
import shutil
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
# stdout 转 utf-8。**用 reconfigure 不用 TextIOWrapper** —— 后者每次都新建一个
# 包装对象，多个模块各包一层时外层被 GC 会关掉共享的 buffer，于是后面任何
# print 都抛 `ValueError: I/O operation on closed file`（动画其实已经写完了，
# 只是收尾的 print 炸掉，看起来像整条失败）。reconfigure 原地改编码，天然幂等。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import moe_out as O          # noqa: E402
import moe_chars as X
import moe_material_contract as MC
import moe_physics_release as PR

USERDIR = ROOT / "work" / "ck3_florence_userdir"

# 加载顺序：公共渲染和机制在前，两个模型包含最终物理动画。
ORDER = [O.SHADER, O.BIG, O.CORE,
         *[O.models(game) for game in O.GAMES]]
# Optional gameplay/map packages are emitted when their builders have produced
# files, but are never implicitly enabled with the required portrait packages. Keeping
# this separate prevents a missing optional directory from failing the base
# material checks or appearing in dlc_load.json by accident.
OPTIONAL_ORDER = [O.COLLECTION, O.TRAVEL, O.UNITS]
LEGACY_PHYSICS = ("moe_physics_gfl2", "moe_physics_endfield")

# ---------------------------------------------------------------- descriptor
#
# 依赖方向（这是第 4 项「第三方自由组合」的关键，方向反了就堵死）：
#   · 模型包依赖 shader —— asset 的 meshsettings 指向覆盖后的
#     gfx/FX/jomini/portrait.shader，并使用 moe_portrait_* effect，
#     缺件报 `Failed to create material`，那一整层不渲染。
#   · 模型包依赖核心 —— 光有 mesh 没人指派，进游戏什么都不会变。
#   · 物理动画随模型包发布，不再产生或启用独立物理补丁。
#   · **核心不依赖任何模型包。** 核心是被依赖方，第三方加模型子 mod 只需
#     依赖核心，不用改核心一个字。
#     早前写成「核心依赖模型包，因为 animations.txt 引用了各 type 名」，
#     那是错的：跨库引用在全部文件加载完之后才解析，与加载顺序无关。
INFO = {
    O.SHADER: {
        "name": "Moe Portraits - Shader",
        "cn": "赛璐璐 / 双面渲染 / 立绘描边 shader",
        "tags": ["Graphics"], "deps": [],
    },
    O.BIG: {
        "name": "Moe Portraits - Big Portrait",
        "cn": "立绘放大（相机 + gui 补丁），对原版角色同样生效",
        "tags": ["Graphics", "User Interface"], "deps": [],
    },
    O.CORE: {
        "name": "Moe Portraits - Core",
        "cn": "机制核心（文化 / 交互 / 换装 GUI / 模型池），本身不含模型",
        "tags": ["Portraits", "Culture", "Gameplay"], "deps": [],
    },
    O.COLLECTION: {
        "name": "Moe Outfits - Collection",
        "cn": "服装获取机制（申请、裁缝、朝圣）",
        "tags": ["Gameplay", "Events"],
        "deps": ["Moe Portraits - Core"],
    },
    O.TRAVEL: {
        "name": "Moe Travel Pawns",
        "cn": "萌化旅行棋子（独立地图 Mod）",
        "tags": ["Graphics", "Map"],
        "deps": ["Moe Portraits - Core"],
    },
    O.UNITS: {
        "name": "Moe Cultural Units",
        "cn": "cosplay认同/类人异族雇佣传统 + 鼠族兵种与旅行棋子（独立 Mod）",
        "tags": ["Military", "Graphics", "Culture"],
        # 0.5.0: 两个自定义传统与 cosplay 资格的文化门迁入本包（2026-09-27）。
        "version": "0.5.0",
        "deps": ["Moe Portraits - Core"],
    },
}
for _g, _spec in O.GAMES.items():
    INFO[O.models(_g)] = {
        "name": f"Moe Portraits - Models: {_spec['en']}",
        "cn": f"模型包：{_spec['cn']}（含物理动画，无机制）",
        "tags": ["Portraits"],
        "deps": ["Moe Portraits - Shader", "Moe Portraits - Core", *_spec.get("deps", [])],
    }


# 当前游戏 1.20.0.2（launcher-settings.json）；写旧版本启动器会标「过时」
SUPPORTED_VERSION = "1.20.*"


def descriptor(mid, version=None):
    info = INFO[mid]
    version = version or info.get("version", "0.3.0")
    dep = ""
    if info["deps"]:
        dep = ("dependencies={\n"
               + "".join(f'\t"{d}"\n' for d in info["deps"]) + "}\n")
    tags = "tags={\n" + "".join(f'\t"{t}"\n' for t in info["tags"]) + "}\n"
    # 工坊缩略图：启动器上传时读 descriptor 的 picture，文件要在包根目录（2026-10-01 备工坊发布）
    # 源图放 assets/workshop/<mid>.png（核心目录每次构建会被清空，不能只放产物里）
    src = ROOT / "assets" / "workshop" / f"{mid}.png"
    if src.is_file() and O.root(mid).is_dir():
        shutil.copyfile(src, O.root(mid) / "thumbnail.png")
    pic = 'picture="thumbnail.png"\n' if (O.root(mid) / "thumbnail.png").is_file() else ""
    return (f'version="{version}"\n{tags}name="{info["name"]}"\n{pic}'
            f'supported_version="{SUPPORTED_VERSION}"\n{dep}')


# ---------------------------------------------------------------- 自检
MODEL_ASSET_MARKS = ("gfx/models/", "common/dna_data/", "common/ethnicities/",
                     "gfx/interface/moe_preview/")


def material_checks(mod_root=None):
    """Validate current material semantics and all texture slots before deployment."""
    mod_root = Path(mod_root) if mod_root else O.OUT
    bad = []
    textures = {path.name for path in mod_root.glob("*/gfx/models/**/*.dds")}
    effects = []
    for relative in ("gfx/FX/jomini/portrait.shader", "gfx/FX/court_scene.shader"):
        path = mod_root / O.SHADER / relative
        if not path.is_file():
            bad.append(f"缺 shader: {path}")
            effects.append(set())
            continue
        text = path.read_text(encoding="utf-8-sig")
        blocks = dict(_re.findall(r"^Effect[ \t]+(\w+)\s*\n\{(.*?)^\}", text, _re.M | _re.S))
        effects.append(set(blocks))
        fill_targets = {
            'moe_portrait_face', 'moe_portrait_eye', 'moe_portrait_skin',
            'moe_portrait_attachment', 'moe_portrait_attachment_alpha_to_coverage',
            'moe_portrait_hair', 'moe_portrait_hair_alpha',
        }
        for name in fill_targets | set(blocks):
            if ('"MOE_FACE_FILL"' in blocks.get(name, "")) != (name in fill_targets):
                bad.append(f"{relative}: {name} 补光范围不符合统一合同")
        if 'float3 FillLighting = max( DiffuseLight + DiffuseIBL' not in text or 'MOE_FACE_FILL_COLOR' in text:
            bad.append(f"{relative}: 补光未跟随场景光色")
        for name in ("moe_portrait_face", "moe_portrait_skin"):
            if '"MOE_SKIN_RESPONSE"' not in blocks.get(name, ""):
                bad.append(f"{relative}: {name} 缺统一皮肤参数")
        if '"MOE_ALPHA_CUTOUT"' not in blocks.get("moe_portrait_attachment", ""):
            bad.append(f"{relative}: 普通衣物缺 alpha cutout")

    checked = 0
    for game in O.GAMES:
        asset = mod_root / O.asset_path(game).relative_to(O.OUT)
        if not asset.is_file():
            bad.append(f"缺材质 asset: {asset}")
            continue
        text = asset.read_text(encoding="utf-8-sig")
        meshes = {}
        for block in _re.findall(r"pdxmesh\s*=\s*\{(.*?)\n\}", text, _re.S):
            filename = _re.search(r'\bfile\s*=\s*"([^"]+)"', block)
            if not filename:
                continue
            settings = {}
            for setting in _re.findall(r"meshsettings\s*=\s*\{(.*?)\}", block, _re.S):
                index = _re.search(r"\bindex\s*=\s*(\d+)", setting)
                if index:
                    settings[int(index.group(1))] = dict(_re.findall(r'(\w+)\s*=\s*"([^"]+)"', setting))
            meshes[Path(filename.group(1)).name] = settings
        for char in (char for char in X.active() if char["game"] == game):
            report = json.loads(X.report_path(char["ck"]).read_text(encoding="utf-8"))
            for part, info in report["parts"].items():
                filename = char["prefix"] + part.removeprefix("moe_") + ".mesh"
                settings = meshes.get(filename, {})
                expected_indices = {sub["index"] for sub in info.get("submeshes", [])}
                if set(settings) != expected_indices:
                    bad.append(f"{char['ck']}/{part}: 材质槽不一致 {set(settings) ^ expected_indices}")
                for sub in info.get("submeshes", []):
                    actual = settings.get(sub["index"], {})
                    label = f"{char['ck']}/{part}/{sub['material']}[{sub['index']}]"
                    diffuse = sub.get("diffuse")
                    expected = "moe_" + MC.pdx_shader(sub["material"], [diffuse] if diffuse else [])
                    if actual.get("shader") != expected:
                        bad.append(f"{label}: shader {actual.get('shader')} != {expected}")
                    if any(expected not in scene for scene in effects):
                        bad.append(f"{label}: portrait/court 缺 effect {expected}")
                    if actual.get("shader_file") != "gfx/FX/jomini/portrait.shader":
                        bad.append(f"{label}: shader_file 错误")
                    no_render = MC.is_no_render(sub["material"])
                    if not diffuse and not no_render:
                        # No external diffuse is an intentional builder case:
                        # keep texture slots unset and use the mesh's embedded
                        # material instead of demanding generated texture maps.
                        if any(actual.get(f"texture_{slot}")
                               for slot in ("diffuse", "specular", "normal")):
                            bad.append(f"{label}: explicit texture refs without a source diffuse")
                        checked += 1
                        continue
                    for slot in ("diffuse", "specular", "normal"):
                        reference = actual.get(f"texture_{slot}")
                        if not reference or reference not in textures:
                            bad.append(f"{label}: 缺 texture_{slot} {reference}")
                    if diffuse and not no_render:
                        stem = Path(_re.sub(r"\.\d{3}$", "", diffuse)).stem
                        expected_diffuse = f"{stem}.dds"
                        opacity_variant = f"{stem}__moe_opacity.dds"
                        if (actual.get("shader") in MC.ALPHA_IS_OPACITY
                                and opacity_variant in textures):
                            expected_diffuse = opacity_variant
                        for slot, suffix in (("diffuse", ""), ("specular", "_properties"), ("normal", "_normal")):
                            expected_texture = (expected_diffuse if slot == "diffuse"
                                                else f"{stem}{suffix}.dds")
                            if actual.get(f"texture_{slot}") != expected_texture:
                                bad.append(f"{label}: texture_{slot} 未引用同一源图的 {stem}{suffix}.dds")
                    checked += 1
    print(f"  材质合同：检查 {checked} 个槽位（shader、三张贴图、portrait/court）")
    return bad


def checks():
    """返回 (通过?, [问题])。判据都来自实际踩过的坑，不是凭直觉。"""
    bad = []

    # 1. 核心不能有模型资产 —— 用户 2026-08-28 明确要求（预览图不准放核心）。
    #    书签**底图**是 UI 资产、归核心，不算违规。
    allow = {f"gfx/interface/bookmarks/bm_{O.GROUP}_flo.dds"}
    core = O.root(O.CORE)
    if core.is_dir():
        for p in core.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(core).as_posix()
            if rel in allow:
                continue
            if any(rel.startswith(m) for m in MODEL_ASSET_MARKS):
                bad.append(f"核心里有模型资产: {rel}")

    # 2. 每个模型包必须有自己的 .asset，**且相对路径跨包唯一**。
    #    缺了就是 mesh 全在、没人描述、**什么都不渲染且日志零报错** ——
    #    只装一个模型包时才暴露。
    #
    #    同名同路径比缺失更阴：文件在，自检「有没有」也过，但 VFS 按路径
    #    覆盖，后加载的包把前一个整份顶掉。2026-08-29 实测两包都叫
    #    `moe_portrait.asset` 时，gfl2 那份从未被读取，日志只有两条
    #    `Undefined entity 'moe_head_entity'`，芙洛伦完全不渲染。
    seen_asset = {}
    for g in O.GAMES:
        m = O.models(g)
        d = O.root(m)
        if not d.is_dir():
            continue
        a = O.asset_path(g)
        if not a.is_file():
            bad.append(f"{m} 没有自己的 {O.asset_name(g)}")
            continue
        seen_asset.setdefault(a.relative_to(d).as_posix(), []).append(m)
    for rel, ms in sorted(seen_asset.items()):
        if len(ms) > 1:
            bad.append(f".asset 路径撞车: {rel} 同时在 {sorted(ms)}"
                       f" —— VFS 按路径覆盖，只有最后加载的那份生效")

    # 4. 贴图基名必须全局唯一。引擎对 `gfx/models` 建**按基名索引的平表**
    #    （pdxassetutil.cpp: ThreadedInitTextureLookup），同名文件存在于两个
    #    位置会互相顶掉并报 `Duplicate texture`。跨 mod 同名同样算冲突 ——
    #    我把全透明工具贴图同时留在两个模型包里就撞过。
    seen = {}
    for m in O.all_mods():
        d = O.root(m)
        if not d.is_dir():
            continue
        for p in d.rglob("*.dds"):
            if "gfx/models" not in p.as_posix():
                continue
            seen.setdefault(p.name, []).append(m)
    for nm, ms in sorted(seen.items()):
        if len(set(ms)) > 1:
            bad.append(f"贴图基名撞车: {nm} 同时在 {sorted(set(ms))}")

    # 5. descriptor 引用的依赖名必须都存在，否则游戏里报缺依赖。
    names = {INFO[m]["name"] for m in INFO}
    for m, info in INFO.items():
        for d in info["deps"]:
            if d not in names:
                bad.append(f"{m} 依赖了不存在的 mod 名: {d}")

    # 6. 每个注册了 entity 的角色，state 表必须是完整的（单角色约 1300 条）。
    #    `build_moe_mod.py` 只写 idle 那 2 条占位，完整表由
    #    `register_all_anims.py` 追加。**单跑 build_moe_mod 会把完整表冲掉**
    #    ——我 2026-08-29 就这么把只剩 2 条 state 的产物装进了游戏，五项自检
    #    全过，而实机会刷几千条 `Unknown animation state` 且所有动作退回
    #    idle。装机步骤必须自己拦住这个，不能只靠 build_all 的编排自检。
    for g in O.GAMES:
        a = O.asset_path(g)
        if not a.is_file():
            continue
        txt = a.read_text(encoding="utf-8-sig")
        ents = set(_re.findall(r'entity\s*=\s*\{\s*\n\s*name\s*=\s*"([^"]+)"',
                               txt))
        nchar = len({e.replace("_head_entity", "").replace("_torso_entity", "")
                     for e in ents})
        n = txt.count("state = {")
        if nchar and n < 500 * nchar:
            bad.append(f"{O.models(g)} 的 asset 只有 {n} 个 state"
                       f"（{nchar} 个角色，应 >{500 * nchar}）"
                       f"—— register_all_anims 没跑，实机会刷 Unknown state")

    bad.extend(material_checks())
    return (not bad), bad


# ---------------------------------------------------------------- 装机
_REFERENCE_SKIP = {'.anim', '.dds', '.png', '.jpg', '.jpeg', '.tga'}


def runtime_animation_selection(mod_root=None):
    """Keep every referenced animation, leaving unused build inputs in work/project.

    CK3 resolves state names through the registered animation sets. Scan every
    shipped non-image resource, including binary meshes, conservatively keeping
    any animation basename mentioned anywhere. Also check exact relative paths
    in asset files, including registered animations unused by current states.
    Source templates and folded variants are not deleted or rewritten.
    """
    root = (Path(mod_root) if mod_root else O.OUT).resolve()
    paths = sorted(p for p in root.rglob('*') if p.is_file())
    animations = [p for p in paths if p.suffix.lower() == '.anim']
    by_name = {}
    for path in animations:
        by_name.setdefault(path.name.lower(), []).append(path)
    mentioned, sources = set(), []
    token = _re.compile(rb'[A-Za-z_0-9./\\-]+\.anim', _re.I)
    def _read(path):
        data = path.read_bytes()
        return path, data, hashlib.sha256(data).hexdigest()
    _scan = [p for p in paths if p.suffix.lower() not in _REFERENCE_SKIP]
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 8) as pool:
        _loaded = list(pool.map(_read, _scan))
    for path, data, _sha in _loaded:
        sources.append(dict(path=path.relative_to(root).as_posix(), size=len(data),
                            sha256=_sha))
        if b'.anim' not in data.lower():
            continue
        reference_data = data
        if b'\0' not in data:
            # Developer notes can mention nonexistent example animations.
            # Preserve # inside quoted strings while excluding actual comments.
            try:
                text = data.decode('utf-8-sig')
            except UnicodeDecodeError:
                pass
            else:
                text = _re.sub(r'"(?:\\.|[^"\\])*"|#[^\r\n]*',
                               lambda match: '' if match.group().startswith('#') else match.group(), text)
                reference_data = text.encode('utf-8')
        refs = [raw.decode('ascii').replace('\\', '/') for raw in token.findall(reference_data)]
        names = {Path(value).name.lower() for value in refs}
        mentioned.update(names)
        if path.suffix.lower() == '.asset':
            for value in refs:
                name = Path(value).name.lower()
                if name not in by_name and not name.startswith('moe_'):
                    continue  # Vanilla resources may live in the base game.
                target = (path.parent / value).resolve()
                if not target.is_relative_to(root) or not target.is_file():
                    raise ValueError(f'Registered animation is missing: {path}: {value}')
                if target.suffix.lower() != '.anim':
                    raise ValueError(f'Unexpected animation reference: {path}: {value}')
    missing = {name for name in mentioned if name.startswith('moe_')} - by_name.keys()
    if missing:
        raise ValueError(f'Referenced Moe animation files are missing: {sorted(missing)[:5]}')
    selected = {p.relative_to(root).as_posix() for p in animations if p.name.lower() in mentioned}
    omitted = [dict(path=p.relative_to(root).as_posix(), bytes=p.stat().st_size)
               for p in animations if p.name.lower() not in mentioned]
    if animations and not selected:
        raise ValueError('No runtime animation references found; refuse an empty animation deployment')
    selected_file_state = {}
    for relative in sorted(selected):
        stat = (root / relative).stat()
        selected_file_state[relative] = dict(size=stat.st_size, mtime_ns=stat.st_mtime_ns)
    return dict(schema=1, status='verified_conservative_runtime_animation_selection',
                source_root=str(root), reference_sources=sources,
                selected=sorted(selected), selected_file_state=selected_file_state, omitted=omitted,
                omitted_bytes=sum(row['bytes'] for row in omitted),
                source_inputs_deleted=False)


def verify_selection_sources(selection):
    root = Path(selection['source_root'])
    current = {p.relative_to(root).as_posix() for p in root.rglob('*')
               if p.is_file() and p.suffix.lower() not in _REFERENCE_SKIP}
    expected = {row['path'] for row in selection['reference_sources']}
    if current != expected:
        raise ValueError('Runtime reference resources were added or removed after selection')
    def _digest(row):
        data = (root / row['path']).read_bytes()
        return row, len(data), hashlib.sha256(data).hexdigest()
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 8) as pool:
        for row, size, digest in pool.map(_digest, selection['reference_sources']):
            if size != row['size'] or digest != row['sha256']:
                raise ValueError(f'Runtime references changed before deployment: {row["path"]}')
    for relative, expected in selection['selected_file_state'].items():
        path = root / relative
        if not path.is_file():
            raise ValueError(f'Required runtime animation disappeared: {relative}')
        stat = path.stat()
        if stat.st_size != expected['size'] or stat.st_mtime_ns != expected['mtime_ns']:
            raise ValueError(f'Required runtime animation changed after selection: {relative}')


def sync_one(mid, jobs=12, *, animation_selection=None):
    """登记一个 mod 到隔离 userdir：只写一个指向仓库 mod/<mid> 的 .mod，不拷数据。

    2026-10-01 用户定：只维护仓库 mod/ 一份。游戏直接读仓库，不再有「忘了同步」
    的副本；发布时才用 copy_pack 拷一份（那份按 selection 省掉未引用的动画）。
    上一轮留下的 userdir 副本目录在这里清掉，否则同名 mod 两处都在。
    """
    src = O.root(mid)
    if not src.is_dir():
        return 0
    link = USERDIR / 'mod' / mid
    # CK3 拒绝 userdir 之外的绝对路径（2026-10-01 实测：dlc.cpp:479 Folder not found，
    # 7 个 mod 全没挂载），所以用 junction 把 userdir/mod/<mid> 指到仓库。
    # **绝不 rmtree 这个路径**：它是 junction 时 rmtree 会删穿到仓库。
    if link.is_junction() or link.is_symlink():
        if Path(os.path.realpath(link)) != src.resolve():
            raise ValueError(f'{link} 指向别处: {os.path.realpath(link)}')
    elif link.exists():
        # 旧式副本目录（2026-10-01 之前的装机方式）
        if link.resolve().parent != (USERDIR / 'mod').resolve():
            raise ValueError(f'Unsafe deployment destination: {link}')
        shutil.rmtree(link)
    if not link.exists():
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(src.resolve())],
                       check=True, capture_output=True)
    outer = descriptor(mid) + f'path="mod/{mid}"\n'
    (USERDIR / 'mod' / f'{mid}.mod').write_text(outer, encoding='utf-8-sig')
    return sum(1 for p in src.rglob('*') if p.is_file())


def copy_pack(mid, dest_root, jobs=12, *, animation_selection=None):
    """拷一个 mod 到 dest_root/<mid>（发布用）。整目录替换，不做增量。

    增量同步会留下上一轮的残留文件（改名过的 .anim、删掉的贴图），
    而残留文件会被引擎当成本轮产物用 —— 这类问题在游戏里表现为
    「明明改了却没生效」，极难查。
    """
    dest_root = Path(dest_root)
    src = O.root(mid)
    selected = None
    if animation_selection is not None:
        if Path(animation_selection['source_root']).resolve() != O.OUT.resolve():
            raise ValueError('Animation selection belongs to another project output')
        # 7 个 mod 共用同一份 selection，只核一次（每次都要重算全部 .mesh 的 sha256，2026-10-01）
        if not animation_selection.get('_verified'):
            verify_selection_sources(animation_selection)
            animation_selection['_verified'] = True
        selected = set(animation_selection['selected'])
    if not src.is_dir():
        return 0
    dst = dest_root / mid
    destination_root = dest_root.resolve()
    if dst.resolve().parent != destination_root:
        raise ValueError(f'Unsafe deployment destination: {dst}')
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    entries = sorted(src.rglob('*'))
    files = []
    for entry in entries:
        target = dst / entry.relative_to(src)
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            if (selected is not None and entry.suffix.lower() == '.anim'
                    and entry.relative_to(O.OUT).as_posix() not in selected):
                continue
            files.append((entry, target))

    def copy_file(pair):
        source, target = pair
        shutil.copy2(source, target)

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        list(pool.map(copy_file, files))
    # 外层 .mod 取仓库那份、把 path 换成目标的绝对路径（启动器认绝对路径）
    outer = O.OUT / f"{mid}.mod"
    if outer.is_file():
        lines = [l for l in outer.read_text(encoding='utf-8-sig').splitlines()
                 if not l.startswith('path=')]
        lines.append(f'path="{dst.resolve().as_posix()}"')
        (dest_root / f"{mid}.mod").write_text('\n'.join(lines) + '\n', encoding="utf-8-sig")
    return len(files)


def retire_installed_physics():
    """Archive only the two retired installed packages and their outer descriptors.

    A successful repeat is a no-op. If an old package was recreated while its
    archive exists, refuse to overwrite the archive. Preflight every move before
    renaming anything; a partial run can resume because completed sources vanish.
    Project packages are retired by the source-cache migration, not by finalize.
    """
    workspace = ROOT.resolve()
    expected_source = workspace / "work/ck3_florence_userdir/mod"
    source_root = (USERDIR / "mod").resolve()
    destination_root = workspace / "work/evidence/integrated_mods_20260918/retired/installed"
    if source_root != expected_source or destination_root.resolve() != destination_root:
        raise ValueError("Retirement paths must stay in the configured workspace")
    plan = []
    for mid in LEGACY_PHYSICS:
        for name, directory in ((mid, True), (mid + ".mod", False)):
            source, target = source_root / name, destination_root / name
            if source.resolve().parent != source_root or target.resolve().parent != destination_root:
                raise ValueError(f"Unsafe retirement path: {source} -> {target}")
            if source.is_symlink() or source.is_junction() or target.is_symlink() or target.is_junction():
                raise ValueError(f"Retirement does not follow links: {source} -> {target}")
            if not source.exists():
                continue
            if (directory and not source.is_dir()) or (not directory and not source.is_file()):
                raise ValueError(f"Unexpected legacy package type: {source}")
            if target.exists():
                raise FileExistsError(f"Refusing to replace archived physics package: {target}")
            plan.append((source, target))
    if not plan:
        return []
    destination_root.mkdir(parents=True, exist_ok=True)
    moved = []
    for source, target in plan:
        # Recheck immediately before each exact, same-volume rename.
        if source.resolve().parent != source_root or target.resolve().parent != destination_root:
            raise ValueError(f"Retirement path changed: {source} -> {target}")
        if source.is_symlink() or source.is_junction() or target.is_symlink() or target.is_junction():
            raise ValueError(f"Retirement link appeared: {source} -> {target}")
        if target.exists():
            raise FileExistsError(f"Retirement destination appeared: {target}")
        source.rename(target)
        moved.append(dict(source=str(source), archived=str(target)))
        print(f"  已归档旧物理包: {source.name} -> {target}")
    return moved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-sync", action="store_true", help="只到自检，不装机")
    ap.add_argument('--jobs', type=int, default=12, help='Parallel file copies (default 12)')
    ap.add_argument(
        "--no-physics",
        action="store_true",
        help="从当前基础烘焙输入恢复无物理对照；不是禁用独立包",
    )
    a = ap.parse_args()
    if a.jobs < 1:
        ap.error('--jobs must be positive')

    mode = "基础动画对照" if a.no_physics else "包含物理动画"
    print(f"物化模型包最终动画（{mode}）：")
    PR.materialize(no_physics=a.no_physics, jobs=a.jobs)

    print("写 descriptor：")
    live = []
    optional_live = []
    for mid in ORDER + OPTIONAL_ORDER:
        d = O.root(mid)
        if not d.is_dir() or not any(d.rglob("*")):
            print(f"  跳过 {mid}（无产物）")
            continue
        if mid in OPTIONAL_ORDER:
            optional_live.append(mid)
        else:
            live.append(mid)
        (d / "descriptor.mod").write_text(descriptor(mid), encoding="utf-8-sig")
        outer = descriptor(mid) + f'path="mod/{mid}"\n'
        (O.OUT / f"{mid}.mod").write_text(outer, encoding="utf-8-sig")
        suffix = "（可选，默认不启用）" if mid in OPTIONAL_ORDER else ""
        print(f"  {mid:26} {INFO[mid]['cn']}{suffix}")

    print("\n自检：")
    ok, bad = checks()
    for b in bad:
        print(f"  !! {b}")
    print("  全部通过 ✓" if ok else f"  **{len(bad)} 项不通过**")

    print(f"\n{'mod':<26} {'文件数':>7} {'体积':>10}")
    for mid in live + optional_live:
        d = O.root(mid)
        fs = [p for p in d.rglob("*") if p.is_file()]
        sz = sum(p.stat().st_size for p in fs)
        print(f"{mid:<26} {len(fs):>7} {sz / 1048576:>9.1f} MB")

    if not ok:
        sys.exit("自检不通过，拒绝装机")
    selection = runtime_animation_selection()
    print(f"\n安装仅省去未被运行资源引用的动画：{len(selection['omitted'])} 个，"
          f"{selection['omitted_bytes'] / 1_000_000:.2f} MB；构建来源保留")
    selection_path = ROOT / 'work' / 'runtime_animation_selection.json'
    selection_path.write_text(json.dumps(selection, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if a.no_sync:
        print(f"\n产物：{O.OUT}（--no-sync，未装机）")
        return

    print(f"\n登记进 {USERDIR}（.mod 直接指向 {O.OUT}，不拷数据；可选包不自动启用）")
    (USERDIR / "mod").mkdir(parents=True, exist_ok=True)
    tot = 0
    for mid in live:
        n = sync_one(mid, a.jobs, animation_selection=selection)
        tot += n
        print(f"  {mid:26} {n:5} 个文件")

    (USERDIR / "dlc_load.json").write_text(json.dumps({
        "disabled_dlcs": [],
        "enabled_mods": [f"mod/{m}.mod" for m in live],
    }, indent=2), encoding="utf-8")
    retire_installed_physics()
    if optional_live:
        print(f"\n可选包已生成 descriptor 但未启用：{', '.join(optional_live)}")
    print(f"\n共 {tot} 个文件，{len(live)} 个基础 mod 已启用")

    # 旧单体 mod 与拆分后的 mod 提供同名文件，两者都启用时覆盖关系不可控。
    legacy = USERDIR / "mod" / "moe_florence_poc"
    if legacy.is_dir():
        print(f"\n!! 旧单体 mod 还在 userdir 里：{legacy}")
        print("   已不在 dlc_load.json 里（不启用），目录本身保留")


if __name__ == "__main__":
    main()
