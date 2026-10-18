"""Hash verified read only viewer for the longitudinal bone Z lock candidate."""
from pathlib import Path
import twist_z_preview as shared

ROOT=shared.ROOT
EVIDENCE=Path('F:/CK3_MoeMod_physics_trials/bone_twist_20260926_five_role10_v2')
SOURCE='bone_local_z_lock_5roles10'
LABEL='物理骨骼纵向 Z 单自由度零锁 · 洛茜、陈千语、梨诺、庄方宜、提弗洛斯 · 固定十动作 · 待验候选'

CHARACTERS=frozenset(('lau','cyu','lin','zfy','tep'))

def verified_path(path,digest):
    p=Path(path).resolve()
    if not (p.is_relative_to(EVIDENCE.resolve()) or p.is_relative_to((ROOT/'work').resolve())):
        raise ValueError('文件不在骨骼 Z 只读候选范围')
    st=p.stat()
    return shared._verify(str(p),st.st_size,st.st_mtime_ns,digest)

def character_index(ck):
    if ck not in CHARACTERS:raise ValueError('角色不在五角色骨骼 Z 候选中')
    index=shared.character_index(ck,evidence=EVIDENCE,limit_deg=None)
    contract=shared.json.loads((EVIDENCE/'characters'/ck/'response_contract.json').read_text(encoding='utf8'))
    result=shared.json.loads((EVIDENCE/'characters'/ck/'results.json').read_text(encoding='utf8'))
    if not contract.get('bone_twist_lock') or not contract.get('bone_twist_locks'):
        raise ValueError('缺少每骨纵向 Z 锁合同')
    if result['status']!='baked':raise ValueError('角色烘焙未完成')
    return index

def available_characters():
    out=[]
    for ck in sorted(CHARACTERS):
        try:character_index(ck);out.append(ck)
        except (ValueError,KeyError,OSError):pass
    return out
