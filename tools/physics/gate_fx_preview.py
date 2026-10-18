"""Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial."""
from pathlib import Path
import twist_z_preview as shared

ROOT=shared.ROOT
EVIDENCE=Path('F:/CK3_MoeMod_physics_trials/gate_20260927/C10_FX')
# 9 个发布角色的表情修正头 mesh 在 fx_rollout（发布目录不可变，只能放 F: 暂存）
EXTRA=Path('F:/CK3_MoeMod_physics_trials/fx_rollout')
SOURCE='gate_fx_10'
LABEL='修复版+表情修正（28 角色）· 待验'

CHARACTERS=frozenset('lot vct mis mir prf suo sws clr clb clu cpx mas and zho zhr qiu fay spr spb col kse cen mac mab mos gro grs grz'.split())

def verified_path(path,digest):
    p=Path(path).resolve()
    if not (p.is_relative_to(EVIDENCE.resolve()) or p.is_relative_to(EXTRA.resolve()) or p.is_relative_to((ROOT/'work').resolve())):
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
