"""芙洛伦独立肖像实体 PoC：拆 head/torso、提取 deform 骨架、造 idle 动画、导出 PDX。

不复用 v5/v7-v10 任何失败产物，从原始 PMX 重新导入。

阶段:
  1. 导入 PMX（rename_bones=False 保原始骨名）
  2. 按材质把单一网格拆成 head / torso 两件，剔除武器与 Hide/Unused
  3. 从 247 骨提取 deform 骨架：剔 dummy/IK/tip/physics，权重合并到最近保留祖先
  4. 造一个短 idle 动画（呼吸 + 轻微重心移动）
  5. 导出 .mesh / .anim
  6. 输出 JSON 报告

用法:
  blender.exe -b --python tools/model/build_florence_poc.py

隔离源候选（这三个覆盖参数必须一起提供，调用者指定独立输出目录）:
  blender.exe -b --python tools/model/build_florence_poc.py -- --model <slug>
    --pmx <candidate.pmx> --out-dir <candidate directory> --out-blend <candidate.blend>
"""
import bpy
import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
from pathlib import Path
from mathutils import Vector, Quaternion

# 让 Blender 能 import 同目录的合同模块
sys.path[:0] = [str(Path(__file__).resolve().parents[2] / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
from moe_bone_contract import build_name_map, contract_report, SEMANTIC_ORDER
import moe_material_contract as MC
import moe_chars as X

ROOT = Path(__file__).resolve().parents[2]
# ---------------------------------------------------------------- 模型选择
# 默认仍是芙洛伦（既有产物与全部验证都基于她）。要导别的角色:
#   blender -b --python tools/model/build_florence_poc.py -- --model gfl2_asteria
#
# 模型清单从 work/pmx_skeleton_inventory/<model>.json 读 source_file，
# 那是盘点阶段实际导入成功的路径，不用再手写。
def resolve_build_paths(argv, root):
    """Resolve inputs without loading Blender state or creating output paths."""
    parser = argparse.ArgumentParser(description="PMX source to head/torso mesh")
    parser.add_argument("--model", default="gfl2_florence")
    parser.add_argument("--pmx", type=Path, help="Explicit isolated source PMX")
    parser.add_argument("--out-dir", type=Path, help="Isolated mesh/report directory")
    parser.add_argument("--out-blend", type=Path, help="Isolated source Blender file")
    args = parser.parse_args(argv)
    root = Path(root).resolve()
    if Path(args.model).name != args.model or args.model in (".", ".."):
        parser.error("--model 必须是登记模型名，不能含目录")
    inventory = root / "work" / "pmx_skeleton_inventory" / f"{args.model}.json"
    if not inventory.is_file():
        parser.error(f"找不到盘点报告 {inventory}（先跑 tools/run_pmx_inventory_batch.sh）")
    record = json.loads(inventory.read_text(encoding="utf-8"))
    overrides = (args.pmx, args.out_dir, args.out_blend)
    if any(value is not None for value in overrides) and not all(value is not None for value in overrides):
        parser.error("隔离候选必须同时提供 --pmx、--out-dir、--out-blend，防止覆盖正常产物")
    if args.pmx is not None:
        pmx, work, blend = (path.resolve() for path in overrides)
        if not pmx.is_file() or pmx.suffix.lower() != ".pmx":
            parser.error(f"候选 PMX 不存在或扩展名错误: {pmx}")
        if blend.suffix.lower() != ".blend":
            parser.error("--out-blend 必须使用 .blend 扩展名")
        if work.is_file() or blend.is_dir():
            parser.error("--out-dir 必须是目录，--out-blend 必须是文件路径")
    else:
        pmx = Path(record["source_file"])
        sub = "florence_poc" if args.model == "gfl2_florence" else args.model
        work = root / "work" / sub
        blend = root / "work" / "blenderProj" / f"{sub}.blend"
    report_name = "florence_poc_report.json" if args.model == "gfl2_florence" else f"{args.model}_report.json"
    return args.model, pmx, work, blend, work / report_name


_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
MODEL, PMX, WORK, OUT_BLEND, REPORT = resolve_build_paths(_argv, ROOT)

MMD_ADDON = "bl_ext.user_default.mmd_tools"
PDX_ADDON = "bl_ext.user_default.io_pdx_mesh"


def namespace_diffuse_image(image):
    """Give newly imported roles unique mesh texture basenames.

    CK3 indexes portrait textures globally by basename, across every enabled mod.
    New batches contain alternate outfits and characters whose source archives reuse
    names such as ``B_body_d.png``. Keep the series-wide atlases shared, and copy
    each other selected diffuse to a stable, character-prefixed filename.
    """
    config = X.by_slug(MODEL)
    if config is None:
        return image
    if image.name in TEXTURE_ALIASES:
        return image
    namespace = bool(config.get("texture_namespace"))
    explicit = {str(k).casefold(): str(v)
                for k, v in (config.get("tex_alias") or {}).items()}
    relocate = {str(k).casefold(): str(v)
                for k, v in (config.get("tex_src") or {}).items()}
    if not namespace and not explicit and not relocate:
        return image

    raw_path = Path(bpy.path.abspath(image.filepath)) if image.filepath else Path()
    raw_name = raw_path.name or Path(image.name).name
    source_name = relocate.get(raw_name.casefold(), raw_name)
    source_root = X.src_dir(config["ck"])
    if source_name.casefold() == raw_name.casefold() and raw_path.is_file():
        try:
            raw_path.relative_to(source_root)
            source_path = raw_path
        except ValueError:
            source_path = None
    else:
        source_path = None
    if source_path is None:
        matches = sorted(p for p in source_root.rglob("*")
                         if p.is_file() and p.name.casefold() == source_name.casefold())
        source_path = matches[0] if matches else None
    if source_path is None or not source_path.is_file():
        log(f"  !! 贴图别名未找到源文件: {raw_name} → {source_name}")
        return image

    explicit_target = explicit.get(source_path.name.casefold())
    is_shared = any(re.match(pattern, source_path.name, re.I)
                    for patterns in X.SHARED_TEX_BY_GAME.values()
                    for pattern in patterns)
    if explicit_target:
        target_name = explicit_target
    elif namespace and not is_shared:
        safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", source_path.stem).strip("._") or "texture"
        digest = hashlib.sha256(source_path.read_bytes()).hexdigest()[:10]
        target_name = f"moe_{config['ck']}_{safe_stem[:96]}_{digest}{source_path.suffix.lower()}"
    else:
        # Shared atlases retain the canonical basename and are routed to the base series pack.
        if source_path != raw_path and source_path.is_file():
            image.filepath = str(source_path)
            image.reload()
        return image

    alias_dir = WORK / "tex_alias"
    alias_dir.mkdir(parents=True, exist_ok=True)
    target = alias_dir / target_name
    if target.is_file():
        if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(source_path.read_bytes()).digest():
            raise RuntimeError(f"贴图别名冲突: {target_name} 指向了不同内容")
    else:
        shutil.copy2(source_path, target)
    image.filepath = str(target)
    image.name = target_name
    image.reload()
    TEXTURE_ALIASES[target_name] = source_path.name
    log(f"  贴图别名 {source_path.name} → {target_name}")
    return image


TEXTURE_ALIASES = {}

# ---------------------------------------------------------------- 材质分组
# head: 脸、口腔、眼、眉、睫毛、头发。CK3 的 head 实体承载表情，发也挂 head。
# 材质分组**不再写死在这里** —— 见 tools/lib/moe_material_contract.py。
# 分组结果在 STEP 1 末尾由合同算出（HEAD_MATS / TORSO_MATS / CLOTHES_MATS /
# DROP_MATS / NO_RENDER_MATS 五个名字在那里赋值），下游代码不变。
#
# 为什么搬走: 材质名跨系列完全不通用，写死等于每个角色改一次代码。
# 合同用「关键词规则 + 贴图名兜底 + 每模型 OVERRIDES」，12 个角色零未识别。

# ---------------------------------------------------------------- deform 骨过滤
# 与 inventory_pmx_skeletons.py 的 classify_bone 保持一致
def classify_bone(name):
    n = name.upper()
    if n.startswith(("MCH-", "ORG-", "DEF-")):
        return "rig_internal"
    if "IK" in n:
        return "ik"
    if any(k in name for k in ("ダミー", "dummy", "Dummy")):
        return "dummy"
    if any(k in name for k in ("+", "先", "tip")):
        return "tip_or_helper"
    if any(k in name for k in ("捩", "twist")):
        return "twist"
    if any(k in name for k in ("スカート", "skirt", "髪", "hair", "リボン", "ribbon",
                               "房", "揺", "物理", "cloth", "Cloth")):
        return "physics"
    return "deform_candidate"


# 保留 twist：芙洛伦的上肢链 parent 恰好是捩骨（腕捩/手捩/肩P/肩C），
# 剔除会打断父级链。physics 保留头发骨（head 需要），其余物理骨剔除。
KEEP_CLASSES = {"deform_candidate", "twist"}
# 额外保留：名字含这些的物理骨（头发要跟着头动；洛茜裙子走 Bullet 也要留骨，
# 2026-08-21 发现 KEEP_PHYSICS_KEYWORDS 缺 skirt 导致裙子骨全被剔除、
# 权重并到了胯部/脚踝，裙子静止不动 —— 见 [[moe-mod-build-order-matters]] 的教训：
# 骨没保留，物理烘焙无从谈起）
#
# cloak/cape 同理但症状更隐蔽：洛茜的斗篷骨分 b/c/d/e/f/g 六组，只有 b/c 是
# deform_candidate 被保留，d~g 落在 physics 类被剔除。它们的权重按
# 「最近保留祖先」合并到了各自链末端的 _b_08/_c_08 —— 而 d~g 的顶点原本
# **散布在斗篷不同位置**，全绑到一根末端骨上后，那根骨一动就把散点一起拽向
# 它，在脸侧拉出放射状针刺（2026-08-21 实机截图可见，本地渲图看不出来，
# 因为单骨架渲染时末端骨几乎不动）。所以 d~g 必须一并保留。
KEEP_PHYSICS_KEYWORDS = ("髪", "hair", "スカート", "skirt", "cloak", "cape",
                         "マント")

log_lines = []


def log(m):
    print(m, flush=True)
    log_lines.append(str(m))


def section(t):
    log("\n" + "=" * 70)
    log(t)
    log("=" * 70)


# ================================================================ 1. 导入
section("STEP 1: 导入芙洛伦 PMX")
if not PMX.is_file():
    raise SystemExit(f"PMX 不存在: {PMX}")

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.preferences.addon_enable(module=MMD_ADDON)
bpy.ops.preferences.addon_enable(module=PDX_ADDON)

bpy.ops.mmd_tools.import_model(
    filepath=str(PMX),
    types={'MESH', 'ARMATURE', 'MORPHS'},
    scale=1.0,
    clean_model=False,
    remove_doubles=False,
    fix_bone_order=False,
    rename_bones=False,
    fix_ik_links=False,
    apply_bone_fixed_axis=False,
    log_level='ERROR',
)

src_meshes = [o for o in bpy.data.objects if o.type == 'MESH']
src_arm = max((o for o in bpy.data.objects if o.type == 'ARMATURE'),
              key=lambda o: len(o.data.bones))
log(f"  网格 {len(src_meshes)} 件, 骨架 {src_arm.name} {len(src_arm.data.bones)} 骨")
if len(src_meshes) != 1:
    log(f"  !! 预期单一网格，实际 {len(src_meshes)}: {[o.name for o in src_meshes]}")
src_mesh = max(src_meshes, key=lambda o: len(o.data.polygons))
log(f"  主网格 {src_mesh.name}: 顶点={len(src_mesh.data.vertices)} 面={len(src_mesh.data.polygons)}")

mat_names = [m.name if m else "(None)" for m in src_mesh.data.materials]
log(f"  材质 {len(mat_names)}: {mat_names}")

# ---------------------------------------------------------------- 材质分类
# 用合同分类，而不是写死的集合 —— 材质命名跨系列完全不通用
# （少前2 BodySkin/Face/Cth*、鸣潮 face/腿/鞋子、终末地 面/肌/发/Cloth1），
# 但同系列内部一致。合同 = 关键词规则 + 贴图名兜底 + 每模型 OVERRIDES。
# 已验证：合同对芙洛伦的分类与原先写死的四个集合**逐项相同**。
#
# 未识别材质是**硬错误**，不能默默剔除:
#   静默丢几何在导出阶段不报错、游戏里少一块，是最难查的一类问题。
#   要么补 moe_material_contract.OVERRIDES，要么补关键词规则。
_mat_objs = []
for m in src_mesh.data.materials:
    if not m:
        continue
    imgs = []
    if m.use_nodes:
        for nd in m.node_tree.nodes:
            if nd.type == "TEX_IMAGE" and nd.image:
                imgs.append(nd.image.name)
    _mat_objs.append({"name": m.name, "images": imgs})

_groups, _unknown = MC.classify_materials(_mat_objs, MODEL)
if _unknown:
    for n in _unknown:
        log(f"  !! 未识别材质: {n}")
    raise SystemExit(
        f"{len(_unknown)} 个材质无法分类 —— 请在 tools/lib/moe_material_contract.py 的 "
        f"OVERRIDES['{MODEL}'] 里补上，或补关键词规则。拒绝静默剔除几何。")

HEAD_MATS = set(_groups["head"])
TORSO_MATS = set(_groups["torso"])
CLOTHES_MATS = set(_groups["clothes"])
DROP_MATS = set(_groups["drop"])
NO_RENDER_MATS = {m["name"] for m in _mat_objs if MC.is_no_render(m["name"])}

# ---------------------------------------------------------------- 衣服并入躯干
# **衣服不再独立成件（2026-08-17 用户决定）。**
#
# 原先按原版做法把衣服拆成独立 entity + accessory 三层，是为了「将来能换装」。
# 但实际素材撑不起这个前提：
#   - 不少角色模型**没有独立衣物层**，衣服与身体焊在一起，拆不出来
#   - 反过来也有**只有衣服、没有身体皮肤**的模型（被遮住的皮肤在建模时就删了，
#     芙洛伦的 torso 只剩 1222 面就是这个情况）
# 两头都不成立时，「衣服可换」这个抽象就没有落地基础，只剩三层配置的复杂度。
#
# 所以合并：衣服材质并进 torso，`moe_clothes` 不再生成。
# 好处是躯干 entity 自带完整外观，「只有衣服没身体」的模型也能正常出人。
# 代价是放弃换装（`portrait_group` 那套 accessory 三层机制随之停用）。
#
# 分类逻辑**保留在合同里不动** —— 将来要拆回来只需去掉这一步合并。
if CLOTHES_MATS:
    log(f"  衣服并入躯干: +{len(CLOTHES_MATS)} 个材质 {sorted(CLOTHES_MATS)}")
    TORSO_MATS |= CLOTHES_MATS
    CLOTHES_MATS = set()

log(f"  分类: head={len(HEAD_MATS)} torso={len(TORSO_MATS)} "
    f"clothes={len(CLOTHES_MATS)}(已并入 torso) drop={len(DROP_MATS)} "
    f"不渲染={sorted(NO_RENDER_MATS)}")
for _p, _s in (("head", HEAD_MATS), ("torso", TORSO_MATS),
               ("drop", DROP_MATS)):
    log(f"    {_p:<8} {sorted(_s)}")
if not TORSO_MATS:
    log("  !! 没有 torso 材质 —— 身体会完全缺失，检查分类")

# ================================================================ 2. 拆件
section("STEP 2: 按材质拆 head / torso")


def slot_indices(mesh_obj, wanted):
    return {i for i, m in enumerate(mesh_obj.data.materials)
            if m and m.name in wanted}


def split_by_materials(source, wanted, new_name):
    """复制 source，只保留 wanted 材质的面。返回新对象或 None。"""
    idxs = slot_indices(source, wanted)
    if not idxs:
        log(f"  {new_name}: 无匹配材质，跳过")
        return None

    dup = source.copy()
    dup.data = source.data.copy()
    dup.name = new_name
    dup.data.name = new_name + "_mesh"
    bpy.context.scene.collection.objects.link(dup)

    # 删除不属于 wanted 的面。用 bmesh 精确操作。
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(dup.data)
    doomed = [f for f in bm.faces if f.material_index not in idxs]
    bmesh.ops.delete(bm, geom=doomed, context='FACES')
    # 清掉孤立顶点
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context='VERTS')
    bm.to_mesh(dup.data)
    bm.free()

    log(f"  {new_name}: 面={len(dup.data.polygons)} 顶点={len(dup.data.vertices)} "
        f"(材质槽 {sorted(idxs)})")
    return dup


def move_body_faces_to_torso(head_o, torso_o, arm_obj):
    """把 head 里「由躯干骨驱动」的面搬到 torso。返回搬走的面数。

    为什么材质拆完还要再拆一次（2026-08-22 洛茜实机）：材质槽 ≠ 部位。
    她的 `发Alpha` 材质（贴图 hair_02）**同时装着**头顶耳饰和一整条垂到
    小腿的飘带 + 袜环 —— 美术为省贴图把不相干的几何塞进了同一个材质。
    整块归 head 的后果：head entity 只播 moe_*_head_*.anim（不含躯干骨
    通道），那些顶点永远停在 rest，torso 一动就整块错位（游戏里表现为
    袜环/内衣/飘带跟身体脱节，且**日志零报错**）。

    判据用**顶点主导骨的父链**，不用名字也不用高度 —— 两者都会骗人：
      · 名字：`bn_m_tail_base_L_a_jnt13` 听着像尾巴，实为下身飘带
      · 高度：`bn_m_hair_side_L_a_03_jnt` rest 只到 Y 6.14（比那根飘带骨
        的 34.60 **还低**），但它确实是头发，挂 bn_m_head 下是对的
    见 moe_material_contract.head_chain_root()。

    面的归属按「顶点全票」：实测洛茜那块 7283 个三角形里**跨界的是 0 个**
    （纯 head 1820 + 纯 body 5463）—— 两部分在拓扑上本来就是分离的，
    所以不存在边界取舍问题。真出现跨界面时保守留在 head（宁可少搬，
    搬错会在脖子上撕开缝），并打印数量供人核对。
    """
    if not head_o or not torso_o or not arm_obj:
        return 0
    # **骨名空间**：这一步跑在骨名转写（STEP 5 的 build_name_map）之前，
    # 此时骨名还是原始 PMX 名（洛茜的头骨叫「頭」、脊椎叫「上半身」）。
    # head_chain_root() 认的是最终 ASCII 名（bn_m_head / bn_m_spine_low），
    # 直接拿原始名去判会全部落空、静默搬 0 个面 —— 同
    # [[moe-anim-migration-two-channels]] 记的转写命名空间坑。
    # 所以先用同一套 build_name_map 把父链映射到最终名再判。
    # 2026-08-22 实测：**顶点组名与骨架骨名可能处在不同空间** —— 顶点组已经
    # 是 bn_m_* 最终名，骨架骨名还是 MMD 日文原名。只按一边建父链会全部查不
    # 到键、21015 个顶点判不出，而"判不出"会被当成"没问题"（假阴性）。
    # 所以父链的**键同时收原名与映射名**，值统一用映射名，两个空间都能走通。
    _raw = [bb.name for bb in arm_obj.data.bones]
    _nmap, _ = build_name_map(_raw)
    def _fin(n):
        return _nmap.get(n, n) if n else None
    parent = {}
    for bb in arm_obj.data.bones:
        _pn = _fin(bb.parent.name) if bb.parent else None
        for _k in {bb.name, _fin(bb.name)}:
            parent[_k] = _pn
    vg_names = {vg.index: vg.name for vg in head_o.vertex_groups}
    # 每个顶点的主导骨 → 归属（"head" / "body" / None）
    side = []
    for v in head_o.data.vertices:
        best = (-1.0, None)
        for g in v.groups:
            _nm = vg_names.get(g.group)
            # 跳过 mmd_edge_scale 这类非骨骼组：它权重恒为 1.0，会把
            # 「主导骨」全部抢走，让归属判定整体落空（踩过）。
            if not MC.is_bone_vgroup(_nm):
                continue
            if g.weight > best[0]:
                best = (g.weight, _nm)
        side.append(MC.head_chain_root(best[1], parent) if best[1] else None)

    # 诊断：判定分布。全 None 说明骨名空间不对（映射落空），
    # 那时函数会静默搬 0 个面 —— 必须能一眼看出来，不能装作"干净"。
    from collections import Counter as _C
    _dist = _C(side)
    log(f"  [归属判定] head={_dist.get('head', 0)} body={_dist.get('body', 0)} "
        f"判不出={_dist.get(None, 0)} / 共 {len(side)} 顶点")
    if not _dist.get("body") and not _dist.get("head"):
        log("  !! 全部判不出 —— 骨名空间不匹配，父链映射落空。"
            "检查 build_name_map 是否已覆盖当前骨名")
        _sample = [(vg_names.get(g.group), round(g.weight, 3))
                   for g in head_o.data.vertices[0].groups][:4]
        log(f"     顶点0 的组名样本: {_sample}")

    doomed, mixed = set(), 0
    for poly in head_o.data.polygons:
        ss = {side[vi] for vi in poly.vertices}
        if ss == {"body"}:
            doomed.add(poly.index)
        elif "body" in ss and "head" in ss:
            mixed += 1
    if mixed:
        log(f"  跨界面 {mixed} 个（顶点分属头/身两边）—— 保守留在 head，"
            f"请核对是否需要手工改判")
    if not doomed:
        return 0

    # 复制一份 head，只留要搬的面 → 合并进 torso → 再从 head 删掉。
    # 用 Blender 的对象复制而不是手工重建 bmesh：顶点组、UV、材质槽
    # 都自动跟着走，省掉逐顶点搬权重（手工搬极易在顶点重排上出错）。
    import bmesh
    moved = head_o.copy()
    moved.data = head_o.data.copy()
    moved.name = "_moved_body_faces"
    bpy.context.scene.collection.objects.link(moved)

    bm = bmesh.new()
    bm.from_mesh(moved.data)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.index not in doomed],
                     context='FACES')
    _loose = [v for v in bm.verts if not v.link_faces]
    if _loose:
        bmesh.ops.delete(bm, geom=_loose, context='VERTS')
    bm.to_mesh(moved.data)
    bm.free()

    bm = bmesh.new()
    bm.from_mesh(head_o.data)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.faces[i] for i in sorted(doomed)],
                     context='FACES')
    _loose = [v for v in bm.verts if not v.link_faces]
    if _loose:
        bmesh.ops.delete(bm, geom=_loose, context='VERTS')
    bm.to_mesh(head_o.data)
    bm.free()

    bpy.ops.object.select_all(action='DESELECT')
    moved.select_set(True)
    torso_o.select_set(True)
    bpy.context.view_layer.objects.active = torso_o
    bpy.ops.object.join()
    return len(doomed)


head_obj = split_by_materials(src_mesh, HEAD_MATS, "moe_head")
torso_obj = split_by_materials(src_mesh, TORSO_MATS, "moe_torso")
clothes_obj = split_by_materials(src_mesh, CLOTHES_MATS, "moe_clothes")

# 按骨骼归属二次拆分（材质拆完之后立刻做，后面所有校验/导出都基于结果）
_moved = move_body_faces_to_torso(head_obj, torso_obj, src_arm)
if _moved:
    log(f"  按骨骼归属把 {_moved} 个面从 head 搬到 torso "
        f"（材质槽骗人：下身几何借用了头部贴图）")
    log(f"  head: 面={len(head_obj.data.polygons)} "
        f"顶点={len(head_obj.data.vertices)}")
    log(f"  torso: 面={len(torso_obj.data.polygons)} "
        f"顶点={len(torso_obj.data.vertices)}")

# ---------------------------------------------------------------- 头身分界校验
# **mod 玩家要能单独换头或换身，所以 head 与 torso 的几何必须干净分开。**
# 材质名分类管不了这件事：芙洛伦的 `Tattoo` 名字命中 torso 关键词，
# 几何却长在右大腿（Y 59~71），丢衣服后就露出「大腿上的红色纹字」。
#
# 所以按材质分类之后**再按几何位置复核一遍**。越界只警告不中断 ——
# 判断「这块该归谁」需要看渲图，脚本给出坐标，人来定 OVERRIDES。
def _mat_world_height(mesh_obj, mat_name):
    """该材质在该对象里占的面的**世界高度**范围。无面返回 None。

    注意轴向：**Blender 里高度是 Z**（本文件别处量骨架高度用的也是 `.z`），
    而导出的 `.mesh` 里高度落在 Y —— `NECK_Y=105` 那个数值取自 .mesh。
    两边数值同尺度，只是轴名不同，所以这里取 Z 再按 NECK_Y 比较。
    取错轴会得到 -0.81 / 2.59 这种量级的值，全部误报越界（踩过）。
    """
    idx = next((i for i, m in enumerate(mesh_obj.data.materials)
                if m and m.name == mat_name), None)
    if idx is None:
        return None
    mw = mesh_obj.matrix_world
    lo, hi = float("inf"), float("-inf")
    for poly in mesh_obj.data.polygons:
        if poly.material_index != idx:
            continue
        for vi in poly.vertices:
            h = (mw @ mesh_obj.data.vertices[vi].co).z   # Blender 高度轴 = Z
            lo = min(lo, h)
            hi = max(hi, h)
    return None if lo == float("inf") else (lo, hi)


section("STEP 2b: 头身分界校验（mod 玩家要能单独换头/换身）")
# 校验插在缩放（STEP 3e）**之前**，所以这里是原始尺度。
# 判据用「占身高的比例」而不是绝对坐标，对尺度与轴向都免疫 ——
# 身高从所有拆件的实际几何量出来，不写死。
_all_h = []
for _o in (head_obj, torso_obj, clothes_obj):
    if not _o:
        continue
    _mw = _o.matrix_world
    for _v in _o.data.vertices:
        _all_h.append((_mw @ _v.co).z)
BODY_H = (max(_all_h) - min(_all_h)) if _all_h else 0.0
log(f"  角色高度（原始尺度，Blender Z 轴）: {BODY_H:.2f}")

_violations = []
for _part, _obj, _mats in (("head", head_obj, HEAD_MATS),
                           ("torso", torso_obj, TORSO_MATS)):
    if not _obj:
        continue
    _entries = []
    for _m in sorted(_mats):
        _rng = _mat_world_height(_obj, _m)
        if _rng:
            _entries.append({"name": _m, "y_min": _rng[0], "y_max": _rng[1]})
            _tag = ""
            if _part == "head" and MC.is_hair(_m):
                _tag = "  (头发，豁免下界)"
            elif _part == "torso" and MC.is_wearable(_m):
                _tag = "  (穿戴物，豁免上下界)"
            log(f"  {_part:<6} {_m:<24} 高度 [{_rng[0]:7.2f}, {_rng[1]:7.2f}]{_tag}")
    _bad = MC.check_part_bounds(_part, _entries, body_height=BODY_H, model=MODEL)
    for _n, _why in _bad:
        log(f"  !! 越界 {_n}: {_why}")
        _violations.append((_part, _n, _why))
if _violations:
    log(f"  {len(_violations)} 处头身越界 —— 在 moe_material_contract.py 的 "
        f"OVERRIDES['{MODEL}'] 里改判（看渲图定归属，别猜）")
else:
    log(f"  头身分界干净（脖子线 = 身高 × {MC.NECK_RATIO}"
        f" = {BODY_H * MC.NECK_RATIO:.2f}，头发与穿戴物豁免）")

section("STEP 2c: 蒙皮归属校验（head 里不能有躯干骨驱动的顶点）")
# 为什么材质与高度两道校验还不够（2026-08-22 洛茜实机踩到）：
# 她的 `发Alpha` 材质（贴图 hair_02）同时装着头顶耳饰和一整条垂到小腿的
# 飘带，还捎带了袜环/内衣。材质名判据把整块归了 head（名字含"发"），
# 高度判据被 is_hair() 无条件豁免放过。结果 4274 个由 pelvis/legD/kneeD/
# spine_low/tail_base_* 驱动的顶点进了 head mesh —— 而 head entity 只播
# moe_*_head_*.anim（不含躯干骨通道），这些顶点永远停在 rest，torso 一动
# 就整块错位（游戏里表现为袜环、内衣、飘带跟身体脱节）。
#
# 判据用**父链归属**而不是名字或高度，因为前两者都会骗人：
#   · 名字：`bn_m_tail_base_L_a_jnt13` 听着像尾巴，实为下身飘带
#   · 高度：`bn_m_hair_side_L_a_03_jnt` rest 只到 Y 6.14（垂到脚踝的
#     长发），但它确实是头发，挂 bn_m_head 下，跟着头动是对的
# 只有父链能区分：hair_side/ear_base ← bn_m_head，tail_base ← spine_low。
_own_bad = []
if head_obj:
    _dom = {}
    _vg_names = {vg.index: vg.name for vg in head_obj.vertex_groups}
    for _v in head_obj.data.vertices:
        _best = (-1.0, None)
        for _g in _v.groups:
            _nm2 = _vg_names.get(_g.group)
            if not MC.is_bone_vgroup(_nm2):
                continue
            if _g.weight > _best[0]:
                _best = (_g.weight, _nm2)
        if _best[1]:
            _dom[_best[1]] = _dom.get(_best[1], 0) + 1
    # 父链取自 Blender 骨架。**键同时收原名与映射名** —— 顶点组名与骨架
    # 骨名可能处在不同空间（顶点组已是 bn_m_*，骨架还是日文原名），
    # 只收一边会全部查不到键。同 move_body_faces_to_torso 里的处理。
    _nmap2, _ = build_name_map([_b.name for _b in src_arm.data.bones])
    _parent = {}
    for _b in src_arm.data.bones:
        _pn = _b.parent.name if _b.parent else None
        _pn = _nmap2.get(_pn, _pn) if _pn else None
        for _k in {_b.name, _nmap2.get(_b.name, _b.name)}:
            _parent[_k] = _pn
    # 真相输出：两边的实际骨名 + 父链键样本。空间不匹配时靠这个定位，
    # 不要靠猜（2026-08-22 猜了三轮都错）。
    _sk = [b.name for b in src_arm.data.bones][:6]
    _vgs = [vg.name for vg in head_obj.vertex_groups][:6]
    log(f"  [真相] 骨架骨名样本: {_sk}")
    log(f"  [真相] 顶点组名样本: {_vgs}")
    log(f"  [真相] 主导骨名样本: {list(_dom)[:6]}")
    log(f"  [真相] 父链键样本:   {list(_parent)[:6]}")
    _hit = sum(1 for b in _dom if b in _parent)
    log(f"  [真相] 主导骨命中父链: {_hit}/{len(_dom)}")
    # 每个顶点的归属，用于算覆盖率
    _dom_side = []
    for _bn2, _cnt2 in _dom.items():
        _r2 = MC.head_chain_root(_bn2, _parent)
        _dom_side.extend([_r2] * _cnt2)
    # **"判不出"不等于"没问题"**：2026-08-22 这里假阴性过一次 —— 骨名空间
    # 不匹配导致 21015 个顶点全部判不出，父链返回 None，而下面的循环
    # 只看 _own_bad 是否为空，于是日志报「蒙皮归属干净」、面一个没搬。
    # 判据必须能区分「查到了且没问题」与「根本没查到」，所以先断言覆盖率。
    _und = sum(1 for _r in _dom_side if _r is None) if _dom_side else 0
    _cov = 1.0 - _und / max(len(_dom_side), 1)
    if _cov < 0.5:
        log(f"  !! 归属判定覆盖率仅 {_cov * 100:.1f}%（{_und}/{len(_dom_side)} "
            f"判不出）—— 骨名空间不匹配，这道校验的结论**不可信**。"
            f"检查 build_name_map 与 HEAD/BODY_CHAIN_ROOTS 是否覆盖当前骨名")
    _own_bad = MC.check_skin_ownership("head", _dom, bone_parent=_parent)
    for _bn, _why in _own_bad:
        log(f"  !! {_why}")
    if _own_bad:
        _n = sum(_dom[b] for b, _ in _own_bad)
        log(f"  {len(_own_bad)} 根躯干骨在 head 里主导了 {_n} 个顶点 —— "
            f"这些几何必须搬到 torso，否则游戏里会与身体脱节。"
            f"修法：在 OVERRIDES['{MODEL}'] 里把对应材质改判 torso；"
            f"若材质里头身几何混装（洛茜的情况），需要按骨骼归属拆 submesh")
    else:
        log("  蒙皮归属干净：head 里没有躯干骨主导的顶点")

# CK3 只支持 3 层 UV（引擎报 "has more than 3 UV channels ... will be ignored"），
# 而且空 UV 层会触发 "UV-Set: N ... has no valid triangle" 错误。
# 芙洛伦源网格有 5 层，只留第一层（主 diffuse UV）。
MAX_UV_LAYERS = 1
for p in (head_obj, torso_obj, clothes_obj):
    if not p:
        continue
    uvs = list(p.data.uv_layers)
    if len(uvs) > MAX_UV_LAYERS:
        removed = []
        for uv in uvs[MAX_UV_LAYERS:]:
            removed.append(uv.name)
            p.data.uv_layers.remove(uv)
        log(f"  {p.name}: UV 层 {len(uvs)} → {len(p.data.uv_layers)}，移除 {removed}")
    else:
        log(f"  {p.name}: UV 层 {len(uvs)}，无需处理")

# ---------------------------------------------------------------- 退化 UV 面
# 引擎报 "UV-Set: 0 in mesh 'X' has no valid triangle to use when calculating
# texture streaming factor"，因为存在 UV 面积为 0 的面。
# 这些是 PMX 源模型里的冗余面（芙洛伦是 Emotions1 与 Hair 材质上各几个），
# 不影响显示，但会让贴图流式加载算不出 LOD 因子。数量极少，直接删。
def uv_area(mesh, poly, uv_data):
    pts = [uv_data[li].uv for li in poly.loop_indices]
    a = 0.0
    for i in range(1, len(pts) - 1):
        p0, p1, p2 = pts[0], pts[i], pts[i + 1]
        a += abs((p1.x - p0.x) * (p2.y - p0.y) - (p2.x - p0.x) * (p1.y - p0.y)) * 0.5
    return a


for p in (head_obj, torso_obj, clothes_obj):
    if not p or not p.data.uv_layers:
        continue
    me = p.data
    uv_data = me.uv_layers[0].data
    bad = [poly.index for poly in me.polygons if uv_area(me, poly, uv_data) <= 0.0]
    if not bad:
        log(f"  {p.name}: 无退化 UV 面")
        continue
    bad_mats = sorted({
        me.materials[me.polygons[i].material_index].name
        for i in bad
        if me.materials and me.polygons[i].material_index < len(me.materials)
        and me.materials[me.polygons[i].material_index]
    })
    bpy.ops.object.select_all(action='DESELECT')
    p.select_set(True)
    bpy.context.view_layer.objects.active = p
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='DESELECT')
    bpy.ops.object.mode_set(mode='OBJECT')
    for i in bad:
        me.polygons[i].select = True
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.delete(type='FACE')
    bpy.ops.object.mode_set(mode='OBJECT')
    log(f"  {p.name}: 删除 {len(bad)} 个退化 UV 面（材质 {bad_mats}），"
        f"剩 {len(me.polygons)} 面")

parts = [p for p in (head_obj, torso_obj, clothes_obj) if p]
if not parts:
    raise SystemExit("拆件失败，没有产出任何部件")

# 原始网格不再需要
bpy.data.objects.remove(src_mesh, do_unlink=True)

# ================================================================ 3. deform 骨架
section("STEP 3: 提取 deform 骨架")

all_bones = {b.name: b for b in src_arm.data.bones}
classes = {n: classify_bone(n) for n in all_bones}

# 决定保留集
from moe_bone_contract import MMD_TO_SEMANTIC

# MMD Tools 会生成 _dummy_* / _shadow_* 辅助骨来实现附加旋转，
# 它们无蒙皮权重且不应出现在导出骨架里。
def is_mmd_helper(n):
    return n.startswith(("_dummy_", "_shadow_"))


keep = set()
for n, c in classes.items():
    if is_mmd_helper(n):
        continue
    if c in KEEP_CLASSES:
        keep.add(n)
    elif c == "physics" and any(k in n for k in KEEP_PHYSICS_KEYWORDS):
        keep.add(n)

# 语义核心骨无条件保留，哪怕当前没蒙皮权重 —— 动画要按名索引它们。
core_jp = [n for n in all_bones if n in MMD_TO_SEMANTIC and not is_mmd_helper(n)]
keep.update(core_jp)
log(f"  语义核心骨强制保留 {len(core_jp)} 根: "
    f"{[MMD_TO_SEMANTIC[n] for n in core_jp][:8]}...")

# 只保留真正有蒙皮权重的骨，或者是保留骨的祖先（维持链完整）
used_groups = set()
for p in parts:
    vgn = {vg.index: vg.name for vg in p.vertex_groups}
    for v in p.data.vertices:
        for g in v.groups:
            if g.weight > 1e-4:
                nm = vgn.get(g.group)
                if nm:
                    used_groups.add(nm)

log(f"  分类统计: " + str({c: sum(1 for x in classes.values() if x == c)
                          for c in sorted(set(classes.values()))}))
log(f"  初选保留 {len(keep)} 骨 (deform_candidate + twist + 头发物理骨)")
log(f"  实际有权重的顶点组 {len(used_groups)}")

# 保留 = 初选 ∩ (有权重 或 是有权重骨的祖先)
def ancestors(name):
    out = []
    b = all_bones.get(name)
    while b and b.parent:
        out.append(b.parent.name)
        b = b.parent
    return out


needed = set()
# 有权重的保留骨 + 语义核心骨（即使无权重）都算种子
seeds = (keep & used_groups) | set(core_jp)
for n in seeds:
    needed.add(n)
    # 祖先必须留，否则父级链断裂；但 MMD 辅助骨要跳过
    needed.update(a for a in ancestors(n) if not is_mmd_helper(a))
final_keep = needed
log(f"  最终保留 {len(final_keep)} 骨（含为维持链而保留的祖先）")

dropped = set(all_bones) - final_keep
log(f"  剔除 {len(dropped)} 骨")
drop_by_class = {}
for n in dropped:
    drop_by_class.setdefault(classes[n], []).append(n)
for c, lst in sorted(drop_by_class.items()):
    log(f"    {c}: {len(lst)}")

# 权重转移：被剔除骨的权重合并到最近的保留祖先
section("STEP 3b: 权重合并到最近保留祖先")


def nearest_kept_ancestor(name):
    b = all_bones.get(name)
    while b and b.parent:
        if b.parent.name in final_keep:
            return b.parent.name
        b = b.parent
    return None


merge_map = {}
for n in dropped:
    if n not in used_groups:
        continue
    target = nearest_kept_ancestor(n)
    if target:
        merge_map[n] = target
log(f"  需要合并权重的骨 {len(merge_map)}")

for p in parts:
    name_to_vg = {vg.name: vg for vg in p.vertex_groups}
    merged = 0
    for src_name, dst_name in merge_map.items():
        svg = name_to_vg.get(src_name)
        if not svg:
            continue
        dvg = name_to_vg.get(dst_name)
        if dvg is None:
            dvg = p.vertex_groups.new(name=dst_name)
            name_to_vg[dst_name] = dvg
        # 逐顶点累加
        for v in p.data.vertices:
            w = 0.0
            for g in v.groups:
                if g.group == svg.index:
                    w = g.weight
                    break
            if w > 1e-4:
                cur = 0.0
                for g in v.groups:
                    if g.group == dvg.index:
                        cur = g.weight
                        break
                dvg.add([v.index], min(1.0, cur + w), 'REPLACE')
                merged += 1
    # 删掉被剔除骨的顶点组
    for n in list(name_to_vg):
        if n not in final_keep:
            vg = name_to_vg.get(n)
            if vg:
                p.vertex_groups.remove(vg)
                del name_to_vg[n]
    log(f"  {p.name}: 合并 {merged} 处权重, 剩余顶点组 {len(p.vertex_groups)}")

# 删除骨骼
section("STEP 3c: 删除被剔除的骨骼")
bpy.ops.object.select_all(action='DESELECT')
src_arm.select_set(True)
bpy.context.view_layer.objects.active = src_arm

# 先清掉 MMD 的姿态约束。mmd_additional_rotation 等约束引用 _shadow_* 影子骨，
# 删骨后会留下悬空引用，依赖图会刷一堆 "Could not find op_from" 错误。
n_con = 0
for pb in src_arm.pose.bones:
    for c in list(pb.constraints):
        pb.constraints.remove(c)
        n_con += 1
log(f"  已移除 {n_con} 个姿态约束（MMD additional_rotation / IK 等）")

bpy.ops.object.mode_set(mode='EDIT')
eb = src_arm.data.edit_bones
for n in list(eb.keys()):
    if n not in final_keep:
        eb.remove(eb[n])
bpy.ops.object.mode_set(mode='OBJECT')
src_arm.name = "moe_rig"
log(f"  骨架剩余 {len(src_arm.data.bones)} 骨")
kept_jp_names = [b.name for b in src_arm.data.bones]

# ---------------------------------------------------------------- 骨名转 ASCII
section("STEP 3d: 骨名转 ASCII（PDX 格式用 latin-1，日文名无法写入）")
contract = contract_report(set(kept_jp_names))
log(f"  语义合同: 核心骨 {len(contract['core_present'])}/{len(SEMANTIC_ORDER)}"
    f"  满足={contract['satisfies_contract']}")
if contract["core_missing"]:
    log(f"  缺失核心骨: {contract['core_missing']}"
        f"（必需缺失: {contract['required_missing'] or '无'}）")

name_map, collisions = build_name_map(kept_jp_names)
if collisions:
    log(f"  !! 转写冲突已加序号消解: {collisions}")

# 只改骨骼名 —— Blender 会自动把绑定网格的同名顶点组一起改。
# 若先手动改顶点组，再改骨名就会撞上"名字已存在"而放弃同步。
for b in src_arm.data.bones:
    if b.name in name_map:
        b.name = name_map[b.name]

# 核对顶点组是否跟着改了，没跟上的手动补
for p in parts:
    stale = [vg for vg in p.vertex_groups if vg.name in name_map]
    for vg in stale:
        target = name_map[vg.name]
        if any(g.name == target for g in p.vertex_groups):
            continue          # 已存在同名组，说明自动同步过了
        vg.name = target
    left = [vg.name for vg in p.vertex_groups if not vg.name.isascii()]
    if left:
        log(f"  !! {p.name} 仍有非 ASCII 顶点组 {len(left)}: {left[:8]}")

kept_names = [b.name for b in src_arm.data.bones]
nonascii = [n for n in kept_names if not n.isascii()]
if nonascii:
    log(f"  !! 仍有非 ASCII 骨名 {len(nonascii)}: {nonascii[:10]}")
else:
    log(f"  全部 {len(kept_names)} 个骨名已是 ASCII")
core_in_rig = [s for s in SEMANTIC_ORDER if s in set(kept_names)]
log(f"  骨架内语义核心骨: {len(core_in_rig)}/{len(SEMANTIC_ORDER)}")
log(f"  样例: {kept_names[:12]}")

# 重建 armature 绑定
for p in parts:
    for m in list(p.modifiers):
        p.modifiers.remove(m)
    p.parent = src_arm
    am = p.modifiers.new(name="Armature", type='ARMATURE')
    am.object = src_arm
    am.use_vertex_groups = True

# 归一化权重
for p in parts:
    bpy.ops.object.select_all(action='DESELECT')
    p.select_set(True)
    bpy.context.view_layer.objects.active = p
    try:
        bpy.ops.object.vertex_group_normalize_all(lock_active=False)
    except Exception as e:
        log(f"  !! {p.name} 归一化失败: {e}")
log("  已归一化权重")

# ================================================================ 3e. 对齐 CK3 尺度
section("STEP 3e: 缩放到 CK3 尺度 + 相机定位骨")
# 为什么必须缩放：CK3 的肖像相机写死在 gfx/portraits/cameras/portrait_cameras.txt 里，
# 半径 150~240 单位，架在一个约 150 单位高的人体上（原版 female_body 骨架
# ground_joint Y=0 → camera_torso_look_at Y=149.98）。
# PMX 原始尺度只有 ~17.4 单位高，直接进游戏在肖像框里就是个点。
#
# 必须在烘焙动画之前缩放：.anim 的 t（平移）通道是绝对单位，不是尺度无关的。
# 先缩放再录关键帧，两者才一致。
CK3_BODY_HEIGHT = 149.98      # 原版 camera_torso_look_at 的世界高度
CAMERA_BONE = "camera_torso_look_at"   # 所有肖像相机的 position_node / look_at_node

bpy.ops.object.mode_set(mode='OBJECT')
zs = [(src_arm.matrix_world @ b.head_local).z for b in src_arm.data.bones]
zs += [(src_arm.matrix_world @ b.tail_local).z for b in src_arm.data.bones]
rig_height = max(zs) - min(zs)
SCALE = CK3_BODY_HEIGHT / rig_height
log(f"  骨架高度 {rig_height:.4f} → 目标 {CK3_BODY_HEIGHT} ，缩放 {SCALE:.4f}x")
# 骨架外包含**骨尾**：耳朵/翅膀/天线这类末端骨的 tail 可以伸到头顶上方很远。
# 实测 92 个角色的 头骨高度 / 骨架外包 比值中位 0.87，但 Death 0.47、祀 0.46、
# 卡缪带翅膀 0.60、大潘 0.68 —— 这四个按外包缩放后只有 80~120 单位高，
# 游戏里就是「角色大小不对」（2026-09-27）。离群时改按头骨高度对齐中位比值；
# 其余角色比值在 0.70 以上，保持原缩放不变（已烘产物与物理都按它做）。
HEAD_TO_EXTENT = 0.87
HEAD_RATIO_FLOOR = 0.69
_head_jp = next((b for b in src_arm.data.bones if MMD_TO_SEMANTIC.get(b.name) == "bn_m_head"
                 or b.name == "bn_m_head"), None)
if _head_jp is not None:
    _hz = (src_arm.matrix_world @ _head_jp.head_local).z - min(zs)
    _ratio = _hz / rig_height
    if _ratio < HEAD_RATIO_FLOOR:
        rig_height = _hz / HEAD_TO_EXTENT
        SCALE = CK3_BODY_HEIGHT / rig_height
        log(f"  !! 头骨高度/骨架外包 = {_ratio:.3f} < {HEAD_RATIO_FLOOR}（外包被末端骨尾撑大），"
            f"改按头骨对齐：等效骨架高度 {rig_height:.4f}，缩放 {SCALE:.4f}x")

# 缩放整个层级并烘进数据，避免 object.scale 残留（导出器 chk_uniform_scale 会拦）
bpy.ops.object.select_all(action='DESELECT')
src_arm.select_set(True)
for p in parts:
    p.select_set(True)
bpy.context.view_layer.objects.active = src_arm
src_arm.scale = (SCALE, SCALE, SCALE)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
log(f"  已应用缩放，骨架 scale={tuple(round(v,6) for v in src_arm.scale)}")

zs2 = [(src_arm.matrix_world @ b.head_local).z for b in src_arm.data.bones]
log(f"  缩放后骨架 Z 范围 [{min(zs2):.2f}, {max(zs2):.2f}]")

# ---------------------------------------------------------------- 定位骨
# 原版相机（gfx/portraits/cameras/portrait_cameras.txt）与环境光
# （gfx/portraits/environments/）的 position_node / look_at_node 只支持
# `default = <骨名>` 一种写法，没有 per-type 分派，所以新 group 也必须
# 提供这些骨名，否则每个 type × 每个相机/环境都报一条：
#   Invalid camera look at node 'bn_h_head_mid' in portrait camera camera_head for type moe_male
#   Invalid light position node 'bn_h_head' in portrait environment environment_testroom ...
#
# 实测 1.18.2 全量引用只有 4 个（统计自 error.log）：
#   camera_torso_look_at  270+915 次   相机/灯光主锚点
#   bn_h_head             129 次       灯光锚点（原版 head 骨架里的头骨）
#   bn_h_head_mid          12 次       相机 look_at（原版头骨中点）
#   bn_sp_cervical           8 次       灯光锚点（原版颈椎）
#   bn_sp_lumbar             2 次       相机 position（原版腰椎）
# 全部是纯定位（use_deform=False），不参与蒙皮，只做坐标锚点。
#
# 高度不能照抄原版数值：原版 camera_torso_look_at 在 149.98，是给
# 头顶约 135 的写实人体设计的取景中心（略高于头顶）。芙洛伦是二次元头身比，
# 头顶 143.96、眼睛 129.1，照抄 149.98 会让 camera_head 对着头顶上方的空气。
# 所以按实测眼高推：取景中心 = 眼高 + 原版的「取景中心高于眼睛」的比例。
def mesh_material_z(obj, mat_names):
    """返回 obj 上属于 mat_names 材质的顶点世界 Z 范围。找不到返回 None。"""
    if not obj or not obj.data.materials:
        return None
    slots = {i for i, m in enumerate(obj.data.materials) if m and m.name in mat_names}
    if not slots:
        return None
    lo, hi = 1e18, -1e18
    mw = obj.matrix_world
    for poly in obj.data.polygons:
        if poly.material_index not in slots:
            continue
        for vi in poly.vertices:
            z = (mw @ obj.data.vertices[vi].co).z
            lo, hi = min(lo, z), max(hi, z)
    return (lo, hi) if lo < 1e17 else None


# 原版基准（work/ck3_skeleton_contract.json + female_head.mesh 顶点实测）：
#   head 骨架局部空间 bn_h_head_mid Y=41.478，attach 到 bn_sp_thoracic(106.5)
#   → 世界 147.98；camera_torso_look_at 世界 149.98。两者只差 2 单位，
#   即原版取景中心 ≈ 头骨中点。所以我们也把它对齐到自己的头骨中点。
# 材质名按合同判定，不写死英文名 —— 洛茜是「面/目/目白」，
# 写死 Face/Eyes 会量不到、静默回落到原版 149.98，相机取景就偏了。
_head_mats = [sl.name for sl in head_obj.material_slots]
eye_z = mesh_material_z(head_obj, {m for m in _head_mats if MC.is_eye(m)})
face_z = mesh_material_z(head_obj, {m for m in _head_mats if MC.is_face(m)})
if face_z:
    # 头骨中点：脸的中间高度。原版 bn_h_head_mid 就在这个位置
    # （眼睛略偏上，嘴略偏下，取脸包围盒中点最接近）。
    LOOK_AT_Z = (face_z[0] + face_z[1]) / 2.0
    eye_note = (f"eye Z [{eye_z[0]:.2f}, {eye_z[1]:.2f}]" if eye_z else "no separate eye material")
    log(f"  measured face Z [{face_z[0]:.2f}, {face_z[1]:.2f}], {eye_note}; view center Z={LOOK_AT_Z:.2f}")
else:
    LOOK_AT_Z = CK3_BODY_HEIGHT
    log(f"  !! 量不到脸部材质，取景中心回落到原版数值 {LOOK_AT_Z}")

# name -> (父骨, 世界 Z)。父骨用语义等价的自家骨，跟随动画一起动。
LOCATOR_BONES = {
    CAMERA_BONE:      ("bn_m_neck",      LOOK_AT_Z),
    "bn_h_head":      ("bn_m_head",      None),   # None = 跟父骨同位置
    "bn_h_head_mid":  ("bn_m_head",      LOOK_AT_Z),
    "bn_sp_cervical": ("bn_m_neck",      None),
    "bn_sp_lumbar":   ("bn_m_spine_low", None),
}

bpy.ops.object.select_all(action='DESELECT')
src_arm.select_set(True)
bpy.context.view_layer.objects.active = src_arm
bpy.ops.object.mode_set(mode='EDIT')
eb = src_arm.data.edit_bones
locator_report = {}
for bname, (parent_name, world_z) in LOCATOR_BONES.items():
    if bname in src_arm.data.bones:
        log(f"  {bname} 已存在，跳过")
        locator_report[bname] = {"created": False, "reason": "已存在"}
        continue
    parent = eb.get(parent_name)
    if parent is None:
        log(f"  !! {bname}: 找不到父骨 {parent_name}，跳过")
        locator_report[bname] = {"created": False, "reason": f"缺父骨 {parent_name}"}
        continue
    nb = eb.new(bname)
    # X 居中（原版这些骨 X 都是 0），Y 跟父骨，Z 按表指定或跟父骨
    pz = parent.head.z if world_z is None else world_z
    nb.head = Vector((0.0, parent.head.y, pz))
    # 骨骼指向必须是世界 +Y（Blender 新建骨骼的默认朝向），不能是 +Z。
    #
    # 原版这批纯定位骨（camera_torso_look_at / bn_h_head_mid / bn_h_head）
    # 导出后的旋转子矩阵是单位阵（无论父骨自身朝向如何倾斜，例如
    # body 骨架里 bn_sp_cervical 有轻微 pitch，但同样是子级的
    # camera_torso_look_at 仍是单位阵）。导出器用的是
    # swap_coord_space(bone.matrix_local).inverted()，Blender 里默认新建
    # 骨骼 head=(0,0,0) tail=(0,1,0) roll=0 时 matrix_local 的旋转部分正是
    # 单位阵，这个默认朝向就是「+Y」。之前误用 tail = head + Z*5（指向
    # 世界 +Z）触发 Blender 骨骼朝向求轴的边界情况，算出来的
    # matrix_local 旋转部分不是单位阵，导出后与原版对不上，很可能就是
    # 书签预览里相机变成仰视角的根因。
    nb.tail = Vector((0.0, parent.head.y + 5.0, pz))
    nb.roll = 0.0
    nb.parent = parent
    nb.use_connect = False
    nb.use_deform = False          # 纯定位骨，不参与蒙皮
    log(f"  已加 {bname}: Z={pz:.2f} parent={parent_name}")
    locator_report[bname] = {
        "created": True, "parent": parent_name, "world_z": round(pz, 3),
    }
bpy.ops.object.mode_set(mode='OBJECT')

# ================================================================ 4. idle 动画
section("STEP 4: 造 idle 动画")
FPS = 30
DURATION = 60          # 帧数，2 秒循环
scn = bpy.context.scene
scn.render.fps = FPS
scn.frame_start = 1
scn.frame_end = DURATION

bpy.ops.object.select_all(action='DESELECT')
src_arm.select_set(True)
bpy.context.view_layer.objects.active = src_arm
bpy.ops.object.mode_set(mode='POSE')

# 呼吸：上半身微幅前后倾 + 头轻微点动。用语义名（已在 STEP 3d 完成改名）。
BREATH = [
    ("bn_m_spine_low", 'X', 1.2),
    ("bn_m_spine_up", 'X', 0.8),
    ("bn_m_neck", 'X', 0.6),
]
anim_bones = []
for bname, axis, deg in BREATH:
    pb = src_arm.pose.bones.get(bname)
    if not pb:
        log(f"  !! 缺少骨骼 {bname}，跳过")
        continue
    pb.rotation_mode = 'QUATERNION'
    anim_bones.append(bname)
    for f, phase in ((1, 0.0), (DURATION // 2, 1.0), (DURATION, 0.0)):
        scn.frame_set(f)
        ang = math.radians(deg) * phase
        pb.rotation_quaternion = Quaternion(
            {'X': (1, 0, 0), 'Y': (0, 1, 0), 'Z': (0, 0, 1)}[axis], ang)
        pb.keyframe_insert(data_path="rotation_quaternion", frame=f)

# center 骨极轻微上下浮动，模拟呼吸重心。
# 位移量按 SCALE 同比放大：pose bone location 是绝对单位，
# 骨架已在 STEP 3e 放大 SCALE 倍，写死的小位移在新尺度下会看不出来。
BREATH_RISE = 0.02 * SCALE
center = src_arm.pose.bones.get("bn_m_center")
if center:
    anim_bones.append("bn_m_center")
    for f, dz in ((1, 0.0), (DURATION // 2, BREATH_RISE), (DURATION, 0.0)):
        scn.frame_set(f)
        center.location = Vector((0.0, 0.0, dz))
        center.keyframe_insert(data_path="location", frame=f)
    log(f"  呼吸起伏 {BREATH_RISE:.4f} 单位（0.02 × {SCALE:.4f}）")

bpy.ops.object.mode_set(mode='OBJECT')
scn.frame_set(1)

act = src_arm.animation_data.action if src_arm.animation_data else None
if act:
    act.name = "moe_idle"
    # Blender 4.4+ fcurves 在 layers/strips/channelbags
    fcs = list(getattr(act, "fcurves", []))
    if not fcs:
        for layer in getattr(act, "layers", []):
            for strip in getattr(layer, "strips", []):
                for bag in getattr(strip, "channelbags", []):
                    fcs.extend(bag.fcurves)
    log(f"  动画 {act.name}: {len(fcs)} fcurve, 驱动骨骼 {sorted(set(anim_bones))}")
    bad = [b for b in anim_bones if not b.isascii()]
    if bad:
        log(f"  !! 动画驱动了非 ASCII 骨名 {bad}，说明改名遗漏")
else:
    log("  !! 未生成 action")

# 验证真的有运动（不能只看 action 存在）
def sample_bone_world(bname, frame):
    scn.frame_set(frame)
    dg = bpy.context.evaluated_depsgraph_get()
    ev = src_arm.evaluated_get(dg)
    pb = ev.pose.bones.get(bname)
    return (ev.matrix_world @ pb.head).copy() if pb else None


log("  运动验证（骨骼世界坐标位移）:")
for bname in ["bn_m_head", "bn_m_spine_up", "bn_m_neck"]:
    p1 = sample_bone_world(bname, 1)
    p2 = sample_bone_world(bname, DURATION // 2)
    if p1 and p2:
        log(f"    {bname}: 帧1→{DURATION//2} 位移={(p2-p1).length:.6f}")
scn.frame_set(1)

# ================================================================ 4b. PDX 材质标记
section("STEP 4b: 给材质打 PDX shader 标记")
# io_pdx_mesh 要求材质带 PDX_SHADER 自定义属性才肯导出。
# 合法 shader 名取自 game/tools/pdxexporter.settings 的白名单。
PDX_SHADER_KEY = "shader"     # 见 io_pdx_mesh/library.py: PDX_SHADER = "shader"

# MMD 材质用自己的节点组，没有标准 Principled BSDF 的 Base Color 输入，
# 导出器会报 'key "Base Color" not found'。这里重建成导出器认得的最简结构：
#   Image Texture → Principled BSDF(Base Color) → Material Output(Surface)
# 只为满足导出契约，Blender 内的视觉效果不重要。
def pick_diffuse_image(mat):
    """从 MMD 材质里挑主 diffuse。toon/spa/sph/shine 是辅助贴图，不能冒充颜色图。"""
    if not mat.node_tree:
        return None
    imgs = [n.image for n in mat.node_tree.nodes
            if n.type == 'TEX_IMAGE' and n.image and n.image.size[0] > 0]
    if not imgs:
        return None
    # PMX 常把 toon 阶梯图记成唯一图片引用。它不是 diffuse，Blender 附加组件
    # 自带的 toon01.bmp 也不在角色源包；映射到 portrait_attachment 会让 PDX
    # mesh 引用一个无法随模型包部署的文件。没有真实颜色图时保留材质本色即可。
    diffuse_imgs = [img for img in imgs
                    if not any(k in img.name.lower() for k in ("toon", "spa", "sph", "shine"))]
    if not diffuse_imgs:
        return None
    aux = ("toon", "spa", "sph", "shine", "skin.png", "eyes.png", "hair-a")

    def score(img):
        nm = img.name.lower()
        s = img.size[0] * img.size[1]
        if any(a in nm for a in aux):
            s //= 100          # 辅助图大幅降权，但不完全排除
        return s

    return max(diffuse_imgs, key=score)


def rebuild_for_export(mat, shader_name, img=None):
    if img is None:
        img = pick_diffuse_image(mat)
    nt = mat.node_tree
    if nt is None:
        mat.use_nodes = True
        nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (300, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    if img is not None:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.location = (-400, 0)
        tex.image = img
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    mat[PDX_SHADER_KEY] = shader_name
    return img


tagged = {}
seen_mats = set()
material_exports = []
for p in parts:
    for mat in p.data.materials:
        if not mat or mat.name in seen_mats:
            continue
        seen_mats.add(mat.name)
        _imgs = [i.name for i in bpy.data.images
                 if mat.node_tree and any(
                     n.type == "TEX_IMAGE" and n.image == i
                     for n in mat.node_tree.nodes)]
        shader = MC.pdx_shader(mat.name, _imgs)
        material_exports.append((mat, shader, pick_diffuse_image(mat)))

# 先基于源贴图名完成 shader 分类，再改名。一个贴图可能被多个材质共用，
# 所以不能一边分类一边重命名，否则后面的材质会看到别名而丢失部位语义。
for _mat, _shader, _img in material_exports:
    if _img is not None:
        namespace_diffuse_image(_img)
for mat, shader, img in material_exports:
        img = rebuild_for_export(mat, shader, img)
        tagged[mat.name] = {"shader": shader,
                            "diffuse": img.name if img else None}
for name, d in sorted(tagged.items()):
    log(f"  {name:<22} → {d['shader']:<40} diff={d['diffuse']}")
log(f"  共重建并标记 {len(tagged)} 个材质")

# ================================================================ 5. 导出
section("STEP 5: 导出 PDX mesh 与 anim")
WORK.mkdir(parents=True, exist_ok=True)
export_results = {}

for p in parts:
    out_mesh = WORK / f"{p.name}.mesh"
    bpy.ops.object.select_all(action='DESELECT')
    p.select_set(True)
    src_arm.select_set(True)
    bpy.context.view_layer.objects.active = p
    try:
        bpy.ops.io_pdx_mesh.export_mesh(
            filepath=str(out_mesh),
            check_existing=False,
            chk_mesh=True, chk_skel=True, chk_locs=False,
            chk_selected=True,
        )
        export_results[p.name] = {"path": str(out_mesh), "ok": out_mesh.is_file(),
                                  "size": out_mesh.stat().st_size if out_mesh.is_file() else 0}
        log(f"  {p.name} → {out_mesh.name} "
            f"({export_results[p.name]['size']:,} bytes)")
    except Exception as e:
        export_results[p.name] = {"path": str(out_mesh), "ok": False, "error": str(e)}
        log(f"  !! {p.name} 导出失败: {e}")

out_anim = WORK / "moe_idle.anim"
bpy.ops.object.select_all(action='DESELECT')
src_arm.select_set(True)
bpy.context.view_layer.objects.active = src_arm
try:
    bpy.ops.io_pdx_mesh.export_anim(
        filepath=str(out_anim),
        check_existing=False,
        int_start=scn.frame_start, int_end=scn.frame_end,
        chk_uniform_scale=True,
    )
    export_results["moe_idle.anim"] = {
        "path": str(out_anim), "ok": out_anim.is_file(),
        "size": out_anim.stat().st_size if out_anim.is_file() else 0}
    log(f"  动画 → {out_anim.name} ({export_results['moe_idle.anim']['size']:,} bytes)")
except Exception as e:
    export_results["moe_idle.anim"] = {"path": str(out_anim), "ok": False, "error": str(e)}
    log(f"  !! 动画导出失败: {e}")

# ================================================================ 6. 报告
section("STEP 6: 保存工作区与报告")
OUT_BLEND.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(OUT_BLEND))
log(f"  工作区: {OUT_BLEND}")

# kept_names 是 STEP 3d 取的快照，STEP 3e 之后又加了相机定位骨，
# 报告要反映导出时的真实骨架，所以重新读一次。
final_bones = [b.name for b in src_arm.data.bones]

report = {
    "source_pmx": str(PMX),
    "texture_aliases": dict(sorted(TEXTURE_ALIASES.items())),
    "source_bone_count": len(all_bones),
    "final_bone_count": len(final_bones),
    "final_bones": final_bones,
    "scale": {
        "source_rig_height": round(rig_height, 4),
        "ck3_body_height": CK3_BODY_HEIGHT,
        "factor": round(SCALE, 6),
        "reason": "CK3 肖像相机半径 150~240，架在约 150 单位高的人体上",
    },
    "locator_bones": {
        "look_at_z": round(LOOK_AT_Z, 3),
        "eye_z": [round(v, 3) for v in eye_z] if eye_z else None,
        "face_z": [round(v, 3) for v in face_z] if face_z else None,
        "created": locator_report,
        "present": {b: (b in src_arm.data.bones) for b in LOCATOR_BONES},
        "reason": "原版相机/环境的 position_node、look_at_node 只支持 default = <骨名>，"
                  "无 per-type 分派，新 group 必须自带同名定位骨",
    },
    "semantic_contract": contract,
    "bone_name_map": name_map,
    "name_collisions": collisions,
    "dropped_count": len(dropped),
    "dropped_by_class": {c: sorted(l) for c, l in sorted(drop_by_class.items())},
    "weight_merge_map": merge_map,
    "material_groups": {
        "head": sorted(HEAD_MATS),
        "torso": sorted(TORSO_MATS),
        "clothes": sorted(CLOTHES_MATS),
        "dropped": sorted(DROP_MATS),
        "no_render": sorted(NO_RENDER_MATS),
        # 合同保证这里是空的（非空会在 STEP 0 就 raise），落盘只为留证
        "unknown": sorted(_unknown),
    },
    "material_shaders": tagged,
    "parts": {
        p.name: {
            "vertices": len(p.data.vertices),
            "polygons": len(p.data.polygons),
            "vertex_groups": len(p.vertex_groups),
            "materials": [m.name for m in p.data.materials if m],
            "uv_layers": [u.name for u in p.data.uv_layers],
            "shape_keys": (len(p.data.shape_keys.key_blocks)
                           if p.data.shape_keys else 0),
            # submesh 索引 → 材质/shader/贴图。生成 .asset 的 meshsettings 需要。
            # 注意 index 沿用原始材质槽编号，是不连续的。
            "submeshes": [
                {
                    "index": slot_i,
                    "material": mat.name,
                    "shader": tagged.get(mat.name, {}).get("shader"),
                    "diffuse": tagged.get(mat.name, {}).get("diffuse"),
                    "polygons": sum(1 for poly in p.data.polygons
                                    if poly.material_index == slot_i),
                    # no_render 的槽位仍出现在报告里（几何确实在 .mesh 中），
                    # 但 build_moe_mod.py 会跳过它的 meshsettings。
                    "no_render": mat.name in NO_RENDER_MATS,
                }
                for slot_i, mat in enumerate(p.data.materials)
                if mat and any(poly.material_index == slot_i
                               for poly in p.data.polygons)
            ],
        } for p in parts
    },
    "animation": {
        "name": act.name if act else None,
        "fps": FPS, "frame_start": 1, "frame_end": DURATION,
        "driven_bones": sorted(set(anim_bones)),
    },
    "exports": export_results,
    "log": log_lines,
}
REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
log(f"  报告: {REPORT}")
log("\n[FLORENCE POC BUILD DONE]")
