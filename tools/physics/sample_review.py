"""Read-only, hash-bound access to the five user-review samples.

The manifest binds already completed staging and independent audits. Opening a
preview never bakes, installs, or changes a release acceptance decision.
"""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / 'work/evidence/overnight_20260919'
MANIFEST = EVIDENCE / 'sample_review/manifest.json'
SOURCE = 'sample_review_20260919'
LABEL = '09-19 待检查候选'


def _require(condition, message):
    if not condition:
        raise ValueError('样本预览不可用：' + message)


def _key(path):
    stat = path.stat()
    return str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@lru_cache(maxsize=8192)
def _verified(path, size, modified, changed, sha):
    with Path(path).open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    _require(actual == sha, f'文件校验不符：{Path(path).name}')
    _require(_key(Path(path)) == (path, size, modified, changed), '校验时文件发生变化')
    return Path(path)


def verified_path(path, sha):
    path = Path(path).resolve()
    _require(path.is_relative_to(EVIDENCE.resolve()), '文件超出候选证据目录')
    _require(isinstance(sha, str) and re.fullmatch('[a-f0-9]{64}', sha), '缺少有效校验值')
    return _verified(*_key(path), sha)


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def manifest():
    return _manifest(_key(MANIFEST))


@lru_cache(maxsize=1)
def _manifest(stat):
    data = _read(MANIFEST)
    _require(data['status'] == 'awaiting_user_review', '样本状态不符')
    return data


def character_index(ck):
    try:
        cfg = manifest()['characters'].get(ck)
        _require(cfg is not None, f'{ck} 不在本次五个样本中')
        paths = [verified_path(cfg[k]['path'], cfg[k]['sha256'])
                 for k in ('staging', 'audit', 'results')]
        return _index(ck, tuple(_key(path) for path in paths))
    except (OSError, KeyError, TypeError) as error:
        raise ValueError(f'样本预览证据不完整：{ck}') from error


@lru_cache(maxsize=5)
def _index(ck, keys):
    staged, audit, results = [_read(key[0]) for key in keys]
    _require(staged['character'] == audit['character'] == ck, '角色记录不一致')
    _require(staged['status'] == 'verified_staging_inputs', '暂存未完成')
    _require(audit['status'] == 'verified_complete_fresh_physics_candidates' and
             audit['body_outputs'] == 338 and audit['head_outputs'] == 331 and
             audit['rejected'] == audit['sdk_errors_or_warnings'] == 0,
             '完整物理独立审核未通过')
    _require(audit['results_sha256'] == manifest()['characters'][ck]['results']['sha256'],
             '审核与动作结果不对应')
    actions = {}
    for row in results:
        action = row['action']
        _require(row['character'] == ck and action not in actions, '重复动作或角色不符')
        outputs = {}
        for part, record in row['outputs'].items():
            staged_record = staged['release'][Path(record['path']).name]
            _require(staged_record['sha256'] == record['sha256'], '暂存与审核结果不一致')
            outputs[part] = staged_record
        _require(set(outputs) == ({'body', 'head'} if row['status'] == 'complete' else {'body'}),
                 '动作头身配对不完整')
        actions[action] = dict(status=row['status'], outputs=outputs)
    _require(len(actions) == 338 and sum(r['status'] == 'complete' for r in actions.values()) == 331,
             '动作覆盖不完整')
    mesh_records = {part: staged['meshes'][f'moe_{suffix}.mesh']
                    for part, suffix in (('head', 'head'), ('body', 'torso'))}
    meshes = {part: verified_path(row['path'], row['sha256']) for part, row in mesh_records.items()}
    return dict(actions=actions, meshes=meshes,
                mesh_sha256={part: row['sha256'] for part, row in mesh_records.items()},
                finished=staged['finished'])


def available_characters():
    if not MANIFEST.is_file():
        return []
    return list(manifest()['characters'])


def review_info(ck):
    return manifest()['characters'].get(ck, {}).get('review', {})
