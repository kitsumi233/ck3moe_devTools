"""Local mesh/animation viewer and single-action native Bullet workbench."""
import argparse
import base64
import contextlib
import gzip
import hashlib
import io
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools' / part) for part in ('lib', 'anim', 'physics')]
import moe_bullet_bake as bake
import moe_chars as chars
import moe_paths as paths
import moe_material_contract as materials
import pdx_parse
import physics_profile as profiles
import retarget_vanilla_anim as anim
import expression_preview
import sample_review
import response_preview
import lau_response_preview
import twist_z_preview
import twist_z_lock_preview
import bone_twist_preview
import bone_twist_partial_preview
import gate_candidate_preview
import gate_control_preview
import gate_c60_preview
import gate_c60dx_preview
import gate_c60dxv_preview
import gate_c60dxg_preview
import gate_c60dxgl_preview
import gate_full_preview
import gate_all92_preview
import gate_all92h_preview
import gate_all92r_preview
import gate_final_preview
import gate_palm_preview
import gate_fx_preview
import gate_allfix_preview
import gate_soft1_preview
import gate_main_preview
import gate_cloud_preview
import gate_old92_preview
import wuwa_preview
import lau_sitting_trials_preview
import response_trial_review_preview

PREVIEW_PROVIDERS = {provider.SOURCE: provider for provider in (expression_preview, sample_review, response_preview, lau_response_preview, twist_z_preview, twist_z_lock_preview, bone_twist_preview, bone_twist_partial_preview, gate_candidate_preview, gate_control_preview, gate_c60_preview, gate_c60dx_preview, gate_c60dxv_preview, gate_c60dxg_preview, gate_c60dxgl_preview, gate_full_preview, gate_all92_preview, gate_all92h_preview, gate_all92r_preview, gate_final_preview, gate_palm_preview, gate_fx_preview, gate_allfix_preview, gate_soft1_preview, gate_main_preview, gate_cloud_preview, gate_old92_preview, wuwa_preview, response_trial_review_preview, *lau_sitting_trials_preview.PROVIDERS)}
LAU_SITTING_PREVIEW_SOURCES = frozenset(provider.SOURCE for provider in lau_sitting_trials_preview.PROVIDERS)

WORK = ROOT / 'work/physics_debug'
UI = Path(__file__).with_name('debug_ui')
PHYSX_GPU_EVIDENCE = ROOT / 'work/evidence/physx_prototype_20260917/gpu'
PHYSX_GPU_SOURCE = 'physx_gpu'
PHYSX_ALL_SOURCE = 'physx_gpu_all'
PHYSX_ALL_EVIDENCE = ROOT / 'work/evidence/physx_prototype_20260917/gpu_all_jia_20260918_material_cache'
JIA_FREE_HAIR_SOURCE = 'jia_freehair_gentle'
JIA_FREE_HAIR_EVIDENCE = Path(r'E:/CK3_MoeMod_physics_evidence/20260925_jia_freehair_gentle/full338')
JIA_FORCES10_SOURCE = 'jia_freehair_forces10'
JIA_FORCES10_EVIDENCE = Path(r'E:/CK3_MoeMod_physics_evidence/20260925_jia_freehair_gentle_forces10')
JIA_SOURCE10_SOURCE = 'jia_sourceangles_forces10'
JIA_SOURCE10_EVIDENCE = Path(r'E:/CK3_MoeMod_physics_evidence/20260925_jia_sourceangles_forces10')
JIA_INTERNAL_HAIR_SOURCE = 'jia_internalhair_forces10'
JIA_INTERNAL_HAIR_EVIDENCE = Path(r'E:/CK3_MoeMod_physics_evidence/20260925_jia_internalhair_forces10')
JIA_SPRING_NARROW_SOURCE = 'jia_spring_narrow10'
JIA_SPRING_NARROW_EVIDENCE = Path(r'E:/CK3_MoeMod_physics_evidence/20260925_jia_spring_narrow10')
JIA_SPRING_HAIR03_SITTING075_SOURCE = 'jia_spring_hair03_sitting07510'
JIA_SPRING_HAIR03_SITTING075_EVIDENCE = Path(r'E:/CK3_MoeMod_physics_evidence/20260926_jia_spring_hair03_sitting_skirt07510')
JIA_SPRING_HAIR100_SITTING075_SOURCE = 'jia_spring_hair100_sitting07510'
JIA_SPRING_HAIR100_SITTING075_EVIDENCE = Path(r'E:/CK3_MoeMod_physics_evidence/20260926_jia_spring_hair100_sitting_skirt07510')
JIA_SPRING_CLOTH_SOURCE = 'jia_spring_cloth10'
JIA_SPRING_CLOTH_EVIDENCE = Path(r'E:/CK3_MoeMod_physics_evidence/20260925_jia_spring_cloth10')
JIA_DANCE_SPIN_HAIR12_SOURCE = 'jia_dance_spin_hair12'
JIA_DANCE_SPIN_HAIR12_EVIDENCE = Path(r'E:/CK3_MoeMod_physics_evidence/20260925_jia_dance_spin_hair12')
PHYSX_PREVIEWS = {
    'physx_gpu_breeze': (ROOT / 'work/evidence/physx_prototype_20260917/jia_breeze_preview_20260918',
                         'PhysX GPU · 轻柔飘动 · 按动作复核的候选'),
    'physx_gpu_breeze_full': (ROOT / 'work/evidence/physx_prototype_20260917/jia_breeze_all_20260918/wind8',
                              'PhysX GPU · 全量统一轻风对照 · 部分动作抖动退化'),
    'physx_gpu_breeze_trial': (ROOT / 'work/evidence/physx_prototype_20260917/jia_breeze_20260918/wind8',
                              'PhysX GPU · 轻柔飘动 · 六动作对照'),
    'physx_gpu_breeze_control': (ROOT / 'work/evidence/physx_prototype_20260917/jia_breeze_20260918/wind0',
                                'PhysX GPU · 无风 · 同批次循环对照'),
    'physx_gpu_noprep': (ROOT / 'work/evidence/physx_prototype_20260917/gpu_all_jia_20260918_noprep',
                         'PhysX GPU · 关闭约束预处理 · 全动作复核'),
    PHYSX_ALL_SOURCE: (PHYSX_ALL_EVIDENCE, 'PhysX GPU · 全动作 · TGS 8/1 · 无循环淡入'),
    'physx_gpu_soft8': (ROOT / 'work/evidence/physx_prototype_20260917/jia_stability_20260918/soft8',
                        'PhysX GPU · 柔化角限位 8 Hz 对照 · 尚未通过质量验收'),
    JIA_FREE_HAIR_SOURCE: (JIA_FREE_HAIR_EVIDENCE,
                           '绛雨旧试验 · 全自由导致上飘 · 已拒收'),
    JIA_FORCES10_SOURCE: (JIA_FORCES10_EVIDENCE,
                          '绛雨诊断对照 · 10动作 · 全自由＋外力顺序修正'),
    JIA_SOURCE10_SOURCE: (JIA_SOURCE10_EVIDENCE,
                         '绛雨快速迭代 · 10动作 · 原始关节＋外力顺序修正'),
    JIA_INTERNAL_HAIR_SOURCE: (JIA_INTERNAL_HAIR_EVIDENCE,
                               '绛雨快速迭代 · 10动作 · 无弹簧消融对照（头发效果差）'),
    JIA_SPRING_NARROW_SOURCE: (JIA_SPRING_NARROW_EVIDENCE,
                               '绛雨快速迭代 · 10动作 · 回中弹簧候选（坐姿裙高频待修）'),
    JIA_SPRING_HAIR03_SITTING075_SOURCE: (JIA_SPRING_HAIR03_SITTING075_EVIDENCE,
                                          '绛雨快速迭代 · 头发弹簧0.3＋坐姿裙阻尼0.75（10动作候选）'),
    JIA_SPRING_HAIR100_SITTING075_SOURCE: (JIA_SPRING_HAIR100_SITTING075_EVIDENCE,
                                           '绛雨快速迭代 · 头发弹簧100＋坐姿裙阻尼0.75（10动作候选，只读）'),
    JIA_SPRING_CLOTH_SOURCE: (JIA_SPRING_CLOTH_EVIDENCE,
                              '绛雨诊断 · 10动作 · 衣带长链放开（已拒收）'),
    JIA_DANCE_SPIN_HAIR12_SOURCE: (JIA_DANCE_SPIN_HAIR12_EVIDENCE,
                                   'JIA dance_spin action tuning - hair return 1.2 - read-only candidate'),
}
CANDIDATE_RESULT_SOURCES = {
    JIA_FREE_HAIR_SOURCE, JIA_FORCES10_SOURCE, JIA_SOURCE10_SOURCE,
    JIA_INTERNAL_HAIR_SOURCE, JIA_SPRING_NARROW_SOURCE, JIA_SPRING_HAIR03_SITTING075_SOURCE,
    JIA_SPRING_HAIR100_SITTING075_SOURCE,
    JIA_SPRING_CLOTH_SOURCE,
    JIA_DANCE_SPIN_HAIR12_SOURCE,
}


def full_gpu_preview(character, source=PHYSX_ALL_SOURCE):
    evidence_root = PHYSX_PREVIEWS[source][0]
    path = evidence_root / 'preview.json'
    if character != 'jia':
        return {}
    if path.is_file():
        return json.loads(path.read_text(encoding='utf-8')).get('actions', {})
    if source in CANDIDATE_RESULT_SOURCES:
        results = evidence_root / 'results.json'
        if not results.is_file():
            return {}
        actions = {}
        for entry in json.loads(results.read_text(encoding='utf-8')):
            if entry.get('status') != 'complete':
                continue
            outputs = entry.get('outputs', {})
            if set(outputs) != {'body', 'head'}:
                continue
            relative = {part: str(Path(record['path']).relative_to(evidence_root)).replace('\\', '/')
                        for part, record in outputs.items()}
            actions[entry['action']] = dict(status='complete',
                sha256={part: record['sha256'] for part,record in outputs.items()}, **relative)
        return actions
    return {}
def _probe_characters():
    """在册角色里筛出能装配 Rig 的那些 —— 名单从登记表派生，不硬编码。

    实测装配，不写死排除名单。纳甘曾因手臂胶囊旋转全 NaN 被拒绝，09-18 已能按
    骨轴与胶囊中心恢复无关节运动学胶囊；重新启动后会自动回归角色清单。
    其它无法可靠恢复的源错误仍在这里明确报告。
    """
    usable = []
    for key in chars.BY_CK:
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                bake.Rig(bake.char_config(key))
        except Exception as error:
            print(f'[debug] {key} 不可调试：{type(error).__name__}: {error}', file=sys.stderr)
            continue
        usable.append(key)
    return tuple(usable)


def _selected_characters():
    requested = os.environ.get('MOE_PHYSICS_DEBUG_CHARACTERS')
    # Read-only candidate browsing may include newly imported roles lacking a
    # release recipe. Their explicit source contract is checked when selected.
    if os.environ.get('MOE_PHYSICS_RESPONSE_TRIAL_VIEW') == '1':
        requested = os.environ.get('MOE_PHYSICS_DEBUG_CHARACTERS')
        selected = tuple(key.strip() for key in requested.split(',') if key.strip()) if requested else tuple(chars.BY_CK)
        unknown = set(selected) - set(chars.BY_CK)
        if unknown:
            raise ValueError(f'Unknown debug character(s): {sorted(unknown)}')
        return selected
    if not requested:
        return _probe_characters()
    selected = tuple(key.strip() for key in requested.split(',') if key.strip())
    unknown = set(selected) - set(chars.BY_CK)
    if unknown:
        raise ValueError(f'Unknown debug character(s): {sorted(unknown)}')
    usable = []
    for key in selected:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                bake.Rig(bake.char_config(key))
        except Exception as error:
            print(f'[debug] {key} unavailable: {type(error).__name__}: {error}', file=sys.stderr)
        else:
            usable.append(key)
    return tuple(usable)


CHARACTERS = _selected_characters()
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.RLock()
JOBS = {}
READ_ONLY = False
ONLY_SOURCE = None
TEXTURES = {}
RUN_ID = re.compile(r'^[a-f0-9]{16}$')


def encoded(values, dtype='<f4'):
    array = np.asarray(values, dtype=dtype)
    if not np.isfinite(array).all():
        raise ValueError('预览数据包含非有限数值')
    return base64.b64encode(array.tobytes()).decode('ascii')


def character_check(character):
    if character not in CHARACTERS:
        raise ValueError(
            f'角色 {character} 不在可调试名单里（共 {len(CHARACTERS)} 个）'
            '—— 装配 Rig 失败的角色会在启动时打印原因')
    return character


@lru_cache(maxsize=128)
def debug_base_anims_dir(character):
    """Resolve and validate a character's unsimulated inputs once per server.

    ``require_base_anims`` verifies the complete input manifest.  Doing that
    once for every action made the UI's action-catalog request spend several
    seconds per row (and made the first character look as if it had no model).
    The directory is immutable while this read-only server is running, so a
    small process-local cache is the correct scope.
    """
    return paths.require_base_anims(character)


@lru_cache(maxsize=1)
def debug_config_pairs():
    """Cache the state-name pairing table used for legacy head animations."""
    return bake.BPI.config_pairs()


def action_path(character, action, part='body', source='current', run=None):
    character_check(character)
    if part not in ('head', 'body'):
        raise ValueError('动画部位无效')
    if not re.fullmatch(r'[A-Za-z0-9_]+', action):
        raise ValueError('动作名无效')
    prefix = chars.BY_CK[character]['prefix']
    name = f'{prefix}{part}_{action}.anim'
    if source in PREVIEW_PROVIDERS:
        provider = PREVIEW_PROVIDERS[source]
        entry = provider.character_index(character)['actions'].get(action)
        if not entry or entry.get('status') != 'complete' or set(entry.get('outputs', {})) != {'head', 'body'}:
            raise ValueError(f'{provider.LABEL} 的 {action} 没有通过完整头身配对校验，不能用于此预览')
        record = entry['outputs'][part]
        return provider.verified_path(record['path'], record['sha256'])
    if source == 'current':
        directory = paths.anims_dir(character)
    elif source == 'base':
        directory = debug_base_anims_dir(character)
    elif source == 'trial' and run and RUN_ID.fullmatch(run):
        directory = WORK / 'runs' / run / 'anims'
        meta = json.loads((directory.parent / 'profile.json').read_text(encoding='utf-8'))
        if meta['character'] != character:
            raise ValueError('试烘结果属于另一个角色')
    elif source in PHYSX_PREVIEWS:
        evidence_root = PHYSX_PREVIEWS[source][0]
        entry = full_gpu_preview(character, source).get(action, {})
        if entry.get('status') != 'complete' or not entry.get(part):
            reason = {'rejected': '未通过物理结果检查，原始结果已保留',
                      'unpaired': '基础动画缺少配对头部'}.get(entry.get('status'), '尚未完成')
            if entry.get('status') == 'rejected':
                report_path = (evidence_root / 'io' / f'{prefix}body_{action}.out.bin.solver.json'
                               if source != 'physx_gpu_soft8' else
                               evidence_root / f'{action}.out.bin.solver.json')
                if report_path.is_file():
                    report = json.loads(report_path.read_text(encoding='utf-8'))
                    reason = f"关节分离误差 {report['max_linear_limit_error_pmx']:.3f} PMX 单位，超过模型尺度，已拦截导出"
            raise ValueError(f'{action} 的 GPU 预览不可用：{reason}；右侧当前为基础动作')
        result = (evidence_root / entry[part]).resolve()
        if not result.is_relative_to(evidence_root.resolve()) or not result.is_file():
            raise ValueError('GPU 证据路径无效')
        if source in CANDIDATE_RESULT_SOURCES:
            digest=hashlib.sha256(result.read_bytes()).hexdigest()
            if digest!=entry['sha256'][part]:
                raise ValueError('候选动画已变更，哈希不匹配')
        return result
    elif source == PHYSX_GPU_SOURCE:
        # 只读证据预览：不复制 PhysX 动画进调试台，不允许任意目录或再烘焙后端混用。
        directory = PHYSX_GPU_EVIDENCE / character
    else:
        raise ValueError('动画来源无效')
    result = directory / name
    # 与写回器使用同一份真实 state 配对表；异名配对适用于全部角色及试烘。
    # 不依赖单个角色的历史证据，也不修改写回器的全局角色前缀。
    if not result.is_file() and part == 'head' and source in ('base', 'current', 'trial'):
        pairs = debug_config_pairs()
        for state in (action, action + '_entry'):
            head_state = pairs.get(state)
            if not head_state:
                continue
            for candidate_state in (head_state, head_state.removesuffix('_entry')):
                candidate = directory / f'{prefix}head_{candidate_state}.anim'
                if candidate.is_file():
                    result = candidate
                    break
            if result.is_file():
                break
    if not result.is_file():
        raise ValueError(f'缺少配对动画 {name}（{source}）')
    return result


# 通用到无信息量的前缀。芙洛伦全部刚体叫 bone<四位数>_<真名>，按「首个数字截断」
# 会把 71/100 条并成一个叫 bone 的组（胶带/包/胸/刘海/头发全混一起）。
GENERIC_RIGID_PREFIX = ('bone', 'rigid', 'body', 'joint', 'obj')


def group_name(name):
    if name.startswith('Skirt_'):
        return 'Skirt'
    head = re.sub(r'[0-9].*$', '', name)
    # 截出来是通用词而原名里还有 _<真名>，就改用真名归组（真名自己再去尾号）
    if head.lower() in GENERIC_RIGID_PREFIX:
        rest = re.sub(r'^[A-Za-z]+[0-9]+_', '', name, count=1)
        if rest != name:
            tail = re.sub(r'[0-9]+$', '', rest)
            if len(tail) >= 2:
                return tail
    return head


# 全部在册角色可选，上限 2 会让来回切换反复重装 Rig。
@lru_cache(maxsize=8)
def rig_for(character):
    character_check(character)
    return bake.Rig(bake.char_config(character))


@lru_cache(maxsize=1)
def base_available_characters():
    """哪些角色有可用的 base（未解算）动画缓存，供 /grid 的 base 来源选用。

    这是一次性 release-revision 快照，不是全员覆盖；大多数角色没有。
    """
    out = []
    for ck in sorted(chars.BY_CK):
        try:
            directory = paths.base_anims_dir(ck)
        except ValueError:
            continue
        if directory.is_dir() and next(directory.glob('moe_body_idle.anim'), None):
            out.append(ck)
    return out


@lru_cache(maxsize=8)
def response_rig_for(character,mesh_paths,source=response_preview.SOURCE):
    import explicit_bake_inputs
    import physics_response
    provider=PREVIEW_PROVIDERS[source]
    cfg=json.loads((provider.EVIDENCE/'characters'/character/'configuration.json').read_text(encoding='utf8'))
    selected={part:filename for part,filename,*_ in mesh_paths}
    with explicit_bake_inputs.rig_inputs(bake,character,head_mesh=selected['head'],torso_mesh=selected['body'],use_release_recipe=False) as rig:
        experiment=cfg.get('experiment',{})
        physics_response.apply(rig,experiment.get('families'),twist_z_limit_deg=experiment.get('twist_z_limit_deg'),
                               bone_twist_lock=experiment.get('bone_twist_lock',False),
                               bone_twist_families=experiment.get('bone_twist_families'))
        return rig


def texture_url(diff):
    if not diff:
        return None
    stem = Path(diff).stem
    if stem not in TEXTURES:
        for extension in ('.dds', '.png'):
            candidate = paths.find(stem + extension, missing_ok=True)
            if candidate.is_file():
                TEXTURES[stem] = candidate
                break
    return '/texture/' + stem if stem in TEXTURES else None


def mesh_data(path, contracts):
    tree = pdx_parse.parse(path.read_bytes())
    bones, submeshes = {}, []
    mesh_index = -1

    def visit(node):
        nonlocal mesh_index
        for name, child in anim.kids(node):
            if name == 'mesh':
                mesh_index += 1
            props = anim.props(child)
            if 'tx' in props and 'ix' in props:
                index = props['ix'][1][0]
                bones[index] = dict(name=name, parent=props.get('pa', (None, [-1]))[1][0], inverse=props['tx'][1])
            if 'p' in props:
                contract = contracts[mesh_index]
                diffuse = contract.get('diffuse')
                shader = materials.pdx_shader(contract['material'], [diffuse] if diffuse else [])
                count = len(props['p'][1]) // 3
                result = dict(positions=encoded(props['p'][1]), triangles=encoded(props['tri'][1], '<u4'), count=count,
                              material=contract['material'], hidden=materials.is_no_render(contract['material']),
                              alpha_test=0 if shader in ('portrait_skin', 'portrait_face', 'portrait_eye') else 0.5,
                              texture=texture_url(diffuse))
                if 'n' in props:
                    result['normals'] = encoded(props['n'][1])
                if 'u0' in props:
                    result['uv'] = encoded(props['u0'][1])
                for kind, section in anim.kids(child):
                    section_props = anim.props(section)
                    if kind == 'skin':
                        influences = section_props['bones'][1][0]
                        indices = np.asarray(section_props['ix'][1]).reshape(count, influences)
                        weights = np.asarray(section_props['w'][1]).reshape(count, influences)
                        if influences > 4:
                            raise ValueError('预览仅支持最多4个骨骼权重')
                        weights = np.where(indices >= 0, weights, 0.0)
                        weights /= np.maximum(weights.sum(axis=1, keepdims=True), 1e-9)
                        result['indices'] = encoded(np.pad(np.maximum(indices, 0), ((0, 0), (0, 4 - influences))), '<u2')
                        result['weights'] = encoded(np.pad(weights, ((0, 0), (0, 4 - influences))))
                submeshes.append(result)
            visit(child)
    visit(tree)
    if sorted(bones) != list(range(len(bones))):
        raise ValueError('骨骼索引不连续')
    return dict(bones=[bones[index] for index in range(len(bones))], meshes=submeshes)


# 同上：预览数据也按角色缓存，切回来时不必重算。
@lru_cache(maxsize=12)
def _model_data(character, mesh_paths, source='current'):
    with LOCK:
        if source in PREVIEW_PROVIDERS and source in {response_preview.SOURCE, lau_response_preview.SOURCE, twist_z_preview.SOURCE, twist_z_lock_preview.SOURCE, bone_twist_preview.SOURCE, bone_twist_partial_preview.SOURCE, gate_candidate_preview.SOURCE, gate_control_preview.SOURCE, gate_c60_preview.SOURCE, gate_c60dx_preview.SOURCE, gate_c60dxv_preview.SOURCE, gate_c60dxg_preview.SOURCE, gate_c60dxgl_preview.SOURCE, gate_full_preview.SOURCE, gate_all92_preview.SOURCE, gate_all92h_preview.SOURCE, gate_all92r_preview.SOURCE, gate_final_preview.SOURCE, gate_palm_preview.SOURCE, gate_fx_preview.SOURCE, gate_allfix_preview.SOURCE, gate_soft1_preview.SOURCE, gate_main_preview.SOURCE, gate_cloud_preview.SOURCE, gate_old92_preview.SOURCE} | LAU_SITTING_PREVIEW_SOURCES:
            rig = response_rig_for(character,mesh_paths,source)
        else:
            rig = rig_for(character)
        report = json.loads(chars.report_path(character).read_text(encoding='utf-8'))
        parts = {part: mesh_data(Path(filename), {sub['index']: sub for sub in report['parts'][key]['submeshes']})
                 for part, filename, _mtime, _size, key in mesh_paths}
        rigids = []
        warnings = []
        for repair in rig.source_repairs:
            if repair['source'] == 'explicit_degenerate_shape_extent':
                warnings.append(f"{repair['name']}：源零尺寸已按登记配置扩为 {repair['epsilon_pmx']:g} PMX，使用有限厚度近似")
            else:
                warnings.append(f"{repair['name']}：源旋转缺失分量已按胶囊与骨轴方向恢复，未改源PMX文件")
        for index, rigid in enumerate(rig.model.rigids):
            bone_index = rigid.bone
            bone = rig.B[bone_index] if bone_index is not None and bone_index >= 0 else None
            bone_name = rig.pmx_to_ours.get(bone.name) if bone else None
            parent = bone
            while parent and bone_name not in rig.bind:
                parent_index = parent.parent
                parent = rig.B[parent_index] if parent_index is not None and parent_index >= 0 else None
                bone_name = rig.pmx_to_ours.get(parent.name) if parent else None
            scale = rig.cfg['scale']
            preview_valid = bool(np.isfinite(rigid.rotation).all())
            if not preview_valid:
                warnings.append(f'{rigid.name} 的源旋转含非有限值，碰撞体预览已隐藏，试烘结果需复核')
            rotation = bake.q_to_m(bake.euler_yxz_q(rigid.rotation)) if preview_valid else np.eye(3)
            position = np.asarray(rigid.location) * scale
            if bone_name in rig.bind:
                bind_rotation, bind_position = rig.bind[bone_name]
                offset_rotation = bind_rotation.T @ rotation
                offset_position = bind_rotation.T @ (position - bind_position)
            else:
                offset_rotation, offset_position = rotation, position
            matrix = np.eye(4)
            matrix[:3, :3] = offset_rotation
            matrix[:3, 3] = offset_position
            rigids.append(dict(index=index, name=rigid.name, group=group_name(rigid.name), bone=bone_name,
                               part='head' if rig.owner.get(bone_name) == rig.cfg['head'] else 'body',
                               type=rigid.type, size=[value * scale for value in rigid.size],
                               offset=matrix.T.reshape(-1).tolist(), mode=rigid.mode,
                               collision_group=rigid.collision_group_number,
                               mass=rigid.mass, linear_damping=rigid.velocity_attenuation,
                               angular_damping=rigid.rotation_attenuation,
                               follow=index in rig.follow_rigids, preview_valid=preview_valid))
        return dict(character=character, parts=parts, rigids=rigids, warnings=warnings)


def model_data(character, source='current'):
    character_check(character)
    if source in CANDIDATE_RESULT_SOURCES:
        cfg=json.loads((PHYSX_PREVIEWS[source][0]/'configuration.json').read_text(encoding='utf-8'))
        if cfg['character']!=character:
            raise ValueError('候选模型所属角色不匹配')
        selected={part:Path(cfg['spec']['candidate_meshes'][key])
                  for part,key in [('head','head'),('body','torso')]}
        for filename in selected.values():
            if hashlib.sha256(filename.read_bytes()).hexdigest()!=cfg['input_hashes'][str(filename)]:
                raise ValueError('候选模型已变更，哈希不匹配')
    elif source in PREVIEW_PROVIDERS:
        provider = PREVIEW_PROVIDERS[source]
        candidate = provider.character_index(character)
        selected = {part: provider.verified_path(candidate['meshes'][part], candidate['mesh_sha256'][part])
                    for part in ('head', 'body')}
    elif source in {'current', 'base', 'trial', PHYSX_GPU_SOURCE, *PHYSX_PREVIEWS}:
        rig = rig_for(character)
        selected = dict(head=rig.head_path, body=rig.torso_path)
    else:
        raise ValueError(f'模型来源无效：{source}')
    mesh_paths = tuple((part, str(filename), filename.stat().st_mtime_ns, filename.stat().st_size,
                        'moe_head' if part == 'head' else 'moe_torso') for part, filename in selected.items())
    # The lock surrounds the cache lookup so simultaneous model/animation fetches
    # cannot build the same expensive mesh payload twice.
    with LOCK:
        return dict(_model_data(character, mesh_paths, source), source=source,
                    source_label=(PREVIEW_PROVIDERS[source].LABEL if source in PREVIEW_PROVIDERS else
                                  PHYSX_PREVIEWS[source][1] if source in PHYSX_PREVIEWS else source),
                    review=sample_review.review_info(character) if source == sample_review.SOURCE else {},
                    mesh_files={part: str(filename) for part, filename in selected.items()})


def animation_data(character, action, source, run=None):
    result = {}
    model = model_data(character, source)
    for part in ('head', 'body'):
        path = action_path(character, action, part, source, run)
        metadata, bones, samples = anim.load_anim(path)
        anim_names = [bone['name'] for bone in bones]
        mesh_names = [bone['name'] for bone in model['parts'][part]['bones']]
        if source == 'base':
            # base 快照可能烘焙于载体化之前：mesh 这边多出载体/锚点骨
            # （ground_joint、body_root、bn_l_prop 等），而且载体化重新编了
            # 骨骼下标，连共有骨的先后顺序也变了（父子关系本身没变）。
            # 前端渲染按骨名查找（不是按下标），缺的骨保留 mesh 自身的
            # rest pose，所以这里只要求动画骨名都在模型骨集合里，不比顺序。
            missing = sorted(set(anim_names) - set(mesh_names))
            if missing:
                raise ValueError(f'{source} 的 {part} 动画有模型里不存在的骨：{", ".join(missing[:5])}')
        elif anim_names != mesh_names:
            raise ValueError(f'{source} 的 {part} 动画骨序与所选模型不一致')
        frames, translations, quaternions = anim.unpack_samples(metadata, bones, samples)
        channels = []
        for bone in bones:
            name = bone['name']
            channels.append(dict(name=name, translation=encoded(translations.get(name, [bone['t']])),
                                 quaternion=encoded(quaternions.get(name, [bone['q']]))))
        result[part] = dict(frames=frames, fps=metadata.get('fps', [30])[0], channels=channels)
    if any(result['head'][key] != result['body'][key] for key in ('frames', 'fps')):
        raise ValueError('头身动画的帧数或帧率不一致')
    return result


def current_profile(character):
    path = WORK / 'presets' / f'{character}.json'
    if path.is_file():
        profile = profiles.load(path, character)
        return dict(profile, solver=dict(profiles.DEFAULT_SOLVER, **profile.get('solver', {})), rigids=profile.get('rigids', {}))
    from physics_release_recipe import source_profile
    profile = source_profile(character)
    if profile is not None:
        return dict(profile, solver=dict(profiles.DEFAULT_SOLVER, **profile.get('solver', {})),
                    rigids=profile.get('rigids', {}))
    return dict(version=1, character=character, solver=dict(profiles.DEFAULT_SOLVER), rigids={})


def save_profile(profile, path):
    if not isinstance(profile, dict):
        raise ValueError('参数方案必须是对象')
    character = character_check(profile.get('character'))
    with LOCK:
        profiles.validate(profile, character, {rigid.name for rigid in rig_for(character).model.rigids})
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)


def start_bake(data):
    profile, action = data['profile'], data['action']
    if not isinstance(profile, dict):
        raise ValueError('参数方案必须是对象')
    character = character_check(profile.get('character'))
    action_path(character, action, source='base')
    with LOCK:
        invalid = [rigid.name for rigid in rig_for(character).model.rigids if not np.isfinite(rigid.rotation).all()]
        if invalid:
            raise ValueError(f'源碰撞体旋转无效：{", ".join(invalid)}；需修复源数据和native读入后才能可靠试烘')
        if any(job['process'].poll() is None for job in JOBS.values()):
            raise ValueError('已有动作正在试烘，请完成或取消后再开始')
        run = secrets.token_hex(8)
        folder = WORK / 'runs' / run
        save_profile(profile, folder / 'profile.json')
        command = [sys.executable, '-u', str(Path(bake.__file__)), '--ck', character, '--anim', action,
                   '--out-dir', str(folder / 'anims'), '--io-dir', str(folder / 'io'),
                   '--profile', str(folder / 'profile.json'), '--stats-json', str(folder / 'stats.json')]
        request = dict(character=character, action=action, command=command, input_contract=2,
                       solver_sha256=hashlib.sha256(bake.EXE.read_bytes()).hexdigest(),
                       source_repairs=rig_for(character).source_repairs)
        (folder / 'request.json').write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding='utf-8')
        stream = (folder / 'bake.log').open('wb')
        process = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        stream.close()
        JOBS[run] = dict(process=process, started=time.time(), character=character, action=action)
        threading.Thread(target=finish_job, args=(run,), daemon=True).start()
    return dict(run=run)


def finish_job(run):
    job = JOBS[run]
    code = job['process'].wait()
    message = ''
    if code == 0:
        try:
            animation_data(job['character'], job['action'], 'trial', run)
        except Exception as error:
            code, message = 1, str(error)
    result = dict(run=run, status='cancelled' if job.get('cancelled') else 'complete' if code == 0 else 'failed',
                  seconds=round(time.time() - job['started'], 1), exit_code=code, error=message,
                  character=job['character'], action=job['action'], finished=time.time())
    folder = WORK / 'runs' / run
    temporary = folder / 'result.tmp'
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(folder / 'result.json')


def trial_history(character, action):
    character_check(character)
    history = []
    for path in (WORK / 'runs').glob('*/result.json'):
        result = json.loads(path.read_text(encoding='utf-8'))
        if result['character'] == character and result['action'] == action and result['status'] == 'complete':
            request = json.loads(path.with_name('request.json').read_text(encoding='utf-8'))
            if request.get('input_contract') == 2:
                history.append(dict(result, label=request.get('label', '')))
    return sorted(history, key=lambda result: result['finished'], reverse=True)


def evidence_history(character, action):
    """Return whitelisted, paired PhysX outputs as view-only history entries."""
    character_check(character)
    prefix = chars.BY_CK[character]['prefix']
    directory = PHYSX_GPU_EVIDENCE / character
    body = directory / f'{prefix}body_{action}.anim'
    head = directory / f'{prefix}head_{action}.anim'
    history = []
    for provider in PREVIEW_PROVIDERS.values():
        try:
            candidate = provider.character_index(character)
        except (ValueError, OSError):
            candidate = None
        if candidate and candidate['actions'].get(action, {}).get('status') == 'complete':
            history.append(dict(source=provider.SOURCE, label=provider.LABEL,
                                finished=candidate['finished'], seconds=None))
    for source, (_, label) in PHYSX_PREVIEWS.items():
        entry = full_gpu_preview(character, source).get(action, {})
        if entry.get('status') == 'complete':
            paths_ = [action_path(character, action, part, source) for part in ('body', 'head')]
            history.append(dict(source=source, label=label,
                                finished=max(path.stat().st_mtime for path in paths_), seconds=None))
    if body.is_file() and head.is_file():
        history.append(dict(source=PHYSX_GPU_SOURCE, label='PhysX GPU · TGS 8/1 · 只读证据预览',
                            finished=max(body.stat().st_mtime, head.stat().st_mtime), seconds=None))
    return history


@lru_cache(maxsize=1)
def action_labels_cn():
    """Pose names from the in-game wardrobe localization (moe_cosplay.0002.<pose>)."""
    path = ROOT / 'mod/moe_core/localization/simp_chinese/moe_moe_ui_l_simp_chinese.yml'
    try:
        text = path.read_text(encoding='utf-8-sig')
    except OSError:
        text = ''
    labels = dict(re.findall(r'^\s*moe_cosplay\.0002\.(\w+):\d+\s+"([^"]*)"', text, re.M))
    # Viewer-only supplement for vanilla actions the wardrobe localization does not list.
    extra = json.loads((Path(__file__).with_name('action_labels_cn.json')).read_text(encoding='utf-8'))
    return {**{k: v for k, v in extra.items() if not k.startswith('_')}, **labels}


def action_label_cn(action):
    labels = action_labels_cn()
    key = max((k for k in labels if action == k or action.startswith(k + '_')), key=len, default=None)
    if key is None:
        return ''
    rest = action[len(key):].strip('_')
    return labels[key] + (f'（{rest}）' if rest else '')


def action_catalog(character, source=None, requested=None):
    character_check(character)
    if source in PREVIEW_PROVIDERS:
        actions = PREVIEW_PROVIDERS[source].character_index(character)['actions']
        return [dict(id=action, label=action_label_cn(action), available=entry.get('status') == 'complete',
                     expression_status=entry.get('status'),
                     unavailable_reason='' if entry.get('status') == 'complete' else '旧动作没有配对头部，不能混用其他头部')
                for action, entry in sorted(actions.items())]
    if source in PHYSX_PREVIEWS:
        actions = full_gpu_preview(character, source)
        return [dict(id=action, label=action_label_cn(action), available=entry.get('status') == 'complete',
                     expression_status=entry.get('status'),
                     unavailable_reason='' if entry.get('status') == 'complete' else '该动作没有完整头身配对')
                for action, entry in sorted(actions.items())]
    if source not in (None, 'current', 'base'):
        raise ValueError(f'动作来源无效：{source}')
    prefix = chars.BY_CK[character]['prefix'] + 'body_'
    preview = full_gpu_preview(character)
    # base 来源扫自己的未解算输入目录；当前装机目录已改成载体合并动画
    # （anims_merged/，单文件含 head+body），不再有独立的 moe_body_*.anim。
    scan_dir = paths.base_anims_dir(character) if source == 'base' else paths.anims_dir(character)
    body_paths = sorted(scan_dir.glob(f'{prefix}*.anim')) if scan_dir.is_dir() else []

    # The complete loop table lives in the game's .asset files.  Parsing those
    # files is correct for baking, but it is far too expensive for a GET that
    # runs every time the user changes a character.  The 50-action focus list
    # is the same list exposed in the catalog and contains paired animations
    # for all 92 registered characters.  Use it until a bake has already
    # populated the loop table; afterward retain the broader historical list.
    if bake._LOOP is None:
        focus_file = ROOT / 'work/evidence/focus_20260910/actions50.json'
        try:
            focus = json.loads(focus_file.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            focus = []
        available_stems = {path.stem[len(prefix):] for path in body_paths}
        action_ids = [action for action in focus if action in available_stems]
        # Deep links may intentionally inspect an action outside the focus
        # list.  Include that one action without bringing back the expensive
        # full .asset loop-table scan.
        if requested in available_stems and requested not in action_ids:
            action_ids.insert(0, requested)
        if not action_ids:
            action_ids = [path.stem[len(prefix):] for path in body_paths]
    else:
        action_ids = [path.stem[len(prefix):] for path in body_paths
                      if bake._strip_char_prefix(path.stem) in bake._LOOP]
    # 没显式指定来源（None）时为旧行为保留：要求 current 和 base 都齐全，
    # 这样左右对比面板随便换来源都不会缺。显式指定某个来源时只检查它自己，
    # 否则 current 目录结构变了（见上）会连带把 base 的可用动作也判掉。
    check_sources = (source,) if source in ('current', 'base') else ('current', 'base')
    result = []
    for action in action_ids:
        available = True
        try:
            for check_source in check_sources:
                for part in ('body', 'head'):
                    action_path(character, action, part, check_source)
        except ValueError:
            available = False
        result.append(dict(id=action, label=action_label_cn(action), available=available, gpu_status=preview.get(action, {}).get('status')))
    return result


def preview_history(character, action):
    return evidence_history(character, action) + trial_history(character, action)


def trial_profile(character, action, run):
    if not RUN_ID.fullmatch(run) or not any(result['run'] == run for result in trial_history(character, action)):
        raise ValueError('找不到当前角色/动作的有效试烘')
    profile = profiles.load(WORK / 'runs' / run / 'profile.json', character)
    return dict(profile, solver=dict(profiles.DEFAULT_SOLVER, **profile.get('solver', {})), rigids=profile.get('rigids', {}))


def job_status(run):
    if run not in JOBS:
        raise ValueError('找不到试烘任务')
    job = JOBS[run]
    log = (WORK / 'runs' / run / 'bake.log').read_text(encoding='utf-8', errors='replace')
    result_path = WORK / 'runs' / run / 'result.json'
    result = json.loads(result_path.read_text(encoding='utf-8')) if result_path.is_file() else dict(
        run=run, status='running', seconds=round(time.time() - job['started'], 1),
        character=job['character'], action=job['action'])
    return dict(result, log=log[-7000:], folder=str(WORK / 'runs' / run))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, content, content_type='application/json; charset=utf-8', status=200):
        if not isinstance(content, bytes):
            content = json.dumps(content, ensure_ascii=False, allow_nan=False).encode('utf-8')
        compressed = len(content) > 2048 and 'gzip' in self.headers.get('Accept-Encoding', '')
        if compressed:
            content = gzip.compress(content, compresslevel=2)
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        if compressed:
            self.send_header('Content-Encoding', 'gzip')
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        try:
            request = urlsplit(self.path)
            route = unquote(request.path)
            query = {key: values[0] for key, values in parse_qs(request.query).items()}
            if route == '/api/catalog':
                action_file = ROOT / 'work/evidence/focus_20260910/actions50.json'
                focus = json.loads(action_file.read_text(encoding='utf-8')) if action_file.is_file() else []
                sources = [dict(id=provider.SOURCE, label=provider.LABEL,
                                characters=provider.available_characters())
                           for provider in PREVIEW_PROVIDERS.values()
                           if not ONLY_SOURCE or provider.SOURCE == ONLY_SOURCE]
                listed = CHARACTERS
                if ONLY_SOURCE:
                    listed = sources[0]['characters']
                    focus = []
                else:
                    sources.extend(dict(id=source, label=label, characters=['jia'])
                                   for source, (_path, label) in PHYSX_PREVIEWS.items())
                    base_characters = base_available_characters()
                    if base_characters:
                        sources.append(dict(id='base', label='未解算基础动画（装机前输入）',
                                            characters=base_characters))
                self.send(dict(characters=[dict(id=key, name=chars.BY_CK[key]['name_cn']) for key in listed], focus=focus,
                               read_only=READ_ONLY, only_source=ONLY_SOURCE, sources=sources))
            elif route == '/api/model':
                self.send(model_data(query['character'], query.get('source', 'current')))
            elif route == '/api/actions':
                self.send(action_catalog(query['character'], query.get('source'), query.get('action')))
            elif route == '/api/animation':
                self.send(animation_data(query['character'], query['action'], query['source'], query.get('run')))
            elif route == '/api/profile':
                self.send(current_profile(character_check(query['character'])))
            elif route == '/api/history':
                self.send(preview_history(query['character'], query['action']))
            elif route == '/api/trial-profile':
                self.send(trial_profile(query['character'], query['action'], query['run']))
            elif route == '/api/job':
                self.send(job_status(query['run']))
            elif route.startswith('/texture/') and route[9:] in TEXTURES:
                with Image.open(TEXTURES[route[9:]]) as image:
                    image = image.convert('RGBA')
                    image.thumbnail((2048, 2048))
                    output = io.BytesIO()
                    image.save(output, format='PNG')
                self.send(output.getvalue(), 'image/png')
            elif route == '/' and ONLY_SOURCE and query.get('source') != ONLY_SOURCE:
                # Review server pinned to one source: never fall back to the base/current pair.
                first = sorted(PREVIEW_PROVIDERS[ONLY_SOURCE].CHARACTERS)[0]
                target = '/?' + urlencode(dict(source=ONLY_SOURCE, character=query.get('character', first),
                                               **({'action': query['action']} if 'action' in query else {})))
                self.send_response(302)
                self.send_header('Location', target)
                self.end_headers()
            elif route == '/':
                content = (UI / 'index.html').read_text(encoding='utf-8').replace('__TOKEN__', TOKEN)
                self.send(content.encode('utf-8'), 'text/html; charset=utf-8')
            elif route == '/grid':
                # 十动作并排只读预览：只用 GET 接口，不需要写令牌
                self.send((UI / 'grid.html').read_bytes(), 'text/html; charset=utf-8')
            else:
                root = WORK / 'runtime/node_modules/three' if route.startswith('/three/') else UI
                relative = route.removeprefix('/three/') if route.startswith('/three/') else route.lstrip('/')
                target = (root / relative).resolve()
                if not target.is_relative_to(root.resolve()) or not target.is_file():
                    self.send(dict(error='找不到文件'), status=404)
                else:
                    content_type = 'text/javascript' if target.suffix == '.js' else mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
                    self.send(target.read_bytes(), content_type)
        except (ValueError, KeyError, FileNotFoundError) as error:
            self.send(dict(error=str(error)), status=400)
        except Exception as error:
            traceback.print_exc()
            self.send(dict(error=str(error)), status=500)

    def do_POST(self):
        if READ_ONLY:
            self.send(dict(error='当前为样本检查，烘焙和参数写入已暂停'), status=403)
            return
        try:
            if self.headers.get('X-Moe-Debug') != TOKEN:
                self.send(dict(error='请刷新本地调试页面'), status=403)
                return
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 1024 * 1024:
                raise ValueError('请求大小无效')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('请求必须是对象')
            if self.path == '/api/bake':
                self.send(start_bake(data))
            elif self.path == '/api/profile':
                profile = data['profile']
                if not isinstance(profile, dict):
                    raise ValueError('参数方案必须是对象')
                path = WORK / 'presets' / f'{character_check(profile.get("character"))}.json'
                save_profile(profile, path)
                self.send(dict(path=str(path)))
            elif self.path == '/api/cancel':
                job = JOBS[data['run']]
                process = job['process']
                if process.poll() is None:
                    job['cancelled'] = True
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.send(dict(cancelled=True))
            else:
                self.send(dict(error='未知操作'), status=404)
        except (ValueError, KeyError) as error:
            self.send(dict(error=str(error)), status=400)
        except Exception as error:
            traceback.print_exc()
            self.send(dict(error=str(error)), status=500)


def main():
    global READ_ONLY, ONLY_SOURCE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--character')
    parser.add_argument('--action')
    parser.add_argument('--source')
    parser.add_argument('--left-source')
    parser.add_argument('--read-only', action='store_true', help='Disable baking and parameter writes during review')
    parser.add_argument('--only-source', help='Review mode: list only this preview source and its characters')
    args = parser.parse_args()
    READ_ONLY = args.read_only
    if args.only_source:
        if args.only_source not in PREVIEW_PROVIDERS:
            raise SystemExit(f'未知来源：{args.only_source}')
        ONLY_SOURCE = args.only_source
    if not (WORK / 'runtime/node_modules/three/build/three.module.js').is_file():
        raise SystemExit('首次运行先安装预览依赖：npm.cmd install --prefix work/physics_debug/runtime --no-audit --no-fund --ignore-scripts three@0.180.0')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    url = f'http://127.0.0.1:{args.port}'
    print(f'物理调试器：{url}', flush=True)
    if args.open:
        query={key:value for key,value in {
            'character':args.character,'action':args.action,'source':args.source,
            'left_source':args.left_source}.items() if value}
        webbrowser.open(url+('/?'+urlencode(query) if query else '/'))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        for job in JOBS.values():
            if job['process'].poll() is None:
                subprocess.run(['taskkill', '/PID', str(job['process'].pid), '/T', '/F'], capture_output=True,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


if __name__ == '__main__':
    main()
