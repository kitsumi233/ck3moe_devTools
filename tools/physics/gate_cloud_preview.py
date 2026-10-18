"""Hash verified read only viewer for the cloud FULL92 re-bake, shrunk (15 fps) anims.
The review root is built by build_cloud_review.py: server configuration.json plus a results.json
whose output_hashes point at the local shrunk files (the files that get deployed)."""
from pathlib import Path
import twist_z_preview as shared

ROOT=shared.ROOT
EVIDENCE=Path('D:/有趣计划/CK3_MoeMod/work/physics_trials/gate_20260927/CLOUD_REVIEW')
SHRUNK=Path('D:/有趣计划/CK3_MoeMod/work/physics_trials/gate_20260927/FULL92_shrunk_cloud')
SOURCE='gate_cloud_review'
LABEL='云端重烘 · gate 超基准角色（降空间 15fps 版）· 待验'

CHARACTERS=frozenset('lav mas grz nem lin tan lzy shi tol'.split())

def verified_path(path,digest):
    p=Path(path).resolve()
    if not (p.is_relative_to(SHRUNK.resolve()) or p.is_relative_to((ROOT/'work').resolve())):
        raise ValueError('文件不在云端候选只读范围')
    st=p.stat()
    return shared._verify(str(p),st.st_size,st.st_mtime_ns,digest)

def character_index(ck):
    if ck not in CHARACTERS:raise ValueError('角色不在云端待验名单')
    return shared.character_index(ck,evidence=EVIDENCE,limit_deg=None,allowed=CHARACTERS)

def available_characters():
    out=[]
    for ck in sorted(CHARACTERS):
        try:character_index(ck);out.append(ck)
        except (ValueError,KeyError,OSError):pass
    return out
