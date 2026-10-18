"""Read-only five-role zero local Z twist candidate, with immutable file hashes."""
from pathlib import Path
import re
import twist_z_preview as shared

ROOT=shared.ROOT
EVIDENCE=Path('F:/CK3_MoeMod_physics_trials/twist_z0_5roles10')
SOURCE='twist_z0_5roles10'
LABEL='旧版关节 Z 锁定失败 · 仅供失败对照 · 五角色十动作'


def verified_path(path,digest):
    p=Path(path).resolve()
    if not (p.is_relative_to(EVIDENCE.resolve()) or p.is_relative_to((ROOT/'work').resolve())):
        raise ValueError('文件不在只读候选范围')
    if not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('缺少 SHA')
    st=p.stat();return shared._verify(str(p),st.st_size,st.st_mtime_ns,digest)


def character_index(ck):
    return shared.character_index(ck,evidence=EVIDENCE,limit_deg=0)


def available_characters():
    out=[]
    for ck in sorted(shared.CHARACTERS):
        try:character_index(ck);out.append(ck)
        except (ValueError,KeyError,OSError):pass
    return out
