"""Hash-verified read-only viewer source for the representative trial review."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
FIVE_ROLE = Path(r'F:\CK3_MoeMod_physics_trials\bone_twist_20260926_five_role10_tuned_v1')
ALL92 = Path(r'F:\CK3_MoeMod_physics_trials\bone_twist_20260926_all92_tuned_v2')
SCREEN_ROOT = Path(r'F:\CK3_MoeMod_physics_trials\representative_review_20260927')
GEOMETRY_ROOT = SCREEN_ROOT
ROLE_ROOTS = {role: FIVE_ROLE for role in ('lau', 'cyu', 'lin', 'tep', 'zfy')}
ROLE_ROOTS.update({role: ALL92 for role in ('jia', 'cen', 'emb')})
REVIEW_ACTIONS = {
    'lau': ('sitting', 'dance_spin'),
    'cyu': ('sitting', 'dance_spin'),
    'lin': ('emotion_shock', 'dance_spin'),
    'zfy': ('dance_spin',),
    'tep': ('emotion_angry_start', 'dance_spin'),
    'jia': ('sitting', 'dance_spin'),
    'cen': ('jockey_lance_couched_gallop', 'sitting'),
    'emb': ('emotion_laugh', 'dance_spin'),
}
SOURCE = 'response_trial_review'
LABEL = 'Local Z lock review candidate - not accepted'


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


@lru_cache(maxsize=4096)
def _verify(path, size, mtime, digest):
    target = Path(path)
    if _sha(target) != digest:
        raise ValueError('Trial candidate SHA mismatch: ' + target.name)
    if (target.stat().st_size, target.stat().st_mtime_ns) != (size, mtime):
        raise ValueError('Trial candidate changed while loading: ' + target.name)
    return target


def verified_path(path, digest):
    target = Path(path).resolve()
    allowed = tuple(root.resolve() for root in (FIVE_ROLE, ALL92, SCREEN_ROOT, ROOT / 'work'))
    if not any(target.is_relative_to(root) for root in allowed):
        raise ValueError('Trial viewer file is outside the configured read-only roots')
    if not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('Missing trial candidate SHA')
    stat = target.stat()
    return _verify(str(target), stat.st_size, stat.st_mtime_ns, digest)


def character_index(character):
    if character not in ROLE_ROOTS:
        raise ValueError('Character is outside the representative trial panel')
    folder = ROLE_ROOTS[character] / 'characters' / character
    cfg = json.loads((folder / 'configuration.json').read_text(encoding='utf-8'))
    result = json.loads((folder / 'results.json').read_text(encoding='utf-8'))
    screen_path = SCREEN_ROOT / 'characters' / character / 'surface_screen.json'
    screen = json.loads(screen_path.read_text(encoding='utf-8'))
    if (cfg.get('character') != character or result.get('character') != character or
            result.get('status') != 'baked' or result.get('accepted') or result.get('installed') or
            screen.get('status') != 'diagnostic_surface_triage' or screen.get('accepted')):
        raise ValueError('Representative candidate is incomplete or not read-only')
    available = set(cfg.get('actions', ())) & set(screen.get('actions', ()))
    actions = {}
    for action in REVIEW_ACTIONS[character]:
        if action not in available:
            raise ValueError(f'Missing screened candidate action: {character}/{action}')
        geometry_path = GEOMETRY_ROOT / 'characters' / character / f'{action}_geometry.json'
        geometry = json.loads(geometry_path.read_text(encoding='utf-8'))
        outputs = {}
        for part in ('head', 'body'):
            matches = [(path, digest) for path, digest in result['output_hashes'].items()
                       if Path(path).name.endswith(f'_{part}_{action}.anim')]
            if len(matches) != 1:
                raise ValueError(f'Head/body pair is incomplete: {character}/{action}/{part}')
            path, digest = matches[0]
            if geometry['variants']['candidate']['anim_sha'].get(part) != digest:
                raise ValueError(f'Surface screen and geometry use different candidates: {character}/{action}')
            outputs[part] = dict(path=path, sha256=digest)
        actions[action] = dict(status='complete', outputs=outputs)
    meshes = {'head': cfg['meshes']['head'], 'body': cfg['meshes']['torso']}
    mesh_sha = {part: cfg['source_hashes'][path]['sha256'] for part, path in meshes.items()}
    return dict(actions=actions, meshes=meshes, mesh_sha256=mesh_sha,
                finished=(folder / 'results.json').stat().st_mtime)


def available_characters():
    result = []
    for character in ROLE_ROOTS:
        try:
            character_index(character)
            result.append(character)
        except (OSError, KeyError, ValueError):
            continue
    return result
