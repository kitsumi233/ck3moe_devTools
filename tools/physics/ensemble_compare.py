"""Compare ensemble_hf result files family by family (mean±std, 2σ rule).

Usage: ensemble_compare.py BASE.json[@variant] OTHER.json[@variant] ...
Each OTHER is compared to BASE on the roles/actions/families both contain.
"""
import json
import math
import sys


def load(spec):
    path, _, variant = spec.rpartition('@') if '@' in spec else (spec, '', '')
    data = json.load(open(path, encoding='utf-8'))['variants']
    variant = variant or next(iter(data))
    return spec, data[variant]


def main():
    base_name, base = load(sys.argv[1])
    for spec in sys.argv[2:]:
        name, other = load(spec)
        better = worse = tie = 0
        sa = sb = 0.
        lines = []
        for ck, acts in other.items():
            for act, fams in acts.items():
                for f, r in fams.items():
                    b = base.get(ck, {}).get(act, {}).get(f)
                    if not b:
                        continue
                    sa += r['mean']; sb += b['mean']
                    if max(r['mean'], b['mean']) < .01:
                        continue
                    sig = abs(r['mean'] - b['mean']) > 2 * math.hypot(r['std'], b['std'])
                    if not sig:
                        tie += 1
                        continue
                    if r['mean'] < b['mean']:
                        better += 1
                    else:
                        worse += 1
                    lines.append(f"  {'+' if r['mean'] < b['mean'] else '-'} {ck:4s} {act[:20]:20s} {f:10s} "
                                 f"{b['mean']:.4f}±{b['std']:.4f} -> {r['mean']:.4f}±{r['std']:.4f}")
        print(f'{name}: sum {sb:.3f} -> {sa:.3f}  better {better} worse {worse} tie {tie}')
        print('\n'.join(lines))


if __name__ == '__main__':
    main()
