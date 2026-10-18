"""Merge each torso state's anim with its paired head anim, so the torso carrier alone drives every bone.

Why: the real head is now an accessory posed from the torso (shared_pose_entity = torso). Head-only bones
(hair physics, fx expression bones) are animated only by the head anim, which no longer runs.
Merge rule per bone: head track for bones the head mesh skins and the torso mesh doesn't (hair / face);
torso track for everything else (body owns the shared spine/neck/head chain).
Output bone set and order = the torso carrier skeleton (anim bone count must equal mesh bone count).

usage: python tools/build/merge_portrait_anims.py <pack dir> <original asset (before split)>
Writes <ck>/anims_merged/*.anim and rewrites the torso carrier's inline animation types to them.
"""
import sys, os, re, glob, struct
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, 'tools/lib')
import pdx_parse as P
from pdx_anim_write import write_anim


def read(path):
    tree = P.parse(open(path, 'rb').read())
    fps = nf = None; bones = []; cur = None; samples = {}
    for p, name, _k, val in P.walk(tree):
        if p == '/info':
            if name == 'fps': fps = val[0]
            elif name == 'sa': nf = val[0]
        elif p.startswith('/info/') and p.count('/') == 2:
            bn = p.split('/info/', 1)[1].split('[')[0]
            if cur is None or cur['name'] != bn:
                cur = {'name': bn}; bones.append(cur)
            cur[name] = val
        elif p == '/samples':
            samples[name] = val
    # unpack to per-bone per-frame tracks
    tracks = {}
    nt = sum(1 for b in bones if 't' in ''.join(b.get('sa') or []))
    nq = sum(1 for b in bones if 'q' in ''.join(b.get('sa') or []))
    ns = sum(1 for b in bones if 's' in ''.join(b.get('sa') or []))
    st, sq, ss = samples.get('t', []), samples.get('q', []), samples.get('s', [])
    it = iq = is_ = 0
    for b in bones:
        sa = ''.join(b.get('sa') or [])
        tr = {'rest': (list(b['t']), list(b['q']), list(b['s'])), 'sa': sa}
        if 't' in sa:
            tr['t'] = [st[(f * nt + it) * 3:(f * nt + it) * 3 + 3] for f in range(nf)]; it += 1
        if 'q' in sa:
            tr['q'] = [sq[(f * nq + iq) * 4:(f * nq + iq) * 4 + 4] for f in range(nf)]; iq += 1
        if 's' in sa:
            tr['s'] = [ss[f * ns + is_:f * ns + is_ + 1] for f in range(nf)]; is_ += 1
        tracks[b['name']] = tr
    return fps, nf, tracks


def head_for(st, H):
    """head anim id for a torso state: same name, else its base state (strip Fat/Dwarf/Infant/_entry/digits), else idle."""
    if st in H:
        return H[st]
    n = re.sub(r'_entry$', '', st)
    cands = [n]
    n2 = re.sub(r'(FatDwarf|Fat|Dwarf|Infant)', '', n); cands.append(n2)
    cands.append(re.sub(r'\d+$', '', n2)); cands.append(re.sub(r'_loop$|_start$', '', n2))
    for c in cands:
        for k in (c, c + '_entry', c + '1', c + '_loop'):
            if k in H:
                return H[k]
    return H.get('idle')


def parents_of(f):
    t = P.parse(open(f, 'rb').read()); names = []; pa = []
    def find(n):
        for cn, ch in n.get('_children', []):
            if cn == 'skeleton':
                for bn, b in ch['_children']:
                    names.append(bn); p = b['_props'].get('pa'); pa.append(p[1][0] if p else None)
            find(ch)
    find(t)
    return {n: (names[p] if p is not None else None) for n, p in zip(names, pa)}


def mesh_info(f):
    t = P.parse(open(f, 'rb').read()); names = []; used = set()
    def find(n):
        for cn, ch in n.get('_children', []):
            if cn == 'skeleton': names.extend(bn for bn, b in ch['_children'])
            if cn == 'skin':
                ix, w = ch['_props']['ix'][1], ch['_props']['w'][1]
                used.update(i for i, x in zip(ix, w) if i >= 0 and x > 0)
            find(ch)
    find(t); return names, {names[i] for i in used}


def merge(job):
    torso_anim, head_anim, out, order, torso_dep = job
    fps, nf, T = read(torso_anim)
    hfps, hnf, H = read(head_anim)
    bones, st, sq, ss = [], [], [], []
    for name in order:
        src = H if (name in H and name not in torso_dep) else T
        if name not in src:
            src = H if name in H else T
        tr = src[name]
        sa = tr['sa']
        rt, rq, rs = tr['rest']
        bones.append((name, sa, rt, rq, rs))
    # frame-major samples; if head has fewer frames, hold its last frame
    def frame(tr, ch, f):
        seq = tr[ch]
        return seq[min(f, len(seq) - 1)]
    for f in range(nf):
        for name in order:
            src = H if (name in H and name not in torso_dep) else T
            if name not in src: src = H if name in H else T
            tr = src[name]
            if 't' in tr['sa']: st.extend(frame(tr, 't', f))
            if 'q' in tr['sa']: sq.extend(frame(tr, 'q', f))
            if 's' in tr['sa']: ss.extend(frame(tr, 's', f))
    write_anim(out, fps, nf, bones, st, sq, drop_constant=False, sample_s=ss if ss else None)
    return out, 'ok'


def main():
    global TRASH
    pack, orig = sys.argv[1].rstrip('/'), sys.argv[2]
    MAP_OUT = 'work/bisect_20261001/merged_map_' + os.path.basename(pack) + '.json'
    TRASH = (sys.argv[3].rstrip('/') + '/') if len(sys.argv) > 3 else ''
    MOE = pack + '/gfx/models/portraits/moe/'
    asset = glob.glob(MOE + 'moe_portrait_*.asset')[0]
    o = open(orig, 'rb').read().decode('utf-8', 'replace')
    ids = dict(re.findall(r'animation = \{ id = "([^"]+)"\s+type = "([^"]+)"', o))

    def seg(name):
        m = re.search(r'entity = \{\s*\n\tname = "' + name + r'"', o)
        if not m: return ''
        n = re.search(r'\nentity = \{', o[m.end():])
        return o[m.start():m.end() + (n.start() if n else len(o) - m.end())]
    states = lambda e: dict(re.findall(r'state = \{\s*name = "([^"]+)"\s+animation = "([^"]+)"', seg(e)))

    jobs = []; remap = {}
    for tf in sorted(glob.glob(pack + '/common/portrait_types/10_moe_types_*.txt')):
        t = open(tf, encoding='utf-8-sig').read()
        he = re.search(r'head = "([^"]+)"', t).group(1); te = re.search(r'torso = "([^"]+)"', t).group(1)
        hm = re.search(r'entity = \{\s*\n\tname = "' + he + r'"\s*\n\tpdxmesh = "([^"]+)"', o).group(1)
        tm = re.search(r'entity = \{\s*\n\tname = "' + te + r'"\s*\n\tpdxmesh = "([^"]+)"', o).group(1)
        hfile = re.search(r'name = "' + hm + r'"\s*\n\tfile = "models/([^"]+)"', o).group(1)
        tfile = re.search(r'name = "' + tm + r'"\s*\n\tfile = "models/([^"]+)"', o).group(1)
        hn, hs = mesh_info(MOE + 'models/' + hfile)
        tn, ts = mesh_info(MOE + 'models/' + tfile)
        order = mesh_info(MOE + 'models/' + tfile.replace('.mesh', '_carrier.mesh'))[0]
        par = parents_of(MOE + 'models/' + tfile)
        torso_dep = set()
        for b in ts:
            while b is not None and b not in torso_dep:
                torso_dep.add(b); b = par.get(b)
        H, T = states(he), states(te)
        for st_name, ta in T.items():
            ha = head_for(st_name, H)
            if not ha or ta not in ids or ha not in ids: continue
            tp, hp = MOE + ids[ta], MOE + ids[ha]
            tp = tp if os.path.exists(tp) else TRASH + ids[ta]
            hp = hp if os.path.exists(hp) else TRASH + ids[ha]
            d = os.path.dirname(MOE + ids[ta]).replace('/anims', '/anims_merged')
            os.makedirs(d, exist_ok=True)
            out = d + '/' + os.path.basename(tp)[:-5] + '__' + os.path.basename(hp)
            key = (ta, ha)
            if key in remap: continue
            remap[key] = os.path.relpath(out, MOE).replace('\\', '/')
            jobs.append((tp, hp, out, order, torso_dep))
    print('jobs', len(jobs))
    with ProcessPoolExecutor(16) as ex:
        res = list(ex.map(merge, jobs, chunksize=8))
    print('done', sum(1 for _, r in res if r == 'ok'), 'skipped', sum(1 for _, r in res if r == 'skip'))
    import json
    json.dump({f'{a}|{b}': v for (a, b), v in remap.items()}, open(MAP_OUT, 'w'))


if __name__ == '__main__':
    main()
