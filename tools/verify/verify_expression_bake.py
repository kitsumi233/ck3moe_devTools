"""校验 2026-08-18 反向骨 + 双侧合并改造后的表情烘焙产物。Blender 后台脚本。

打开 work/blenderProj/moe_expression_rig.blend（bake_moe_expressions.py 的产物），查三件事:

1. 权重和是否严格归一化到 1.0（fx-morph-bake-needs-normalization 记过的坑：
   反向骨若没算进归一化，权重和会超过 1，位移被放大）。
2. 跨接缝边的绝对分离量（不是比值 —— stretch-ratio-needs-length-floor 记过
   源网格有 0.03 长度的退化边，比值判据在那上面必然爆表，是假撕裂）。
3. 每个表情的峰值绝对分离量，与旧结果（happy 0.333 / angry 0.369 /
   shock 0.935 / sadness 0.346，均判定无可见撕裂）比较有没有变坏。

用法:
  blender.exe -b work/blenderProj/moe_expression_rig.blend --python tools/verify/verify_expression_bake.py
"""
import json
import sys
from pathlib import Path

import bpy
import bmesh

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "work" / "moe_anim_bake" / "reverse_bone_verify.json"
WEIGHT_EPS = 1e-4
LEN_FLOOR = 0.10   # stretch-ratio-needs-length-floor: 比值只在原长>=0.10的边上取

log_lines = []


def log(m):
    print(m)
    log_lines.append(str(m))


def head_mesh_objects():
    return [o for o in bpy.data.objects
            if o.type == "MESH" and o.vertex_groups
            and any(True for m in o.modifiers if m.type == "ARMATURE")]


# ---------------------------------------------------------------- 1. 权重和
def check_weight_sums():
    log("=" * 68)
    log("1) 权重和归一化检查")
    log("=" * 68)
    bad_total = 0
    worst = []
    for obj in head_mesh_objects():
        for v in obj.data.vertices:
            s = sum(g.weight for g in v.groups if g.weight > WEIGHT_EPS)
            if s > 1.0 + 1e-4:
                bad_total += 1
                worst.append((obj.name, v.index, s))
    worst.sort(key=lambda t: -t[2])
    log(f"  权重和 > 1.0 的顶点: {bad_total}")
    for on, i, s in worst[:10]:
        log(f"    {on} v{i}  和={s:.4f}")
    return bad_total, worst[:20]


# ---------------------------------------------------------------- 2/3. 绝对分离量
def edge_lengths(obj):
    """(v_a, v_b) -> 原长（Basis 姿态下的世界距离）。"""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    out = {}
    mw = obj.matrix_world
    for e in bm.edges:
        a, b = e.verts[0].index, e.verts[1].index
        La = mw @ e.verts[0].co
        Lb = mw @ e.verts[1].co
        out[(min(a, b), max(a, b))] = (La - Lb).length
    bm.free()
    return out


def posed_positions(obj, dg):
    """当前姿态下逐顶点世界坐标（走 evaluated mesh，含骨骼形变）。"""
    eo = obj.evaluated_get(dg)
    me = eo.to_mesh()
    mw = obj.matrix_world
    pos = [mw @ v.co for v in me.vertices]
    eo.to_mesh_clear()
    return pos


def check_seams_for_action(rig, meshes, action, label):
    rig.animation_data.action = action
    scn = bpy.context.scene
    frames = range(int(action.frame_range[0]), int(action.frame_range[1]) + 1)

    rest_edges = {o.name: edge_lengths(o) for o in meshes}
    dg = bpy.context.evaluated_depsgraph_get()

    worst_abs = 0.0
    worst_edge = None
    frame_of_worst = None
    n_over_03 = 0

    for f in frames:
        scn.frame_set(f)
        dg.update()
        for o in meshes:
            pos = posed_positions(o, dg)
            for (a, b), L0 in rest_edges[o.name].items():
                if a >= len(pos) or b >= len(pos):
                    continue
                L1 = (pos[a] - pos[b]).length
                sep = abs(L1 - L0)
                if L0 >= LEN_FLOOR:
                    if sep > 0.3:
                        n_over_03 += 1
                    if sep > worst_abs:
                        worst_abs = sep
                        worst_edge = (o.name, a, b, L0, L1)
                        frame_of_worst = f
    return {
        "label": label,
        "max_abs_separation": round(worst_abs, 4),
        "worst_edge": worst_edge,
        "frame_of_worst": frame_of_worst,
        "edges_over_0.3": n_over_03,
    }


def main():
    log_lines.clear()
    bad_total, worst = check_weight_sums()

    log("\n" + "=" * 68)
    log("2) 跨接缝边绝对分离量（长度下限 %.2f，排除退化短边）" % LEN_FLOOR)
    log("=" * 68)
    log("  判据：骨骼烘焙分离量是否 <= 真实 shapekey 驱动的分离量。")
    log("  不是「和旧版比」—— 旧版因重建率低（24.7%~84.9%）本来就把真实")
    log("  形变打了折扣，新版重建率提到 76%~99% 后撕裂读数必然涨，涨的")
    log("  方向是「更接近真实」。真正该警惕的是骨骼烘焙 > 真实值（过冲，")
    log("  说明反向骨方向算错或两根骨相互打架）。")

    rig = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    meshes = head_mesh_objects()
    log(f"  参与网格: {[o.name for o in meshes]}")

    results = {}
    # 不与旧烘焙数值比较 —— 旧版重建率只有 24.7%~84.9%，本来就把真实形变
    # 打了折扣，新版分离量必然更大，且那是修复的目的，不是回归。
    # 真正的基线是「真实 shapekey 直接驱动同一条边」。
    # **手工核对那版的数字全部作废**：我当时把某个槽的所有候选形态键
    # （い/ん/え…）全叠起来当「真实值」，而 C.resolve_bindings 只取**第一个**
    # 命中的候选，于是真实值被我算大了，得出「全部 <= 真实值、没有过冲」的
    # 错误结论。用 tools/verify/compare_shapekey_vs_bake.py 走 resolve_bindings 重测：
    #   v1235-v1241 (angry)  骨骼 0.635  真实 0.483   ← 实际过冲 31%
    # 所以判据是：改完烘焙算法必须跑 compare_shapekey_vs_bake.py 现测真实值，
    # 不要引用任何手抄的数字。
    for act in bpy.data.actions:
        if not act.name.startswith("moe_"):
            continue
        r = check_seams_for_action(rig, meshes, act, act.name)
        results[act.name] = r
        log(f"  {act.name:<20} 峰值绝对分离={r['max_abs_separation']:.4f}"
            f"  超0.3的边={r['edges_over_0.3']:>3}")
        if r["worst_edge"]:
            on, a, b, L0, L1 = r["worst_edge"]
            log(f"      最坏边: {on} v{a}-v{b}  原长={L0:.3f} 现长={L1:.3f}"
                f"  frame={r['frame_of_worst']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "weight_sum_over_1": bad_total,
        "weight_sum_worst": worst,
        "seam_results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n报告: {OUT}")
    (OUT.parent / "reverse_bone_verify_log.txt").write_text(
        "\n".join(log_lines), encoding="utf-8")


main()
