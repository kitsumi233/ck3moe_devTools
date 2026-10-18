"""Keep verified physics inputs in work/ and publish one animation per model path.

The cache is a build input, not a loadable mod. Its manifest binds every animation
to the canonical mesh/animation inputs. Rebuilding models restores base files;
finalize then checks the cache and copies complete physical files over those paths.
No exported animation channels or mesh weights are edited here.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import time

import moe_chars as X
import moe_out as O
import pdx_parse as P

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / 'work' / 'physics_release'
ASSET_REL = Path('gfx/models/portraits/moe')


def signature(path):
    path = Path(path)
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'bytes': path.stat().st_size, 'sha256': digest}


def _stat_barrier_item(path):
    stat = path.stat()
    return path, stat.st_size, stat.st_mtime_ns


def inventory(paths, jobs=12, *, pool=None):
    paths = sorted(paths)
    if pool is not None:
        return dict(zip((p.name for p in paths), pool.map(signature, paths)))
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return dict(zip((p.name for p in paths), pool.map(signature, paths)))


def cache_root(sources=None):
    from moe_release_sources import resolve, _work_path
    selected=resolve(sources)
    return _work_path(selected['data']['physics_release_root']) if selected else CACHE


def canonical_files(ck, *, sources=None):
    """Map final prefixed names to canonical inputs, rejecting ambiguous names."""
    prefix = X.prefix_of(ck)
    result = {}
    from moe_release_sources import role
    selected=role(ck,sources)
    directory=selected['canonical'] if selected else X.bake_dir(ck)
    for path in sorted(directory.iterdir()):
        if path.suffix not in ('.anim', '.mesh') or not path.is_file():
            continue
        name = prefix + path.name[4:] if path.name.startswith('moe_') else path.name
        if name in result:
            raise ValueError(f'{ck}: duplicate canonical name {name}')
        result[name] = path
    return result


def _canonical_inventory(ck, jobs, *, sources=None, pool=None):
    paths = canonical_files(ck,sources=sources)
    if pool is not None:
        return dict(zip(paths, pool.map(signature, paths.values())))
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return dict(zip(paths, pool.map(signature, paths.values())))


def source_dir(ck, *, sources=None):
    if ck not in X.BY_CK:
        raise ValueError(f'Unknown character: {ck}')
    return cache_root(sources) / ck / 'anims'


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def adopt(ck, directory, *, provenance, jobs=12, sources=None):
    """Adopt a complete, already quality-reviewed character's physics outputs.

    This checks byte provenance and input compatibility, not physical aesthetics.
    Replacing an existing release cache needs a new explicit adoption; ordinary
    builds never accept changed inputs or silently discard stale physics.
    """
    directory = Path(directory).resolve()
    destination = source_dir(ck,sources=sources)
    if destination.parent.exists():
        raise FileExistsError(f'Physics cache already exists: {destination.parent}')
    canonical = canonical_files(ck,sources=sources)
    from moe_release_sources import role
    selected_sources=role(ck,sources)
    if selected_sources is not None:
        from moe_base_inputs import require_base_animation_inputs
        require_base_animation_inputs(ck,verify_hashes=True,sources=sources)
    prefix = X.prefix_of(ck)
    physical = sorted(directory.glob('*.anim'))
    heads = [p for p in physical if p.name.startswith(prefix + 'head_')]
    bodies = [p for p in physical if p.name.startswith(prefix + 'body_')]
    if len(heads) != 331 or len(bodies) != 338 or len(physical) != 669:
        raise ValueError(f'{ck}: expected reviewed 331 head + 338 body files, got {len(heads)}/{len(bodies)}/{len(physical)}')
    for path in physical:
        if path.name not in canonical:
            raise ValueError(f'{ck}: no canonical counterpart for {path.name}')
        actual, base = P.anim_info(path), P.anim_info(canonical[path.name])
        if any(actual.get(key) != base.get(key) for key in ('j', 'fps', 'sa')):
            raise ValueError(f'{ck}: animation metadata differs: {path.name}: {actual} vs {base}')
    print(f'  {ck}: binding 669 physics files to canonical inputs', flush=True)
    inputs = _canonical_inventory(ck, jobs,sources=sources)
    records = inventory(physical, jobs)
    stage = cache_root(sources) / (ck + '.staging')
    if stage.exists():
        raise FileExistsError(f'Inspect incomplete adoption first: {stage}')
    (stage / 'anims').mkdir(parents=True)
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        list(pool.map(lambda p: shutil.copy2(p, stage / 'anims' / p.name), physical))
    if inventory((stage / 'anims').glob('*.anim'), jobs) != records:
        raise ValueError(f'{ck}: copied physics cache differs')
    # Inputs may be edited or rebuilt while a large copy is running.
    if _canonical_inventory(ck, jobs,sources=sources) != inputs:
        raise ValueError(f'{ck}: canonical inputs changed during adoption')
    manifest = {'schema': 1, 'character': ck, 'status': 'verified_inputs',
                'created': time.time(), 'source': str(directory),
                'provenance': str(provenance), 'canonical': inputs, 'physics': records,
                'quality_review': 'provided by cited provenance; not inferred from file checks'}
    if selected_sources is not None:
        selected=selected_sources
        manifest['release_source_provenance']=selected['provenance']
        manifest['base_input_manifest']=dict(path=str(selected['base'].parent/'manifest.json'),
            sha256=signature(selected['base'].parent/'manifest.json')['sha256'])
    _write_json(stage / 'manifest.json', manifest)
    stage.rename(destination.parent)
    return manifest


def _load_verified(ck, jobs, *, hash_jobs=None, hash_pool=None, sources=None):
    # ``jobs`` is the caller's total concurrency budget.  A materialize
    # worker may reserve only part of it for file hashing so that several
    # characters can be preflighted at once without creating jobs**2
    # threads.  Keep the old positional API intact for adoption callers.
    hash_jobs = jobs if hash_jobs is None else max(1, hash_jobs)
    path = source_dir(ck,sources=sources).parent / 'manifest.json'
    watched = [path, *canonical_files(ck,sources=sources).values(), *source_dir(ck,sources=sources).glob('*.anim')]
    if hash_pool is None:
        barrier = [_stat_barrier_item(p) for p in watched]
    else:
        barrier = list(hash_pool.map(_stat_barrier_item, watched))
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema') != 1 or data.get('character') != ck or data.get('status') != 'verified_inputs':
        raise ValueError(f'Invalid physics cache manifest: {path}')
    if _canonical_inventory(ck, hash_jobs, sources=sources,
                            pool=hash_pool) != data['canonical']:
        raise ValueError(f'{ck}: physics was baked against different canonical inputs; recompose/rebake and explicitly adopt before publishing')
    if inventory(source_dir(ck,sources=sources).glob('*.anim'), hash_jobs,
                 pool=hash_pool) != data['physics']:
        raise ValueError(f'{ck}: physics cache inventory/hash changed')
    if 'base_input_manifest' in data:
        from moe_release_sources import reference
        reference(data['base_input_manifest'])
    _check_barrier(barrier)
    data['_barrier'] = barrier
    return data


def _check_barrier(barrier):
    for path, size, mtime in barrier:
        stat = path.stat()
        if (stat.st_size, stat.st_mtime_ns) != (size, mtime):
            raise ValueError(f'Input changed during physics publication preflight: {path}')


def _check_published_meshes(ck, canonical, expected=None):
    barrier = []
    prefix = X.prefix_of(ck)
    for part in ('head', 'torso'):
        name = f'{prefix}{part}.mesh'
        source = canonical[name].read_bytes()
        if expected is not None and {'bytes': len(source), 'sha256': hashlib.sha256(source).hexdigest()} != expected[name]:
            raise ValueError(f'{ck}: {part} mesh changed after canonical verification')
        # This is the same object-name-only transformation as build_moe_mod.
        source = source.replace(f'moe_{part}_mesh\0'.encode(),
                                f'{prefix}{part}_mesh\0'.encode())
        target = O.root(O.models_of(ck)) / ASSET_REL / 'models' / name
        before = target.stat()
        if target.read_bytes() != source:
            raise ValueError(f'{ck}: published {part} mesh differs from current canonical input')
        barrier.append((target, before.st_size, before.st_mtime_ns))
    return barrier


def _ensure_base_inputs(ck, jobs, *, sources=None):
    from moe_release_sources import role
    selected=role(ck,sources)
    if selected:
        from moe_base_inputs import require_base_animation_inputs
        # Immutable release sources must already be complete; builds cannot
        # silently replace historical inputs or repair a corrupt selected base.
        require_base_animation_inputs(ck,sources=sources)
        return
    import moe_paths as MP
    try:
        MP.require_base_anims(ck)
    except (FileNotFoundError, ValueError):
        print(f'  {ck}: refresh unsimulated work inputs', flush=True)
        MP.sync_base_animation_inputs(ck, jobs=jobs)


def materialize(*, no_physics=False, jobs=12, sources=None):
    """Preflight all release inputs, then atomically replace full animation files."""
    # Resolve the immutable source once.  ``role()`` and ``canonical_files()``
    # are called thousands of times below; repeatedly validating every role in
    # the release manifest otherwise becomes the serial bottleneck before any
    # hash worker receives work.
    selected_sources = None
    if sources is not None:
        from moe_release_sources import resolve
        selected_sources = resolve(sources)
    else:
        from moe_release_sources import resolve
        selected_sources = resolve()
    selected_cache=cache_root(selected_sources)
    index_path = selected_cache / 'index.json'
    if not index_path.is_file():
        raise RuntimeError('Missing physics release index; explicitly adopt verified inputs before deployment')
    index = json.loads(index_path.read_text(encoding='utf-8'))
    characters = index['characters']
    active = {c['ck'] for c in X.active() if c.get('release_managed', True)}
    if (index.get('schema') != 1 or not characters
            or len(characters) != len(set(characters))
            or set(characters) != active or set(index.get('manifests', {})) != active):
        raise ValueError('Invalid physics cache character index')
    # Reserve a role planning pool and one shared hash pool within the total
    # budget.  With jobs=12 this is 3 role workers and 9 SHA workers.  A
    # shared pool is deliberate: nested per-role pools can leave only the
    # outer workers active on Windows and never use the available CPU.  All
    # preflight work remains read-only; installation stays behind the global
    # barrier and keeps its atomic copy/verify/replace semantics.
    role_workers = min(len(characters), max(1, min(4, jobs // 4)))
    hash_jobs = max(1, jobs - role_workers)
    plans = []
    index_stat = index_path.stat()
    barrier = [(index_path, index_stat.st_size, index_stat.st_mtime_ns)]
    if selected_sources is not None:
        # The active pointer and immutable source manifest are the selection
        # contract.  They must remain unchanged while workers read the role
        # files, even though the validated config is reused in memory.
        from moe_release_sources import ACTIVE
        for watched_source in (ACTIVE, Path(selected_sources['path'])):
            if watched_source.is_file():
                stat = watched_source.stat()
                barrier.append((watched_source, stat.st_size, stat.st_mtime_ns))
    print(f'验证 {len(characters)} 个形态的物理源与基础输入指纹 '
          f'（角色并行 {role_workers}，共享哈希 {hash_jobs}）：', flush=True)

    def plan_character(ck, hash_pool):
        print(f'  {ck}: preflight start', flush=True)
        manifest_path = source_dir(ck,sources=selected_sources).parent / 'manifest.json'
        if signature(manifest_path)['sha256'] != index['manifests'][ck]:
            raise ValueError(f'{ck}: physics manifest differs from release index')
        data = (json.loads(manifest_path.read_text(encoding='utf-8'))
                if no_physics else _load_verified(
                    ck, jobs, hash_jobs=hash_jobs, hash_pool=hash_pool,
                    sources=selected_sources))
        canonical = canonical_files(ck,sources=selected_sources)
        local_barrier = list(data.get('_barrier', []))
        local_barrier.extend(_check_published_meshes(
            ck, canonical, None if no_physics else data['canonical']))
        destination = O.root(O.models_of(ck)) / ASSET_REL / ck / 'anims'
        local_plans = []
        for name, physical_signature in data['physics'].items():
            if Path(name).name != name or name not in canonical:
                raise ValueError(f'{ck}: invalid physics animation path: {name}')
            target = destination / name
            if not target.is_file():
                raise ValueError(f'Model animation missing before integration: {target}')
            src = canonical[name] if no_physics else source_dir(ck,sources=selected_sources) / name
            expected = signature(src) if no_physics else physical_signature
            if no_physics:
                stat = src.stat()
                local_barrier.append((src, stat.st_size, stat.st_mtime_ns))
            local_plans.append((src, target, expected))
        print(f'  {ck}: preflight complete ({len(local_plans)} files)', flush=True)
        return ck, local_barrier, local_plans

    # executor.map preserves input order, so barrier and copy-plan order stay
    # deterministic even though role planning and file hashing are concurrent.
    with ThreadPoolExecutor(max_workers=hash_jobs) as hash_pool:
        with ThreadPoolExecutor(max_workers=role_workers) as role_pool:
            results = list(role_pool.map(
                lambda ck: plan_character(ck, hash_pool), characters))
    for ck, local_barrier, local_plans in results:
        barrier.extend(local_barrier)
        plans.extend(local_plans)
        print(f'  {ck}: {len(local_plans)} files verified', flush=True)

    def install(item):
        src, target, expected = item
        if signature(target) == expected:
            return 0
        temporary = target.with_suffix('.anim.integrating')
        shutil.copy2(src, temporary)
        if signature(temporary) != expected:
            raise ValueError(f'Copy differs: {target}')
        temporary.replace(target)
        return 1

    for ck in characters:
        _ensure_base_inputs(ck, jobs,sources=selected_sources)
    _check_barrier(barrier)
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        copied = sum(pool.map(install, plans))
    result = {'status': 'materialized', 'mode': 'base' if no_physics else 'physics',
              'characters': characters, 'files': len(plans), 'copied': copied,
              'finished': time.time()}
    _write_json(selected_cache / 'last_materialization.json', result)
    print(f'  最终动画：{len(plans)} 个路径，更新 {copied} 个，模式 {result["mode"]}', flush=True)
    return result


def main():
    from moe_release_sources import resolve
    if resolve() is not None:
        raise ValueError('Legacy adoption cannot replace an active immutable release; prepare a new explicit revision')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adopt-legacy', action='store_true')
    parser.add_argument('--provenance', type=Path, required=True)
    parser.add_argument('--verified-project-report', type=Path,
                        help='Use verified project inputs for a direct five-package installation')
    parser.add_argument('--jobs', type=int, default=12)
    args = parser.parse_args()
    if not args.adopt_legacy or args.jobs < 1:
        parser.error('Use --adopt-legacy with positive --jobs')
    evidence = json.loads(args.provenance.read_text(encoding='utf-8'))
    if args.verified_project_report:
        report = json.loads(args.verified_project_report.read_text(encoding='utf-8'))
        if (evidence.get('status') != 'superseded_by_integrated_install'
                or evidence.get('installation_attempted') is not False
                or report.get('status') != 'verified' or report.get('installed') is not False
                or set(report.get('characters', [])) != set(evidence['characters'])
                or report.get('untouched_characters', {}).get('status') != 'verified_unchanged'):
            raise ValueError('Direct integration requires the verified project and unchanged remaining characters')
    elif evidence.get('status') != 'complete' or evidence.get('installation_state') != 'verified':
        raise ValueError('Legacy adoption requires a verified installation or explicit verified project report')
    characters = [c['ck'] for c in X.active() if c.get('release_managed', True)]
    if (CACHE / 'index.json').exists():
        raise FileExistsError('Physics release cache is already initialized')
    for ck in characters:
        directory = O.root(O.physics(X.game_of(ck))) / ASSET_REL / ck / 'anims'
        manifest = source_dir(ck).parent / 'manifest.json'
        if manifest.is_file():
            # Resume only if both current original outputs and all inputs match.
            prior = _load_verified(ck, args.jobs)
            if prior['physics'] != inventory(directory.glob('*.anim'), args.jobs):
                raise ValueError(f'{ck}: resumed source differs')
        else:
            adopt(ck, directory, provenance=args.provenance.resolve(), jobs=args.jobs)
    _write_json(CACHE / 'index.json', {'schema': 1, 'characters': characters,
                                     'manifests': {ck: signature(source_dir(ck).parent / 'manifest.json')['sha256'] for ck in characters},
                                     'verified_project_report': str(args.verified_project_report.resolve()) if args.verified_project_report else None,
                                     'verified_project_sha256': signature(args.verified_project_report)['sha256'] if args.verified_project_report else None,
                                     'provenance': str(args.provenance.resolve())})
    print(f'Adopted {len(characters)} characters; legacy packages can now be retired.', flush=True)


if __name__ == '__main__':
    main()
