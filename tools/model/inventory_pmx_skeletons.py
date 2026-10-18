"""导入单个 PMX，输出骨架/网格/材质/权重的结构化 JSON 报告。

每个 PMX 必须用独立 Blender 后台进程运行，避免名称复用、材质缓存和 armature 复用污染报告。

关键：rename_bones=False、fix_bone_order=False。
MMD Tools 默认会把日文骨名改写成英文并重排顺序，那样拿到的不是原始 PMX 骨架，
无法用于跨模型父级签名/对称性比较。

环境变量:
  MOE_PMX_PATH    必填，PMX 绝对路径
  MOE_PMX_REPORT  必填，输出 JSON 绝对路径

用法:
  $env:MOE_PMX_PATH='...'; $env:MOE_PMX_REPORT='...'
  blender.exe --background --python tools/inventory_pmx_skeletons.py
"""
import bpy
import json
import os
import sys
import traceback
from pathlib import Path

ADDON = "bl_ext.user_default.mmd_tools"

# MMD 标准核心人体骨（日文名）。用于计算标准骨覆盖率，
# 判断模型是否遵循 MMD 通用骨架约定。
MMD_CORE_BONES = [
    "全ての親", "センター", "グルーブ", "腰",
    "上半身", "上半身2", "首", "頭",
    "左肩", "左腕", "左ひじ", "左手首",
    "右肩", "右腕", "右ひじ", "右手首",
    "下半身",
    "左足", "左ひざ", "左足首", "左つま先",
    "右足", "右ひざ", "右足首", "右つま先",
]

# 关键人体链：用于测量骨段长度比例，跨模型归一化后比较体型
HUMAN_CHAINS = {
    "spine": ["センター", "腰", "上半身", "上半身2", "首", "頭"],
    "arm_l": ["左肩", "左腕", "左ひじ", "左手首"],
    "arm_r": ["右肩", "右腕", "右ひじ", "右手首"],
    "leg_l": ["左足", "左ひざ", "左足首"],
    "leg_r": ["右足", "右ひざ", "右足首"],
}

# 左右配对前缀，用于对称性检查
LR_PAIRS = [("左", "右")]


def log(msg):
    print(msg, flush=True)


def vec3(v):
    return [round(float(v[0]), 6), round(float(v[1]), 6), round(float(v[2]), 6)]


def classify_bone(name):
    """按命名约定粗分骨骼用途，区分 deform 与控制/物理/辅助层。"""
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


def main():
    pmx_path = os.environ.get("MOE_PMX_PATH")
    report_path = os.environ.get("MOE_PMX_REPORT")
    if not pmx_path or not report_path:
        log("!! 需要环境变量 MOE_PMX_PATH 和 MOE_PMX_REPORT")
        sys.exit(2)

    pmx = Path(pmx_path)
    out = Path(report_path)
    if not pmx.is_file():
        log(f"!! PMX 不存在: {pmx}")
        sys.exit(2)

    report = {
        "source_file": str(pmx),
        "source_name": pmx.name,
        "source_dir": pmx.parent.name,
        "file_size_bytes": pmx.stat().st_size,
        "import_ok": False,
    }

    log("=" * 70)
    log(f"导入 PMX: {pmx.name}")
    log("=" * 70)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)

    try:
        bpy.ops.mmd_tools.import_model(
            filepath=str(pmx),
            types={'MESH', 'ARMATURE', 'MORPHS'},
            scale=1.0,
            clean_model=False,
            remove_doubles=False,
            fix_bone_order=False,      # 保持原始 PMX 骨序
            rename_bones=False,        # 保持原始日文骨名，比较的前提
            fix_ik_links=False,
            apply_bone_fixed_axis=False,
            log_level='ERROR',
        )
        report["import_ok"] = True
        log("  导入成功")
    except Exception as e:
        report["import_error"] = f"{type(e).__name__}: {e}"
        report["import_traceback"] = traceback.format_exc()
        log(f"!! 导入失败: {e}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(1)

    # ---------------------------------------------------------------- MMD 根信息
    mmd_root = None
    for o in bpy.data.objects:
        if getattr(o, "mmd_type", None) == 'ROOT':
            mmd_root = o
            break
    if mmd_root is not None and hasattr(mmd_root, "mmd_root"):
        report["mmd_model_name"] = getattr(mmd_root.mmd_root, "name", "") or ""
        report["mmd_model_name_e"] = getattr(mmd_root.mmd_root, "name_e", "") or ""
        report["mmd_comment"] = (getattr(mmd_root.mmd_root, "comment_text", "") or "")[:400]

    # ---------------------------------------------------------------- 场景统计
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    armatures = [o for o in bpy.data.objects if o.type == 'ARMATURE']
    report["counts"] = {
        "objects": len(bpy.data.objects),
        "mesh_objects": len(meshes),
        "mesh_datablocks": len(bpy.data.meshes),
        "armature_objects": len(armatures),
        "materials": len(bpy.data.materials),
        "images": len(bpy.data.images),
        "actions": len(bpy.data.actions),
    }
    log(f"  对象={len(bpy.data.objects)} 网格={len(meshes)} 骨架={len(armatures)} "
        f"材质={len(bpy.data.materials)} 贴图={len(bpy.data.images)}")

    total_verts = sum(len(o.data.vertices) for o in meshes)
    total_polys = sum(len(o.data.polygons) for o in meshes)
    report["geometry"] = {
        "total_vertices": total_verts,
        "total_polygons": total_polys,
        "per_mesh": sorted(
            [
                {
                    "name": o.name,
                    "vertices": len(o.data.vertices),
                    "polygons": len(o.data.polygons),
                    "materials": [m.name for m in o.data.materials if m],
                    "uv_layers": [u.name for u in o.data.uv_layers],
                    "vertex_groups": len(o.vertex_groups),
                    "shape_keys": (len(o.data.shape_keys.key_blocks)
                                   if o.data.shape_keys else 0),
                }
                for o in meshes
            ],
            key=lambda d: -d["polygons"],
        )[:60],
    }
    log(f"  顶点总计={total_verts} 面总计={total_polys}")

    # ---------------------------------------------------------------- 材质与贴图
    report["materials"] = [
        {
            "name": m.name,
            "images": sorted({
                n.image.name for n in (m.node_tree.nodes if m.node_tree else [])
                if n.type == 'TEX_IMAGE' and n.image
            }),
        }
        for m in bpy.data.materials
    ][:80]
    report["images"] = [
        {"name": i.name, "size": list(i.size), "filepath": i.filepath}
        for i in bpy.data.images
    ][:80]

    # ---------------------------------------------------------------- 骨架详情
    arm_reports = []
    for arm_obj in armatures:
        arm = arm_obj.data
        bones = list(arm.bones)
        names = [b.name for b in bones]
        name_set = set(names)

        # 分类统计
        by_class = {}
        for b in bones:
            c = classify_bone(b.name)
            by_class.setdefault(c, []).append(b.name)

        # 骨骼明细
        bone_details = []
        for b in bones:
            depth = 0
            p = b.parent
            while p:
                depth += 1
                p = p.parent
            head = arm_obj.matrix_world @ b.head_local
            tail = arm_obj.matrix_world @ b.tail_local
            bone_details.append({
                "name": b.name,
                "parent": b.parent.name if b.parent else None,
                "depth": depth,
                "head": vec3(head),
                "tail": vec3(tail),
                "length": round(float(b.length), 6),
                "class": classify_bone(b.name),
                "children": [c.name for c in b.children],
                "use_connect": bool(b.use_connect),
                "use_deform": bool(b.use_deform),
            })

        # MMD 标准骨覆盖
        present = [n for n in MMD_CORE_BONES if n in name_set]
        missing = [n for n in MMD_CORE_BONES if n not in name_set]

        # 左右对称率
        pair_found, pair_missing = [], []
        for lp, rp in LR_PAIRS:
            for n in names:
                if n.startswith(lp):
                    counterpart = rp + n[len(lp):]
                    (pair_found if counterpart in name_set else pair_missing).append(n)

        # 关键链骨段长度（用于跨模型归一化比较）
        chains = {}
        for cname, seq in HUMAN_CHAINS.items():
            got = [n for n in seq if n in name_set]
            segs = []
            for a, b in zip(got, got[1:]):
                ba = arm.bones[a]
                bb = arm.bones[b]
                pa = arm_obj.matrix_world @ ba.head_local
                pb = arm_obj.matrix_world @ bb.head_local
                segs.append({"from": a, "to": b, "dist": round((pb - pa).length, 6)})
            chains[cname] = {
                "expected": seq,
                "present": got,
                "coverage": round(len(got) / len(seq), 4),
                "segments": segs,
            }

        # 身高参考：全部骨骼 head/tail 的 Z 跨度
        zs = [p for b in bones for p in
              (( arm_obj.matrix_world @ b.head_local).z,
               ( arm_obj.matrix_world @ b.tail_local).z)]
        z_span = (max(zs) - min(zs)) if zs else 0.0

        # 蒙皮权重覆盖：哪些骨骼真的被顶点组使用
        used_groups = set()
        for o in meshes:
            vg_names = {vg.index: vg.name for vg in o.vertex_groups}
            for v in o.data.vertices:
                for g in v.groups:
                    if g.weight > 0.0001:
                        nm = vg_names.get(g.group)
                        if nm:
                            used_groups.add(nm)
        skinned = sorted(name_set & used_groups)
        unskinned = sorted(name_set - used_groups)

        arm_reports.append({
            "armature_object": arm_obj.name,
            "armature_data": arm.name,
            "bone_count": len(bones),
            "root_bones": [b.name for b in bones if b.parent is None],
            "max_depth": max((d["depth"] for d in bone_details), default=0),
            "z_span": round(z_span, 6),
            "class_counts": {k: len(v) for k, v in sorted(by_class.items())},
            "mmd_core_coverage": {
                "present_count": len(present),
                "total": len(MMD_CORE_BONES),
                "ratio": round(len(present) / len(MMD_CORE_BONES), 4),
                "present": present,
                "missing": missing,
            },
            "symmetry": {
                "left_bones_with_pair": len(pair_found),
                "left_bones_without_pair": len(pair_missing),
                "unpaired_examples": pair_missing[:20],
            },
            "human_chains": chains,
            "skin_coverage": {
                "bones_with_weights": len(skinned),
                "bones_without_weights": len(unskinned),
                "ratio": round(len(skinned) / len(bones), 4) if bones else 0,
                "unskinned_examples": unskinned[:40],
            },
            "bones": bone_details,
        })

        log(f"  骨架 {arm_obj.name}: {len(bones)} 骨, 深度 {max((d['depth'] for d in bone_details), default=0)}, "
            f"Z跨度 {z_span:.3f}")
        log(f"    MMD 核心骨 {len(present)}/{len(MMD_CORE_BONES)}  "
            f"蒙皮骨 {len(skinned)}/{len(bones)}")
        log(f"    分类: {dict((k, len(v)) for k, v in sorted(by_class.items()))}")

    report["armatures"] = arm_reports

    # ---------------------------------------------------------------- 形态键
    morphs = {}
    for o in meshes:
        if o.data.shape_keys:
            kb = [k.name for k in o.data.shape_keys.key_blocks]
            if len(kb) > 1:
                morphs[o.name] = kb
    report["shape_keys"] = morphs

    # ---------------------------------------------------------------- 刚体/关节
    rigid, joints = [], []
    for o in bpy.data.objects:
        t = getattr(o, "mmd_type", None)
        if t == 'RIGID_BODY':
            rigid.append(o.name)
        elif t == 'JOINT':
            joints.append(o.name)
    report["physics"] = {
        "rigid_body_count": len(rigid),
        "joint_count": len(joints),
        "rigid_body_examples": rigid[:30],
        "joint_examples": joints[:30],
    }
    log(f"  刚体={len(rigid)} 关节={len(joints)} 带形态键网格={len(morphs)}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n  报告已写入: {out}")
    log("[PMX INVENTORY DONE]")


main()
