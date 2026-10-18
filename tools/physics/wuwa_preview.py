"""Hash verified read only viewer for the 鸣潮 (Wuthering Waves) 44-role 10-action bake.
Results live in three isolated roots (2026-10-09): W10b (39 roles), W10c (wbz/wcr/wct/wyl, zero-size
rigids + negative springs), W10d (wfl, ±π swing limits). Each role is read from the latest root
where it baked."""
from pathlib import Path
import twist_z_preview as shared

ROOT=shared.ROOT
ROOTS=[Path(f'F:/wuwa_phys/{r}') for r in ('W14',)]   # 2026-10-13: W14 全量 = 10-13 合并重生成动画（付与骨+整腿外展+spine_up）+ patch_W14（W13 全部 + 鉴心飘带 joint_frame_limits）
EVIDENCE=ROOTS[-1]
SOURCE='wuwa_10main'
LABEL='鸣潮 44 角色 · 10 主动作（W14：新动画 + 反馈修复）· 待验'

import sys
sys.path.insert(0,str(ROOT/'tools'/'lib'))
import moe_chars as _X
CHARACTERS=frozenset(c['ck'] for c in _X.CHARS if c['game'].startswith('wuwa'))


def _root_of(ck):
    for r in ROOTS:
        f=r/'characters'/ck/'results.json'
        if f.is_file() and shared.json.loads(f.read_text(encoding='utf8')).get('status')=='baked':
            return r
    raise ValueError('角色烘焙未完成')


def verified_path(path,digest):
    p=Path(path).resolve()
    if not (any(p.is_relative_to(r.resolve()) for r in ROOTS) or p.is_relative_to((ROOT/'work').resolve())):
        raise ValueError('文件不在鸣潮只读候选范围')
    st=p.stat()
    return shared._verify(str(p),st.st_size,st.st_mtime_ns,digest)


def character_index(ck):
    if ck not in CHARACTERS:raise ValueError('角色不在鸣潮候选中')
    return shared.character_index(ck,evidence=_root_of(ck),limit_deg=None,allowed=CHARACTERS)


def available_characters():
    out=[]
    for ck in sorted(CHARACTERS):
        try:character_index(ck);out.append(ck)
        except (ValueError,KeyError,OSError):pass
    return out
