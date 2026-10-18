"""Hash-verified, read-only preview for the user-paused all-role candidate."""
import json
import re
from pathlib import Path

import twist_z_preview as shared

ROOT = shared.ROOT
EVIDENCE = Path('F:/CK3_MoeMod_physics_trials/bone_twist_20260926_all92_tuned_v2')
SOURCE = 'bone_local_z_lock_partial92'
LABEL = '物理骨骼纵向局部 Z 单自由度锁定 · 已完成角色逐动作审核 · 弹簧与阻尼候选'


def verified_path(path, digest):
    p = Path(path).resolve()
    if not (p.is_relative_to(EVIDENCE.resolve()) or p.is_relative_to((ROOT / 'work').resolve())):
        raise ValueError('预览文件不在只读候选范围')
    if not re.fullmatch('[a-f0-9]{64}', digest):
        raise ValueError('候选动画缺少 SHA-256')
    st = p.stat()
    return shared._verify(str(p), st.st_size, st.st_mtime_ns, digest)


def character_index(ck):
    if not re.fullmatch('[a-z]{3}', ck):
        raise ValueError('无效角色标识')
    folder = EVIDENCE / 'characters' / ck
    try:
        cfg = json.loads((folder / 'configuration.json').read_text(encoding='utf-8'))
        result = json.loads((folder / 'results.json').read_text(encoding='utf-8'))
        contract = json.loads((folder / 'response_contract.json').read_text(encoding='utf-8'))
    except OSError as exc:
        raise ValueError('角色没有完整烘焙结果') from exc
    experiment = cfg.get('experiment', {})
    if cfg.get('character') != ck or result.get('character') != ck or result.get('status') != 'baked':
        raise ValueError('角色烘焙未完成')
    if not experiment.get('bone_twist_lock') or experiment.get('twist_z_limit_deg') is not None:
        raise ValueError('角色没有使用物理骨骼纵向 Z 锁')
    if not contract.get('bone_twist_lock') or not contract.get('bone_twist_locks'):
        raise ValueError('角色缺少物理骨骼锁合同')
    if any(contract.get(key, 0) for key in ('new_angular_limits', 'new_swing_limits', 'new_translation_limits')):
        raise ValueError('角色候选含有额外角度或位移限制')
    actions = {}
    for action in cfg['actions']:
        outputs = {}
        for part in ('head', 'body'):
            matches = [(path, digest) for path, digest in result['output_hashes'].items()
                       if Path(path).name.endswith(f'_{part}_{action}.anim')]
            if len(matches) != 1:
                raise ValueError(f'{action} 缺少唯一头身配对动画')
            path, digest = matches[0]
            outputs[part] = dict(path=path, sha256=digest)
        actions[action] = dict(status='complete', outputs=outputs)
    mesh_paths = {part: cfg['meshes'][key] for part, key in (('head', 'head'), ('body', 'torso'))}
    return dict(actions=actions, meshes=mesh_paths,
                mesh_sha256={part: cfg['source_hashes'][path]['sha256'] for part, path in mesh_paths.items()},
                finished=(folder / 'results.json').stat().st_mtime)


def available_characters():
    out = []
    for result_path in sorted((EVIDENCE / 'characters').glob('*/results.json')):
        ck = result_path.parent.name
        try:
            character_index(ck)
            out.append(ck)
        except (ValueError, KeyError, OSError):
            continue
    return out
