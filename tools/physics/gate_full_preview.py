"""Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial."""
from pathlib import Path
import twist_z_preview as shared

ROOT=shared.ROOT
EVIDENCE=Path('F:/CK3_MoeMod_physics_trials/gate_20260927/C10_FULL8')
SOURCE='gate_full_5roles10'
LABEL='定案候选：头发旧参数 + 按深度推开0.03/0.15 + 写回低通1.5Hz + 奇异骨不去自转 + 接缝淡入定号 · 五角色十动作 · 待验'

CHARACTERS=frozenset(('lau','cyu','lin','zfy','tep'))

def verified_path(path,digest):
    p=Path(path).resolve()
    if not (p.is_relative_to(EVIDENCE.resolve()) or p.is_relative_to((ROOT/'work').resolve())):
        raise ValueError('文件不在快速门只读候选范围')
    st=p.stat()
    return shared._verify(str(p),st.st_size,st.st_mtime_ns,digest)

def character_index(ck):
    if ck not in CHARACTERS:raise ValueError('角色不在快速门五角色候选中')
    result=shared.json.loads((EVIDENCE/'characters'/ck/'results.json').read_text(encoding='utf8'))
    if result['status']!='baked':raise ValueError('角色烘焙未完成')
    return shared.character_index(ck,evidence=EVIDENCE,limit_deg=None)

def available_characters():
    out=[]
    for ck in sorted(CHARACTERS):
        try:character_index(ck);out.append(ck)
        except (ValueError,KeyError,OSError):pass
    return out
