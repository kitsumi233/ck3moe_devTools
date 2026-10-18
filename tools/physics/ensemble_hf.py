"""Replicate-aware HF gate for isolated response trials.

One batch holds the same scene several times. Instances only differ by their
grid origin, yet chaotic contact states (e.g. a skirt squeezed between legs)
amplify that float rounding into visibly different motion. A single run of such
an action is one sample, not a measurement, so variants are compared by the
mean and spread over replicas.

HF is measured per dynamic rigid in its joint parent's frame (body motion is
removed), as windowed RMS above --cut Hz. Reads the trial's prepared native
inputs; writes only under <root>/ensemble. Nothing is accepted or installed.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import time

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]


def read(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def load_out(p):
    head = np.fromfile(p, dtype=np.int32, count=2)
    return np.fromfile(p, dtype=np.float32, offset=8).reshape(int(head[0]), int(head[1]), 7)


def qrot_inv(q, v):
    # rotate v by the inverse of unit quaternion q (xyzw), vectorized
    u, w = -q[..., :3], q[..., 3:]
    t = 2 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def local_positions(out, parent):
    pos, q = out[..., :3], out[..., 3:]
    child = np.array(sorted(parent))
    par = np.array([parent[c] for c in child])
    return child, qrot_inv(q[:, par], pos[:, child] - pos[:, par])


def windowed_hf(x, fps, cut, window=1.0):
    # RMS of content above cut in sliding windows; p95 over windows
    n = max(8, int(round(window * fps)))
    if len(x) < n:
        n = len(x)
    freq = np.fft.rfftfreq(n, 1 / fps)
    hann = np.hanning(n)[:, None, None]
    vals = []
    for s in range(0, len(x) - n + 1, max(1, n // 4)):
        seg = x[s:s + n]
        spec = np.fft.rfft((seg - seg.mean(0)) * hann, axis=0)
        spec[freq < cut] = 0
        hf = np.fft.irfft(spec, n=n, axis=0)
        vals.append(np.sqrt((hf ** 2).sum(-1).mean(0)))
    return np.percentile(np.array(vals), 95, axis=0)


def parents(inv):
    rs = inv['rigids']
    par = {}
    for j in inv['joints']:
        a, b = j['a'], j['b']
        if a is None or b is None or a == b or min(a, b) < 0:
            continue
        if rs[b]['mode'] and b not in par:
            par[b] = a
        elif rs[a]['mode'] and not rs[b]['mode'] and a not in par:
            par[a] = b
    return par


def run_character(root, ck, copies, out_root, extra=(), actions=None):
    folder = root / 'characters' / ck
    io = folder / 'io'
    record = read(io / 'batch_command.json')
    cmd = list(record['command'])
    if cmd and isinstance(cmd[0], list):   # chunked record: one command per chunk, same flags
        cmd = list(cmd[0])
    # --gpu-chunks N writes batch_0.tsv .. batch_{N-1}.tsv instead of batch.tsv
    tsvs = [io / 'batch.tsv'] if (io / 'batch.tsv').is_file() else sorted(io.glob('batch_*.tsv'))
    rows = [l.split('\t') for t in tsvs for l in t.read_text(encoding='utf-8').splitlines() if l.strip()]
    dest = out_root / ck
    dest.mkdir(parents=True, exist_ok=True)
    manifest, outs = [], {}
    for r in rows:
        base = Path(r[2]).name[:-len('.out.bin')]
        if actions and base.split('body_', 1)[1] not in actions:
            continue
        for k in range(copies):
            op = dest / f'{base}.r{k}.out.bin'
            outs.setdefault(base, []).append(op)
            manifest.append('\t'.join([r[0], r[1], str(op)] + r[3:]))
    tsv = dest / 'batch.tsv'
    tsv.write_text('\n'.join(manifest) + '\n', encoding='utf-8')
    cmd[cmd.index('--batch') + 1] = str(tsv)
    if '--contact-report' in cmd:
        cmd[cmd.index('--contact-report') + 1] = str(dest / 'contacts.csv')
    cmd += list(extra)
    stamp = dest / 'done.json'
    key = dict(command=cmd, inputs=record['input_hashes'], copies=copies)
    if not (stamp.exists() and read(stamp).get('key') == key and all(p.exists() for v in outs.values() for p in v)):
        t = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode:
            raise RuntimeError(f'{root.name}/{ck} native failed: {proc.stderr[-600:]}')
        stamp.write_text(json.dumps(dict(key=key, seconds=time.time() - t)), encoding='utf-8')
    return outs


def measure(root, ck, outs, fps, cut):
    inv = read(root / f'{ck}_inventory.json')
    contract = read(root / 'characters' / ck / 'response_contract.json')
    fam = {b['index']: b['family'] for b in contract['bodies']}
    par = {c: p for c, p in parents(inv).items() if c in fam}
    names = [r['name'] for r in inv['rigids']]
    rows = {}
    for base, paths in outs.items():
        action = base.split('body_', 1)[1]
        per = []
        for p in paths:
            child, loc = local_positions(load_out(p), par)
            per.append(windowed_hf(loc, fps, cut))
        per = np.array(per)  # copies x bodies
        groups = {}
        for i, c in enumerate(child):
            groups.setdefault(fam[int(c)], []).append(i)
        fam_rows = {}
        for f, idx in groups.items():
            v = per[:, idx]
            # family score per copy: p95 over its bodies
            score = np.percentile(v, 95, axis=1)
            worst = int(np.argmax(v.mean(0)))
            fam_rows[f] = dict(mean=float(score.mean()), std=float(score.std(ddof=1)) if len(score) > 1 else 0.,
                               copies=[round(float(s), 5) for s in score],
                               worst_rigid=names[int(child[idx[worst]])], worst_mean=float(v.mean(0)[worst]),
                               worst_std=float(v.std(0, ddof=1)[worst]) if len(v) > 1 else 0.)
        rows[action] = fam_rows
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--roots', nargs='+', type=Path, required=True)
    ap.add_argument('--characters', nargs='*')
    ap.add_argument('--copies', type=int, default=4)
    ap.add_argument('--fps', type=float, default=30.)
    ap.add_argument('--cut', type=float, default=6.)
    ap.add_argument('--jobs', type=int, default=3, help='concurrent native batches (GPU shared)')
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--actions', nargs='*', help='subset of trial actions')
    ap.add_argument('--native-extra', default='', help='extra native solver flags for a sweep (space separated)')
    ap.add_argument('--tag', default='', help='ensemble subfolder tag; required with --native-extra')
    a = ap.parse_args()
    if a.native_extra and not a.tag:
        raise SystemExit('--native-extra needs --tag so sweep outputs never mix with the baseline')
    tasks = []
    for root in a.roots:
        cks = a.characters or read(root / 'trial_scope.json')['characters']
        tasks += [(root, ck) for ck in cks]

    def job(t):
        root, ck = t
        extra = a.native_extra.split()
        folder = root / ('ensemble' + ('_' + a.tag if a.tag else ''))
        outs = run_character(root, ck, a.copies, folder, extra, a.actions)
        print(root.name + (':' + a.tag if a.tag else ''), ck, 'solved', flush=True)
        return root.name, ck, measure(root, ck, outs, a.fps, a.cut)

    result = {}
    with ThreadPoolExecutor(max_workers=a.jobs) as pool:
        for name, ck, rows in pool.map(job, tasks):
            result.setdefault(name, {})[ck] = rows
    a.out.write_text(json.dumps(dict(status='diagnostic_ensemble_not_accepted', copies=a.copies,
                                     cut_hz=a.cut, variants=result), ensure_ascii=False, indent=1), encoding='utf-8')
    names = [r.name for r in a.roots]
    if len(names) >= 2:
        A, B = names[0], names[1]
        print(f'\n{A} vs {B}: family p95 HF mean±std over {a.copies} copies; * = |diff| > 2*combined std')
        for ck in result[A]:
            for action in result[A][ck]:
                for f, ra in result[A][ck][action].items():
                    rb = result[B][ck][action].get(f)
                    if not rb or max(ra['mean'], rb['mean']) < .02:
                        continue
                    sig = abs(ra['mean'] - rb['mean']) > 2 * np.hypot(ra['std'], rb['std'])
                    print(f"{'*' if sig else ' '} {ck:4s} {action[:22]:22s} {f:10s} "
                          f"{ra['mean']:.4f}±{ra['std']:.4f}  {rb['mean']:.4f}±{rb['std']:.4f}  "
                          f"worst {ra['worst_rigid'][:20]}")


if __name__ == '__main__':
    main()
