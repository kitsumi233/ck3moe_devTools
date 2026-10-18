"""Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial."""
from pathlib import Path
import twist_z_preview as shared

ROOT=shared.ROOT
EVIDENCE=Path('F:/CK3_MoeMod_physics_trials/gate_20260927/C10_SOFT1')
# 9 个发布角色的表情修正头 mesh 在 fx_rollout（发布目录不可变，只能放 F: 暂存）
EXTRA=Path('F:/CK3_MoeMod_physics_trials/fx_rollout')
# 洁尔佩塔在独立 root 补烘后拷入，其记录的产物路径仍指向那个 root
PER_ROOT=Path('F:/CK3_MoeMod_physics_trials/gate_20260927/C10_SOFT1_per')
PEA_ROOT=Path('F:/CK3_MoeMod_physics_trials/gate_20260927/C10_SOFT1_pea')
SOURCE='gate_soft1_10'
LABEL='终末地发/尾/耳变软试验（8 角色，头发 4/2.5Hz、尾 ζ1.2、耳 3/2Hz）· 待验'

CHARACTERS=frozenset('lav yvo lin zxu tan dea per pea'.split())

def verified_path(path,digest):
    p=Path(path).resolve()
    if not (p.is_relative_to(EVIDENCE.resolve()) or p.is_relative_to(EXTRA.resolve()) or p.is_relative_to(PER_ROOT.resolve()) or p.is_relative_to(PEA_ROOT.resolve()) or p.is_relative_to((ROOT/'work').resolve())):
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
