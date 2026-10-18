"""Split every moe head/torso of a model pack into skeleton carrier + real mesh accessory.

Per character <ck> (see build_portrait_carriers.py for why):
  * torso pdxmesh -> models/<pre>torso_carrier.mesh (real torso skeleton, one degenerate triangle);
    keeps the entity, its states and its animation import. Streams (no Never).
  * head pdxmesh  -> same carrier treatment for now; build_cam_rig.py later swaps it for the 5-bone rig.
  * real meshes   -> new pdxmesh moe_<ck>_face_mesh / moe_<ck>_outfit_mesh (all meshsettings moved
    there) + entities moe_<ck>_face_entity / moe_<ck>_outfit_entity, worn as accessories with
    shared_pose_entity = torso via gene moe_<ck>_face / moe_<ck>_outfit (automatic modifier, usage game).

usage: python tools/build/split_portrait_carriers.py <pack dir>
Run once on a pack that has not been split.
"""
import glob
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / 'lib')]
import build_portrait_carriers as C

PACK = sys.argv[1].rstrip('/') if len(sys.argv) > 1 else ''
MODELS = PACK + '/gfx/models/portraits/moe/models/'


def make_carrier(src, dst):
    C.torso_carrier(src, dst)


def main():
    asset = glob.glob(PACK + '/gfx/models/portraits/moe/moe_portrait_*.asset')[0]
    s = open(asset, 'rb').read()
    assert b'_carrier.mesh' not in s, 'already split'
    ent_mesh = dict(re.findall(rb'entity = \{\r?\n\tname = "([^"]+)"\r?\n\tpdxmesh = "([^"]+)"', s))
    mesh_re = re.compile(
        rb'pdxmesh = \{(\r?\n)\tname = "(?P<name>[^"]+)"\r?\n\tfile = "models/(?P<file>[^"]+)\.mesh"\r?\n'
        rb'\tscale = (?P<scale>[\d.]+)\r?\n\r?\n\timport = \{\r?\n\t\ttype = skeletal_animation_set\r?\n'
        rb'\t\tname = "(?P<set>[^"]+)"\r?\n\t\}\r?\n')
    blocks = {m.group('name'): m for m in mesh_re.finditer(s)}

    jobs, entities_add, replacements = [], [], []
    genes, accs, mods = {}, {}, {}
    for tf in sorted(glob.glob(PACK + '/common/portrait_types/10_moe_types_*.txt')):
        ck = os.path.basename(tf)[len('10_moe_types_'):-4]
        t = open(tf, encoding='utf-8-sig').read()
        group = re.search(r'^(moe_\w+_g) = \{', t, re.M).group(1)
        types = re.findall(r'^\t(moe_\w+) = \{\s*\n\t\tsex', t, re.M)
        head_ent = re.search(r'head = "([^"]+)"', t).group(1)
        torso_ent = re.search(r'torso = "([^"]+)"', t).group(1)
        gl, al, ml = [], [], []
        for part, ent, pose in (('face', head_ent, 'head'), ('outfit', torso_ent, 'torso')):
            mname = ent_mesh[ent.encode()].decode()
            m = blocks[mname.encode()]
            nl = m.group(1).decode()
            f = m.group('file').decode()
            jobs.append((MODELS + f + '.mesh', MODELS + f + '_carrier.mesh'))
            real_mesh, real_ent = f'moe_{ck}_{part}_mesh', f'moe_{ck}_{part}_entity'
            acc, gene = f'moe_{ck}_{part}_base', f'moe_{ck}_{part}'
            L = lambda *a: (nl.join(a) + nl).encode()
            new = (L('pdxmesh = {', f'\tname = "{mname}"', f'\tfile = "models/{f}_carrier.mesh"',
                     f'\tscale = {m.group("scale").decode()}', '',
                     '\timport = {', '\t\ttype = skeletal_animation_set', f'\t\tname = "{m.group("set").decode()}"', '\t}', '',
                     '\tmeshsettings = {', f'\t\tname = "{mname}"', '\t\tindex = 0',
                     '\t\ttexture_diffuse = "moe_carrier_clear.dds"',
                     '\t\tshader = "portrait_attachment_alpha_to_coverage"',
                     '\t\tshader_file = "gfx/FX/jomini/portrait.shader"', '\t}', '}', '')
                   + L('pdxmesh = {', f'\tname = "{real_mesh}"', f'\tfile = "models/{f}.mesh"',
                       f'\tscale = {m.group("scale").decode()}', ''))
            replacements.append((m.start(), m.end(), new))
            entities_add.append(
                f'entity = {{\r\n\tname = "{real_ent}"\r\n\tpdxmesh = "{real_mesh}"\r\n'
                f'\tgame_data = {{\r\n\t\tportrait_entity_user_data = {{\r\n\t\t\tcolor_mask_remap_interval = {{\r\n'
                f'\t\t\t\tinterval = {{ 0.0 1.0 }}\r\n\t\t\t}}\r\n\t\t}}\r\n\t}}\r\n}}\r\n')
            tmap = f'\t\t\t\t{types[0]} = {{\r\n\t\t\t\t\t1 = {acc}\r\n\t\t\t\t}}\r\n' + ''.join(
                f'\t\t\t\t{x} = {types[0]}\r\n' for x in types[1:])
            gl.append(f'\t\t{gene} = {{\r\n\t\t\tgroup = {group}_portrait_group\r\n\t\t\t{acc} = {{\r\n'
                      f'\t\t\t\tindex = 0\r\n{tmap}\t\t\t}}\r\n\t\t}}\r\n')
            al.append(f'{acc} = {{\r\n\tportrait_group = {group}\r\n'
                      f'\tentity = {{ required_tags = "" shared_pose_entity = torso entity = "{real_ent}" }}\r\n}}\r\n')
            ml.append(f'{gene}_automatic = {{\r\n\tportrait_group = {group}\r\n\tusage = game\r\n'
                      f'\t{acc}_automatic = {{\r\n\t\tdna_modifiers = {{ accessory = {{ mode = add gene = {gene} '
                      f'template = {acc} value = 1 }} }}\r\n\t\tweight = {{ base = 100 }}\r\n\t}}\r\n}}\r\n')
        hdr = f'﻿# 骨架载体 + 外观 accessory 拆分。{ck}\r\n'
        genes[ck] = hdr + f'special_genes = {{\r\n\taccessory_genes = {{\r\n\t\tportrait_group = {group}\r\n\r\n' + ''.join(gl) + '\t}\r\n}\r\n'
        accs[ck] = hdr + ''.join(al)
        mods[ck] = hdr + ''.join(ml)

    with ProcessPoolExecutor(16) as ex:
        list(ex.map(make_carrier, *zip(*jobs)))
    for a, b, new in sorted(replacements, reverse=True):
        s = s[:a] + new + s[b:]
    s = s.rstrip(b'\r\n') + b'\r\n\r\n' + ''.join(entities_add).encode()
    open(asset, 'wb').write(s)
    for ck in genes:
        for d, txt in (('common/genes/43_moe_genes_outfit_', genes[ck]),
                       ('gfx/portraits/accessories/43_moe_outfit_', accs[ck]),
                       ('gfx/portraits/portrait_modifiers/43_moe_outfit_', mods[ck])):
            open(f'{PACK}/{d}{ck}.txt', 'w', encoding='utf-8', newline='').write(txt)
    print(len(genes), 'groups', len(replacements), 'meshes split')


if __name__ == '__main__':
    main()
