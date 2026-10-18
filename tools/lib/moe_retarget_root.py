"""Shared read-only body-root contract for unsimulated retargeted animations.

Compare every frame with the source selected by Catalog('torso').bases. The
orientation contract is q_source_world * inverse(q_standing_world) *
q_template_world, using female_body_idle.anim's stored standing pose and the
character's actual moe_torso_idle_calm.anim template. This detects both missing
motion and missing constant placement; quaternion signs are immaterial.

Examples:
    python tools/verify/verify_retarget_root.py --characters mab lec chs grz
    python tools/verify/verify_retarget_root.py --output work/root_contract.json
    python tools/verify/verify_retarget_root.py --characters jia --actions dance_spin \
        --input-dir work/example --output work/example/root_contract.json

Animation, mesh, and cache files are never changed. Only --output is written.
This is a source orientation contract, not a physics or visual quality verdict.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / part) for part in ("lib", "anim")]
import moe_anim_catalog as C
import moe_chars as X
import retarget_vanilla_anim as R


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def normalize(q):
    q = np.asarray(q, dtype=np.float64)
    length = np.linalg.norm(q, axis=-1, keepdims=True)
    if not np.isfinite(q).all() or not np.isfinite(length).all() or (length < 1e-12).any():
        raise ValueError("Nonfinite or zero quaternion")
    return q / length


def multiply(a, b):
    a, b = np.broadcast_arrays(a, b)
    av, bv = a[..., :3], b[..., :3]
    aw, bw = a[..., 3:], b[..., 3:]
    return normalize(np.concatenate((aw * bv + bw * av + np.cross(av, bv),
                                     aw * bw - np.sum(av * bv, axis=-1, keepdims=True)), axis=-1))


def conjugate(q):
    return np.asarray(q) * np.array([-1., -1., -1., 1.])


def angles(a, b):
    """Shortest orientation distance in degrees, invariant to either sign."""
    dot = np.sum(normalize(a) * normalize(b), axis=-1)
    return np.degrees(2 * np.arccos(np.clip(np.abs(dot), 0., 1.)))


def bare(name):
    return name.rsplit(":", 1)[-1]


class Clip:
    def __init__(self, path):
        self.path = Path(path)
        before = self.path.stat()
        self.meta, self.bones, samples = R.load_anim(self.path)
        self.sha256 = digest(self.path)
        after = self.path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f"Input changed during read: {path}")
        self.frames = int(self.meta["sa"][0])
        self.fps = float(self.meta["fps"][0])
        if self.frames <= 0 or not math.isfinite(self.fps) or self.fps <= 0:
            raise ValueError(f"Invalid timing in {path}")
        self.by_name = {}
        self.indices = {}
        for i, bone in enumerate(self.bones):
            name = bare(bone["name"])
            if name in self.by_name:
                raise ValueError(f"Ambiguous bone suffix {name!r}: {path}")
            self.by_name[name] = bone
            self.indices[name] = i
        q_names = [bare(b["name"]) for b in self.bones if "q" in b["sa"]]
        flat = np.asarray(samples.get("q", []), dtype=np.float64)
        if flat.size != self.frames * len(q_names) * 4:
            raise ValueError(f"Invalid quaternion sample layout: {path}")
        values = flat.reshape(self.frames, len(q_names), 4)
        # Retain only the root chain values on demand; no full unpacked t/q lists.
        self.samples = {name: values[:, i, :] for i, name in enumerate(q_names)}

    def local(self, name, rest=False):
        bone = self.by_name[name]
        if not rest and name in self.samples:
            return normalize(self.samples[name])
        q = normalize(bone["q"])
        return q if rest else np.broadcast_to(q, (self.frames, 4))

    def world(self, name, parents, rest=False, chain=()):
        if name in chain:
            raise ValueError(f"Cyclic root ancestry: {chain + (name,)}")
        if name not in parents:
            raise ValueError(f"Bone {name!r} absent from mesh hierarchy: {self.path}")
        q = self.local(name, rest)
        parent = parents[name]
        # Some vanilla clips omit ground_joint: the retargeter treats body_root
        # as a root in that case. Unrelated animation-only 'root' is ignored.
        if parent is not None and parent in self.by_name:
            q = multiply(self.world(parent, parents, rest, chain + (name,)), q)
        return q


def parents(path):
    result = {}
    for child, parent in R.mesh_parents(path).items():
        key = bare(child)
        if key in result:
            raise ValueError(f"Ambiguous mesh bone suffix {key!r}: {path}")
        result[key] = bare(parent) if parent else None
    return result


class SourceContract:
    def __init__(self, actions=None):
        self.catalog = C.Catalog("torso")
        if not self.catalog.bases:
            raise ValueError(f"No torso sources resolved under {C.GAME}")
        self.selected = set(self.catalog.bases)
        if actions:
            requested = {a if a.startswith("body_") else "body_" + a for a in actions}
            missing = requested - self.selected
            if missing:
                raise ValueError(f"Actions absent from torso source catalog: {sorted(missing)}")
            self.selected = requested
        self.mesh = C.GAME / "female_body" / "female_body.mesh"
        self.parents = parents(self.mesh)
        self.standing = Clip(C.GAME / "female_body" / "female_body_idle.anim")
        self.standing_q = self.standing.world("body_root", self.parents, rest=True)
        self.rows = {}

    def source(self, action):
        if action not in self.rows:
            folder, name = self.catalog.bases[action]
            clip = Clip(C.GAME / folder / name)
            world = clip.world("body_root", self.parents)
            self.rows[action] = dict(
                path=str(clip.path), sha256=clip.sha256, frames=clip.frames, fps=clip.fps,
                bone=clip.by_name["body_root"]["name"], bone_index=clip.indices["body_root"],
                animated="body_root" in clip.samples, world=world,
                excursion_deg=float(angles(world, world[0]).max()),
                standing_offset_deg=float(angles(world, self.standing_q).max()))
        return self.rows[action]


def check_character(character, contract, input_dir=None, template=None, rest_mesh=None,
                    tolerance_deg=0.01, fps_tolerance=0.0001):
    canonical = X.bake_dir(character)
    input_dir = Path(input_dir) if input_dir else ROOT / "work/base_animation_inputs" / character / "anims"
    template = Path(template) if template else canonical / "moe_torso_idle_calm.anim"
    rest_mesh = Path(rest_mesh) if rest_mesh else canonical / "moe_torso.mesh"
    tpl = Clip(template)
    hierarchy = parents(rest_mesh)
    # Current pipeline requires the literal torso placement bone. An old
    # bn_m_groove-only skeleton must be rebuilt, not silently exempted.
    target_rest = tpl.world("body_root", hierarchy, rest=True)
    parent = hierarchy["body_root"]
    parent_rest = (tpl.world(parent, hierarchy, rest=True) if parent else
                   np.array([0., 0., 0., 1.]))
    correction = multiply(conjugate(contract.standing_q), target_rest)
    prefix = X.BY_CK[character]["prefix"]
    result = dict(character=character, input_dir=str(input_dir), template=str(template),
                  template_sha256=tpl.sha256, rest_mesh=str(rest_mesh),
                  rest_mesh_sha256=digest(rest_mesh), target_bone_index=tpl.indices["body_root"],
                  target_rest_world=target_rest.tolist(), correction=correction.tolist(),
                  rows=[], errors=[])
    for action in sorted(contract.selected):
        candidates = list(dict.fromkeys([input_dir / f"{prefix}{action}.anim",
                                         input_dir / f"moe_{action}.anim"]))
        existing = [p for p in candidates if p.is_file()]
        row = dict(action=action, path=str(existing[0] if existing else candidates[0]))
        try:
            if len(existing) != 1:
                raise ValueError(f"Expected exactly one input file, found {len(existing)}: {candidates}")
            source = contract.source(action)
            row.update(source={k: v for k, v in source.items() if k != "world"})
            actual = Clip(existing[0])
            row.update(sha256=actual.sha256, frames=actual.frames, fps=actual.fps,
                       bone_index=actual.indices.get("body_root"),
                       animated="body_root" in actual.samples)
            failures = []
            if actual.frames != source["frames"]:
                failures.append(f"frames {actual.frames} != source {source['frames']}")
            if abs(actual.fps - source["fps"]) > fps_tolerance:
                failures.append(f"fps {actual.fps:.9g} != source {source['fps']:.9g}")
            if actual.frames == source["frames"]:
                expected_world = multiply(source["world"], correction)
                expected_local = multiply(conjugate(parent_rest), expected_world)
                actual_world = actual.world("body_root", hierarchy)
                local_error = angles(actual.local("body_root"), expected_local)
                world_error = angles(actual_world, expected_world)
                rest_error = float(angles(actual.local("body_root", rest=True), expected_local[0]))
                frame = int(np.argmax(world_error))
                row.update(max_world_error_deg=float(world_error[frame]),
                           max_local_error_deg=float(local_error.max()),
                           rest_error_deg=rest_error,
                           worst_frame=frame,
                           expected_world_q=expected_world[frame].tolist(),
                           actual_world_q=actual_world[frame].tolist(),
                           actual_excursion_deg=float(angles(actual_world, actual_world[0]).max()),
                           mismatched_frames=int(np.count_nonzero(np.maximum(local_error, world_error) > tolerance_deg)))
                if max(row["max_world_error_deg"], row["max_local_error_deg"], rest_error) > tolerance_deg:
                    failures.append(f"body_root error world={row['max_world_error_deg']:.6f}deg "
                                    f"local={row['max_local_error_deg']:.6f}deg "
                                    f"stored_rest={rest_error:.6f}deg at frame {frame}")
            row["status"] = "failed" if failures else "passed"
            if failures:
                row["errors"] = failures
        except (OSError, ValueError, KeyError, AssertionError, IndexError) as error:
            row.update(status="failed", errors=[f"{type(error).__name__}: {error}"])
        result["rows"].append(row)
    result["checked"] = len(result["rows"])
    result["failed"] = sum(row["status"] != "passed" for row in result["rows"])
    result["passed"] = result["checked"] - result["failed"]
    result["status"] = "failed" if result["failed"] else "passed"
    result["mismatched_actions"] = [row["action"] for row in result["rows"] if row["status"] != "passed"]
    result["max_world_error_deg"] = max((row.get("max_world_error_deg", 0.) for row in result["rows"]), default=0.)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--characters", nargs="+", choices=list(X.BY_CK),
                        help="Default: every registered character with an independent base cache")
    parser.add_argument("--actions", nargs="+", help="Catalog action names, with or without body_ prefix")
    parser.add_argument("--input-dir", type=Path, help="One character only; accepts generic or exported filenames")
    parser.add_argument("--template", type=Path, help="One character only; actual neutral retarget template")
    parser.add_argument("--rest-mesh", type=Path, help="One character only; template skeleton mesh")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--tolerance-deg", type=float, default=0.01)
    parser.add_argument("--fps-tolerance", type=float, default=0.0001)
    args = parser.parse_args()
    chars = args.characters or [c for c in X.BY_CK if (ROOT / "work/base_animation_inputs" / c / "anims").is_dir()]
    if not chars:
        parser.error("No independent base caches found")
    if len(chars) != 1 and any((args.input_dir, args.template, args.rest_mesh)):
        parser.error("Path overrides require exactly one character")
    if (not math.isfinite(args.tolerance_deg) or args.tolerance_deg <= 0 or
            not math.isfinite(args.fps_tolerance) or args.fps_tolerance < 0):
        parser.error("Tolerances must be finite and positive (FPS can be zero)")
    contract = SourceContract(args.actions)
    report = dict(schema=1, started_utc=datetime.now(timezone.utc).isoformat(),
                  contract="q_source_world * inverse(q_fixed_standing_world) * q_template_world",
                  scope="Unsimulated body-root orientation and source timing; no visual or physics verdict",
                  tolerance_deg=args.tolerance_deg, fps_tolerance=args.fps_tolerance,
                  standing_source=str(contract.standing.path), standing_sha256=contract.standing.sha256,
                  source_mesh=str(contract.mesh), source_mesh_sha256=digest(contract.mesh),
                  checker_sha256=digest(__file__), characters=[])
    for character in chars:
        try:
            result = check_character(character, contract, args.input_dir, args.template,
                                     args.rest_mesh, args.tolerance_deg, args.fps_tolerance)
        except (OSError, ValueError, KeyError, AssertionError, IndexError) as error:
            result = dict(character=character, status="failed", checked=0, passed=0, failed=1,
                          errors=[f"{type(error).__name__}: {error}"])
        report["characters"].append(result)
        print(f"{character}: {result['status']} {result['passed']}/{result['checked']} "
              f"max_root_error={result.get('max_world_error_deg', 0.):.6f}deg", flush=True)
        if result.get("errors"):
            print("  " + "; ".join(result["errors"]), flush=True)
        # Persist completed characters so a long all-role audit is reviewable.
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report.update(completed_utc=datetime.now(timezone.utc).isoformat(),
                  checked=sum(c["checked"] for c in report["characters"]),
                  failed=sum(c["failed"] for c in report["characters"]))
    report["status"] = "failed" if report["failed"] else "passed"
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{report['status']}: {report['checked']} actions checked, {report['failed']} failed", flush=True)
    return int(bool(report["failed"]))


if __name__ == "__main__":
    raise SystemExit(main())
