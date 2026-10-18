"""Detect skirt bodies that tunnelled to the wrong side of a leg wall.

Strict triangle crossings miss a skirt that passed completely through a thigh:
once through, no triangles intersect. This compares each skirt body's side of
each leg capsule (sign of the offset along the capsule's local frame, taken at
the nearest point of the capsule axis) against the unsimulated FK layout
(rigids placed at bind pose, bone-driven). A body whose side flips relative to
FK and stays flipped is reported. Reads native out.bin files only.
"""
import argparse
import json
from pathlib import Path
import sys

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]
import ensemble_hf as E

LEG_WALLS = ('左足', '右足', '左ひざ', '右ひざ', '左足D', '右足D', '左ひざD', '右ひざD', '左足2', '右足2', '左足3', '右足3')
SKIRT_KEYS = ('裙', 'スカート', 'QZ_', 'qunbai')


def qrot(q, v):
    u, w = q[..., :3], q[..., 3:]
    t = 2 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def sides(out, skirt, walls):
    """Signed distance of each skirt body from each wall axis, in the wall's local XZ plane.

    Returns frames x skirt x walls x 2 (local x and z offsets).
    """
    wp, wq = out[:, walls, :3], out[:, walls, 3:]
    sp = out[:, skirt, :3]
    rel = sp[:, :, None, :] - wp[:, None, :, :]
    inv = wq.copy(); inv[..., :3] *= -1
    local = qrot(np.broadcast_to(inv[:, None], rel.shape[:-1] + (4,)), rel)
    return local[..., [0, 2]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--reference', type=Path, required=True, help='root whose same clip is trusted to be on the right side')
    ap.add_argument('--character', required=True)
    ap.add_argument('--action', required=True)
    ap.add_argument('--near', type=float, default=2.0, help='only judge bodies within this distance of a wall axis')
    ap.add_argument('--out-bin', type=Path, help='judge this out.bin (e.g. an ensemble replica) instead of the io/ one')
    a = ap.parse_args()
    inv = E.read(a.root / f'{a.character}_inventory.json')
    rs = inv['rigids']
    skirt = [i for i, r in enumerate(rs) if r['mode'] and any(k in r['name'] for k in SKIRT_KEYS)]
    walls = [i for i, r in enumerate(rs) if not r['mode'] and r['name'] in LEG_WALLS]
    name = f'moe_{a.character}_body_{a.action}.out.bin'
    cur = E.load_out(a.out_bin if a.out_bin else a.root / 'characters' / a.character / 'io' / name)
    ref = E.load_out(a.reference / 'characters' / a.character / 'io' / name)
    s_cur, s_ref = sides(cur, skirt, walls), sides(ref, skirt, walls)
    # judge per (body, wall) at the frame of closest approach in the reference
    dist_ref = np.linalg.norm(s_ref, axis=-1)
    flipped = np.zeros((len(skirt), len(walls)), bool)
    for bi in range(len(skirt)):
        for wi in range(len(walls)):
            near = dist_ref[:, bi, wi] < a.near
            if near.sum() < 5:
                continue
            dots = (s_cur[near, bi, wi] * s_ref[near, bi, wi]).sum(-1)
            # opposite side for most of the time both are near this wall
            flipped[bi, wi] = (dots < 0).mean() > .5
    bodies = flipped.any(1)
    print(json.dumps(dict(character=a.character, action=a.action, skirt_bodies=len(skirt),
                          judged_walls=[rs[w]['name'] for w in walls],
                          tunnelled_bodies=int(bodies.sum()),
                          examples=[rs[skirt[i]]['name'] for i in np.flatnonzero(bodies)[:8]]), ensure_ascii=False))


if __name__ == '__main__':
    main()
