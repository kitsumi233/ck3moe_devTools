"""Inline every skeletal_animation_set into the pdxmesh that imports it (carriers carry their own anim list).

usage: python tools/build/inline_anim_sets.py <asset>
- Each `import = { type = skeletal_animation_set name = "X" }` block is replaced by the animation lines of set X.
- No `streaming = Never`: on the torso carrier it blanks every vanilla portrait.
- The skeletal_animation_set blocks themselves are removed.
Backup is written next to the bisect backups by the caller.
"""
import re, sys

path = sys.argv[1]
raw = open(path, 'rb').read()
bom = raw.startswith(b'\xef\xbb\xbf')
s = raw[3:].decode('utf-8') if bom else raw.decode('utf-8')
nl = '\r\n' if '\r\n' in s else '\n'
lines = s.split(nl)

# 1. collect sets
sets = {}
out = []
i = 0
while i < len(lines):
    l = lines[i]
    if l.startswith('skeletal_animation_set'):
        j = i + 1
        name = None
        anims = []
        while not lines[j].startswith('}'):
            m = re.match(r'\s*name = "([^"]+)"', lines[j])
            if m and name is None:
                name = m.group(1)
            if lines[j].lstrip().startswith('animation = {'):
                anims.append('\t' + lines[j].strip())
            j += 1
        sets[name] = anims
        i = j + 1
        # drop one blank line after the block
        if i < len(lines) and lines[i].strip() == '':
            i += 1
        continue
    out.append(l)
    i += 1

# 2. replace import blocks
res = []
i = 0
n_imp = 0
n_anim = 0
while i < len(out):
    l = out[i]
    if l.strip() == 'import = {' and i + 3 < len(out) and 'skeletal_animation_set' in out[i + 1]:
        m = re.search(r'name = "([^"]+)"', out[i + 2])
        assert m and out[i + 3].strip() == '}', out[i:i + 4]
        anims = sets[m.group(1)]
        res.extend(anims)
        n_imp += 1
        n_anim += len(anims)
        i += 4
        continue
    res.append(l)
    i += 1

txt = nl.join(res)
open(path, 'wb').write((b'\xef\xbb\xbf' if bom else b'') + txt.encode('utf-8'))
print('sets', len(sets), 'imports inlined', n_imp, 'anim lines', n_anim)
