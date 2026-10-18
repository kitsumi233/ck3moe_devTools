"""Build the pinned native PhysX prototype on Windows (no Python package install).

Downloads SDK source and the matching official GPU runtime wheel into work/.
The wheel is only an archive for its native GPU DLL and license files.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = Path(__file__).resolve().parent
SDK_COMMIT = 'da950a3537927784951853c66618036f332ca0ce'
SDK_HASH = '792928e0a621a5dd3f2fa28567ce95943c17f9d2ca4c6f9562b5efafd9fda554'
WHEEL_URL = ('https://files.pythonhosted.org/packages/ff/10/6adc8dc8872e1f2625c2fb92bfcf215bbc6dc4f584244d7c0ef7faf0ac3c/'
             'ovphysx-0.6.3-py3-none-win_amd64.whl')
WHEEL_HASH = '42f9132eb49295ec07792464e2605f1bac5152435e4020b76f8a0040eb22cb48'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fetch(url, path, expected):
    if path.exists():
        if digest(path) != expected:
            raise RuntimeError(f'Existing download hash mismatch; preserve and inspect: {path}')
        return
    pending = path.with_suffix(path.suffix + '.partial')
    if pending.exists():
        raise RuntimeError(f'Incomplete earlier download exists; preserve and inspect: {pending}')
    print(f'Download {url}', flush=True)
    with urllib.request.urlopen(url, timeout=90) as response, pending.open('wb') as out:
        shutil.copyfileobj(response, out, length=1024 * 1024)
    if digest(pending) != expected:
        raise RuntimeError(f'Download hash mismatch: {pending}')
    pending.rename(path)


def extract(archive, destination, select):
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as contents:
        for member in contents.infolist():
            if not select(member.filename):
                continue
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise RuntimeError(f'Unsafe archive entry: {member.filename}')
            if not target.exists():
                contents.extract(member, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=12)
    parser.add_argument('--cpu-only', action='store_true', help='Build host support, skip fetching GPU runtime')
    args = parser.parse_args()
    deps, build = ROOT / 'work/physx_sdk', ROOT / 'work/moe_physx_build'
    deps.mkdir(parents=True, exist_ok=True)
    build.mkdir(parents=True, exist_ok=True)
    archive = deps / f'{SDK_COMMIT}.zip'
    fetch(f'https://codeload.github.com/NVIDIA-Omniverse/PhysX/zip/{SDK_COMMIT}', archive, SDK_HASH)
    extract(archive, deps, lambda name: '/physx/' in name or name.endswith('/LICENSE.md'))
    sdk = deps / f'PhysX-{SDK_COMMIT}' / 'physx'
    commands = [
        ['cmake', '-S', str(SOURCE), '-B', str(build), '-G', 'Visual Studio 17 2022', '-A', 'x64',
         f'-DPHYSX_ROOT_DIR={sdk.as_posix()}'],
        ['cmake', '--build', str(build), '--config', 'release', '--target', 'moe_physx_bake', '--parallel', str(args.jobs)],
    ]
    for index, command in enumerate(commands):
        log = build / f'build_{index}.log'
        print(f'Build step {index + 1}: {log}', flush=True)
        with log.open('wb') as out:
            result = subprocess.run(command, stdout=out, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f'Build failed ({result.returncode}); see {log}')
    licenses = build / 'licenses'
    licenses.mkdir(exist_ok=True)
    shutil.copy2(sdk / 'LICENSE.md', licenses / 'PhysX-LICENSE.md')
    runtime = []
    if not args.cpu_only:
        wheel = deps / WHEEL_URL.rsplit('/', 1)[1]
        fetch(WHEEL_URL, wheel, WHEEL_HASH)
        unpacked = deps / 'runtime'
        extract(wheel, unpacked, lambda name: name.lower().endswith('.dll') or 'license' in name.lower())
        gpu = list(unpacked.rglob('PhysXGpu_64.dll'))
        if len(gpu) != 1:
            raise RuntimeError(f'Expected exactly one matching PhysXGpu_64.dll: {gpu}')
        # Use only GPU/runtime dependencies. The CPU SDK is linked statically.
        for dll in gpu[0].parent.glob('*.dll'):
            if dll.name.lower().startswith(('physxgpu', 'cudart', 'nvrtc', 'nvjitlink', 'nvtoolsext')):
                shutil.copy2(dll, build / dll.name)
                runtime.append(dict(name=dll.name, sha256=digest(dll)))
        for path in unpacked.rglob('*'):
            if path.is_file() and 'license' in str(path.relative_to(unpacked)).lower():
                target = licenses / 'runtime' / path.relative_to(unpacked)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    manifest = dict(sdk_commit=SDK_COMMIT, sdk_archive_sha256=SDK_HASH,
                    runtime_wheel_url=None if args.cpu_only else WHEEL_URL,
                    runtime_wheel_sha256=None if args.cpu_only else WHEEL_HASH,
                    executable_sha256=digest(build / 'moe_physx_bake.exe'), runtime=runtime,
                    source_sha256={name: digest(SOURCE / name) for name in ('main.cpp', 'twist_z.h', 'CMakeLists.txt', 'build.py')},
                    commands=commands)
    (build / 'build_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(f'Built {build / "moe_physx_bake.exe"}', flush=True)


if __name__ == '__main__':
    main()
