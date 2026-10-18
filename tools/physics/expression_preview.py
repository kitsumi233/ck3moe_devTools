"""Read-only access to the twelve audited expression-v2 preview candidates.

No solver, installation, guessed head pairing, or incomplete-batch fallback is
allowed here. Full output hashes were checked by each batch audit; requested
assets are checked again, with cache keys including their current file stat.
"""
from collections import Counter
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / 'work/evidence/expression_upgrade_20260918_v2'
SOURCE = 'expression_v2'
LABEL = '新版表情 · 已校验的物理合成候选'
_BATCHES = (
    ('recomposed_physics', ('flo', 'jia', 'lau', 'per')),
    ('recomposed_remaining/batch_01', ('eld', 'len', 'pri', 'cha')),
    ('recomposed_remaining/batch_02', ('sab', 'sha', 'che', 'col')),
)
_BY_CHARACTER = {ck: (folder, characters) for folder, characters in _BATCHES for ck in characters}
_SHA256 = re.compile(r'[a-f0-9]{64}')


def _require(condition, message):
    if not condition:
        raise ValueError('新版表情预览不可用：' + message)


def _read(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ValueError(f'新版表情预览证据无法读取：{path.name}') from error


def _stat_key(path):
    try:
        stat = path.stat()
    except OSError as error:
        raise ValueError(f'新版表情预览文件不存在：{path.name}') from error
    _require(path.is_file(), f'不是文件：{path.name}')
    return (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


@lru_cache(maxsize=8192)
def _verified_stat(path_string, size, modified_ns, changed_ns, expected):
    path = Path(path_string)
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    _require(actual == expected, f'文件校验值不一致：{path.name}')
    _require(_stat_key(path) == (path_string, size, modified_ns, changed_ns),
             f'校验时文件发生变化：{path.name}')
    return path


def verified_path(path, sha):
    """Return an evidence path only after checking its expected SHA-256."""
    path = Path(path).resolve()
    _require(path.is_relative_to(EVIDENCE.resolve()), '文件超出候选证据目录')
    _require(isinstance(sha, str) and _SHA256.fullmatch(sha), '缺少有效文件校验值')
    try:
        return _verified_stat(*_stat_key(path), sha)
    except OSError as error:
        raise ValueError(f'新版表情预览文件无法读取：{path.name}') from error


def _evidence_keys(folder, characters):
    batch = EVIDENCE / folder
    evidence = [batch / name for name in ('summary.json', 'coverage_audit.json', 'run.json', 'spec.json', 'results.json')]
    for ck in characters:
        evidence.extend((ROOT / 'work/evidence/physx_all_20260918/install_manifests' / f'{ck}.json',
                         EVIDENCE / 'base_heads' / ck / 'manifest.json'))
    return tuple(_stat_key(path) for path in evidence)


@lru_cache(maxsize=3)
def _load_batch(folder, characters, evidence_keys):
    batch = (EVIDENCE / folder).resolve()
    summary, audit, run, spec, results = (_read(batch / name) for name in
        ('summary.json', 'coverage_audit.json', 'run.json', 'spec.json', 'results.json'))
    _require(summary.get('status') == 'verified_candidates' and summary.get('rejected') == 0,
             '批次尚未完整完成')
    _require(audit.get('status') == 'verified_complete_character_candidates', '批次尚未通过完整审计')
    _require(run.get('status') == 'complete' and run.get('rejected') == 0, '批次仍在运行或含拒绝结果')
    _require(set(run.get('characters', [])) == set(characters) == set(audit.get('characters', {})),
             '批次角色覆盖不一致')
    _require(all(row.get('solver_invoked') is False for row in (summary, audit, run)), '批次物理来源不符')
    _require(summary.get('installed') is False and audit.get('installed') is False, '候选状态不符')
    total = len(characters) * 338
    _require(isinstance(results, list) and len(results) == total and
             all(value == total for value in (summary.get('actions'), run.get('actions'),
                 run.get('completed'), audit.get('actions'), audit.get('body_outputs'))), '批次动作覆盖不完整')
    _require(audit.get('head_outputs') == len(characters) * 331 and
             audit.get('output_files') == len(characters) * 669, '批次头身数量不一致')
    _require(Path(audit.get('summary', '')).resolve() == batch / 'summary.json' and
             Path(audit.get('results', '')).resolve() == batch / 'results.json', '审计引用了其他批次')
    indexes, all_paths = {}, set()
    _require(all(row.get('ck') in characters for row in results), '结果含批次外角色')
    for ck in characters:
        installed = _read(ROOT / 'work/evidence/physx_all_20260918/install_manifests' / f'{ck}.json')
        selection = installed.get('selection', {})
        previous = {row['name']: row for row in installed['files']}
        cfg = spec.get('characters', {}).get(ck, {})
        head_folder = (EVIDENCE / 'base_heads' / ck).resolve()
        _require(Path(cfg.get('candidate_anims', '')).resolve() == head_folder, '基础表情目录不符')
        base = _read(head_folder / 'manifest.json')
        _require(base.get('status') == 'completed' and base.get('head_count') == 357 and
                 base.get('old_nonface_changed_actions') == 0, '基础表情未完整通过校验')
        heads = {row['name']: row for row in base['heads']}
        rows = [row for row in results if row['ck'] == ck]
        actions = {row['action']: row for row in rows}
        _require(len(rows) == len(actions) == len(selection) == 338 and set(actions) == set(selection),
                 f'{ck} 动作缺失、重复或与正式物理选择不一致')
        _require(Counter(row['status'] for row in rows) == Counter(complete=331, unpaired=7),
                 f'{ck} 配对覆盖不完整')
        coverage = audit['characters'][ck]
        _require(coverage.get('actions') == 338 and coverage.get('outputs') == {'body': 338, 'head': 331} and
                 coverage.get('statuses') == {'complete': 331, 'unpaired': 7} and
                 coverage.get('body_sha256_equal_to_installed') == 338 and coverage.get('paired_heads') == 331,
                 f'{ck} 审计覆盖不一致')
        for field in ('base_head_nonface_max_error', 'physics_head_nonface_max_error', 'mesh_bind_max_error'):
            _require(coverage.get(field) == 0.0, f'{ck} 审计未保持非表情或绑定数据')
        meshes = {part: (EVIDENCE / 'baked' / ck / f'moe_{suffix}.mesh').resolve()
                  for part, suffix in (('head', 'head'), ('body', 'torso'))}
        mesh_hashes = {part: set() for part in meshes}
        names = set()
        for action, row in actions.items():
            _require(re.fullmatch(r'[A-Za-z0-9_]+', action), '动作名无效')
            paired = row['status'] == 'complete'
            _require(set(row.get('outputs', {})) == ({'head', 'body'} if paired else {'body'}),
                     f'{ck}/{action} 头身记录不符')
            _require(row.get('stats', {}).get('_solver', {}).get('sdk_errors_or_warnings') == 0,
                     f'{ck}/{action} 物理记录含错误')
            for part, mesh in meshes.items():
                source_part = 'torso' if part == 'body' else 'head'
                check = row['mesh_checks'][source_part]
                _require(Path(check['candidate']).resolve() == mesh ==
                         Path(cfg['candidate_meshes'][source_part]).resolve(), '候选模型路径不符')
                _require(check.get('bind_max_error') == 0.0, '候选模型绑定不一致')
                mesh_hashes[part].add(check['candidate_sha256'])
            for part, product in row['outputs'].items():
                path = Path(product['path']).resolve()
                expected_folder = (batch / ck / 'anims' / action).resolve()
                _require(path.parent == expected_folder and path.is_relative_to(batch), '动画路径超出动作目录')
                _require(path.suffix == '.anim' and path not in all_paths and path.name not in names,
                         '动画路径重复或格式不符')
                _require(path.is_file(), f'动画文件缺失：{path.name}')
                _require(isinstance(product.get('sha256'), str) and _SHA256.fullmatch(product['sha256']),
                         '动画缺少有效校验值')
                old = previous.get(path.name, {})
                _require(product.get('previous_physics_sha256') == old.get('sha256') and
                         product.get('mod_relative_path') == old.get('path'), '正式物理来源不一致')
                if part == 'body':
                    _require(product['sha256'] == old.get('sha256'), '身体动画与正式物理不一致')
                all_paths.add(path)
                names.add(path.name)
            if paired:
                head_name = Path(row['outputs']['head']['path']).name
                _require(Path(row['source_head']).resolve() == head_folder / head_name and
                         row['source_head_sha256'] == heads.get(head_name, {}).get('sha256'), '表情头部来源不一致')
                for field in ('base_head_preservation', 'physics_head_preservation'):
                    _require(row.get(field, {}).get('max_error') == 0.0 and row[field].get('preserved_bones', 0) > 0,
                             '非表情骨骼校验未通过')
        _require(names == set(previous), '输出动画未完整覆盖正式物理选择')
        _require(all(len(values) == 1 for values in mesh_hashes.values()), '动作记录引用不同模型')
        mesh_hashes = {part: next(iter(values)) for part, values in mesh_hashes.items()}
        for part, mesh in meshes.items():
            verified_path(mesh, mesh_hashes[part])
        indexes[ck] = dict(actions=actions, meshes=meshes, mesh_sha256=mesh_hashes,
                           finished=float(run['finished']))
    _require(_evidence_keys(folder, characters) == evidence_keys, '读取时批次证据发生变化')
    return indexes


def character_index(ck):
    """Resolve only a fully audited, explicitly whitelisted character."""
    _require(ck in _BY_CHARACTER, f'角色 {ck} 尚未完成候选物理合成')
    folder, characters = _BY_CHARACTER[ck]
    try:
        return _load_batch(folder, characters, _evidence_keys(folder, characters))[ck]
    except (KeyError, TypeError, OSError) as error:
        raise ValueError(f'新版表情预览证据不完整：{ck}') from error


def available_characters():
    """Enumerate validated candidates; unverified batches are never advertised."""
    result = []
    for ck in _BY_CHARACTER:
        try:
            character_index(ck)
        except ValueError:
            continue
        result.append(ck)
    return result
