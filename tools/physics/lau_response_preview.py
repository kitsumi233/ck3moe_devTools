"""Hash-verified read-only access to the Lau ten-action tuning trial."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]
EVIDENCE=Path('F:/CK3_MoeMod_physics_trials/lau_all_spring_skirt_damped10')
SOURCE='lau_all_spring_skirt_damped10'
LABEL='???????????? ? 10?? ? ????'


@lru_cache(maxsize=4096)
def _verify(path,size,mtime,digest):
    p=Path(path)
    with p.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
    if actual!=digest:raise ValueError('候选文件 SHA 不匹配：'+p.name)
    if (p.stat().st_size,p.stat().st_mtime_ns)!=(size,mtime):raise ValueError('校验时文件发生变化')
    return p


def verified_path(path,digest):
    p=Path(path).resolve()
    if not (p.is_relative_to(EVIDENCE.resolve()) or p.is_relative_to((ROOT/'work').resolve())):
        raise ValueError('文件不在只读候选范围')
    if not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('缺少 SHA')
    st=p.stat();return _verify(str(p),st.st_size,st.st_mtime_ns,digest)


def character_index(ck):
    if not re.fullmatch('[a-z]{3}',ck):raise ValueError('无效角色')
    folder=EVIDENCE/'characters'/ck
    try:
        cfg=json.loads((folder/'configuration.json').read_text(encoding='utf8'))
        result=json.loads((folder/'results.json').read_text(encoding='utf8'))
    except OSError as e:raise ValueError('该角色候选尚未完成') from e
    if cfg['character']!=ck or result['character']!=ck or result['status']!='baked':raise ValueError('候选状态不符')
    actions={}
    for action in cfg['actions']:
        outputs={}
        for part in ('head','body'):
            matches=[(p,h) for p,h in result['output_hashes'].items() if Path(p).name.endswith('_'+part+'_'+action+'.anim')]
            if len(matches)!=1:raise ValueError('头身配对缺失')
            p,h=matches[0];outputs[part]=dict(path=p,sha256=h)
        actions[action]=dict(status='complete',outputs=outputs)
    meshes={p:cfg['meshes'][m] for p,m in [('head','head'),('body','torso')]}
    return dict(actions=actions,meshes=meshes,mesh_sha256={p:cfg['source_hashes'][path]['sha256'] for p,path in meshes.items()},
                finished=(folder/'results.json').stat().st_mtime)


def available_characters():
    out=[]
    for p in sorted((EVIDENCE/'characters').glob('*/results.json')):
        try:character_index(p.parent.name);out.append(p.parent.name)
        except (ValueError,KeyError,OSError):pass
    return out
