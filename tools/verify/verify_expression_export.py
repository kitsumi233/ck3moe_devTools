"""Compare exported expression geometry with native Blender shape-key targets.

Run with Blender, not system Python::

    blender -b --python-exit-code 1 --python tools/verify/verify_expression_export.py -- \
        --ck jia --bake-dir work/jia_anim_bake \
        --blend work/blenderProj/jia_expression_rig.blend --out report.json

Only the JSON report is written. Geometry and weights are read from the actual
.mesh, transforms from the actual .anim, and targets from native shape keys.
Exporter geometry/vertex IDs are used solely to establish correspondence; every
position and triangle must match the binary before the correspondence is used.
No weights are normalized or discarded by this verifier. Native eye-gaze bones
and body motion are held at mesh bind pose on both sides to isolate expressions.
The report is a geometric measurement, not a CK3 screenshot or visual acceptance.
"""
import argparse
import hashlib
import importlib
import json
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Quaternion

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools/lib")]
import moe_chars as X
import moe_expression_contract as C
from moe_expression_closure import native_blink_contacts
import pdx_parse as P

SPACE = np.eye(4)[[0, 2, 1, 3]]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def need(condition, message):
    if not condition:
        raise ValueError(message)


def read_mesh(path):
    grouped = {}
    for node, key, _kind, value in P.walk(P.parse(Path(path).read_bytes())):
        grouped.setdefault(node, {})[key] = value
    bones = []
    for node, values in grouped.items():
        if "/skeleton/" in node and "ix" in values and "tx" in values:
            tx = np.eye(4)
            tx[:3] = np.asarray(values["tx"], float).reshape(4, 3).T
            bones.append(dict(name=node.rsplit("/", 1)[1],
                              index=values["ix"][0], parent=values.get("pa", [-1])[0],
                              inverse_bind=tx))
    bones.sort(key=lambda b: b["index"])
    need([b["index"] for b in bones] == list(range(len(bones))), "Non-contiguous skeleton indices")
    need(len({b["name"] for b in bones}) == len(bones), "Duplicate skeleton names")
    parts = []
    for node, values in grouped.items():
        if "p" not in values or "tri" not in values:
            continue
        sk = grouped.get(node + "/skin", {})
        width = sk.get("bones", [0])[0]
        pos = np.asarray(values["p"], float).reshape(-1, 3)
        need(0 < width <= 4, f"{node}: missing skin or more than four influence columns")
        parts.append(dict(node=node, p=pos, tri=np.asarray(values["tri"], int),
                          ix=np.asarray(sk["ix"], int).reshape(len(pos), width),
                          w=np.asarray(sk["w"], float).reshape(len(pos), width)))
    need(bones and parts, "No skeleton or skinned geometry found")
    return bones, parts


def read_animation(path, mesh_names):
    tree = P.parse(Path(path).read_bytes())
    nodes = {}
    for node, key, _kind, value in P.walk(tree):
        nodes.setdefault(node, {})[key] = value
    meta = nodes["/info"]
    frames, fps = int(meta["sa"][0]), float(meta["fps"][0])
    rows = [(p[len("/info/"):], v) for p, v in nodes.items()
            if p.startswith("/info/") and p.count("/") == 2]
    need([n for n, _ in rows] == mesh_names, f"{path.name}: animation/mesh bone order mismatch")
    need(meta["j"][0] == len(rows), f"{path.name}: incorrect joint count")
    need(frames > 0 and fps > 0, f"{path.name}: invalid timing")
    channels = {}
    quat_error = 0.
    for channel, width, default in (("t", 3, [0., 0., 0.]),
                                     ("q", 4, [0., 0., 0., 1.]), ("s", 1, [1.])):
        # Export source animations use uniform scale; reject unsupported scale
        # encodings explicitly instead of ignoring a channel.
        base = np.asarray([v.get(channel, default) for _, v in rows], float)
        if channel == "s" and base.ndim == 2 and base.shape[1] == 3:
            need(np.max(np.ptp(base, axis=1)) < 1e-6, "Nonuniform animation scale")
            base = base[:, :1]
        need(base.shape == (len(rows), width), f"Unsupported {channel} width")
        data = np.broadcast_to(base, (frames,) + base.shape).copy()
        animated = [i for i, (_, v) in enumerate(rows) if channel in (v.get("sa") or [""])[0]]
        samples = np.asarray(nodes.get("/samples", {}).get(channel, []), float)
        need(len(samples) == frames * len(animated) * width,
             f"{path.name}: wrong {channel} sample count")
        if animated:
            data[:, animated] = samples.reshape(frames, len(animated), width)
        need(np.isfinite(data).all(), f"{path.name}: nonfinite {channel} values")
        if channel == "q":
            quat_error = float(np.max(np.abs(np.linalg.norm(data, axis=2) - 1)))
            need(quat_error <= 1e-4, f"{path.name}: nonunit quaternions ({quat_error})")
        channels[channel] = data
    return dict(frames=frames, fps=fps, quaternion_norm_error_max=quat_error, channels=channels)


def transforms(animation, frame, bones, bind_local, isolated=True):
    matrices = []
    for i, bone in enumerate(bones):
        if isolated and not bone["name"].startswith("bn_m_fx_"):
            local = bind_local[i]
        else:
            q = animation["channels"]["q"][frame, i]
            local = np.asarray(Quaternion((q[3], *q[:3])).to_matrix().to_4x4(), float)
            local[:3, :3] *= animation["channels"]["s"][frame, i, 0]
            local[:3, 3] = animation["channels"]["t"][frame, i]
        parent = bone["parent"]
        need(parent < i, "Skeleton must be parent-before-child")
        matrices.append(matrices[parent] @ local if parent >= 0 else local)
    return np.asarray(matrices)


def skin(parts, matrices, inverse_bind):
    deform = matrices @ inverse_bind
    output = []
    for part in parts:
        points = np.column_stack((part["p"], np.ones(len(part["p"]))))
        result = np.zeros_like(part["p"])
        for col in range(part["ix"].shape[1]):
            index, weight = part["ix"][:, col], part["w"][:, col]
            valid = weight > 0
            moved = np.einsum("nij,nj->ni", deform[index[valid]], points[valid])[:, :3]
            result[valid] += moved * weight[valid, None]
        output.append(result)
    return np.concatenate(output)


def evaluated_positions(obj):
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    arr = np.empty(len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", arr)
    evaluated.to_mesh_clear()
    points = np.column_stack((arr.reshape(-1, 3), np.ones(len(arr) // 3)))
    return (SPACE @ np.asarray(obj.matrix_world, float) @ points.T).T[:, :3]


def measure(actual, expected, mask):
    a, e = actual[mask], expected[mask]
    if not len(e):
        return {"vertices": 0}
    en, an = np.linalg.norm(e, axis=1), np.linalg.norm(a, axis=1)
    err = np.linalg.norm(a - e, axis=1)
    energy = float(np.sum(en ** 2))
    dot = float(np.sum(a * e))
    return dict(vertices=len(e), target_rms=float(np.sqrt(np.mean(en ** 2))),
                actual_rms=float(np.sqrt(np.mean(an ** 2))),
                error_rms=float(np.sqrt(np.mean(err ** 2))),
                normalized_vector_rmse=float(np.sqrt(np.sum(err ** 2) / energy)),
                displacement_magnitude_ratio=float(an.sum() / en.sum()),
                target_direction_projection_ratio=dot / energy,
                vector_cosine=dot / max(float(np.sqrt(np.sum(an ** 2) * energy)), 1e-30),
                error_p95=float(np.quantile(err, .95)), error_max=float(err.max()),
                nearly_static_fraction=float(np.mean(an < en * .1)))


def measure_blink_closure(obj, mapping, source_neutral, actual_delta, expected_delta,
                          native_deltas, bindings):
    """Compare native eye-boundary contacts with the actual exported blink.

    Detection radii select pairs from unit native keys; measured target gaps
    use the actual sampled strengths. They are not release tolerances.
    Each eye's scale is the 5th-to-95th percentile vertical extent
    of vertices moved by its native blink key, in PDX world units. No pair is
    required to coincide exactly: the native nonzero gap remains the target.
    """
    transform = (SPACE @ np.asarray(obj.matrix_world))[:3, :3]
    blink_keys = {bindings.get(slot) for slot in ("eye_blink_l", "eye_blink_r")}
    fields = {key: native_deltas[key] @ transform.T for key in blink_keys if key in native_deltas}
    obj.data.calc_loop_triangles()
    triangles = {}
    for triangle in obj.data.loop_triangles:
        triangles.setdefault(triangle.material_index, []).append(list(triangle.vertices))
    selection = native_blink_contacts(source_neutral, triangles, fields, up_axis=1)
    wanted = np.zeros_like(source_neutral)
    got = np.zeros_like(source_neutral)
    wanted[mapping] = expected_delta
    got[mapping] = actual_delta
    target_points, actual_points = source_neutral + wanted, source_neutral + got
    materials = {row["material_index"]: dict(row, material_name=obj.data.materials[row["material_index"]].name,
                                            pairs=[]) for row in selection["materials"]}
    all_pairs = []
    for pair in selection["pairs"]:
        a, b = pair["vertices"]
        native_vector = target_points[a] - target_points[b]
        actual_vector = actual_points[a] - actual_points[b]
        candidate_gap = float(np.linalg.norm(actual_vector))
        native_gap = float(np.linalg.norm(native_vector))
        candidate_gap_xy = float(np.linalg.norm(actual_vector[:2]))
        native_gap_xy = float(np.linalg.norm(native_vector[:2]))
        row = dict(source_vertices=[a, b], blink_key=pair["blink_key"],
                   native_eye_height=pair["eye_height"], open_gap=pair["open_gap"],
                   detector_unit_native_gap=pair["native_gap"], native_gap=native_gap,
                   candidate_gap=candidate_gap, extra_gap=candidate_gap-native_gap,
                   native_separation_xyz=native_vector.tolist(),
                   candidate_separation_xyz=actual_vector.tolist(),
                   native_gap_xy=native_gap_xy, candidate_gap_xy=candidate_gap_xy,
                   extra_gap_xy=candidate_gap_xy-native_gap_xy,
                   native_vertical_gap=abs(float(native_vector[1])),
                   candidate_vertical_gap=abs(float(actual_vector[1])),
                   extra_vertical_gap=abs(float(actual_vector[1]))-abs(float(native_vector[1])),
                   extra_gap_over_eye_height=(candidate_gap-native_gap) / pair["eye_height"],
                   pair_vector_error=float(np.linalg.norm(actual_vector-native_vector)))
        materials[pair["material_index"]]["pairs"].append(row)
        all_pairs.append(row)
    for material in materials.values():
        material["pairs"].sort(key=lambda row: -row["extra_gap"])
    evidence = dict(status="measured" if selection["status"] == "available" else "unavailable",
                    interpretation="Target-relative local closure measurements, not a visual PASS or universal safety threshold. Duplicate contacts may occur across material seams.",
                    detector="Shared native_blink_contacts: unit-key boundary pairs; native gap <= 0.03 eye height and open gap >= 0.12 eye height. Reported gaps use actual sampled target strength.",
                    eye_height_definition="Native blink affected vertices: PDX Y 95th percentile minus 5th percentile in neutral pose",
                    eye_scales=selection["regions"], pair_count=len(all_pairs),
                    unique_geometric_pairs=selection["unique_geometric_pairs"], materials=list(materials.values()))
    if selection["status"] != "available":
        evidence["reason"] = selection["reason"]
    if all_pairs:
        evidence["extra_gap_max"] = max(row["extra_gap"] for row in all_pairs)
        evidence["extra_gap_p95"] = float(np.quantile([row["extra_gap"] for row in all_pairs], .95))
        evidence["extra_gap_over_eye_height_max"] = max(row["extra_gap_over_eye_height"] for row in all_pairs)
        evidence["pair_vector_error_max"] = max(row["pair_vector_error"] for row in all_pairs)
        evidence["extra_gap_xy_max"] = max(row["extra_gap_xy"] for row in all_pairs)
        evidence["extra_vertical_gap_max"] = max(row["extra_vertical_gap"] for row in all_pairs)
    return evidence


def main(args):
    bake_dir = (args.bake_dir or X.bake_dir(args.ck)).resolve()
    report_path = bake_dir / "bake_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    blend = (args.blend or Path(report["output_blend"])).resolve()
    if not args.blend and not blend.is_file():
        blend = ROOT / "work/blenderProj" / f"{'moe' if args.ck == 'flo' else args.ck}_expression_rig.blend"
    mesh_path = (args.mesh or bake_dir / "moe_head.mesh").resolve()
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    addon = "bl_ext.user_default.io_pdx_mesh"
    bpy.ops.preferences.addon_enable(module=addon)
    exporter = importlib.import_module(addon + ".pdx_blender.blender_import_export")
    obj = bpy.data.objects[args.object]
    rig = next(m.object for m in obj.modifiers if m.type == "ARMATURE")
    keys = obj.data.shape_keys.key_blocks
    if rig.animation_data:
        rig.animation_data.action = None
        for track in rig.animation_data.nla_tracks:
            track.mute = True
    if obj.data.shape_keys.animation_data:
        obj.data.shape_keys.animation_data_clear()

    def clear():
        if rig.animation_data:
            rig.animation_data.action = None
        for bone in rig.pose.bones:
            bone.matrix_basis = Matrix.Identity(4)
        for key in keys:
            key.value = 0.
        bpy.context.view_layer.update()

    clear()
    bones, parts = read_mesh(mesh_path)
    names = [b["name"] for b in bones]
    inverse_bind = np.asarray([b["inverse_bind"] for b in bones])
    bind_world = np.linalg.inv(inverse_bind)
    bind_local = np.asarray([inverse_bind[b["parent"]] @ bind_world[i]
                             if b["parent"] >= 0 else bind_world[i] for i, b in enumerate(bones)])
    mapping, offsets, max_pos_error = [], [], 0.
    part_index = 0
    for material_index in range(len(obj.data.materials)):
        geometry, ids = exporter.get_mesh_info(obj, material_index)
        if not ids:
            continue
        need(part_index < len(parts), "Blend has more material geometry than binary")
        part = parts[part_index]
        generated = np.asarray(geometry["p"], float).reshape(-1, 3)
        need(generated.shape == part["p"].shape, "Source/binary vertex count differs")
        delta = float(np.max(np.linalg.norm(generated - part["p"], axis=1)))
        need(delta <= args.mapping_tolerance, f"Source/binary positions differ: {delta}")
        need(np.array_equal(geometry["tri"], part["tri"]), "Source/binary triangle mapping differs")
        max_pos_error = max(max_pos_error, delta)
        offsets.append((len(mapping), len(mapping) + len(ids), material_index))
        mapping.extend(ids)
        part_index += 1
    need(part_index == len(parts), "Binary has unmapped material geometry")
    mapping = np.asarray(mapping, int)
    neutral = evaluated_positions(obj)
    exported_neutral = np.concatenate([p["p"] for p in parts])
    source_error = float(np.max(np.linalg.norm(neutral[mapping] - exported_neutral, axis=1)))
    need(source_error <= args.mapping_tolerance, f"Native neutral differs from binary: {source_error}")
    source_bind = np.asarray([SPACE @ np.asarray(rig.matrix_world @ rig.data.bones[n].matrix_local) @ SPACE
                              for n in names])
    bind_error = float(np.max(np.abs(source_bind - bind_world)))
    need(bind_error < .002, f"Blend/binary bone bind matrices differ: {bind_error}")
    max_weight_error = 0.
    for part in parts:
        need(np.isfinite(part["p"]).all() and np.isfinite(part["w"]).all(), "Nonfinite mesh values")
        need(np.min(part["w"]) >= 0, "Negative skin weights")
        active = part["w"] > 0
        need(np.all((part["ix"][active] >= 0) & (part["ix"][active] < len(bones))), "Invalid weighted bone indices")
        err = float(np.max(np.abs(part["w"].sum(axis=1) - 1)))
        max_weight_error = max(max_weight_error, err)
    need(max_weight_error <= 1e-5, f"Weights do not sum to one: {max_weight_error}")
    bind_skin_error = float(np.max(np.linalg.norm(skin(parts, bind_world, inverse_bind) - exported_neutral, axis=1)))
    need(bind_skin_error < args.mapping_tolerance, f"Bind LBS reconstruction failed: {bind_skin_error}")

    masks = {name: np.zeros(len(neutral), bool) for name in ("decal", "eyelid", "brow", "mouth")}
    raw_basis = np.array([v.co[:] for v in keys[0].data])
    native_deltas = {key.name: np.array([v.co[:] for v in key.data]) - raw_basis for key in keys}
    for slot, name in report["bindings"].items():
        if name not in keys:
            continue
        category = "decal" if slot.startswith("decal_") else "eyelid" if slot.startswith("eye_") else "brow" if slot.startswith("brow_") else "mouth" if slot.startswith("mouth_") else None
        if category:
            mask = np.linalg.norm(native_deltas[name], axis=1) > 1e-5
            masks[category] |= mask
    masks = {name: mask[mapping] for name, mask in masks.items()}
    curves_snapshot = json.loads(args.curves.read_text(encoding="utf-8-sig")) if args.curves else None
    result = dict(character=args.ck, status="measured", scope=__doc__,
                  space="PDX world/model units: X right, Y up, Z depth; non-fx bones at mesh bind pose",
                  sources={"blend": str(blend), "mesh": str(mesh_path), "bake_report": str(report_path)},
                  hashes={"blend": sha(blend), "mesh": sha(mesh_path), "bake_report": sha(report_path),
                          "contract": report.get("expression_contract_sha256") or (curves_snapshot or {}).get("contract_sha256") or sha(C.__file__)},
                  mapping=dict(method="exporter vertex IDs after exact binary position/triangle cross-check",
                               exported_vertices=len(mapping), source_vertices=len(neutral),
                               source_vertices_represented=len(set(mapping.tolist())),
                               position_error_max=max_pos_error, native_neutral_error_max=source_error,
                               bone_bind_matrix_error_max=bind_error, bind_lbs_error_max=bind_skin_error),
                  weights=dict(max_influences=max(p["ix"].shape[1] for p in parts),
                               sum_error_max=max_weight_error, normalized_by_verifier=False), expressions=[])
    for expression in args.expressions.split(","):
        info = report["baked"][expression]
        anim_path = bake_dir / f"moe_{expression}.anim"
        animation = read_animation(anim_path, names)
        target_curves = report.get("sampled_expression_targets", {}).get(expression)
        if target_curves is not None:
            target_source = "bake_report.sampled_expression_targets"
        else:
            slots = (curves_snapshot or {}).get("expressions", {}).get(expression)
            if slots is None:
                slots = C.sample_expression(expression, frames=info["frames"])
            need(len(slots) == info["frames"], "Frozen curve/source animation frame count mismatch")
            slots, _ = C.substitute_missing(slots, report["missing_slots"])
            target_curves = []
            for values in slots:
                targets = {}
                for slot, value in values.items():
                    if slot in report["weights"] and slot in report["bindings"]:
                        name = report["bindings"][slot]
                        targets[name] = max(targets.get(name, 0.), value * report.get("slot_gain", {}).get(slot, 1.))
                target_curves.append(targets)
            target_source = "frozen_contract_curves" if curves_snapshot else "current_contract_curves"
        need(len(target_curves) == animation["frames"], "Targets and binary animation frame count mismatch")
        # Score native facial target energy, not bone motion or the bake formula.
        target_positions = []
        energies = []
        for targets in target_curves:
            delta = np.zeros((len(neutral), 3))
            for name, value in targets.items():
                need(name in keys, f"Target shape key missing: {name}")
                delta += native_deltas[name] * value
            transformed = delta @ (SPACE @ np.asarray(obj.matrix_world))[:3, :3].T
            target_positions.append(transformed[mapping])
            energies.append(float(np.sum(transformed[mapping][~masks["decal"]] ** 2)))
        peak = int(np.argmax(energies))
        frames = list(range(animation["frames"])) if args.frame_mode == "all" else [peak]
        rows = []
        # Direct shape-key evaluation cross-checks the vector target construction.
        clear()
        for name, value in target_curves[peak].items():
            keys[name].mute = False
            keys[name].slider_max = max(keys[name].slider_max, value)
            keys[name].value = value
        evaluated_target = (evaluated_positions(obj) - neutral)[mapping]
        target_error = float(np.max(np.linalg.norm(evaluated_target - target_positions[peak], axis=1)))
        need(target_error < args.mapping_tolerance, f"Native shape-key target evaluation mismatch: {target_error}")
        clear()
        for frame in frames:
            actual = skin(parts, transforms(animation, frame, bones, bind_local), inverse_bind) - exported_neutral
            expected = target_positions[frame]
            moving = np.linalg.norm(expected, axis=1) > args.moving_floor
            row = dict(frame=frame + 1, targets=target_curves[frame],
                       facial=measure(actual, expected, moving & ~masks["decal"]),
                       unintended_motion_max=float(np.linalg.norm(actual[~moving], axis=1).max()) if (~moving).any() else 0.)
            for category, mask in masks.items():
                row[category] = measure(actual, expected, moving & mask & (masks["decal"] if category == "decal" else ~masks["decal"]))
            if expression == "life" and frame == peak:
                row["blink_closure"] = measure_blink_closure(
                    obj, mapping, neutral, actual, expected, native_deltas,
                    report["bindings"])
            rows.append(row)
        item = dict(expression=expression, animation=str(anim_path), sha256=sha(anim_path),
                    frames=animation["frames"], fps=animation["fps"], target_source=target_source,
                    quaternion_norm_error_max=animation["quaternion_norm_error_max"],
                    native_target_crosscheck_error_max=target_error, peak_frame=peak + 1, measurements=rows)
        result["expressions"].append(item)
        print(args.ck, expression, json.dumps(next(r for r in rows if r["frame"] == peak + 1)["facial"]), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ck", required=True, choices=sorted(X.BY_CK))
    parser.add_argument("--bake-dir", type=Path)
    parser.add_argument("--blend", type=Path)
    parser.add_argument("--mesh", type=Path)
    parser.add_argument("--object", default="moe_head")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--curves", type=Path, help="Frozen old contract sample curves, only for legacy bake reports")
    parser.add_argument("--expressions", default="happy,angry,shock,sadness,talk,life")
    parser.add_argument("--frame-mode", choices=("peak", "all"), default="peak")
    parser.add_argument("--mapping-tolerance", type=float, default=2e-4)
    parser.add_argument("--moving-floor", type=float, default=1e-4)
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = parser.parse_args(argv)
    try:
        result = main(args)
    except Exception as exc:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"character": args.ck, "status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Export expression measurement written:", args.out, flush=True)
