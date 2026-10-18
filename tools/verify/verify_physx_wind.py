"""Check wind units, dynamic-only force, per-instance phase and short loop continuation."""
import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
from verify_physx_bake import ROOT, EXE, body, inputs, read_output


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--device', choices=('cpu', 'gpu'), default='gpu')
    ap.add_argument('--out-dir', type=Path, required=True)
    a = ap.parse_args()
    folder = a.out_dir.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    common = ['--device', a.device, '--allow-angular-approximation', '--disable-preprocessing',
              '--wind', '8', '--wind-dir', '1', '0', '0', '--wind-accel', '--intro', '0',
              '--settle', '0', '--warmup', '0']

    def run(name, args):
        p = subprocess.run([str(EXE), *args], capture_output=True, text=True, encoding='utf-8', errors='replace')
        (folder / f'{name}.log').write_text(p.stdout + p.stderr, encoding='utf-8')
        assert p.returncode == 0, p.stderr

    # A constant 8 PMX/s² acceleration must move both mass 1 and mass 4 equally;
    # a kinematic body stays at its authored pose. This catches internal scale errors.
    pair = [body(mode=0, mask=0), body(mask=0), body(pos=(0, 3, 0), mask=0)]
    scene, frames = inputs(folder, 'constant', pair, gravity=0, frames=30)
    lines = scene.read_text().splitlines()
    rigid_lines = [i for i, s in enumerate(lines) if s.startswith('rigid ')]
    values = lines[rigid_lines[2]].split()
    values[12] = '4'  # rigid mass
    lines[rigid_lines[2]] = ' '.join(values)
    scene.write_text('\n'.join(lines) + '\n')
    out = folder / 'constant.bin'
    run('constant', [str(scene), str(frames), str(out), *common, '--wind-base', '1', '--wind-turb', '0'])
    data = read_output(out)
    assert np.max(np.abs(data[:, 0, :3])) < 1e-6
    assert abs(data[-1, 1, 0] - 4.033333) < .002, data[-1]
    assert np.max(np.abs(data[:, 1, 0] - data[:, 2, 0])) < .001
    # Force mode preserves mass response too.
    force = folder / 'force.bin'
    run('force', [str(scene), str(frames), str(force), *[v for v in common if v != '--wind-accel'],
                  '--wind-base', '1', '--wind-turb', '0'])
    d = read_output(force)
    assert abs(d[-1, 1, 0] / d[-1, 2, 0] - 4) < .002

    options = [*common, '--intro', '7', '--warmup', '1', '--loop-extra', '12',
               '--wind-period', '5', '--wind-base', '0', '--wind-turb', '.2']
    rows, reference = [], {}
    for count in (3, 10, 90, 240):
        scene, frames = inputs(folder, f'period{count}', pair[:2], gravity=0, frames=count)
        out = folder / f'period{count}.bin'
        run(f'period{count}', [str(scene), str(frames), str(out), *options, '--loop'])
        d = read_output(out).astype(float)
        extra = min(12, count // 2) if count >= 4 else 0
        assert len(d) == count + extra
        if extra >= 3:
            acc = np.diff(d[:, 1, :3], n=2, axis=0)
            assert np.max(np.abs(acc[:extra-2] - acc[count:])) < 2e-5, count
        reference[count] = d
        rows.append('\t'.join(map(str, [scene, frames, folder / f'period{count}.batch.bin', 1])))
    manifest = folder / 'batch.tsv'
    manifest.write_text('\n'.join(rows) + '\n', encoding='utf-8')
    run('batch', ['--batch', str(manifest), *options])
    errors = {}
    for count, d in reference.items():
        other = read_output(folder / f'period{count}.batch.bin')
        errors[count] = float(np.max(np.abs(d - other)))
        assert errors[count] < .003, errors
    (folder / 'verification.json').write_text(json.dumps(dict(passed=True, device=a.device,
        constant_acceleration_final_pmx=float(data[-1, 1, 0]), batch_component_errors=errors), indent=2))
    print('PASS: wind units, mass/force modes, kinematic exclusion, loop phase, short clips and mixed-duration GPU batch')


if __name__ == '__main__':
    main()
