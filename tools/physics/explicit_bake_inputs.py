"""Normal native bake API with explicit candidate meshes and animation sources.

Call inside an isolated worker process: the legacy mesh resolver/writeback
module stores character state globally. Source files remain read-only. This
module does not select a physics profile, solver options or release destination.
"""
from contextlib import contextmanager
from pathlib import Path
from types import MethodType
from unittest.mock import patch

import moe_anim_observability
import moe_bone_contract


@contextmanager
def rig_inputs(bake, character, *, head_mesh, torso_mesh, profile=None,
               simulate_follow_bones=False, ball_cap=None, use_release_recipe=False):
    """Construct a normal Rig against the two exact destination mesh files."""
    cfg = bake.char_config(character)
    cfg.update(profile=profile, simulate_follow_bones=simulate_follow_bones, ball_cap=ball_cap,
               release_recipe=bool(use_release_recipe))
    meshes = {cfg['head']: Path(head_mesh).resolve(), cfg['torso']: Path(torso_mesh).resolve()}
    for source in meshes.values():
        if not source.is_file():
            raise FileNotFoundError(source)
    find = bake.MP.find
    old_names = tuple(getattr(bake.BPI, key) for key in ('HEAD_MESH', 'TORSO_MESH', 'HEAD_PREFIX', 'BODY_PREFIX'))
    old_cache = bake.BPI._SKIN_OWNER_CACHE

    def explicit_find(filename, missing_ok=False):
        # Only the exact two mesh identifiers from char_config are redirected.
        # No guesses based on prefix, substring, animation name, or part labels.
        if str(filename) in meshes:
            return meshes[str(filename)]
        if str(filename).endswith('.anim'):
            raise ValueError('Animation reads must use explicit source directories')
        return find(filename, missing_ok=missing_ok)

    bake.BPI._SKIN_OWNER_CACHE = {}
    try:
        with patch.object(bake.MP, 'find', side_effect=explicit_find):
            yield bake.Rig(cfg)
    finally:
        bake.BPI._SKIN_OWNER_CACHE = old_cache
        for key, value in zip(('HEAD_MESH', 'TORSO_MESH', 'HEAD_PREFIX', 'BODY_PREFIX'), old_names):
            setattr(bake.BPI, key, value)


@contextmanager
def prepare_sources(rig, *, head_source_dir, observable_contract=None):
    """Attach explicit head-source and optional observability to every prepare.

    ``Rig.finish_one`` validates the exact head bone table before using these
    dependencies. No source animation is reduced before solving physics.
    """
    directory = Path(head_source_dir).resolve()
    if not directory.is_dir():
        raise NotADirectoryError(directory)
    original = rig.prepare_one

    def explicit_prepare(self, source, options):
        prepared = original(source, options)
        prepared['source_dir'] = str(directory)
        if observable_contract is not None:
            if Path(self.head_path).resolve() != Path(observable_contract.mesh_path):
                raise ValueError('Observability contract belongs to a different head mesh')
            prepared['head_observable_bones'] = sorted(observable_contract.observable_names)
            prepared['head_observable_bone_table'] = list(observable_contract.bone_names)
            prepared['head_observable_mesh_sha256'] = observable_contract.mesh_sha256
        return prepared

    rig.prepare_one = MethodType(explicit_prepare, rig)
    try:
        yield
    finally:
        rig.prepare_one = original


def bake_batch(bake, character, options, *, head_mesh, torso_mesh, body_source_dir,
               head_source_dir, actions, output_dir, profile=None, reference_paths=None,
               use_release_recipe=False):
    """Bake a caller-selected action batch with the standard GPU/CPU pipeline.

    ``options`` is the normal complete bake Namespace, including explicit native
    executable and solver parameters. ``profile`` is a validated source profile
    dict. Pass actual runtime text paths to opt into lossless head-channel export;
    None preserves ordinary writer behavior. No registry or published asset is
    changed, and no acceptance/promotion decision is made here.
    """
    source = Path(body_source_dir).resolve()
    output = Path(output_dir).resolve()
    if not source.is_dir():
        raise NotADirectoryError(source)
    if source == output or output == Path(head_source_dir).resolve():
        raise ValueError('Bake output must differ from both source directories')
    with rig_inputs(bake, character, head_mesh=head_mesh, torso_mesh=torso_mesh, profile=profile,
                    simulate_follow_bones=getattr(options, 'simulate_follow_bones', False),
                    ball_cap=getattr(options, 'ball_cap', None), use_release_recipe=use_release_recipe) as rig:
        contract = None
        if reference_paths is not None:
            contract = moe_anim_observability.from_mesh(
                head_mesh, protected_names=moe_bone_contract.SEMANTIC_ORDER,
                reference_paths=reference_paths)
        files = [source / (rig.cfg['body_prefix'] + action + '.anim') for action in actions]
        if not files or any(not file.is_file() for file in files):
            raise ValueError('Explicit body action list is empty or contains missing files')
        with prepare_sources(rig, head_source_dir=head_source_dir, observable_contract=contract):
            return rig.bake_batch(files, output, options)
