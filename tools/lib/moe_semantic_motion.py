"""Opt-in, pre-physics torso motion; head channels must be grafted afterwards.

This is an explicit semantic allowlist, not a rule based on substrings such as
``idle``. Prop contact, complex movement, and unknown clips stay unchanged.
The two additive rotation-vector signals have C2 identity endpoints and only
DC/fundamental/second-harmonic components. This does not claim a band limit on
the original performance, composed quaternions, or the eventual cloth solve.
"""
from __future__ import annotations

import math
import re
import numpy as np

VERSION = "gentle-v1"
# Maximum yaw, maximum side bend, upper-spine counter-rotation, semantic label.
PROFILES = {
    "idle": (1.00, 0.45, -0.35, "quiet alternating waist sway"),
    "AI_rational": (0.50, -0.18, -0.45, "restrained thoughtful lean"),
    "AI_content": (0.65, 0.25, -0.35, "relaxed contentment"),
    "emotion_happy": (1.10, 0.60, -0.25, "soft cheerful shoulder-waist arc"),
    "frontEnd_right": (0.75, -0.30, -0.35, "small presentation sway"),
    "frontEnd_left": (0.75, 0.30, -0.35, "small presentation sway"),
    "frontEnd_middle": (0.65, 0.25, -0.35, "small presentation sway"),
}
SPINES = ("bn_m_spine_low", "bn_m_spine_up")


def loop_uses(action, states, fold):
    """Read actual state cycles; unknown sources do not default to looping.

    A clip can be shared by an entry and a loop. Such an entry necessarily gets
    the same small overlay; identity endpoints protect its boundary pose.
    Dedicated entry/exit clips are outside the semantic allowlist.
    """
    graph = {}
    for name, _, body in states:
        graph.setdefault(name, set()).update(re.findall(r'next_state\s*=\s*"([^"]+)"', body))

    def cyclic(start):
        pending, seen = list(graph.get(start, ())), set()
        while pending:
            name = pending.pop()
            if name == start:
                return True
            if name not in seen:
                seen.add(name)
                pending.extend(graph.get(name, ()))
        return False

    loops, other = set(), set()
    for name, aid, body in states:
        if fold(aid) == action:
            target = loops if cyclic(name) or re.search(r'\blooping\s*=\s*yes\b', body) else other
            target.add(name)
    return dict(loop_states=sorted(loops), other_shared_states=sorted(other))


def normalize(q):
    q = np.asarray(q, dtype=np.float64)
    length = np.linalg.norm(q, axis=-1, keepdims=True)
    if not np.isfinite(q).all() or (length < 1e-12).any():
        raise ValueError("Invalid semantic-motion quaternion")
    return q / length


def multiply(a, b):
    a, b = np.broadcast_arrays(a, b)
    av, bv, aw, bw = a[..., :3], b[..., :3], a[..., 3:], b[..., 3:]
    return normalize(np.concatenate((aw * bv + bw * av + np.cross(av, bv),
                                     aw * bw - np.sum(av * bv, axis=-1, keepdims=True)), axis=-1))


def axes_from_inverse_bind(matrix):
    matrix = np.asarray(matrix, float)
    axes = matrix[:3, :3][:, [1, 2]].T.copy()
    length = np.linalg.norm(axes, axis=1, keepdims=True)
    if not np.isfinite(axes).all() or (length < 1e-12).any():
        raise ValueError("Invalid inverse bind axes")
    return axes / length


def signals(phase):
    """Zero displacement, speed and acceleration at both clip boundaries."""
    phase = np.asarray(phase, dtype=float)
    theta = 2 * np.pi * phase
    # max(abs(sin(x)-sin(2x)/2)) = 3*sqrt(3)/4.
    alternating = (np.sin(theta) - 0.5 * np.sin(2 * theta)) / (3 * np.sqrt(3) / 4)
    arc = (1 - np.cos(theta)) ** 2 / 4
    at_boundary = (phase == 0) | (phase == 1)
    alternating[at_boundary] = 0
    arc[at_boundary] = 0
    return alternating, arc


def apply(action, frames, fps, rest, rotations, bind_axes, *,
          looping, contact_sensitive=False):
    """Return a new rotation dict plus an auditable decision; never mutate input.

    ``bind_axes`` comes from the actual mesh inverse bind, not animation
    frame zero. Axes are converted to each bone's local frame before right-
    multiplying the original local quaternion. Placement/legs are untouched.
    Camera compensation must run after this operation, before head generation.
    """
    if frames < 2 or not math.isfinite(fps) or fps <= 0:
        raise ValueError("Invalid animation duration")
    action = action.removeprefix("body_")
    duration = (frames - 1) / fps
    report = dict(version=VERSION, action=action, duration_seconds=duration,
                  applied=False, changed_bones=[], physics_wind_target=0)
    reason = None
    if action not in PROFILES:
        reason = "not_in_reviewed_semantic_allowlist"
    elif not looping:
        reason = "entry_exit_or_oneshot_preserved"
    elif contact_sensitive:
        reason = "hand_or_prop_contact_preserved"
    elif duration < 5:
        reason = "short_clip_requires_separate_long_cycle_design"
    if reason:
        report["reason"] = reason
        return dict(rotations), report
    if any(n not in rest or n not in bind_axes for n in SPINES):
        raise ValueError("Semantic motion requires both spine bones and mesh bind orientations")
    yaw, bend, upper_factor, label = PROFILES[action]
    alternating, arc = signals(np.linspace(0, 1, frames))
    result = dict(rotations)
    # One slow cycle over the existing clip. Do not retime the source or force
    # several cycles into short clips. 2/T < .5 applies to the added signals.
    report.update(applied=True, semantic=label, changed_bones=list(SPINES),
                  period_seconds=duration, highest_control_frequency_hz=2/duration,
                  lower_spine_yaw_cap_deg=abs(yaw), lower_spine_bend_cap_deg=abs(bend),
                  upper_spine_factor=upper_factor, original_fps_and_frames_preserved=True)
    for n, factor in zip(SPINES, (1.0, upper_factor)):
        base = np.asarray(rotations.get(n, [rest[n]["q"]] * frames), float)
        if base.shape != (frames, 4):
            raise ValueError(f"Invalid source channel: {n}")
        local_up, local_forward = bind_axes[n]
        rv = np.deg2rad(factor) * (yaw * alternating[:, None] * local_up
                                 + bend * arc[:, None] * local_forward)
        angle = np.linalg.norm(rv, axis=1)
        vector_scale = 0.5 * np.sinc(angle / (2 * np.pi))
        delta = np.column_stack((rv * vector_scale[:, None], np.cos(angle/2)))
        new = multiply(normalize(base), delta)
        for i in range(1, frames):
            if np.dot(new[i - 1], new[i]) < 0:
                new[i] *= -1
        # Preserve exact endpoints, including source float precision.
        new[0] = base[0]
        new[-1] = base[-1] * (1 if np.dot(new[-2], base[-1]) >= 0 else -1)
        result[n] = [tuple(q) for q in new]
    return result, report
