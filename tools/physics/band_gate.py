"""Perceptual jitter gate: secondary motion above 0.5 Hz, per named region.

The user's acceptance line is that visible secondary motion must stay below
0.5 Hz; anything faster reads as jitter. For each region (rigid-name rule) this
reports the p90-over-bodies amplitude of parent-local motion in bands
<0.5, 0.5-2, 2-6, >6 Hz, averaged over ensemble replicas, plus the
fast/slow ratio sqrt(sum(>0.5)^2)/(<0.5). Lower is better on both.
Reads <root>/ensemble*/ outputs from ensemble_hf.py; writes nothing.
"""
import argparse
import glob
import json
from pathlib import Path
import sys

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]
import ensemble_hf as E

# User-named problem regions; extend as new reports come in.
REGIONS = {
    'tep': {'emotion_laugh': {
        '背后头发': lambda n: n.startswith(('hair_L_base_a_01', 'hair_R_base_a_01')),
        '提灯': lambda n: 'Gadget' in n,
        '裙子': lambda n: '裙' in n}},
    'cyu': {'sitting': {'裙子': lambda n: n.startswith('QZ_')}},
}
BANDS = ((0, .5), (.5, 2), (2, 6), (6, 15.1))


def bands(loc, fps):
    x = loc - loc.mean(0)
    w = np.hanning(len(x))[:, None, None]
    power = (np.abs(np.fft.rfft(x * w, axis=0)) ** 2).sum(-1)
    freq = np.fft.rfftfreq(len(x), 1 / fps)
    return np.array([np.sqrt(power[(freq >= lo) & (freq < hi)].sum(0) * 2) / len(x) * 2 for lo, hi in BANDS])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--roots', nargs='+', type=Path, required=True)
    ap.add_argument('--ensemble', default='ensemble', help='ensemble subfolder name')
    ap.add_argument('--fps', type=float, default=30.)
    a = ap.parse_args()
    for ck, acts in REGIONS.items():
        for act, regions in acts.items():
            for label, pick in regions.items():
                for root in a.roots:
                    paths = sorted(glob.glob(str(root / a.ensemble / ck / f'moe_{ck}_body_{act}.r*.out.bin')))
                    if not paths:
                        print(f'{ck} {act} {label} {root.name}: no ensemble output')
                        continue
                    inv = E.read(root / f'{ck}_inventory.json')
                    par = E.parents(inv)
                    idx = {i: par[i] for i, r in enumerate(inv['rigids']) if r['mode'] and pick(r['name']) and i in par}
                    per = np.array([np.percentile(bands(E.local_positions(E.load_out(p), idx)[1], a.fps), 90, axis=1)
                                    for p in paths])
                    m, s = per.mean(0), per.std(0, ddof=1) if len(per) > 1 else np.zeros(4)
                    ratio = np.sqrt((per[:, 1:] ** 2).sum(1)) / per[:, 0]
                    print(f'{ck} {act[:10]:10s} {label:5s} {root.name:14s} <0.5 {m[0]:.3f} | 0.5-2 {m[1]:.3f} '
                          f'2-6 {m[2]:.3f} >6 {m[3]:.3f} | fast/slow {ratio.mean():.2f}±{ratio.std():.2f}')
                print()


if __name__ == '__main__':
    main()
