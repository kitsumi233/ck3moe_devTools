"""Validate profile isolation, capsule repair, and native input rejection."""
import copy
import math
import pickle
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/physics'))
import moe_bullet_bake as bake
import physics_profile as profiles


def main():
    for character in ('dai', 'che'):
        rig = bake.Rig(bake.char_config(character))
        assert len(rig.source_repairs) == (1 if character == 'che' else 0)
        model = copy.deepcopy(rig.model)
        original = pickle.dumps([[vars(item) for item in group] for group in (model.rigids, model.joints)])
        follow = set(rig.follow_rigids)
        profiles.apply_rigids(model, dict(version=1, character=character), follow)
        assert original == pickle.dumps([[vars(item) for item in group] for group in (model.rigids, model.joints)])
        assert follow == rig.follow_rigids
        index = next(index for index, rigid in enumerate(model.rigids) if rigid.mode != 0)
        name = model.rigids[index].name
        original_others = [pickle.dumps(vars(rigid)) for rigid in model.rigids if rigid.name != name]
        profiles.apply_rigids(model, dict(version=1, character=character, rigids={name: dict(mode='follow', size_scale=0.8)}), follow)
        assert index in follow
        assert original_others == [pickle.dumps(vars(rigid)) for rigid in model.rigids if rigid.name != name]
    for values in (dict(hz=float('nan')), dict(iters=10.5), dict(drive_gain=True), dict(unknown=1)):
        try:
            profiles.validate(dict(version=1, character='che', solver=values), 'che')
        except ValueError:
            pass
        else:
            raise AssertionError(values)
    invalid = SimpleNamespace(rigids=[SimpleNamespace(name='bad', rotation=(float('nan'), 0, 0), type=1)])
    try:
        bake.repair_capsule_rotations(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError('unsupported invalid rotation accepted')

    with tempfile.TemporaryDirectory(dir=ROOT / 'work', prefix='verify_physics_') as temporary:
        folder = Path(temporary)
        scene = folder / 'scene.txt'
        frames = folder / 'frames.bin'
        output = folder / 'out.bin'
        valid = 'fps 30\nnbones 1\nbone -1 0 0 0\nnrigids 1\nrigid 0 0 1 0 0 0 0 0 0 0 0 1 0.5 0.5 0 0.5 0 0 65535\nnjoints 0\n'
        frames.write_bytes(struct.pack('<ii7f', 1, 1, 0, 0, 0, 0, 0, 0, 1))
        command = [str(bake.EXE), str(scene), str(frames), str(output), '--settle', '0', '--warmup', '1']
        def invoke(text, success):
            scene.write_text(text, encoding='utf-8')
            if output.exists():
                output.unlink()
            result = subprocess.run(command, capture_output=True)
            assert (result.returncode == 0) == success, result.stderr.decode(errors='replace')
            assert output.exists() == success
        invoke(valid, True)
        for text in (
            valid.replace('0.5 0.5', 'nan 0.5'), valid.replace('1 0 0 0 0 0 0 0 0', '1 0 0 0 0 0 0 0 nan'),
            valid.replace('65535', ''), valid.replace('nrigids 1', 'nrigids 2'),
            valid.replace('rigid 0 0', 'rigid 99 0'), valid.replace('fps 30', 'fps inf'),
        ):
            invoke(text, False)
        joint = 'joint 0 0 ' + ' '.join(['0'] * 24)
        invoke(valid.replace('njoints 0', 'njoints 1') + joint + '\n', True)
        invoke(valid.replace('njoints 0', 'njoints 1') + joint + ' 1\n', True)
        invoke(valid.replace('njoints 0', 'njoints 1') + joint + ' 0.25\n', False)
        frames.write_bytes(struct.pack('<ii7f', 1, 1, math.nan, 0, 0, 0, 0, 0, 1))
        invoke(valid, False)
    print('PASS: DAI/CHE profile isolation, capsule repair, invalid profiles, native malformed/non-finite input and optional joint flag')


if __name__ == '__main__':
    main()
