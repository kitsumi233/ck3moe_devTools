"""Explicit immutable release inputs; an absent active pointer preserves legacy paths.

Preparing a source manifest never activates it. The single optional pointer is
the only default-path switch for canonical models, unsimulated inputs and physics.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
ACTIVE=ROOT/'work/active_release_sources.json'
PIN_ENV='MOE_RELEASE_SOURCES_PIN'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _work_path(value):
    path=Path(value)
    if not path.is_absolute():path=ROOT/path
    path=path.resolve()
    if not path.is_relative_to((ROOT/'work').resolve()) or path==(ROOT/'work').resolve():
        raise ValueError(f'Release source must remain inside work/: {path}')
    return path


def reference(row):
    path=_work_path(row['path'])
    if sha(path)!=row['sha256']:raise ValueError(f'Release source reference changed: {path}')
    return path


def load(path, expected_sha256=None, *, active=False):
    path=_work_path(path)
    digest=sha(path)
    if expected_sha256 is not None and digest!=expected_sha256:
        raise ValueError(f'Release source manifest changed: {path}')
    data=read(path)
    if data.get('schema')!=1 or data.get('kind')!='immutable_release_sources':
        raise ValueError('Invalid immutable release source manifest')
    allowed=('preparing','verified_immutable_release_sources')
    if data.get('status') not in allowed or (active and data['status']!=allowed[1]):
        raise ValueError('Active release source manifest must be completely verified')
    root=_work_path(data['root'])
    if path.parent!=root or not data.get('characters'):
        raise ValueError('Source manifest must sit at the revision root and contain roles')
    physics=_work_path(data['physics_release_root'])
    if physics!=root/'physics_release':raise ValueError('Physics root does not belong to this revision')
    for ck,row in data['characters'].items():
        if not ck.isascii() or not ck.isalpha() or len(ck)!=3:raise ValueError('Invalid release role')
        canonical=_work_path(row['canonical_dir']);base=_work_path(row['base_animation_dir'])
        if canonical!=root/'canonical'/ck or base!=root/'base_animation_inputs'/ck/'anims':
            raise ValueError(f'Misrouted immutable release input: {ck}')
        if active:
            for field in ('stage_manifest','base_manifest','source_spec','source_root_contract','quality_review'):
                reference(row[field])
            if reference(row['base_manifest'])!=base.parent/'manifest.json':
                raise ValueError(f'Misrouted base manifest: {ck}')
            for field in ('baseline_revision','original_base_manifest'):
                if field in row:reference(row[field])
    if active:
        if reference(data['physics_index'])!=physics/'index.json':
            raise ValueError('Physics index does not belong to this revision')
        reference(data['activation_review'])
        if len(data['characters'])!=31:raise ValueError('Active release must include all31 roles')
    return dict(path=path,sha256=digest,data=data,root=root)


def resolve(explicit=None):
    """Explicit preparation manifests override no global state."""
    # A caller that has already loaded and validated the immutable release may
    # pass that config through the read-only preflight.  Reusing it avoids
    # reparsing and revalidating all 31 character rows for every path lookup;
    # the caller must retain a barrier for the pointer/manifest files.
    if isinstance(explicit, dict) and {'data', 'root', 'path', 'sha256'} <= set(explicit):
        return explicit
    if explicit is not None:
        return load(explicit)
    pin=json.loads(os.environ[PIN_ENV]) if PIN_ENV in os.environ else None
    if not ACTIVE.exists():
        if pin and pin.get('kind')!='legacy':raise ValueError('Active release pointer vanished during build')
        return None
    if pin and pin.get('kind')=='legacy':raise ValueError('Release source activated during a legacy build')
    pointer=read(ACTIVE)
    if pointer.get('schema')!=1 or pointer.get('kind')!='active_release_sources':
        raise ValueError('Invalid active release pointer')
    if pin and sha(ACTIVE)!=pin['pointer_sha256']:
        raise ValueError('Active release pointer changed during build')
    return load(pointer['manifest']['path'],pointer['manifest']['sha256'],active=True)


def role(character, explicit=None):
    if explicit is None:
        # Batch imports use their own bake directories until they join a reviewed
        # immutable release. Avoid resolving the 31-role active release for these
        # intentionally unmanaged candidates.
        import moe_chars as X
        registered=X.BY_CK.get(character)
        if registered is not None and registered.get('release_managed') is False:
            return None
    config=resolve(explicit)
    if config is None:return None
    if character not in config['data']['characters']:
        raise ValueError(f'Character missing from explicitly selected release: {character}')
    row=config['data']['characters'][character]
    return dict(canonical=_work_path(row['canonical_dir']),base=_work_path(row['base_animation_dir']),
        physics_root=_work_path(config['data']['physics_release_root']),
        manifest=config['path'],manifest_sha256=config['sha256'],provenance=row)


def pin_environment():
    config=resolve()
    value=dict(kind='legacy') if config is None else dict(kind='active',pointer_sha256=sha(ACTIVE),
        manifest=str(config['path']),manifest_sha256=config['sha256'])
    return {PIN_ENV:json.dumps(value,ensure_ascii=False)}


def activation_pointer(manifest):
    """Return a reviewable pointer value; this function never writes or activates."""
    config=load(manifest,active=True)
    import moe_chars as X
    release_roles={c['ck'] for c in X.CHARS if c.get('release_managed',True)}
    if set(config['data']['characters'])!=release_roles:
        raise ValueError('Activation role set differs from the release-managed character registry')
    review=read(reference(config['data']['activation_review']))
    if review.get('status')!='accepted_for_release' or set(review.get('characters',[]))!=set(config['data']['characters']):
        raise ValueError('Activation requires complete all31 visual and numerical release acceptance')
    if not all(review.get(key) is True for key in ('expressions_verified','physics_verified','four_weights_verified')):
        raise ValueError('Activation quality review is incomplete')
    if review.get('staging_sha256')!=config['data']['staging_sha256']:
        raise ValueError('Activation review refers to a different staging index')
    preflight=read(reference(config['data']['release_preflight']))
    if (preflight.get('status')!='verified_all31_candidate_inputs_before_promotion'
            or preflight.get('stage_index_sha256')!=config['data']['staging_sha256']):
        raise ValueError('Activation requires current all31 source and byte preflight')
    holds=read(reference(config['data']['visual_holds']))
    historical_holds={ck for ck,row in holds['characters'].items() if row.get('accepted') is not True}
    if historical_holds:
        effective=read(reference(preflight['effective_visual_holds']))
        if (effective.get('status')!='verified_effective_visual_holds_for_exact_staging'
                or effective.get('unresolved_characters')
                or effective['staging_index']['sha256']!=config['data']['staging_sha256']
                or effective['hold_document']!=config['data']['visual_holds']
                or set(effective['revision_resolutions'])!=historical_holds):
            raise ValueError('Unresolved visual hold for this release revision prohibits activation')
        reference(effective['staging_index']);reference(effective['verifier'])
        for ck,row in effective['revision_resolutions'].items():
            if row['stage_manifest']!=config['data']['characters'][ck]['stage_manifest']:
                raise ValueError('Visual resolution belongs to a different staged source')
            stage=read(reference(row['stage_manifest']))
            flat_path=reference(row['flat_manifest']);flat=read(flat_path)
            if reference(stage['sources']['flat_manifest'])!=flat_path:
                raise ValueError('Visual resolution flat source differs from staged source')
            resolution_path=reference(row['resolution']);resolution=read(resolution_path)
            if (resolution.get('character')!=ck or resolution.get('accepted') is not True
                    or reference(stage['sources']['visual_hold_resolution'])!=resolution_path):
                raise ValueError('Invalid revision-specific visual resolution')
            batch=Path(flat['physical_source']).resolve()
            for key,name in [('batch_configuration','configuration.json'),('batch_results','results.json'),
                             ('independent_coverage','coverage_audit.json')]:
                if reference(resolution[key])!=batch/name:
                    raise ValueError('Visual resolution belongs to a different physical batch')
    return dict(schema=1,kind='active_release_sources',manifest=dict(path=str(config['path']),sha256=config['sha256']))
