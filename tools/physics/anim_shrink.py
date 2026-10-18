"""Shrink baked physics .anim files: tolerance channel drop + 30 -> 15 fps.

Four space-saving steps agreed 2026-09-30 (see memory phys-full-batch-cost-is-cpu-bound):
  1. t channels whose whole-clip deviation from the mean is < --tol-t become constant;
  2. same for q channels under --tol-q-deg (angle to the normalized mean);
  3. frames are decimated by 2 (every other frame), always keeping the last frame;
  4. write_anim's lossless _drop_constant then folds every constant channel into rest.

Duration is preserved: CK3 plays sa frames at fps, so the new fps is chosen as
(F_new - 1) / ((F - 1) / fps) -- same first-to-last span. When F - 1 is odd the kept
last frame sits closer to its predecessor; the span is still exact, the final interval
is slightly shorter (at most one source frame).

Constant channels are written as the mean, not deleted: a still-but-sagging hair bone
must keep its sagged pose (see moe_bullet_bake tolerance block for the same rule).
Rest follows frame 0 (write_anim does that), keeping the rest==frame0 invariant.

Input files are never modified; output goes to a separate directory tree so resume
hash checks on the source stay valid.
Usage: python anim_shrink.py <src dir> <dst dir> [--tol-t 0.01] [--tol-q-deg 0.0573] [--jobs 8]
"""
import argparse
import math
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / 'lib'), str(HERE.parent / 'anim')]
import pdx_anim_write as W
import retarget_vanilla_anim as RV


def decimate_index(F):
    """Every other frame, last frame always kept."""
    idx = list(range(0, F, 2))
    if idx[-1] != F - 1:
        idx.append(F - 1)
    return idx


def shrink(src, dst, tol_t, tol_q_deg, step=2):
    meta, bones, samp = RV.load_anim(src)
    F, ts, qs = RV.unpack_samples(meta, bones, samp)
    fps = float(meta['fps'][0])
    idx = decimate_index(F) if step == 2 and F > 2 else list(range(F))
    Fn = len(idx)
    fps_n = fps if Fn == F else (Fn - 1) / ((F - 1) / fps)
    stats = dict(t_const=0, q_const=0)
    out_t, out_q = {}, {}
    for n, tl in ts.items():
        T = np.asarray(tl, dtype=np.float64)
        m = T.mean(axis=0)
        if tol_t > 0 and np.abs(T - m).max() < tol_t:
            out_t[n] = [tuple(m)] * Fn
            stats['t_const'] += 1
        else:
            out_t[n] = [tuple(T[i]) for i in idx]
    cos_tol = math.cos(math.radians(tol_q_deg) / 2)
    for n, ql in qs.items():
        Q = np.asarray(ql, dtype=np.float64)
        # Sign-align to frame 0 before averaging (q and -q are the same rotation).
        Q[(Q @ Q[0]) < 0] *= -1
        m = Q.mean(axis=0)
        m /= max(np.linalg.norm(m), 1e-12)
        if tol_q_deg > 0 and np.abs(Q @ m).min() > cos_tol:
            out_q[n] = [tuple(m)] * Fn
            stats['q_const'] += 1
        else:
            out_q[n] = [tuple(np.asarray(ql[i], dtype=np.float64)) for i in idx]
    rows, flat_t, flat_q = [], [], []
    t_names = [b['name'] for b in bones if 't' in b['sa']]
    q_names = [b['name'] for b in bones if 'q' in b['sa']]
    for f in range(Fn):
        for n in t_names:
            flat_t.extend(out_t[n][f])
        for n in q_names:
            flat_q.extend(out_q[n][f])
    for b in bones:
        if 's' in b['sa']:
            raise ValueError(f'scale channel on {b["name"]} in {src.name}: not supported')
        rows.append((b['name'], b['sa'], list(b['t']), list(b['q']), list(b['s'])))
    dst.parent.mkdir(parents=True, exist_ok=True)
    size = W.write_anim(dst, fps_n, Fn, rows, flat_t, flat_q, drop_constant=True)
    # Self-check: bone table identical, duration identical, all finite.
    m2, b2, s2 = RV.load_anim(dst)
    assert [x['name'] for x in b2] == [x['name'] for x in bones], 'bone table changed'
    F2 = m2['sa'][0]
    span, span2 = (F - 1) / fps, (F2 - 1) / float(m2['fps'][0])
    assert F2 == Fn and abs(span - span2) < 1e-4, f'duration changed {span} -> {span2}'
    _, t2, q2 = RV.unpack_samples(m2, b2, s2)
    assert all(np.isfinite(v).all() for v in t2.values()) and all(np.isfinite(v).all() for v in q2.values())
    return dict(src=src.stat().st_size, dst=size, F=F, Fn=Fn, **stats)


def _job(args):
    src, dst, tol_t, tol_q = args
    try:
        return str(src), shrink(Path(src), Path(dst), tol_t, tol_q), None
    except Exception as e:
        return str(src), None, repr(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src', type=Path)
    ap.add_argument('dst', type=Path)
    ap.add_argument('--tol-t', type=float, default=0.01)
    ap.add_argument('--tol-q-deg', type=float, default=math.degrees(0.001))
    ap.add_argument('--jobs', type=int, default=8)
    a = ap.parse_args()
    if a.dst.resolve() == a.src.resolve() or a.dst.resolve().is_relative_to(a.src.resolve()):
        raise SystemExit('dst must be outside src')
    files = sorted(a.src.rglob('*.anim'))
    work = [(str(p), str(a.dst / p.relative_to(a.src)), a.tol_t, a.tol_q_deg) for p in files]
    tot_s = tot_d = 0
    errors = []
    with ProcessPoolExecutor(a.jobs) as pool:
        for src, r, err in pool.map(_job, work, chunksize=8):
            if err:
                errors.append((src, err))
                continue
            tot_s += r['src']
            tot_d += r['dst']
    print(f'files {len(files)} ok {len(files) - len(errors)} err {len(errors)}  '
          f'{tot_s / 2**20:.0f} MB -> {tot_d / 2**20:.0f} MB ({100 * tot_d / max(tot_s, 1):.1f}%)')
    for src, err in errors[:20]:
        print('ERR', src, err)
    sys.exit(1 if errors else 0)


if __name__ == '__main__':
    main()
