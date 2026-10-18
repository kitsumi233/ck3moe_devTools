"""Point torso carriers at the merged head+torso anims.

For every torso entity state whose (torso anim id, head anim id) pair was merged, register a new animation
id `<torso id>__h` on that torso carrier pdxmesh and switch the state to it.

usage: python tools/build/apply_merged_anims.py <pack dir> <original asset (before split)>
"""
import sys, os, re, glob, json
sys.path[:0] = [os.path.dirname(os.path.abspath(__file__)), 'tools/lib']
from merge_portrait_anims import head_for

pack, orig = sys.argv[1].rstrip('/'), sys.argv[2]
MOE = pack + '/gfx/models/portraits/moe/'
asset = glob.glob(MOE + 'moe_portrait_*.asset')[0]
mp = {tuple(k.split('|')): v for k, v in json.load(open('work/bisect_20261001/merged_map_' + os.path.basename(pack) + '.json')).items()}
o = open(orig, 'rb').read().decode('utf-8', 'replace')
s = open(asset, 'rb').read().decode('utf-8')


def seg(text, name):
    m = re.search(r'entity = \{\s*\n\tname = "' + name + r'"', text)
    if not m: return None
    n = re.search(r'\nentity = \{', text[m.end():])
    return m.start(), m.end() + (n.start() if n else len(text) - m.end())


states = lambda text, e: dict(re.findall(r'state = \{\s*name = "([^"]+)"\s+animation = "([^"]+)"', text[slice(*seg(text, e))]))
total_states = total_ids = 0
for tf in sorted(glob.glob(pack + '/common/portrait_types/10_moe_types_*.txt')):
    t = open(tf, encoding='utf-8-sig').read()
    ents = sorted(set(re.findall(r'(?:head|torso) = "([^"]+)"', t)))
    heads = [e for e in ents if '_head_' in e]; torsos = [e for e in ents if '_torso_' in e]
    for te in torsos:
        he = te.replace('_torso_', '_head_')
        H = states(o, he); T = states(o, te)
        pm = re.search(r'entity = \{\s*\n\tname = "' + te + r'"\s*\n\tpdxmesh = "([^"]+)"', s).group(1)
        new_ids = {}
        for st, ta in T.items():
            ha = head_for(st, H)
            if ha and (ta, ha) in mp:
                new_ids[st] = (ta + '__' + ha, mp[(ta, ha)])
        # register ids on the torso carrier pdxmesh (once per pdxmesh)
        m = re.search(r'(pdxmesh = \{\r?\n\tname = "' + pm + r'"\r?\n.*?)(\r?\n\}\r?\n)', s, re.S)
        body = m.group(1)
        nl = '\r\n' if '\r\n' in body else '\n'
        add = ''.join(f'{nl}\tanimation = {{ id = "{i}" type = "{f}" }}' for i, f in sorted(set(new_ids.values()))
                      if f'id = "{i}"' not in body)
        s = s[:m.start()] + body + add + m.group(2) + s[m.end():]
        total_ids += add.count('animation = {')
        # switch states inside the torso entity
        a, b = seg(s, te)
        e = s[a:b]
        def rep(mm):
            st = mm.group(1)
            return mm.group(0).replace(f'animation = "{mm.group(2)}"', f'animation = "{new_ids[st][0]}"') if st in new_ids else mm.group(0)
        e2, n = re.subn(r'state = \{\s*name = "([^"]+)"\s+animation = "([^"]+)"', rep, e)
        total_states += sum(1 for k in new_ids)
        s = s[:a] + e2 + s[b:]
open(asset, 'wb').write(s.encode('utf-8'))
print('registered ids', total_ids, 'states switched', total_states)
