"""Candidate-only, unit-explicit secondary-motion response design.

No release defaults change. Frequencies and damping ratios describe joint
drives; body drag is expressed as a velocity half-life. Original collision,
mass, translation bounds, anchors, and nonzero/limited angular bounds are
preserved. Zero-angle dynamic chains are deliberately made angularly free so
the response spring can move them and pull them back to the authored pose.
"""
import math
import re
from collections import Counter

VERSION = 'response-v2-all-dynamic-springs'
# angular Hz at the root/tip, joint ratio, linear/angular velocity half-life s
FAMILIES = {
    'hair': (2.5, 1.6, .85, .50, .70),
    'skirt': (1.6, 1.0, 1.00, .30, .45),
    'cloth': (1.5, .9, .95, .35, .50),
    'ribbon': (1.5, .8, .80, .50, .75),
    'tail': (2.0, 1.2, .85, .50, .65),
    'sleeve': (1.8, 1.1, 1.00, .30, .45),
    'ear': (2.5, 1.8, .90, .40, .50),
    'soft_body': (3.0, 2.2, 1.00, .25, .35),
    'accessory': (2.0, 1.4, 1.00, .30, .45),
}
TOKENS = (
    ('hair', ('hair', '髪', '髮', '前发', '後发', '后发', '头发', 'plait', '辫', 'bang', 'mawei')),
    ('ribbon', ('ribbon', 'ribbb', 'piaodai', '飘带', '裙带', '裙绳', 'streamer', 'strap', 'sash', 'belt', 'tie', 'tape', 'yidai', '带子', 'daizi', 'bow', 'lace', 'tassel', 'fringe', '流苏', '蝴蝶结')),
    ('skirt', ('skirt', 'スカート', '裙', 'qunbai')),
    ('sleeve', ('sleeve', '袖')),
    ('tail', ('tail', '尻尾', '尾')),
    ('ear', ('ear', '耳')),
    ('soft_body', ('breast', 'bust', 'chest', '胸', 'boby', 'bust', 'butt')),
    ('cloth', ('cloth', 'clotch', 'cape', 'coat', 'jacket', 'マント', '外套', '衣', 'pifeng', 'jiake', 'yifu', 'hb')),
)


def family(name, extra_tokens=()):
    """extra_tokens: ((group, (token, ...)), ...) checked after TOKENS, before the fallback.
    Per-character opt-in (鸣潮 2026-10-09: Dress/披风/领子/穗穗/侧发 fell into 'accessory');
    callers that pass nothing get exactly the old classification."""
    low = name.lower()
    for group, tokens in TOKENS:
        if any(t in low for t in tokens):
            return group
    for group, tokens in extra_tokens:
        if any(t in low for t in tokens):
            return group
    if re.search(r'(^|[ _])(?:[lr]?qb|qj|qz)[\d_]', low):
        return 'skirt'
    return 'accessory'


def fraction_from_half_life(seconds):
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('Velocity half-life must be finite and positive')
    return -math.expm1(-math.log(2) / seconds)


def apply(rig, family_overrides=None, *, twist_z_limit_deg=None, bone_twist_lock=False, bone_twist_families=None,
          keep_light_welds=False, light_weld_ratio=.2, keep_zero_lock_families=(), keep_zero_lock_names=(),
          family_tokens=None, zero_lock_limit_deg=None, widen_limits_deg=None, mass_floor=None,
          linear_limit_scale=None, joint_frame_limits=None):
    """Apply reversible candidate parameters and return an exhaustive contract."""
    model = rig.model
    if bone_twist_lock and twist_z_limit_deg is not None:
        raise ValueError('Bone twist and legacy cross-link locks cannot be combined')
    if twist_z_limit_deg is not None and (not math.isfinite(twist_z_limit_deg) or not 0 <= twist_z_limit_deg < 180):
        raise ValueError('twist_z_limit_deg must be in [0, 180); zero is a hard lock')
    parameters = dict(FAMILIES)
    for group, values in (family_overrides or {}).items():
        if group not in parameters or len(values) != 5 or any(not math.isfinite(x) or x <= 0 for x in values):
            raise ValueError('Invalid response family override: '+group)
        parameters[group] = tuple(values)
    names = [(str(r.name) + ' ' + (model.bones[r.bone].name if r.bone is not None and r.bone >= 0 else ''))
             for r in model.rigids]
    extra = tuple((g, tuple(t.lower() for t in toks)) for g, toks in (family_tokens or {}).items())
    for g, _ in extra:
        if g not in parameters:
            raise ValueError('Unknown family in family_tokens: ' + g)
    families = [family(n, extra) for n in names]
    bones = {r.bone: i for i, r in enumerate(model.rigids) if r.bone is not None and r.bone >= 0}
    depth = {}
    degree = Counter(endpoint for j in model.joints for endpoint in (j.src_rigid, j.dest_rigid))

    def level(i, active=None):
        if i in depth:
            return depth[i]
        active = set() if active is None else active
        if i in active:
            raise ValueError('Cyclic PMX parent hierarchy')
        active.add(i)
        r = model.rigids[i]
        parent = model.bones[r.bone].parent if r.bone is not None and r.bone >= 0 else None
        p = bones.get(parent)
        depth[i] = 1 + level(p, active) if (p is not None and model.rigids[p].mode and families[p] == families[i]) else 0
        active.remove(i)
        return depth[i]

    body_rows = []
    raised_mass = []
    for t, m in (mass_floor or {}).items():
        if not math.isfinite(m) or m <= 0:
            raise ValueError('mass_floor must be positive: ' + t)
    for i, r in enumerate(model.rigids):
        if not r.mode:
            continue
        # 按刚体名抬质量下限（2026-10-10 菲比：披风穗穗 1e-4、前带子 1e-5 挂在 0.01~1 的父体上，
        # 质量比上千，关节限位 ±30° 实测被冲到 158°）。只对显式列出的名字生效，默认 None 不变。
        floor = next((m for t, m in (mass_floor or {}).items() if t in str(r.name)), None)
        if floor is not None and r.mass < floor:
            raised_mass.append(dict(index=i, name=str(r.name), mass_before=float(r.mass), mass_after=floor))
            r.mass = floor
        group = families[i]
        params = parameters[group]
        old = [float(r.velocity_attenuation), float(r.rotation_attenuation)]
        r.velocity_attenuation = fraction_from_half_life(params[3])
        r.rotation_attenuation = fraction_from_half_life(params[4])
        body_rows.append(dict(index=i, name=r.name, family=group, depth=level(i), mass=float(r.mass),
                             old_damping=old, damping=[r.velocity_attenuation, r.rotation_attenuation],
                             velocity_half_life_seconds=list(params[3:5])))
    joint_rows = []
    unlocked = []
    unlocked_zero_lock = []
    limited_zero_lock = []
    kept_welds = []
    twist_z_rows = []
    widened = []
    linear_scaled = []
    reframed = []
    for t, f in (joint_frame_limits or {}).items():
        if (len(f) != 3 or any(len(x) != 3 for x in f) or not all(math.isfinite(v) for x in f for v in x)
                or any(not -180 < f[1][k] <= f[2][k] < 180 for k in range(3))):
            raise ValueError('joint_frame_limits must be [[euler rad x3], [lo deg x3], [hi deg x3]]: ' + t)
    for t, v in (linear_limit_scale or {}).items():
        if not math.isfinite(v) or not 0 <= v <= 1:
            raise ValueError('linear_limit_scale must be in [0, 1]: ' + t)
    for t, d in (widen_limits_deg or {}).items():
        if len(d) != 3 or any(not math.isfinite(x) or not 0 <= x < 180 for x in d):
            raise ValueError('widen_limits_deg must be [x, y, z] degrees in [0, 180): ' + t)
    for i, j in enumerate(model.joints):
        a, b = j.src_rigid, j.dest_rigid
        if a is None or b is None or min(a, b) < 0 or max(a, b) >= len(model.rigids):
            continue
        if not model.rigids[a].mode and not model.rigids[b].mode:
            continue
        child = b if model.rigids[b].mode else a
        group = families[child]
        before = [list(j.minimum_rotation), list(j.maximum_rotation)]
        # A zero-width angular lock on a dynamic physical chain has no visible
        # spring: PhysX keeps every axis hard-locked and the response drive is
        # never allowed to move.  Give such joints free angular axes and let
        # the acceleration drive pull them back to the authored relative pose.
        # This removes an angle restriction; it does not add one.  Preserve
        # joints with an authored nonzero fixed angle (their target offset is
        # serialized by the scene's shift transform) and joints with a linear
        # range, where the source intentionally describes a sliding/welded
        # constraint rather than an angular chain.
        zero_lock = before == [[0., 0., 0.], [0., 0., 0.]]
        linear_locked = all(j.minimum_location[k] == j.maximum_location[k] for k in range(3))
        # A light prop welded onto a much heavier dynamic body (bag on a skirt
        # hem, bow on a hair root) is authored as rigid cargo. Freeing it turns
        # the host's own jitter into a whip; keep the authored weld.
        parent = a if child == b else b
        # Hair roots keep the authored hair unlock (their huge PMX masses are
        # nominal, not cargo).
        welded_cargo = (keep_light_welds and zero_lock and linear_locked and model.rigids[parent].mode
                        and families[child] != 'hair'
                        and model.rigids[parent].mass > 0
                        and model.rigids[child].mass < light_weld_ratio * model.rigids[parent].mass)
        if welded_cargo:
            kept_welds.append(dict(index=i, name=j.name, child=model.rigids[child].name,
                                   parent=model.rigids[parent].name,
                                   mass_ratio=model.rigids[child].mass / model.rigids[parent].mass))
        elif (zero_lock and linear_locked and group in (zero_lock_limit_deg or {})
              and (model.rigids[a].mode or model.rigids[b].mode)):
            # 鸣潮裙子（2026-10-09）：纵向关节全是作者零锁，解成角自由后整条竖链只剩 0.55~1 Hz 的软弹簧，
            # 跳舞时相邻行来回翻（父系摆角逐帧中位 13°）；表现正常的散华/坎特蕾拉纵向是 ±2~10° 限位。
            # 这里改成对称小限位而不是完全放开。只对显式列出的族生效，默认 None = 旧行为不变。
            # 标量 = 三轴对称（旧行为）；三元列表 = 按 PMX 关节轴 [x, y, z] 分别限位（2026-10-10，
            # 鸣潮裙竖向关节 X=切向、Y=链向、Z=径向：只限链向扭转、放宽两个摆角，免得坐下撑成圆锥）。
            # 每轴可再写成 [下限, 上限]（非对称，如只放向内折、向外仍 8°）。
            deg = zero_lock_limit_deg[group]
            deg = [deg] * 3 if isinstance(deg, (int, float)) else list(deg)
            pairs = [(-d, d) if isinstance(d, (int, float)) else tuple(d) for d in deg]
            if len(pairs) != 3 or any(len(p) != 2 or not all(math.isfinite(x) for x in p)
                                      or not -180 < p[0] < 0 < p[1] < 180 for p in pairs):
                raise ValueError('zero_lock_limit_deg must be a degree, or 3 axes of degree / [lo, hi] with lo < 0 < hi: ' + group)
            j.minimum_rotation = [math.radians(p[0]) for p in pairs]
            j.maximum_rotation = [math.radians(p[1]) for p in pairs]
            limited_zero_lock.append(i)
        elif (zero_lock and linear_locked and (model.rigids[a].mode or model.rigids[b].mode)
              # Families listed here keep the authored zero lock: a breast authored
              # as rigid becomes a gravity pendulum once freed (bone0348_Chest_L
              # settled 78 deg from rest; a 20 Hz spring still left 43 deg).
              and group not in keep_zero_lock_families
              # Named bodies (any family) keep it too, e.g. a hair-named top tuft
              # that the author welded to the head.
              and not any(t in str(model.rigids[child].name) for t in keep_zero_lock_names)):
            j.minimum_rotation = [1., 1., 1.]
            j.maximum_rotation = [-1., -1., -1.]
            unlocked_zero_lock.append(i)
            if families[a] == families[b] == 'hair' and model.rigids[a].mode and model.rigids[b].mode:
                unlocked.append(i)
        # 按关节名放宽作者的非零限位（2026-10-10 布兰特：左披肩横向连接锁死 X/Z、左带子根只放 Z），
        # 每轴取 [min(原下限,-deg), max(原上限,deg)]，只放宽不收紧；零锁关节归上面的分支管。默认 None 不生效。
        widen = next((d for t, d in (widen_limits_deg or {}).items() if t in str(j.name)), None)
        if widen is not None and not zero_lock and linear_locked:
            j.minimum_rotation = [min(j.minimum_rotation[k], -math.radians(widen[k])) for k in range(3)]
            j.maximum_rotation = [max(j.maximum_rotation[k], math.radians(widen[k])) for k in range(3)]
            widened.append(i)
        # 按关节名缩小作者的线性（滑动）范围（2026-10-12 千咲百褶裙：横向关节作者给 ±0.65、零弹簧，
        # 相邻列静止时就被拉开到 rest 的 1.96 倍，褶子之间开口）。系数 0 = 焊死，1 = 不变；默认 None 不生效。
        # 放在零锁分类之后，不改变上面任何分支的判定。
        # 按关节名（全名精确匹配）换关节坐标系并重设角限位（2026-10-13 鉴心后飘带：作者关节系是世界轴，
        # 三轴 ±10° 放任绕飘带法线的面内弯曲，扁带子横着拐，看着像扭）。值 = [欧拉 YXZ 弧度, 下限三轴度, 上限三轴度]，
        # 欧拉由飘带网格的 拍动/链向/法线 三轴离线求出。默认 None 不生效。
        frame = (joint_frame_limits or {}).get(str(j.name))
        if frame is not None and not zero_lock and linear_locked:
            j.rotation = [float(x) for x in frame[0]]
            j.minimum_rotation = [math.radians(x) for x in frame[1]]
            j.maximum_rotation = [math.radians(x) for x in frame[2]]
            reframed.append(i)
        lin_scale = next((s for t, s in (linear_limit_scale or {}).items() if t in str(j.name)), None)
        if lin_scale is not None:
            j.minimum_location = [x * lin_scale for x in j.minimum_location]
            j.maximum_location = [x * lin_scale for x in j.maximum_location]
            linear_scaled.append(i)
        active_axes = [k for k in range(3) if j.minimum_rotation[k] != j.maximum_rotation[k]]
        lin_axes =[k for k in range(3) if j.minimum_location[k] != j.maximum_location[k] and j.spring_constant[k] > 0]
        if not active_axes and not lin_axes:
            continue
        # This is a separate swing-twist degree of freedom about the authored
        # joint Z, NOT an Euler Z/swing2 clamp. Keep original bounds untouched.
        # Already locked Z axes, self-joints and follow-bone exclusions are
        # never turned into an additional anti-twist constraint.
        if twist_z_limit_deg is not None and 2 in active_axes and a != b:
            twist_z_rows.append(dict(index=i, name=j.name, family=group,
                limit_rad=math.radians(twist_z_limit_deg), limit_deg=twist_z_limit_deg,
                mode='locked' if twist_z_limit_deg == 0 else 'limited'))
        root_hz, tip_hz, ratio, _, _ = parameters[group]
        hz = tip_hz + (root_hz - tip_hz) * math.exp(-level(child) / 4)
        # Parallel cross-links must not sum a fresh whole spring per edge.
        hz /= math.sqrt(max(1., degree[child] / 2))
        joint_rows.append(dict(index=i, name=j.name, family=group,
            angular_hz=hz if active_axes else 0., damping_ratio=ratio,
            linear_hz=3. if lin_axes else 0., linear_damping_ratio=1.,
            active_axes=active_axes, slerp=(len(active_axes) == 3),
            original_angles=before, candidate_angles=[list(j.minimum_rotation),list(j.maximum_rotation)]))
    bone_locks=[]
    if bone_twist_lock:
        import bone_twist
        bone_locks=bone_twist.contract(rig)
        if bone_twist_families is not None:
            # Solver-side lock only for the listed families (e.g. long tails);
            # the rest are de-twisted at writeback. Parent-first order is kept.
            keep=set(bone_twist_families)
            bone_locks=[r for r in bone_locks if families[r['child']] in keep]
    return dict(version=VERSION, accepted=False, parameters=parameters, bodies=body_rows, joints=joint_rows,
                bone_twist_lock=bone_twist_lock,bone_twist_locks=bone_locks,bone_twist_families=bone_twist_families,
                unlocked_internal_hair=unlocked, unlocked_zero_lock_joints=unlocked_zero_lock,
                limited_zero_lock_joints=limited_zero_lock, zero_lock_limit_deg=zero_lock_limit_deg,
                widened_limit_joints=widened, widen_limits_deg=widen_limits_deg,
                **(dict(linear_scaled_joints=linear_scaled, linear_limit_scale=linear_limit_scale) if linear_limit_scale else {}),
                **(dict(reframed_joints=reframed, joint_frame_limits=joint_frame_limits) if joint_frame_limits else {}),
                raised_mass_bodies=raised_mass, mass_floor=mass_floor,
                kept_light_welds=kept_welds, light_weld_ratio=light_weld_ratio if keep_light_welds else None, keep_zero_lock_families=list(keep_zero_lock_families), keep_zero_lock_names=list(keep_zero_lock_names),
                new_angular_limits=len(twist_z_rows), authorized_twist_z_limits=twist_z_rows,
                twist_z_limit_deg=twist_z_limit_deg, new_swing_limits=0, new_translation_limits=0,
                twist_z_locked_joints=len(twist_z_rows) if twist_z_limit_deg == 0 else 0,
                family_counts=dict(Counter(r['family'] for r in body_rows)),
                unknown_accessory_names=sorted({r['name'] for r in body_rows if r['family']=='accessory'}))


TORSO_WALL_NAMES = ('上半身2', '上半身2_1', '上半身2_2', '上半身3', '胸')


def hair_torso_walls(scene, model, contract):
    """Let hair collide with upper-back/chest walls the PMX mask leaves open.

    Many sources only let hair hit the small waist spheres, so long hair sinks
    into the back. Only static torso walls gain the hair group bits; dynamic
    bodies and every other pair keep the authored mask. Returns (scene, rows).
    """
    lines = scene.splitlines()
    rigid_at = [k for k, s in enumerate(lines) if s.startswith('rigid ')]
    if len(rigid_at) != len(model.rigids):
        raise ValueError('Scene rigid count differs from PMX')
    fam = {b['index']: b['family'] for b in contract['bodies']}
    hair_groups = set()
    for i, f in fam.items():
        row = lines[rigid_at[i]].split()
        if f == 'hair' and int(row[19]):
            hair_groups.add(int(row[18]))
    changed = []
    for i, r in enumerate(model.rigids):
        if r.mode != 0 or str(r.name) not in TORSO_WALL_NAMES:
            continue
        row = lines[rigid_at[i]].split()
        grp, mask = int(row[18]), int(row[19])
        new = mask
        for g in hair_groups:
            new |= 1 << g
        if new != mask:
            # The pair only collides if each side also admits the other group.
            row[19] = str(new)
            lines[rigid_at[i]] = ' '.join(row)
            changed.append(dict(index=i, name=str(r.name), group=grp, mask_before=mask, mask_after=new))
    # Hair whose own mask excludes the wall group stays excluded (authored choice).
    admitted = 0
    for i, f in fam.items():
        mask = int(lines[rigid_at[i]].split()[19])
        if f == 'hair' and mask and changed and all((mask >> c['group']) & 1 for c in changed):
            admitted += 1
    return '\n'.join(lines) + '\n', dict(hair_groups=sorted(hair_groups), walls=changed,
                                         hair_bodies_now_hitting_walls=admitted)


def exclude_pairs(scene, model, contract, rules):
    """Drop collision between named rigids and whole families.

    rules: list of {rigids: [name substrings], families: [family names]}.
    A pair collides only if each side admits the other's group, so clearing the
    listed families' group bits on the named rigids is enough; every other
    body keeps its authored mask. Returns (scene, rows).
    """
    lines = scene.splitlines()
    rigid_at = [k for k, s in enumerate(lines) if s.startswith('rigid ')]
    if len(rigid_at) != len(model.rigids):
        raise ValueError('Scene rigid count differs from PMX')
    fam = {b['index']: b['family'] for b in contract['bodies']}
    changed = []
    for rule in rules:
        groups = {int(lines[rigid_at[i]].split()[18]) for i, f in fam.items() if f in rule.get('families', ())}
        # 'groups': explicit PMX collision groups (static walls carry no family; 鸣潮 legs/torso are all g0)
        groups |= {int(g) for g in rule.get('groups', ())}
        for i, r in enumerate(model.rigids):
            if not (any(t in str(r.name) for t in rule.get('rigids', ()))
                    or str(r.name) in rule.get('exact', ())):
                continue
            row = lines[rigid_at[i]].split()
            own = int(row[18])
            # never clear the rigid's own group: that would also cut its own chain/family
            clear = sorted(g for g in groups if g != own)
            mask = int(row[19]); new = mask
            for g in clear:
                new &= ~(1 << g)
            if new != mask:
                row[19] = str(new); lines[rigid_at[i]] = ' '.join(row)
                changed.append(dict(index=i, name=str(r.name), group=own, mask_before=mask, mask_after=new,
                                    cleared_groups=clear, families=list(rule.get('families', ()))))
    return '\n'.join(lines) + '\n', changed


def admit_pairs(scene, model, contract, rules):
    """Add collision between named static walls and whole families (inverse of exclude_pairs).

    rules: list of {rigids: [wall name substrings], families: [family names]}.
    Both sides must admit each other: the walls gain the family groups and the
    family bodies gain the wall groups; nothing else changes. Returns (scene, rows).
    """
    lines = scene.splitlines()
    rigid_at = [k for k, s in enumerate(lines) if s.startswith('rigid ')]
    if len(rigid_at) != len(model.rigids):
        raise ValueError('Scene rigid count differs from PMX')
    fam = {b['index']: b['family'] for b in contract['bodies']}
    changed = []

    def add(i, groups):
        row = lines[rigid_at[i]].split()
        mask = int(row[19]); new = mask
        for g in groups:
            new |= 1 << g
        if new != mask:
            row[19] = str(new); lines[rigid_at[i]] = ' '.join(row)
            changed.append(dict(index=i, name=str(model.rigids[i].name), mask_before=mask, mask_after=new))

    for rule in rules:
        # optional 'bodies': name substrings narrowing the family bodies (e.g. front clothes only)
        bodies = [i for i, f in fam.items() if f in rule['families']
                  and (not rule.get('bodies') or any(t in str(model.rigids[i].name) for t in rule['bodies']))]
        walls = [i for i, r in enumerate(model.rigids)
                 if r.mode == 0 and any(t == str(r.name) for t in rule['rigids'])]
        body_groups = {int(lines[rigid_at[i]].split()[18]) for i in bodies}
        wall_groups = {int(lines[rigid_at[i]].split()[18]) for i in walls}
        for i in walls:
            add(i, body_groups)
        for i in bodies:
            add(i, wall_groups)
    return '\n'.join(lines) + '\n', changed


def append_scene_contract(scene, contract):
    lines = scene.splitlines()
    rigid_lines = [s.split() for s in lines if s.startswith('rigid ')]
    joint_lines = [s.split() for s in lines if s.startswith('joint ')]
    for r in contract['bodies']:
        row = rigid_lines[r['index']]
        for actual, expected in zip(map(float, row[13:15]), r['damping']):
            if not math.isclose(actual, expected, rel_tol=2e-6, abs_tol=2e-7):
                raise ValueError('Body damping overwritten during serialization: '+r['name'])
    for j in contract['joints']:
        row=joint_lines[j['index']]
        for actual, expected in zip(map(float,row[15:21]),sum(j['candidate_angles'],[])):
            if not math.isclose(actual,expected,rel_tol=2e-6,abs_tol=2e-7):
                raise ValueError('Angular bounds overwritten during serialization: '+j['name'])
        lines.append('response %d %.9g %.9g %.9g %.9g' %
                     (j['index'],j['angular_hz'],j['damping_ratio'],j['linear_hz'],j['linear_damping_ratio']))
    for j in contract.get('authorized_twist_z_limits', []):
        lines.append('twist_z %d %.9g' % (j['index'], j['limit_rad']))
    text='\n'.join(lines)+'\n'
    if contract.get('bone_twist_lock'):
        import bone_twist
        text=bone_twist.append(text,contract['bone_twist_locks'])
    return text


def _zero_lock_pairs(deg, group):
    deg = [deg] * 3 if isinstance(deg, (int, float)) else list(deg)
    pairs = [(-d, d) if isinstance(d, (int, float)) else tuple(d) for d in deg]
    if len(pairs) != 3 or any(len(p) != 2 or not all(math.isfinite(x) for x in p)
                              or not -180 < p[0] < 0 < p[1] < 180 for p in pairs):
        raise ValueError('zero_lock_limit_deg must be a degree, or 3 axes of degree / [lo, hi] with lo < 0 < hi: ' + group)
    return pairs


def action_override(scene, model, contract, override):
    """按动作覆盖（2026-10-11 千咲/今汐/桃夭坐姿裙撑开）：只改本条动画的场景与契约副本。

    override 键：
      zero_lock_limit_deg {族: 度 | [x,y,z]}  —— 只作用于 apply 已登记为 limited_zero_lock 的关节
        （即全局配置里已被限位的零锁关节），重写其角限位；不新增、不删除关节，response 计数不变。
      rigid_fit {刚体下标(str|int): {size:[3], location:[3]}} —— 覆盖运动学墙的尺寸/位置。
      follow_families [族名, ...] —— 把指定族的所有动态刚体（mode 1/2）临时改成 kinematic（mode 0），
        让它们跟骨架走而不参与物理解算；同时把这些刚体之间的关节限位开到 ±180° 使其失效。
        用于坐姿等 PMX bind pose 与动作差异极大导致物理失稳的场景。
    返回 (scene, contract_copy, rows)。契约副本给 append_scene_contract 校验用，原契约不动。
    """
    import copy
    lines = scene.splitlines()
    joint_at = [k for k, s in enumerate(lines) if s.startswith('joint ')]
    rigid_at = [k for k, s in enumerate(lines) if s.startswith('rigid ')]
    if len(joint_at) != len(model.joints) or len(rigid_at) != len(model.rigids):
        raise ValueError('Scene joint/rigid count differs from PMX')
    c = copy.deepcopy(contract)
    fam = {b['index']: b['family'] for b in c['bodies']}
    rows = dict(joints=[], rigids=[])
    limits = override.get('zero_lock_limit_deg') or {}
    if limits:
        jrow = {j['index']: j for j in c['joints']}
        for i in c.get('limited_zero_lock_joints', []):
            j = model.joints[i]
            child = j.dest_rigid if model.rigids[j.dest_rigid].mode else j.src_rigid
            group = fam.get(child)
            if group not in limits:
                continue
            pairs = _zero_lock_pairs(limits[group], group)
            lo = [math.radians(p[0]) for p in pairs]; hi = [math.radians(p[1]) for p in pairs]
            parts = lines[joint_at[i]].split()
            parts[15:21] = ['%.7g' % x for x in lo + hi]
            lines[joint_at[i]] = ' '.join(parts)
            if i in jrow:
                jrow[i]['candidate_angles'] = [[float('%.7g' % x) for x in lo], [float('%.7g' % x) for x in hi]]
            rows['joints'].append(i)
        if not rows['joints']:
            raise ValueError('action override zero_lock_limit_deg matched no limited zero-lock joint')
    for key, fit in (override.get('rigid_fit') or {}).items():
        i = int(key)
        if model.rigids[i].mode != 0:
            raise ValueError('rigid_fit only applies to kinematic walls: ' + str(model.rigids[i].name))
        parts = lines[rigid_at[i]].split()
        parts[3:6] = ['%.7g' % x for x in fit['size']]
        parts[6:9] = ['%.7g' % x for x in fit['location']]
        lines[rigid_at[i]] = ' '.join(parts)
        rows['rigids'].append(i)
    follow_fams = set(override.get('follow_families') or [])
    if follow_fams:
        # 把指定族的动态刚体（mode 1/2）改为 kinematic（mode 0），让它们跟骨架走。
        # rigid 行（moe_bullet_bake 写出）：
        #   rigid <bone> <type> <size×3> <loc×3> <rot×3> <mass> <vel_att> <ang_damp> <bounce> <friction> <mode> <grp> <msk>
        # 下标   0     1      2     3-5      6-8     9-11    12      13        14        15       16        17     18    19
        # 第 12 列是质量，不是 mode；mode 在第 17 列（exclude_pairs 读 row[18]/row[19] 与此一致）。
        follow_set = {b['index'] for b in c['bodies'] if b.get('family') in follow_fams}
        made_kinematic = set()
        for i in follow_set:
            parts = lines[rigid_at[i]].split()
            if parts[17] != '0':
                parts[17] = '0'
                lines[rigid_at[i]] = ' '.join(parts)
                made_kinematic.add(i)
        # 从契约副本里移除 follow_fams 族的关节，让 response_joints 计数与 native 一致。
        # contract joints 本身带 family 字段，直接按族名过滤，无需经由 src_rigid/dest_rigid。
        follow_fam_joint_idx = {j['index'] for j in c['joints'] if j.get('family') in follow_fams}
        c['joints'] = [j for j in c['joints'] if j.get('family') not in follow_fams]
        # limited_zero_lock_joints 存 joint index，用上面同一批 index 过滤
        c['limited_zero_lock_joints'] = [i for i in c.get('limited_zero_lock_joints', [])
                                         if i not in follow_fam_joint_idx]
        c.setdefault('follow_families_action', []).extend(sorted(follow_fams))
        rows['follow_families_kinematic'] = len(made_kinematic)
        if not made_kinematic:
            raise ValueError('follow_families matched no dynamic rigids: ' + str(sorted(follow_fams)))
        # 记录被移除的关节数，供 trial_response_batch 校验时从期望值里扣除。
        # skirt 链关节都是正常的 src→dest 连接，不会是无效自关节，所以直接用 len() 无需再减 invalid。
        rows['follow_fam_removed_joints'] = len(follow_fam_joint_idx)
    c['action_override'] = dict(override, applied=rows)
    return '\n'.join(lines) + '\n', c, rows
