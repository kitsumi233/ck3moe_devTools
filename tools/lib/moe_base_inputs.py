"""Unsimulated animation cache for physics input and base/current comparison.

Only canonical bake directories supply these inputs. Published model animations
already contain physics and must never be used to seed this cache. Canonical and
cached files are hashed during synchronization and before a physics solve. The
viewer uses the same provenance checks with sizes/mtimes, cached for one second
to avoid rescanning a character's full library for every row of its action list.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

import moe_chars as X

ROOT = Path(__file__).resolve().parents[2]
_QUICK_CHECKS = {}


def _paths(character,sources=None):
    if character not in X.BY_CK:
        raise ValueError(f"Unknown base-animation character: {character}")
    from moe_release_sources import role
    selected=role(character,sources)
    source = selected['canonical'] if selected else X.bake_dir(character).resolve()
    target = selected['base'] if selected else (ROOT / 'work' / 'base_animation_inputs' / character / 'anims').resolve()
    work = (ROOT / 'work').resolve()
    if not source.is_relative_to(work) or not target.is_relative_to(work):
        raise ValueError('Base animation inputs must remain under work/')
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError('Canonical and cached animation directories must be distinct')
    return source, target


def _fingerprint(path):
    stat = path.stat()
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def _sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _inputs(character, source):
    prefix = X.BY_CK[character]['prefix']
    files = sorted(source.glob('*.anim'))
    if not files:
        raise FileNotFoundError(f'No canonical base animations: {source}')
    result = {}
    for path in files:
        # Canonical output is generic, even for characters with an exported
        # prefix. Refuse mixed/deployed input rather than prefixing it twice.
        if not path.name.startswith('moe_') or any(
                path.name.startswith(c['prefix']) for c in X.CHARS if c['ck'] != 'flo'):
            raise ValueError(f'Expected generic canonical animation, got {path}')
        name = prefix + path.name[len('moe_'):]
        if name in result or not path.resolve().is_relative_to(source):
            raise ValueError(f'Duplicate or redirected canonical animation: {path}')
        result[name] = path
    if not any(name.startswith(prefix + 'body_') for name in result):
        raise ValueError(f'Canonical input has no body animations: {source}')
    if not any(name.startswith(prefix + 'head_') for name in result):
        raise ValueError(f'Canonical input has no head animations: {source}')
    return result


def _write_json(path, payload):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def sync_base_animation_inputs(character, jobs=8, *, sources=None):
    """Copy generic canonical files losslessly under their exported names.

    Return a complete manifest suitable for build evidence. The manifest is
    explicitly marked syncing before any file is replaced, so an interrupted
    refresh can never be mistaken for a complete solver input. Unexpected old
    files are rejected instead of silently deleting local work.
    """
    source, target = _paths(character,sources)
    from moe_release_sources import role
    selected=role(character,sources)
    if selected and (target.parent/'manifest.json').exists():
        require_base_animation_inputs(character,verify_hashes=True,sources=sources)
        return json.loads((target.parent/'manifest.json').read_text(encoding='utf8'))
    inputs = _inputs(character, source)
    # Byte provenance alone can faithfully copy an obsolete retarget. Verify
    # the source orientation before publishing another complete cache.
    _require_retarget_root(character, source)
    stale = {p.name for p in target.glob('*.anim')} - set(inputs)
    if stale:
        raise ValueError(f'Stale base cache files for {character}; archive them before syncing: {sorted(stale)}')
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target.parent / 'manifest.json'
    payload = dict(schema=1, status='syncing', source_kind='canonical_base', character=character,
                   source_dir=str(source.relative_to(ROOT)), cache_dir=str(target.relative_to(ROOT)),
                   started_utc=datetime.now(timezone.utc).isoformat())
    if selected:
        payload['release_source_provenance']=selected['provenance']
        payload['release_source_configuration']=dict(path=str(selected['manifest']),sha256=selected['manifest_sha256'])
    _QUICK_CHECKS.pop((character,str(target)), None)
    # Exclusive creation prevents two build processes from racing the same
    # cache/manifest. A killed process leaves a visible lock to investigate.
    lock_path = target.parent / 'sync.lock'
    try:
        lock = lock_path.open('x', encoding='utf-8')
    except FileExistsError as error:
        raise RuntimeError(f'Base input cache is already syncing or needs recovery: {lock_path}') from error
    with lock:
        lock.write(json.dumps({'pid': os.getpid(), 'started_utc': payload['started_utc']}))

    def copy_one(item):
        name, origin = item
        destination = target / name
        before = _fingerprint(origin)
        digest = _sha256(origin)
        existing = destination.is_file() and destination.stat().st_size == before['size']
        copied = not existing or _sha256(destination) != digest
        if copied:
            temporary = destination.with_suffix('.anim.tmp')
            shutil.copy2(origin, temporary)
            if _sha256(temporary) != digest:
                raise ValueError(f'Canonical animation changed during copy: {origin}')
            temporary.replace(destination)
        after = _fingerprint(origin)
        if after != before:
            raise ValueError(f'Canonical animation changed during synchronization: {origin}')
        return dict(filename=name, source=str(origin.relative_to(ROOT)),
                    source_stat=after, cache_stat=_fingerprint(destination), sha256=digest, copied=copied)

    try:
        _write_json(manifest_path, payload)
        with ThreadPoolExecutor(max_workers=max(1, int(jobs))) as pool:
            rows = list(pool.map(copy_one, inputs.items()))
        if set(_inputs(character, source)) != set(inputs):
            raise ValueError(f'Canonical animation coverage changed during synchronization: {source}')
        payload.update(status='complete', files=rows, count=len(rows),
                       total_bytes=sum(row['source_stat']['size'] for row in rows),
                       copied=sum(row['copied'] for row in rows),
                       completed_utc=datetime.now(timezone.utc).isoformat())
        _write_json(manifest_path, payload)
        require_base_animation_inputs(character,sources=sources)
    except Exception as error:
        payload.update(status='failed', error=str(error))
        _write_json(manifest_path, payload)
        raise
    finally:
        lock_path.unlink()
    return payload


def require_base_animation_inputs(character, verify_hashes=False, *, sources=None):
    """Reject missing, incomplete, stale, modified, or misrouted base inputs.

    ``verify_hashes=True`` also detects edits that preserve size and mtime. Never
    cached: solver startup verifies both bytes and source root orientations.
    """
    return _require_base_animation_inputs(character, verify_hashes, verify_retarget=verify_hashes,sources=sources)


def require_base_animation_provenance(character, verify_hashes=True, *, sources=None):
    """Validate only byte provenance for diagnosis or revision lineage.

    An obsolete export can have valid byte provenance. This explicit API lets
    repair tools prove which historical inputs they replace without granting
    permission to solve them. Solver entry points must use
    require_base_animation_inputs(..., verify_hashes=True).
    """
    return _require_base_animation_inputs(character, verify_hashes, verify_retarget=False,sources=sources)


def _require_base_animation_inputs(character, verify_hashes, verify_retarget, sources=None):
    source, target = _paths(character,sources)
    manifest_path = target.parent / 'manifest.json'
    if not manifest_path.is_file() or not target.is_dir():
        raise FileNotFoundError(f'Missing unsimulated animation cache for {character}; run tools/build/sync_base_animation_inputs.py')
    manifest_stat = _fingerprint(manifest_path)
    memo = _QUICK_CHECKS.get((character,str(target)))
    if not verify_hashes and memo and memo[0] == manifest_stat and time.monotonic() - memo[1] < 1.0:
        return target
    info = json.loads(manifest_path.read_text(encoding='utf-8'))
    if (info.get('schema') != 1 or info.get('status') != 'complete' or
            info.get('character') != character or info.get('source_kind') != 'canonical_base' or
            info.get('source_dir') != str(source.relative_to(ROOT)) or
            info.get('cache_dir') != str(target.relative_to(ROOT))):
        raise ValueError(f'Incomplete or invalid unsimulated animation cache for {character}')
    inputs = _inputs(character, source)
    rows = info.get('files', [])
    expected = {row['filename'] for row in rows}
    if len(rows) != len(expected) or not expected or expected != set(inputs) or expected != {p.name for p in target.glob('*.anim')}:
        raise ValueError(f'Unsimulated animation coverage changed for {character}; sync the cache')
    for row in rows:
        origin = inputs[row['filename']]
        destination = target / row['filename']
        if (row['source'] != str(origin.relative_to(ROOT)) or
                not destination.resolve().is_relative_to(target) or
                _fingerprint(origin) != row['source_stat'] or
                _fingerprint(destination) != row['cache_stat']):
            raise ValueError(f'Unsimulated input changed for {character}: {row["filename"]}; sync the cache')
        if verify_hashes and (_sha256(origin) != row['sha256'] or _sha256(destination) != row['sha256']):
            raise ValueError(f'Unsimulated input hash mismatch for {character}: {row["filename"]}; sync the cache')
    if verify_hashes:
        # The solver must reject semantically stale exports even when their
        # canonical and cache hashes agree. This checks every catalog action,
        # including static nonidentity placement, against current vanilla data.
        if verify_retarget:
            _require_retarget_root(character, target)
        # Hashing a complete character takes time; catch modifications that
        # occur after an early file was hashed while later files are checked.
        if _fingerprint(manifest_path) != manifest_stat or set(_inputs(character, source)) != expected:
            raise ValueError(f'Base input manifest or canonical coverage changed during verification: {character}')
        if {p.name for p in target.glob('*.anim')} != expected:
            raise ValueError(f'Base input cache coverage changed during verification: {character}')
        for row in rows:
            if (_fingerprint(inputs[row['filename']]) != row['source_stat'] or
                    _fingerprint(target / row['filename']) != row['cache_stat']):
                raise ValueError(f'Base input changed during verification: {character}/{row["filename"]}')
    _QUICK_CHECKS[(character,str(target))] = (manifest_stat, time.monotonic())
    return target


def _require_retarget_root(character, animation_dir):
    """Read-only semantic gate shared by cache publication and solver startup."""
    from moe_retarget_root import SourceContract, check_character

    report = check_character(character, SourceContract(), input_dir=animation_dir)
    if report['status'] != 'passed':
        failed = [row for row in report['rows'] if row['status'] != 'passed']
        details = '; '.join(f"{row['action']}: {', '.join(row.get('errors', []))}"
                            for row in failed[:3])
        raise ValueError(
            f"Stale body-root retarget for {character}: {report['failed']}/{report['checked']} "
            f"actions fail the vanilla source contract. Regenerate body animations and "
            f"dependent heads through the normal retarget/graft pipeline, then sync. {details}")
    return report
