"""Hash-verified, read-only Lau sitting variants for the physics viewer."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=4096)
def _verify(path, size, mtime, digest):
    p = Path(path)
    with p.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != digest:
        raise ValueError('候选动画 SHA 不匹配：' + p.name)
    if (p.stat().st_size, p.stat().st_mtime_ns) != (size, mtime):
        raise ValueError('核验后候选动画已改变：' + p.name)
    return p


class SittingTrialPreview:
    def __init__(self, evidence, source, label):
        self.EVIDENCE = Path(evidence)
        self.SOURCE = source
        self.LABEL = label

    def verified_path(self, path, digest):
        p = Path(path).resolve()
        if not (p.is_relative_to(self.EVIDENCE.resolve()) or
                p.is_relative_to((ROOT / 'work').resolve())):
            raise ValueError('候选动画或模型网格不在登记的只读范围内')
        if not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('候选动画缺少有效 SHA-256')
        stat = p.stat()
        return _verify(str(p), stat.st_size, stat.st_mtime_ns, digest)

    def character_index(self, character):
        if character != 'lau':
            raise ValueError('该预览只包含洛茜')
        folder = self.EVIDENCE / 'characters' / character
        cfg = json.loads((folder / 'configuration.json').read_text(encoding='utf-8'))
        result = json.loads((folder / 'results.json').read_text(encoding='utf-8'))
        contract = json.loads((folder / 'response_contract.json').read_text(encoding='utf-8'))
        if cfg.get('character') != character or result.get('character') != character or result.get('status') != 'baked':
            raise ValueError('洛茜坐姿候选尚未完成烘焙')
        if 'sitting' not in cfg.get('actions', ()) or result.get('accepted') or result.get('installed'):
            raise ValueError('坐姿候选范围或只读状态无效')
        if not contract.get('bone_twist_lock') or any(
            contract.get(key, 0) for key in ('new_angular_limits', 'new_swing_limits', 'new_translation_limits')
        ):
            raise ValueError('坐姿候选的骨骼约束合同不符合审核范围')
        outputs = {}
        for part in ('head', 'body'):
            matches = [(path, digest) for path, digest in result['output_hashes'].items()
                       if Path(path).name.endswith(f'_{part}_sitting.anim')]
            if len(matches) != 1:
                raise ValueError(f'洛茜坐姿缺少唯一的{part}动画')
            path, digest = matches[0]
            outputs[part] = dict(path=path, sha256=digest)
        meshes = {part: cfg['meshes'][key] for part, key in (('head', 'head'), ('body', 'torso'))}
        return dict(actions={'sitting': dict(status='complete', outputs=outputs)}, meshes=meshes,
                    mesh_sha256={part: cfg['source_hashes'][path]['sha256'] for part, path in meshes.items()},
                    finished=(folder / 'results.json').stat().st_mtime)

    def available_characters(self):
        try:
            self.character_index('lau')
            return ['lau']
        except (ValueError, KeyError, OSError):
            return []


PROVIDERS = (
    SittingTrialPreview(
        Path(r'F:/CK3_MoeMod_physics_trials/bone_twist_20260926_lau_dynamics_spring_only10_v1'),
        'lau_sitting_spring_v1',
        '洛茜坐姿 · 弹簧阻尼基线 · 无额外风力 · 待审候选',
    ),
    SittingTrialPreview(
        Path(r'F:/CK3_MoeMod_physics_trials/bone_twist_20260926_lau_sitting_dynamic_v2'),
        'lau_sitting_dynamic_v1',
        '洛茜坐姿 · 低频水平风动态候选 · 裙腿交叉风险，待直接预览',
    ),
)
