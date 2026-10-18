"""Shared integrity checks and content keys for isolated physics trials."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct

import numpy as np


RAW_SCHEMA = 1
ARRAY_SCHEMA = 1


def file_sha256(path: Path | str) -> str:
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical_sha256(value) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def save_json_atomic(path: Path | str, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                    allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)


def load_array_cache(path: Path | str, key: str):
    """Load a small deterministic NumPy cache only when its key and bytes match."""
    path = Path(path)
    manifest_path = path.with_suffix(path.suffix + '.json')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('schema') != ARRAY_SCHEMA or manifest.get('key') != key:
            return None
        if file_sha256(path) != manifest.get('sha256'):
            return None
        array = np.load(path, allow_pickle=False)
        if (list(array.shape) != manifest.get('shape') or
                str(array.dtype) != manifest.get('dtype') or
                not np.isfinite(array).all()):
            return None
        return array, manifest.get('metadata', {})
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def save_array_cache(path: Path | str, key: str, array,
                     metadata: dict | None = None) -> None:
    """Write an array cache atomically; the manifest is always committed last."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    array = np.asarray(array)
    if not np.isfinite(array).all():
        raise ValueError('Refusing to cache a non-finite array')
    with temporary.open('wb') as stream:
        np.save(stream, array, allow_pickle=False)
    os.replace(temporary, path)
    save_json_atomic(path.with_suffix(path.suffix + '.json'), dict(
        schema=ARRAY_SCHEMA, key=key, sha256=file_sha256(path),
        shape=list(array.shape), dtype=str(array.dtype), metadata=metadata or {}))


def raw_output_error(*, output: Path | str, frames: Path | str, rigids: int,
                     tail_frames: int, report: dict | None = None,
                     command_record: dict | None = None,
                     solver_sha256: str | None = None,
                     expected_command: list[str] | None = None,
                     batch_instances: int | None = None) -> str | None:
    """Return a short cache-miss reason unless a PhysX raw is safe to reuse."""
    output, frames = Path(output), Path(frames)
    try:
        with frames.open('rb') as stream:
            frame_header = stream.read(8)
        if len(frame_header) != 8:
            return 'truncated frames header'
        source_frames, source_bones = struct.unpack('<ii', frame_header)
        if source_frames < 1 or source_bones < 1:
            return 'frames header has invalid dimensions'
        expected_frames_bytes = 8 + source_frames * source_bones * 7 * 4
        if frames.stat().st_size != expected_frames_bytes:
            return 'frames byte length does not match header'
        if rigids < 1 or tail_frames < 0:
            return 'expected output dimensions are invalid'

        with output.open('rb') as stream:
            output_header = stream.read(8)
        if len(output_header) != 8:
            return 'truncated output header'
        output_frames, output_rigids = struct.unpack('<ii', output_header)
        if output_frames != source_frames + tail_frames or output_rigids != rigids:
            return 'output header does not match expected dimensions'
        expected_bytes = 8 + output_frames * output_rigids * 7 * 4
        if output.stat().st_size != expected_bytes:
            return 'output byte length does not match header'

        poses = np.memmap(output, dtype='<f4', mode='r', offset=8,
                          shape=(output_frames, output_rigids, 7))
        try:
            for begin in range(0, output_frames, 128):
                chunk = poses[begin:begin + 128]
                if not np.isfinite(chunk).all():
                    return 'output contains non-finite values'
                quat_norm = np.linalg.norm(chunk[:, :, 3:], axis=2)
                if np.max(np.abs(quat_norm - 1.0), initial=0.0) >= 0.001:
                    return 'output contains non-unit quaternions'
        finally:
            del poses

        if report is not None:
            if report.get('sdk_errors_or_warnings') != 0:
                return 'solver report contains SDK warnings or errors'
            if report.get('batch_instances', 0) < 1:
                return 'solver report has no completed batch instances'
            if batch_instances is not None and report.get('batch_instances') != batch_instances:
                return 'solver report batch size mismatch'
        if command_record is not None:
            if command_record.get('returncode') != 0:
                return 'command record is not successful'
            if solver_sha256 and command_record.get('solver_sha256') != solver_sha256:
                return 'solver executable hash mismatch'
            if expected_command is not None and command_record.get('command') != expected_command:
                return 'solver command mismatch'
    except (OSError, ValueError, struct.error, json.JSONDecodeError) as exc:
        return f'raw cache validation failed: {exc}'
    return None
