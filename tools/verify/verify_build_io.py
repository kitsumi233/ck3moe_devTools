"""Compare fast metadata with full parsing and check parallel deployment fidelity."""
import importlib.util
import tempfile
import time
import types
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/lib'))
import pdx_parse as pdx


def main():
    files = sorted((ROOT / 'work/evidence/focus_20260910/baked50').rglob('*.anim'))
    if not files:
        raise RuntimeError('No baked animations available for metadata comparison')
    started = time.perf_counter()
    fast = [pdx.anim_info(path) for path in files]
    fast_seconds = time.perf_counter() - started
    started = time.perf_counter()
    for path, metadata in zip(files, fast):
        full = {name: values[0] if len(values) == 1 else tuple(values)
                for node, name, kind, values in pdx.walk(pdx.parse(path.read_bytes()))
                if node == '/info'}
        assert metadata == full, (path, metadata, full)
    full_seconds = time.perf_counter() - started
    spec = importlib.util.spec_from_file_location('finalize_verify', ROOT / 'tools/build/finalize_mods.py')
    finalize = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(finalize)
    with tempfile.TemporaryDirectory(prefix='moe_build_io_') as temporary:
        test_root = Path(temporary)
        source_root = test_root / 'source'
        source = source_root / 'sample'
        source.mkdir(parents=True)
        (source / 'empty').mkdir()
        (source / '中文').mkdir()
        for index in range(40):
            (source / '中文' / f'{index}.bin').write_bytes(bytes(range(256)) * (index + 1))
        (source_root / 'sample.mod').write_bytes(b'descriptor')
        finalize.O = types.SimpleNamespace(OUT=source_root, root=lambda mid: source_root / mid)
        for jobs in (1, 12):
            finalize.USERDIR = test_root / f'user_{jobs}'
            destination = finalize.USERDIR / 'mod/sample'
            destination.mkdir(parents=True)
            (destination / 'stale.txt').write_bytes(b'old')
            assert finalize.copy_pack('sample', finalize.USERDIR / 'mod', jobs) == 40
            assert not (destination / 'stale.txt').exists()
            assert (destination / 'empty').is_dir()
            for path in source.rglob('*.bin'):
                assert path.read_bytes() == (destination / path.relative_to(source)).read_bytes()
            assert (finalize.USERDIR / 'mod/sample.mod').is_file()
        # Runtime selection must preserve registered-but-unused and additive
        # files, cross-package references, and explicit binary references.
        assets = source / 'assets'
        assets.mkdir()
        filenames = ['moe_body_idle.anim', 'moe_head_idle.anim',
                     'moe_registered_unused.anim', 'moe_additive.anim',
                     'moe_binary_reference.anim', 'moe_cross_package.anim',
                     'moe_source_template.anim', 'moe_folded_var2.anim']
        for index, name in enumerate(filenames):
            (assets / name).write_bytes(b'complete untouched animation ' + bytes([index]))
        asset = assets / 'model.asset'
        asset.write_text('''skeletal_animation_set = {
 name = "set"
 animation = { id = "body_idle" type = "moe_body_idle.anim" }
 animation = { id = "head_idle" type = "moe_head_idle.anim" }
 animation = { id = "unused_by_states" type = "moe_registered_unused.anim" }
}
additive_animation = { id = "face" type = "moe_additive.anim" }
entity = { name = "body" state = { name = "idle" animation = "body_idle" } }
entity = { name = "head" state = { name = "idle" animation = "head_idle" } }
# Example only: additive_animation = { type = "moe_nonexistent_example.anim" }
''', encoding='utf-8')
        (assets / 'mesh.mesh').write_bytes(b'\0moe_binary_reference.anim\0')
        other = source_root / 'other'
        other.mkdir()
        (other / 'genes.txt').write_text('animation = "moe_cross_package.anim"', encoding='utf-8')
        selection = finalize.runtime_animation_selection(source_root)
        assert {Path(r['path']).name for r in selection['omitted']} == {
            'moe_source_template.anim', 'moe_folded_var2.anim'}
        for jobs in (1, 12):
            finalize.USERDIR = test_root / f'filtered_user_{jobs}'
            finalize.copy_pack('sample', finalize.USERDIR / 'mod', jobs, animation_selection=selection)
            deployed = finalize.USERDIR / 'mod/sample/assets'
            for name in filenames[:6]:
                assert (deployed / name).read_bytes() == (assets / name).read_bytes()
            for name in filenames[6:]:
                assert not (deployed / name).exists()
                assert (assets / name).is_file()
            assert (deployed / 'model.asset').read_bytes() == asset.read_bytes()
        previous_asset = asset.read_bytes()
        asset.write_bytes(previous_asset + b'\n# Changed reference source\n')
        marker = finalize.USERDIR / 'mod/sample/do_not_delete.txt'
        marker.write_text('existing installation')
        try:
            selection.pop('_verified', None)  # 只核一次的缓存，反例要重核
            finalize.copy_pack('sample', finalize.USERDIR / 'mod', animation_selection=selection)
        except ValueError:
            assert marker.is_file()  # Reject stale selection before touching installation.
        else:
            raise AssertionError('Changed runtime references were accepted')
        asset.write_bytes(previous_asset)
        extra_asset = assets / 'new_reference.asset'
        extra_asset.write_text('animation = { type = "moe_source_template.anim" }', encoding='utf-8')
        try:
            selection.pop('_verified', None)  # 只核一次的缓存，反例要重核
            finalize.copy_pack('sample', finalize.USERDIR / 'mod', animation_selection=selection)
        except ValueError:
            assert marker.is_file()
        else:
            raise AssertionError('New runtime references were ignored by an old selection')
        extra_asset.unlink()
        required = assets / 'moe_registered_unused.anim'
        required_bytes = required.read_bytes()
        required.unlink()
        try:
            selection.pop('_verified', None)  # 只核一次的缓存，反例要重核
            finalize.copy_pack('sample', finalize.USERDIR / 'mod', animation_selection=selection)
        except ValueError:
            assert marker.is_file()
        else:
            raise AssertionError('Missing selected animation did not protect the existing installation')
        try:
            finalize.runtime_animation_selection(source_root)
        except ValueError:
            pass
        else:
            raise AssertionError('Missing registered animation was accepted')
        required.write_bytes(required_bytes)
        malformed = test_root / 'bad.anim'
        for data in (b'broken', b'@@b@[info\0!\x01ji\x01\x00\x00\x00'):
            malformed.write_bytes(data)
            try:
                pdx.anim_info(malformed)
            except ValueError:
                pass
            else:
                raise AssertionError('Malformed metadata was accepted')
    print(f'OK: {len(files)} headers match full parsing; fast={fast_seconds:.3f}s full={full_seconds:.3f}s')
    print('OK: serial/12-thread deployment retains exact files and removes stale content')
    print('OK: runtime selection preserves state/additive/cross-package/binary references and rejects stale or missing inputs')


if __name__ == '__main__':
    main()
