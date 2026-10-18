"""Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial."""
from pathlib import Path
import twist_z_preview as shared

ROOT=shared.ROOT
EVIDENCE=Path('F:/CK3_MoeMod_physics_trials/gate_20260927/C10_ALL92')
SOURCE='gate_all92_10'
LABEL='定案设置 · 92 角色十动作试烘 · 待验'

CHARACTERS=frozenset(('ada','aet','ale','and','ant','avi','biy','byp','cen','cha','che','chr','chs','clb','clr','clu','col','cpx','cyu','dai','das','dea','dus','eld','emb','fad','fay','flo','flu','gro','grs','grz','jia','jun','kac','kam','kfm','kfw','kse','lau','lav','lec','len','lin','lit','lnc','lom','lot','lzy','mab','mac','mad','mas','mif','mir','mis','mos','nag','nem','pan','pea','per','prf','prh','pri','qil','qio','qiu','sab','sbs','sei','sha','shi','spb','spr','suo','sws','tan','tep','tol','tos','ull','vct','vec','vep','vps','wol','yvo','zfy','zho','zhr','zxu'))

def verified_path(path,digest):
    p=Path(path).resolve()
    if not (p.is_relative_to(EVIDENCE.resolve()) or p.is_relative_to((ROOT/'work').resolve())):
        raise ValueError('文件不在快速门只读候选范围')
    st=p.stat()
    return shared._verify(str(p),st.st_size,st.st_mtime_ns,digest)

def character_index(ck):
    if ck not in CHARACTERS:raise ValueError('角色不在 92 角色候选中')
    result=shared.json.loads((EVIDENCE/'characters'/ck/'results.json').read_text(encoding='utf8'))
    if result['status']!='baked':raise ValueError('角色烘焙未完成')
    return shared.character_index(ck,evidence=EVIDENCE,limit_deg=None,allowed=CHARACTERS)

def available_characters():
    out=[]
    for ck in sorted(CHARACTERS):
        try:character_index(ck);out.append(ck)
        except (ValueError,KeyError,OSError):pass
    return out
