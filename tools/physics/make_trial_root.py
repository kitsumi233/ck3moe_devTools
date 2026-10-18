"""Create a small isolated trial root from a base root with an experiment patch.

Copies the native solver, verification folders, options template and the listed
characters' inventories, then writes experiment.json = base experiment merged with
--patch (JSON; top-level keys replace, 'families' and 'solver' are merged per key,
'exclude_pairs' is merged per character). Never touches the base root.
"""
import argparse
import json
from pathlib import Path
import shutil


RELEASE_CONFIG = Path(__file__).resolve().parent / 'response_release.json'


def release_experiment(path=RELEASE_CONFIG):
    """Expand the repository release config into a per-character experiment.

    families_by_game is keyed by the moe_chars game prefix ('endfield' also
    covers 'endfield_2') and wins over families_by_character for the families it
    names, so per-character hair overrides survive for families it leaves alone.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
    import moe_chars
    cfg = json.loads(Path(path).read_text(encoding='utf8'))
    e = {k: v for k, v in cfg.items() if k not in ('_doc', 'families_by_game')}
    byc = {ck: dict(v) for ck, v in cfg.get('families_by_character', {}).items()}
    for game, fams in cfg.get('families_by_game', {}).items():
        fams = {k: v for k, v in fams.items() if not k.startswith('_')}
        for c in moe_chars.CHARS:
            g = str(c.get('game', ''))
            if g == game or g.startswith(game + '_'):
                byc.setdefault(c['ck'], {}).update(fams)
    e['families_by_character'] = byc
    repo = Path(__file__).resolve().parents[2]
    e['mesh_overrides_by_character'] = {ck: {part: str(repo / rel) for part, rel in parts.items()}
                                        for ck, parts in cfg.get('mesh_overrides_by_character', {}).items()}
    e['head_sources_by_character'] = {ck: str(repo / rel) for ck, rel in cfg.get('head_sources_by_character', {}).items()}
    return e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', type=Path, required=True)
    ap.add_argument('--release-config', nargs='?', const=RELEASE_CONFIG, type=Path,
                    help='take physics parameters from the repository release config instead of the base experiment')
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--characters', nargs='+', required=True)
    ap.add_argument('--actions', nargs='+')
    ap.add_argument('--patch', default='{}', help='JSON merged into the base experiment')
    ap.add_argument('--name', required=True)
    a = ap.parse_args()
    if a.out.exists():
        raise SystemExit(f'{a.out} exists; choose a fresh root')
    a.out.mkdir(parents=True)
    for d in ('native', 'native_bone_twist', 'native_verified_v1'):
        shutil.copytree(a.base / d, a.out / d)
    for f in ('options_template.json', 'inventory.json'):
        shutil.copy2(a.base / f, a.out / f)
    for ck in a.characters:
        shutil.copy2(a.base / f'{ck}_inventory.json', a.out / f'{ck}_inventory.json')
    e = json.loads((a.base / 'experiment.json').read_text(encoding='utf8'))
    if a.release_config:
        e = release_experiment(a.release_config)
    patch = json.loads(Path(a.patch[1:]).read_text(encoding='utf-8') if a.patch.startswith('@') else a.patch)  # @file：Windows 命令行 32K 上限装不下全量 patch
    for key, value in patch.items():
        if key in ('families', 'solver', 'exclude_pairs', 'admit_pairs', 'families_by_character', 'keep_zero_lock_names_by_character',
                   'mesh_overrides_by_character', 'family_tokens_by_character', 'head_sources_by_character', 'bone_twist_families_by_character', 'zero_lock_limit_deg_by_character', 'widen_limits_deg_by_character', 'mass_floor_by_character', 'action_overrides_by_character', 'leg_skin_fit_by_character', 'solver_by_character', 'follow_rigids_by_character', 'linear_limit_scale_by_character', 'joint_frame_limits_by_character') and isinstance(value, dict):
            e.setdefault(key, {}).update(value)
        else:
            e[key] = value
    e['name'] = a.name
    (a.out / 'experiment.json').write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding='utf8')
    scope = json.loads((a.base / 'trial_scope.json').read_text(encoding='utf8'))
    scope['characters'] = list(a.characters)
    if a.actions:
        scope['actions'] = list(a.actions)
    (a.out / 'trial_scope.json').write_text(json.dumps(scope, ensure_ascii=False, indent=2), encoding='utf8')
    print(a.out, json.dumps(patch, ensure_ascii=False))


if __name__ == '__main__':
    main()
