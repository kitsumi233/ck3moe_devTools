"""Eyes+ (eye highlight layer) back on; face/outfit entity game_data back to interval 0 1, no decal.

* Eyes+ / Eye+ meshsettings were pointed at moe_invisible.dds. The gfl2 iris maps carry no
  highlight; it lives in <ck>_eyeblend. Bright part of eyeblend is alpha > 128, dark overlay
  pixels are ~0.02 %, so a hard alpha cut (moe_portrait_attachment = MOE_ALPHA_CUTOUT) keeps
  the highlight and drops the soft halo.
* portrait_decal on the face accessory entity draws the head decal layer a second time over
  our face -> grey shadow under the chin. Restore interval 0 1 without decal.

usage: python tools/build/fix_eye_highlight.py <pack dir> [...]
"""
import re
import sys
from pathlib import Path

ASSET_REL = Path('gfx/models/portraits/moe')
SLOT = re.compile(r'(\tmeshsettings = \{\r?\n\t\tname = "[^"]+"\r?\n\t\tindex = \d+\r?\n)'
                  r'\t\t# 刻意不可见[^\n]*\n'
                  r'\t\ttexture_diffuse = "moe_invisible\.dds"\r?\n'
                  r'\t\ttexture_specular = "moe_invisible_properties\.dds"\r?\n'
                  r'\t\ttexture_normal = "moe_invisible_normal\.dds"\r?\n'
                  r'\t\tshader = "[^"]+"\r?\n'
                  r'(\t\tshader_file = "[^"]+"\r?\n\t\}\t# Eyes?\+ \(\d+ 面\)) 刻意不可见')
GAMEDATA = re.compile(r'(entity = \{\r?\n\tname = "moe_(?:\w+_)?(?:face|outfit)_entity"\r?\n'
                      r'\tpdxmesh = "[^"]+"\r?\n)\tgame_data = \{.*?\r?\n\t\}\r?\n\}', re.S)


def blend_for(eye_tex, texnames):
    stem = eye_tex[:-len('.dds')]
    stem = re.sub(r'_[0-9a-f]{10}$', '', stem)           # later packs: moe_<ck>_<name>_<hash>
    base = re.sub(r'_(?:[LR]_)?eye_d$', '', stem)
    for cand in (base + '_eyeblend', base + '_eyeblend_d', base + '_eyeblend '):
        if cand + '.dds' in texnames:
            return cand
        hashed = sorted(n[:-4] for n in texnames
                        if re.fullmatch(re.escape(cand) + r'_[0-9a-f]{10}\.dds', n))
        if hashed:
            return hashed[0]
    return None


def main():
    for pack in map(Path, sys.argv[1:]):
        moe = pack / ASSET_REL
        asset = next(moe.glob('moe_portrait_*.asset'))
        s = asset.read_bytes().decode('utf-8')
        nl = '\r\n' if '\r\n' in s else '\n'
        texnames = {p.name for p in (moe / 'models').rglob('*.dds')}
        on = miss = 0
        out = []; pos = 0
        for m in re.finditer(r'pdxmesh = \{.*?\n\}', s, re.S):
            blk = m.group(0)
            eyes = re.findall(r'texture_diffuse = "([^"]+_eye_d(?:_[0-9a-f]{10})?\.dds)"', blk)
            blend = blend_for(eyes[0], texnames) if eyes else None

            def rep(mm):
                nonlocal on, miss
                if not blend:
                    miss += 1
                    return mm.group(0)
                on += 1
                spec = blend + '_properties.dds' if blend + '_properties.dds' in texnames else eyes[0][:-4] + '_properties.dds'
                nrm = blend + '_normal.dds' if blend + '_normal.dds' in texnames else eyes[0][:-4] + '_normal.dds'
                return (mm.group(1) + f'\t\ttexture_diffuse = "{blend}.dds"{nl}'
                        f'\t\ttexture_specular = "{spec}"{nl}\t\ttexture_normal = "{nrm}"{nl}'
                        f'\t\tshader = "moe_portrait_attachment"{nl}' + mm.group(2))
            out.append(s[pos:m.start()]); out.append(SLOT.sub(rep, blk)); pos = m.end()
        out.append(s[pos:]); s = ''.join(out)
        gd = (f'\tgame_data = {{{nl}\t\tportrait_entity_user_data = {{{nl}\t\t\tcolor_mask_remap_interval = {{{nl}'
              f'\t\t\t\tinterval = {{ 0.0 1.0 }}{nl}\t\t\t}}{nl}\t\t}}{nl}\t}}{nl}}}')
        s, ng = GAMEDATA.subn(lambda mm: mm.group(1) + gd, s)
        asset.write_bytes(s.encode('utf-8'))
        print(asset.name, 'eyes+ on', on, 'no eyeblend', miss, 'face/outfit game_data', ng)


if __name__ == '__main__':
    main()
