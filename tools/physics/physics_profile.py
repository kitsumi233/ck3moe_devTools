"""Validated, reproducible overrides for interactive native Bullet experiments."""
import json
import math
from pathlib import Path


RIGID_RANGES = {
    'mass_scale': (0.1, 10.0),
    'size_scale': (0.1, 3.0),
    'linear_damping': (0.0, 1.0),
    'angular_damping': (0.0, 1.0),
    'limit_scale': (0.0, 2.0),
    'spring_scale': (0.0, 3.0),
    # 接触摩擦。PMX 里 eld 的 390 个裙刚体全是 0.000，腿墙是 0.500,
    # Bullet 取两者乘积 → 有效摩擦 0，裙子贴着腿纯弹不带切向阻力。
    #
    # **已实测：看着免费，其实压的是「如实响应」那一半，别当抖动修法。**
    # 2026-09-14 eld/horse_archer_idle_var 三档 A/B（各 87 s，scene.txt 第 16 列
    # 已核对落值）。末列 |r| 来自 probe_excitation_arg.py，是「残余运动里
    # 受迫响应占多少」的出口判据（基线 0.859 = 裙子在如实跟随马背颠簸）:
    #     方案               kick%  p95    max     穿腿%  |r|    指纹
    #     BASE               18.4  14.78  25.23    2.7   0.859  间歇跳变
    #     裙 0.3             19.0   8.80  15.63    2.8   —      连续颤动 ✗
    #     裙 0.8             16.8  10.39  14.86    2.8   0.416  间歇跳变
    #     裙 0.8 + 墙 1.0    17.6   8.87  18.50    3.1   —      间歇跳变
    # max 从 25.23 压到 14.86 且穿透不变，看起来是唯一「免费」的杠杆 ——
    # 但 |r| 同时从 0.859 掉到 0.416：切向阻力让裙子黏在腿上不跟身体走,
    # 压掉的是响应本身而不是求解器噪声。0.3 那档更直接：指纹退化成
    # 「连续颤动」，低摩擦引入黏滑（stick-slip）。
    #
    # 与免碰叠加也不行（P 组：裙 0.8 + 行 2~15 免碰 g0）：kick 18.1、max 17.97、
    # 穿腿 12.6%、|r| 0.312 —— 两种机理互相抵消，付了免碰的全额代价却一分没降。
    'friction': (0.0, 1.0),
    # 接触弹性。原版裙与墙都已是 0.000（无余量可减），留着是为了能测而不是为了调。
    'restitution': (0.0, 1.0),
}
SOLVER_RANGES = {
    'drive_gain': (1.0, 12.0),
    'iters': (10, 120),
    'hz': (60, 480),
    'erp_limit': (0.0, 0.2),
    'erp2': (0.0, 0.5),
    'gravity': (-196.0, 0.0),
    'warmup': (1, 5),
    # 接触容差带（Bullet m_linearSlop，原默认 0.0）。上限 0.1 ≈ 裙格球半径的 22%，
    # 再大就是让裙子明显陷进墙里；0.06 实测与 0.03 打平但穿透代价翻倍。
    'linear_slop': (0.0, 0.1),
    # 裙子行 0 那 29 个横向关节的旋转余量（度）。0 = 保持原版焊死（默认）。
    #
    # **已实测：方向是反的，别再走这条路。** 2026-09-14 eld/horse_archer_idle_var
    # 单动作 A/B（两组各 66 s，日志确认改动 29 个、产物 md5 不同）：
    #     skirt 链   kick%  sust%   p95    max     指纹
    #     0°（对照）  18.4    45   14.78  25.23   间歇跳变
    #     3.3°       21.9    50   22.50  65.61   连续颤动
    # 每一项都恶化，max 涨 2.6 倍，指纹从「间歇跳变」变成「连续颤动」。
    #
    # 原因：行 0 焊死不是「闭环病灶」，而是**整片裙子的刚性基座**。放开后裙根自己
    # 会晃，386 根骨的悬挂链失去固定端，从「偶尔被限位弹一下」变成「持续晃」。
    # 我当初的依据是「环上骨 kick 31.21% vs 环外 18.18%」，那是**把相关当成了因果**
    # —— 裙根 kick 高是因为它承受整片裙子的载荷，不是因为它在环上。
    #
    # 连带否掉方案 3（删掉 353 个横向关节）：放松 29 个就恶化到这个程度，删光更糟。
    # 方案 2（横向改弹簧）同样可疑：弹簧比硬限位更软，等于更弱的基座。
    #
    # 参数保留是为了记录这条已证伪的路，默认 0.0 不影响任何产物。
    'skirt_row0_deg': (0.0, 10.0),
}
# skirt_row0_deg 默认 0.0 = 保持原版焊死，所以加这个参数本身不改变任何产物。
# warmup 3：2026-09-15 与入场过渡（--intro，见 moe_bullet_bake）一起定的档。
# 组合矩阵（绛雨 sitting，入场 60 固定）预热1 kick 31.2/峰 29.71，预热3 28.2/20.44。
# 这里的值会覆盖 CLI 默认（moe_bullet_bake:1129 setattr），所以两边必须一致，
# 否则网页试烘与全量烘焙跑的不是同一个档。
# linear_slop 0.03：2026-09-16 绛雨 A/B 定（用户「全部开吧，我看看效果」）。
# Bullet 的 m_linearSlop 默认 0.0 = 求解器把穿透精确压到零，静置接触上一直在争最后
# 那一点点 —— 坐姿裙抖的极限循环就长在这里。末端位移 p95：坐姿 -29%、idle -15%、
# dance_spin -25%；唯一回归 writing（裙子本就静垂）0.0131→0.0489。
# 与 CLI 默认必须一致（moe_bullet_bake:1129 setattr 会覆盖 CLI），否则网页试烘与
# 全量烘焙跑的不是同一个档。
# 新方案与 CLI 共用未放大的驱动基线。2026-09-17 jia/sitting 同源复测：
# 原工具默认 8、CLI 默认 1，scene 逐字相同而 frames 不同，裙摆末端逐帧位移
# p95 0.2013 -> 0.7906（模型空间）；两档均逐字节复现旧产物。先对齐输入，
# 才能比较接触参数。已有 profile 显式写的 drive_gain 仍覆盖默认值。
DEFAULT_SOLVER = dict(drive_gain=1.0, iters=60, hz=120, erp_limit=0.02,
                      erp2=0.1, gravity=-98.0, warmup=3, skirt_row0_deg=0.0,
                      linear_slop=0.03)


def validate(profile, character, rigid_names=None):
    if not isinstance(profile, dict) or type(profile.get('version')) is not int or profile['version'] != 1:
        raise ValueError('参数文件需要 version=1')
    if profile.get('character') != character:
        raise ValueError('参数文件与当前角色不匹配')
    if set(profile) - {'version', 'character', 'solver', 'rigids', 'hair'}:
        raise ValueError('参数文件含未知字段')
    solver = profile.get('solver', {})
    rigids = profile.get('rigids', {})
    if not isinstance(solver, dict) or not isinstance(rigids, dict):
        raise ValueError('solver / rigids 必须是对象')

    def number(value, limits, key):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f'{key} 必须是数值')
        if not math.isfinite(value) or not limits[0] <= value <= limits[1]:
            raise ValueError(f'{key} 超出范围 {limits}')

    for key, value in solver.items():
        if key not in SOLVER_RANGES:
            raise ValueError(f'未知解算参数 {key}')
        number(value, SOLVER_RANGES[key], key)
        if key in ('iters', 'warmup') and int(value) != value:
            raise ValueError(f'{key} 必须是整数')
    for name, values in rigids.items():
        if rigid_names is not None and name not in rigid_names:
            raise ValueError(f'不存在的刚体 {name}')
        if not isinstance(values, dict):
            raise ValueError(f'{name} 的参数必须是对象')
        for key, value in values.items():
            if key in RIGID_RANGES:
                number(value, RIGID_RANGES[key], key)
            elif key == 'mode':
                if value not in ('current', 'follow', 'no_collision', 'physics'):
                    raise ValueError(f'未知物理模式 {value}')
            elif key == 'blocked_groups':
                if not isinstance(value, list) or any(type(group) is not int or group not in range(16) for group in value):
                    raise ValueError('碰撞组只能是0到15的整数列表')
            elif key == 'shape_geometry':
                validate_shape_geometry(value)
                if 'size_scale' in values:
                    raise ValueError('shape_geometry 与 size_scale 不得同时使用')
            else:
                raise ValueError(f'未知刚体参数 {key}')
    if 'hair' in profile:
        hair = profile['hair']
        if not isinstance(hair, dict) or set(hair) != {'rigids', 'locked_rotation_half_width_deg'}:
            raise ValueError('hair 需要 rigids 与 locked_rotation_half_width_deg')
        names = hair['rigids']
        if (not isinstance(names, list) or len(names) < 2 or
                any(not isinstance(name, str) or not name for name in names) or
                len(set(names)) != len(names)):
            raise ValueError('hair.rigids 必须是不重复的刚体名称列表，至少包含两节')
        if any(not any(token in name.lower() for token in ('hair', '髪', '髮', '前发', '後发'))
               for name in names):
            raise ValueError('hair 只允许显式选择头发刚体')
        if rigid_names is not None and set(names) - rigid_names:
            raise ValueError('hair 包含不存在的刚体')
        widths = hair['locked_rotation_half_width_deg']
        if not isinstance(widths, list) or len(widths) != 3:
            raise ValueError('头发旋转半宽需要三个轴')
        for value in widths:
            number(value, (0.0, 15.0), 'locked_rotation_half_width_deg')
        if not any(widths):
            raise ValueError('头发旋转半宽不能全为零')
    return profile


def validate_shape_geometry(value):
    """Explicit PMX-space collider fit guarded against a different source.

    This changes the source rigid before native local joint/bone offsets are
    built. It does not move the PMX bone, the joint world anchors, or exported
    animation samples. A caller must supply the exact authored shape it fitted.
    """
    required = {'source_type', 'source_position', 'source_size', 'position', 'size'}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError('shape_geometry 必须包含原类型、原位置、原尺寸、新位置、新尺寸')
    shape = value['source_type']
    if type(shape) is not int or shape not in (0, 1, 2):
        raise ValueError('shape_geometry.source_type 只能是 PMX 球、盒、胶囊类型 0/1/2')
    for field in ('source_position', 'source_size', 'position', 'size'):
        vector = value[field]
        if (not isinstance(vector, list) or len(vector) != 3 or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector)):
            raise ValueError(f'shape_geometry.{field} 必须为三个有限数值')
        if field.endswith('size') and any(v < 0 for v in vector):
            raise ValueError('shape_geometry 尺寸不能为负')
    size = value['size']
    if size[0] <= 0 or (shape == 1 and any(v <= 0 for v in size)):
        raise ValueError('shape_geometry 球/胶囊半径、盒体三轴必须为正')
    # Capsule height may be zero (a sphere). PMX's unused components stay
    # explicit, so no downstream code invents or silently discards dimensions.
    if shape == 0 and size[1:] != value['source_size'][1:]:
        raise ValueError('球体未使用的尺寸分量必须保留')
    if shape == 2 and size[2] != value['source_size'][2]:
        raise ValueError('胶囊未使用的尺寸分量必须保留')


def load(path, character):
    return validate(json.loads(Path(path).read_text(encoding='utf-8')), character)


SKIRT_PATTERN = None      # 惰性编译，见 relax_skirt_row0


def relax_skirt_row0(model, degrees):
    """给裙子行 0 的横向关节一点旋转余量，打开闭环最紧的那一圈。

    只认 `スカート_<行>_<列>` 这个 PMX 命名，且只改**同行相邻列**的关节
    （横向）—— 纵向关节（同列相邻行）不动，那是裙子的悬挂链，放松它会让裙子塌。

    判定相邻列用 `abs(列差) == 1`，不含首尾相接的那一对（列 0 与列 28）：
    模型里本来就没有那条关节，裙子是开口的圆筒而非闭合圈；真正的闭环来自
    「纵向 + 横向」组成的网格，不是单独一圈。

    返回改动的关节数，供调用方核对（0 意味着命名不匹配，是静默失效的信号）。
    """
    import re
    global SKIRT_PATTERN
    if SKIRT_PATTERN is None:
        SKIRT_PATTERN = re.compile(r'^スカート_(\d+)_(\d+)$')
    if degrees <= 0.0:
        return 0

    def cell(index):
        if index is None or index < 0 or index >= len(model.rigids):
            return None
        found = SKIRT_PATTERN.match(model.rigids[index].name)
        return (int(found.group(1)), int(found.group(2))) if found else None

    limit = math.radians(degrees)
    changed = 0
    for joint in model.joints:
        source, destination = cell(joint.src_rigid), cell(joint.dest_rigid)
        if not source or not destination:
            continue
        if source[0] != 0 or destination[0] != 0 or abs(source[1] - destination[1]) != 1:
            continue
        # 只放开原本焊死的那些；已有余量的不动，免得把别处调好的值改掉
        span = max(abs(high - low) for low, high
                   in zip(joint.minimum_rotation, joint.maximum_rotation))
        if span > 1e-6:
            continue
        # 与行 1 同构：放开 X 与 Z（摆动），Y（沿骨轴扭转）保持锁死
        joint.minimum_rotation = [-limit, 0.0, -limit]
        joint.maximum_rotation = [limit, 0.0, limit]
        changed += 1
    return changed


def apply_rigids(model, profile, follow_rigids):
    overrides = profile.get('rigids', {}) if profile else {}
    if profile:
        validate(profile, profile['character'], {rigid.name for rigid in model.rigids})
    # Check every expected source before mutating any rigid or joint. A profile
    # fitted to an earlier PMX/capsule repair must fail visibly on source change.
    for rigid in model.rigids:
        geometry = overrides.get(rigid.name, {}).get('shape_geometry')
        if geometry is not None:
            if (rigid.type != geometry['source_type'] or
                    list(rigid.location) != geometry['source_position'] or
                    list(rigid.size) != geometry['source_size']):
                raise ValueError(f'{rigid.name}: shape_geometry 原始几何不匹配，需重新拟合源形状')
    for index, rigid in enumerate(model.rigids):
        values = overrides.get(rigid.name, {})
        if not values:
            continue
        mode = values.get('mode', 'current')
        if mode == 'follow':
            follow_rigids.add(index)
        elif mode in ('physics', 'no_collision'):
            follow_rigids.discard(index)
        if mode == 'no_collision':
            rigid.collision_group_mask = 0
        if 'mass_scale' in values:
            rigid.mass *= values['mass_scale']
        if 'size_scale' in values:
            rigid.size = [size * values['size_scale'] for size in rigid.size]
        if 'shape_geometry' in values:
            rigid.location = list(values['shape_geometry']['position'])
            rigid.size = list(values['shape_geometry']['size'])
        if 'linear_damping' in values:
            rigid.velocity_attenuation = values['linear_damping']
        if 'angular_damping' in values:
            rigid.rotation_attenuation = values['angular_damping']
        if 'friction' in values:
            rigid.friction = values['friction']
        if 'restitution' in values:
            rigid.bounce = values['restitution']
        for group in values.get('blocked_groups', []):
            rigid.collision_group_mask &= ~(1 << group) & 0xffff
    for joint in model.joints:
        child = joint.dest_rigid
        if child is None or child < 0 or model.rigids[child].mode == 0:
            child = joint.src_rigid
        if child is None or child < 0:
            continue
        values = overrides.get(model.rigids[child].name, {})
        if 'limit_scale' in values:
            factor = values['limit_scale']
            minimum, maximum = joint.minimum_rotation, joint.maximum_rotation
            center = [(low + high) / 2 for low, high in zip(minimum, maximum)]
            joint.minimum_rotation = [middle + (value - middle) * factor for value, middle in zip(minimum, center)]
            joint.maximum_rotation = [middle + (value - middle) * factor for value, middle in zip(maximum, center)]
        if 'spring_scale' in values:
            spring = values['spring_scale']
            joint.spring_constant = [value * spring for value in joint.spring_constant]
            joint.spring_rotation_constant = [value * spring for value in joint.spring_rotation_constant]
    if profile and 'hair' in profile:
        return relax_hair_internal_locks(model, profile['hair'], follow_rigids)


def relax_hair_internal_locks(model, settings, follow_rigids):
    """Give explicitly selected internal hair joints finite angular freedom.

    Both ends must be dynamic hair rigid bodies on the same ancestral bone
    chain. Head/root attachments, sibling links, translation locks, existing
    angular freedom, collision masks, springs, mass and damping stay intact.
    Nonzero authored lock angles remain the center of the new interval.
    This changes solver inputs only; it never edits sampled animation output.
    """
    selected = set(settings['rigids'])
    widths = [math.radians(value) for value in settings['locked_rotation_half_width_deg']]
    eligible = {index for index, rigid in enumerate(model.rigids)
                if rigid.name in selected and rigid.mode != 0 and index not in follow_rigids}

    def is_ancestor(parent, child):
        if parent is None or child is None or parent < 0 or child < 0 or parent == child:
            return False
        seen = set()
        cursor = model.bones[child].parent
        while cursor is not None and cursor >= 0 and cursor not in seen:
            if cursor == parent:
                return True
            seen.add(cursor)
            cursor = model.bones[cursor].parent
        return False

    changes = []
    for index, joint in enumerate(model.joints):
        a, b = joint.src_rigid, joint.dest_rigid
        if a not in eligible or b not in eligible:
            continue
        ba, bb = model.rigids[a].bone, model.rigids[b].bone
        if not (is_ancestor(ba, bb) or is_ancestor(bb, ba)):
            continue
        lo, hi = list(joint.minimum_rotation), list(joint.maximum_rotation)
        if lo != hi:
            continue
        if not all(math.isfinite(value) for value in lo):
            raise ValueError('头发源关节含无效锁角')
        minimum = [center-width for center, width in zip(lo, widths)]
        maximum = [center+width for center, width in zip(lo, widths)]
        if any(low <= -math.pi or high >= math.pi for low, high in zip(minimum, maximum)):
            raise ValueError('头发有限角域超出 PhysX 支持范围')
        changes.append((joint, dict(index=index, name=joint.name,
                                   source=joint.src_rigid, destination=joint.dest_rigid,
                                   original_rotation=lo, minimum_rotation=minimum,
                                   maximum_rotation=maximum)))
    if not changes:
        raise ValueError('hair 未匹配到可放开的内部全锁关节；拒绝无效果试验')
    for joint, change in changes:
        joint.minimum_rotation = change['minimum_rotation']
        joint.maximum_rotation = change['maximum_rotation']
    return dict(hair_internal_locks=[change for _, change in changes])
