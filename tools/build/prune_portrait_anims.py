"""Drop animation registrations no state uses, then move the unreferenced .anim files out of the mod.

usage: prune_anims.py <pack dir> <dest dir>
Same-volume move (rename), structure preserved under <dest>.
"""
import re, os, glob, sys, shutil

pack, dest = sys.argv[1].rstrip('/'), sys.argv[2].rstrip('/')
P = pack + '/gfx/models/portraits/moe/'
asset = glob.glob(P + 'moe_portrait_*.asset')[0]
s = open(asset, 'rb').read().decode('utf-8')
used_ids = set(re.findall(r'animation = "([^"]+)"', s))
line = re.compile(r'\r?\n[ \t]*animation = \{ id = "([^"]+)"\s+type = "([^"]+)" \}')
dropped = [0]


def rep(m):
    if m.group(1) in used_ids:
        return m.group(0)
    dropped[0] += 1
    return ''


s = line.sub(rep, s)
open(asset, 'wb').write(s.encode('utf-8'))
ids = dict(re.findall(r'animation = \{ id = "([^"]+)"\s+type = "([^"]+)"', s))
keep = {ids[i] for i in used_ids if i in ids}
norm = lambda f: os.path.relpath(f, P).replace(os.sep, '/')
moved = 0
for f in glob.glob(P + '**/*.anim', recursive=True):
    r = norm(f)
    if r in keep:
        continue
    d = os.path.join(dest, r)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    os.replace(f, d)
    moved += 1
# remove now-empty anim dirs
for d in sorted(glob.glob(P + '**/', recursive=True), key=len, reverse=True):
    if os.path.isdir(d) and not os.listdir(d):
        os.rmdir(d)
print('registrations dropped', dropped[0], 'files moved', moved, 'kept', len(keep))
