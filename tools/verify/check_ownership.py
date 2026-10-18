"""校验 head/torso mesh 的顶点是否划分正确（按顶点主导骨的父链归属）。

用法: python work/_check_ownership.py <head.mesh> [torso.mesh]
"""
import sys, io, pathlib
from collections import Counter
from pathlib import Path as _P
sys.path[:0] = [str(_P(__file__).resolve().parents[1] / _d) for _d in ('lib', 'anim')]
import pdx_parse, moe_material_contract as MC


def kids(n):
    ch = n.get('_children') or []
    return list(ch.items()) if isinstance(ch, dict) else list(ch)


def props(n):
    return n.get('_props') or {}


def load(mp):
    tree = pdx_parse.parse(pathlib.Path(mp).read_bytes())
    order, pa = [], {}

    def walk(n):
        for k, c in kids(n):
            pr = props(c)
            if 'ix' in pr and 'tx' in pr:
                order.append(k)
                pa[k] = pr['pa'][1][0] if 'pa' in pr else -1
            walk(c)
    walk(tree)
    parent = {b: (order[pa[b]] if 0 <= pa[b] < len(order) else None)
              for b in order}
    dom, per_sm = Counter(), []
    for _, obj in kids(tree):
        for mname, mn in kids(obj):
            for smname, cn in kids(mn):
                pr = props(cn)
                if 'p' not in pr:
                    continue
                nv = len(pr['p'][1]) // 3
                sd = Counter()
                for n4, gn in kids(cn):
                    gp = props(gn)
                    if n4 != 'skin' or 'bones' not in gp:
                        continue
                    npb = gp['bones'][1][0]
                    ix, w = gp['ix'][1], gp['w'][1]
                    for v in range(nv):
                        best = (-1.0, None)
                        for sl in range(npb):
                            bi = ix[v * npb + sl]
                            if bi < 0 or bi >= len(order):
                                continue
                            if w[v * npb + sl] > best[0]:
                                best = (w[v * npb + sl], order[bi])
                        if best[1]:
                            dom[best[1]] += 1
                            sd[best[1]] += 1
                per_sm.append((nv, sd))
    return order, parent, dom, per_sm


out = io.open('work/_ownership_report.txt', 'w', encoding='utf-8')
head_mesh = sys.argv[1]
order, parent, dom, per_sm = load(head_mesh)
bad = MC.check_skin_ownership('head', dom, bone_parent=parent)
out.write(f'=== {head_mesh} ===\n')
out.write(f'骨 {len(order)}  主导骨 {len(dom)} 种  总顶点 {sum(dom.values())}\n')
out.write(f'违规 {len(bad)} 条\n\n')
tot = 0
for b, why in bad:
    out.write(f'  [{dom[b]:5d} 顶点] {b}\n')
    tot += dom[b]
out.write(f'\n合计错划顶点: {tot}\n')
out.write('\n=== 按 submesh 拆分（错划顶点集中在哪块）===\n')
badset = {b for b, _ in bad}
for nv, sd in per_sm:
    n_bad = sum(c for b, c in sd.items() if b in badset)
    if n_bad:
        out.write(f'  submesh {nv:6d} 顶点 → 其中 {n_bad} 个错划\n')
out.close()
print(io.open('work/_ownership_report.txt', encoding='utf-8').read())
