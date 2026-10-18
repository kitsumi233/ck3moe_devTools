"""Physical/IO contract checks for the optional native PhysX baker; evidence is retained."""
import argparse
import json
import math
import struct
import subprocess
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
EXE = ROOT / 'work/moe_physx_build/moe_physx_bake.exe'


def body(pos=(0, 0, 0), mode=1, shape=0, size=(0.5, 0, 0), mask=65535):
    return dict(pos=pos, mode=mode, shape=shape, size=size, mask=mask)


def joint(a, b, pos=(0, 0, 0), lin_lo=(0, 0, 0), lin_hi=(0, 0, 0),
          ang_lo=(0, 0, 0), ang_hi=(0, 0, 0), spring=(0, 0, 0)):
    return (a, b, *pos, 0, 0, 0, *lin_lo, *lin_hi, *ang_lo, *ang_hi, 0, 0, 0, *spring, 1)


def inputs(folder, name, bodies, joints=(), gravity=-98, frames=90, rotation=None, root_motion=None):
    folder.mkdir(parents=True, exist_ok=True)
    scene, animation = folder / f'{name}.scene.txt', folder / f'{name}.frames.bin'
    lines = ['fps 30', f'gravity {gravity}', f'nbones {len(bodies)}']
    lines += ['bone -1 ' + ' '.join(map(str, b['pos'])) for b in bodies]
    lines += [f'nrigids {len(bodies)}']
    for index, b in enumerate(bodies):
        values = (index, b['shape'], *b['size'], *b['pos'], 0, 0, 0, 1, 0, 0, 0, 0.5, b['mode'], 0, b['mask'])
        lines.append('rigid ' + ' '.join(map(str, values)))
    lines += [f'njoints {len(joints)}']
    lines += ['joint ' + ' '.join(map(str, j)) for j in joints]
    scene.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    data = np.zeros((frames, len(bodies), 7), dtype='<f4')
    data[:, :, :3] = [b['pos'] for b in bodies]
    data[:, :, 6] = 1
    if root_motion is not None:
        data[:, 0, :3] += root_motion
    if rotation is not None:
        axis, angle = rotation
        data[:, 1, 3 + axis] = math.sin(angle / 2)
        data[:, 1, 6] = math.cos(angle / 2)
    animation.write_bytes(struct.pack('<ii', frames, len(bodies)) + data.tobytes())
    return scene, animation


def read_output(path):
    raw = path.read_bytes()
    frames, bodies = struct.unpack('<ii', raw[:8])
    data = np.frombuffer(raw[8:], dtype='<f4').reshape(frames, bodies, 7)
    assert np.isfinite(data).all()
    assert np.allclose(np.linalg.norm(data[:, :, 3:], axis=2), 1, atol=1e-4)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', choices=('cpu', 'gpu'), default='cpu')
    parser.add_argument('--iters', type=int, default=8)
    parser.add_argument('--disable-preprocessing', action='store_true')
    parser.add_argument('--out-dir', type=Path, default=ROOT / 'work/evidence/physx_prototype_20260917/contracts')
    args = parser.parse_args()
    folder = args.out_dir / args.device
    folder.mkdir(parents=True, exist_ok=True)
    common = ['--device', args.device, '--allow-angular-approximation', '--intro', '0',
              '--settle', '0', '--warmup', '0', '--iters', str(args.iters)]
    if args.disable_preprocessing:
        common.append('--disable-preprocessing')
    cases, results = {}, {}

    def simulate(name, *positional, options=(), **kwargs):
        scene, animation = inputs(folder, name, *positional, **kwargs)
        output = folder / f'{name}.out.bin'
        command = [str(EXE), str(scene), str(animation), str(output), *common, *options]
        proc = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
        (folder / f'{name}.log').write_text(proc.stdout + proc.stderr, encoding='utf-8')
        assert proc.returncode == 0, (name, proc.returncode, proc.stdout, proc.stderr)
        data = read_output(output)
        cases[name] = (scene, animation, output, data)
        report = json.loads(Path(str(output) + '.solver.json').read_text())
        assert report['sdk_errors_or_warnings'] == 0 and report['backend'] == f'physx-{args.device}'
        results[name] = report
        return data

    data = simulate('fall', [body(pos=(0, 10, 0))], frames=30)
    assert abs(data[-1, 0, 1] - (10 - 98 / 2)) < 0.6, data[-1, 0, 1]

    bodies = [body(mode=0, shape=2, size=(0.5, 4, 0)), body(pos=(0, 8, 0))]
    # A vertical guide prevents a ball balanced on a rounded cap from rolling off.
    guide = [joint(0, 1, pos=(0, 8, 0), lin_lo=(0, 1, 0), lin_hi=(0, -1, 0))]
    data = simulate('capsule', bodies, guide)
    assert 2.95 < data[-1, 1, 1] < 3.05, data[-1, 1]
    bodies[0]['mask'] = 0
    data = simulate('mask', bodies, guide)
    assert data[-1, 1, 1] < -300, data[-1, 1, 1]

    lift = np.zeros((90, 3))
    lift[30:, 1] = np.minimum(np.arange(60) / 15, 2)
    data = simulate('moving_platform', [body(mode=0, shape=1, size=(2, 0.5, 2)),
                                       body(pos=(0, 2, 0))], root_motion=lift)
    assert abs(data[-1, 0, 1] - 2) < 1e-5 and abs(data[-1, 1, 1] - 3) < 0.05, data[-1]

    bodies = [body(mode=0, mask=0), body(pos=(1, 0, 0), shape=1, size=(0.6, 0.1, 0.1), mask=0)]
    data = simulate('asymmetric_angle', bodies, [joint(0, 1, ang_lo=(0, 0, 0.15), ang_hi=(0, 0, 0.35))])
    angle = 2 * np.arctan2(data[-1, 1, 5], data[-1, 1, 6])
    assert -0.36 < angle < -0.14, angle

    bodies = [body(mode=0, mask=0), body(mask=0)]
    data = simulate('fixed_translation', bodies, [joint(0, 1, lin_lo=(0, 0.25, 0), lin_hi=(0, 0.25, 0))], gravity=0)
    assert abs(data[-1, 1, 1] - 0.25) < 0.002, data[-1, 1]
    data = simulate('fixed_angle', bodies, [joint(0, 1, ang_lo=(0, -0.2, 0), ang_hi=(0, -0.2, 0))], gravity=0)
    angle = 2 * np.arctan2(data[-1, 1, 4], data[-1, 1, 6])
    assert abs(angle - 0.2) < 0.002, angle

    for axis in range(3):
        lower, upper, spring = [0] * 3, [0] * 3, [0] * 3
        lower[axis], upper[axis], spring[axis] = 1, -1, 20
        data = simulate(f'spring_{axis}', bodies, [joint(0, 1, ang_lo=lower, ang_hi=upper, spring=spring)],
                        gravity=0, rotation=(axis, 0.5))
        angle = 2 * np.arctan2(data[:, 1, 3 + axis], data[:, 1, 6])
        assert abs(angle[0]) > 0.2 and np.max(np.abs(angle[-15:])) < 0.01, (axis, angle[0], angle[-1])

    # Compliant limits must return an initially displaced body to its allowed
    # range without an explosive correction. No animation smoothing or drive
    # spring participates in these cases; all three angular limit axes are tested.
    for axis in range(3):
        data = simulate(f'compliant_limit_{axis}', bodies,
                        [joint(0, 1, ang_lo=(-0.2, -0.2, -0.2), ang_hi=(0.2, 0.2, 0.2))],
                        gravity=0, rotation=(axis, 0.8), options=['--angular-limit-hz', '8'])
        angle = 2 * np.arctan2(data[:, 1, 3 + axis], data[:, 1, 6])
        assert 0.21 < abs(angle[0]) < 0.8, (axis, angle[0])
        assert np.max(np.abs(angle[-15:])) < 0.21, (axis, angle[-15:])
        assert np.max(np.abs(angle)) < 0.81, (axis, angle)

    bodies = [body(pos=(i, 0, 0), mode=0 if i == 0 else 1, mask=0) for i in range(8)]
    joints = [joint(i - 1, i, pos=(i - 0.5, 0, 0), ang_lo=(1, 1, 1), ang_hi=(-1, -1, -1)) for i in range(1, 8)]
    simulate('chain', bodies, joints)
    assert results['chain']['max_linear_limit_error_pmx'] < 0.03, results['chain']
    bodies = [body(pos=p, mode=0 if i == 0 else 1, mask=0)
              for i, p in enumerate([(0, 0, 0), (1, 0, 0), (1, -1, 0), (0, -1, 0)])]
    joints = [joint(i, (i + 1) % 4, pos=tuple((np.array(bodies[i]['pos']) + bodies[(i + 1) % 4]['pos']) / 2),
                    ang_lo=(1, 1, 1), ang_hi=(-1, -1, -1)) for i in range(4)]
    simulate('closed_loop', bodies, joints)
    assert results['closed_loop']['max_linear_limit_error_pmx'] < 0.03, results['closed_loop']

    names = ['fall', 'capsule', 'asymmetric_angle']
    manifest = folder / 'batch.tsv'
    manifest.write_text('\n'.join('\t'.join(map(str, [*cases[n][:2], folder / f'{n}.batch.bin', 0])) for n in names) + '\n', encoding='utf-8')
    proc = subprocess.run([str(EXE), '--batch', str(manifest), *common], capture_output=True, text=True, encoding='utf-8', errors='replace')
    (folder / 'batch.log').write_text(proc.stdout + proc.stderr, encoding='utf-8')
    assert proc.returncode == 0, proc.stderr
    for name in names:
        actual = read_output(folder / f'{name}.batch.bin')
        reference = cases[name][3]
        error = float(np.max(np.abs(actual - reference)))
        assert error < 0.003, (name, error)
        results[name]['batch_max_component_difference'] = error

    bad = folder / 'bad.frames.bin'
    bad.write_bytes(struct.pack('<ii7f', 1, 1, 0, 0, 0, 0, 0, 0, 0))
    bad_output = folder / 'rejected.bin'
    proc = subprocess.run([str(EXE), str(cases['fall'][0]), str(bad), str(bad_output), *common], capture_output=True)
    assert proc.returncode != 0 and not bad_output.exists()
    (folder / 'verification.json').write_text(json.dumps(dict(device=args.device, passed=True, cases=results), indent=2), encoding='utf-8')
    print(f'PASS: {len(results)} physical scenarios, isolated batch comparison and invalid quaternion rejection ({args.device})')


if __name__ == '__main__':
    main()
