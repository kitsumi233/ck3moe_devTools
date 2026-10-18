"""Fit kinematic thigh capsules to the real skinned leg (2026-10-11, 桃祈/洛可可坐姿裙被腿夹).

PMX 作者的大腿胶囊是手摆的：轴线可能偏离腿芯、半径与实际腿粗不符。这里按实际蒙皮重拟合：
取主权重骨为该刚体骨（含 D/非 D 孪生骨）、且材质不是衣物的顶点，变到胶囊局部系，
只留轴向 |y|<=半长 的那段；轴线平移到这些点的横截面中位位置，半径取到新轴距离的分位数。
只改尺寸和位置（不改朝向、长度、碰撞组），返回 {刚体下标: (size, location)} 供 _scene_text 覆盖。
"""
import math

import numpy as np

SKIP_MAT = ('裙', 'スカート', 'skirt', 'Skirt', 'dress', 'Dress', '衣', '袜', 'sock', 'Sock', '靴', 'shoe', 'Shoe')


def _rot_yxz(e):
    x, y, z = e
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]]); Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Ry @ Rx @ Rz


def _dominant_bones(model):
    dom = np.full(len(model.vertices), -1)
    for i, v in enumerate(model.vertices):
        w = v.weight
        bs = list(w.bones)
        ww = w.weights
        if hasattr(ww, 'weight'):   # SDEF：mmd_tools 把权重包在 BoneWeightSDEF 里
            ww = [ww.weight]
        ws = list(ww) if isinstance(ww, (list, tuple)) else [ww]
        if len(ws) < len(bs):
            ws = ws + [1 - sum(ws)] * (len(bs) - len(ws))
        if bs:
            dom[i] = bs[int(np.argmax(ws[:len(bs)]))]
    return dom


def _skin_mask(model):
    ok = np.ones(len(model.vertices), bool)
    off = 0
    for mat in model.materials:
        n = mat.vertex_count
        if any(t in mat.name for t in SKIP_MAT):
            for k in range(off, off + n):
                ok[model.faces[k // 3][k % 3]] = False
        off += n
    return ok


def fit(model, names, pct=75.0, shift=True, min_points=40):
    """names: kinematic capsule rigid names to refit (e.g. ('左足', '右足'))."""
    bname = [b.name for b in model.bones]
    dom = _dominant_bones(model)
    skin = _skin_mask(model)
    P = np.array([v.co for v in model.vertices], float)
    out = {}
    for ri, r in enumerate(model.rigids):
        if r.mode != 0 or r.type != 2 or str(r.name) not in names or r.bone is None or r.bone < 0:
            continue
        bn = bname[r.bone]
        twins = [i for i, n in enumerate(bname) if n == bn or n == bn.rstrip('D') or n == bn + 'D']
        sel = np.isin(dom, twins) & skin
        Rm = _rot_yxz(r.rotation)
        loc = (P[sel] - np.array(r.location, float)) @ Rm
        h = r.size[1] / 2
        loc = loc[np.abs(loc[:, 1]) <= h]
        if len(loc) < min_points:
            continue
        # 主权重归到大腿骨的顶点里混着臀/胯部的远点：只信作者轴线 1.5×半径以内的点定中心，
        # 再以新中心同样 1.5×半径截一次（迭代按中位收缩会在双峰分布上塌缩，桃祈右腿 1.2→0.21）。
        near = np.hypot(loc[:, 0], loc[:, 2]) <= 1.5 * r.size[0]
        if near.sum() < min_points:
            continue
        c = np.median(loc[near][:, [0, 2]], axis=0) if shift else np.zeros(2)
        loc = loc[np.hypot(loc[:, 0] - c[0], loc[:, 2] - c[1]) <= 1.5 * r.size[0]]
        rad = np.hypot(loc[:, 0] - c[0], loc[:, 2] - c[1])
        radius = float(np.percentile(rad, pct))
        edges = np.linspace(-h, h, 9)
        prof = [round(float(np.percentile(rad[(loc[:, 1] >= a) & (loc[:, 1] < b)], pct)), 2)
                if ((loc[:, 1] >= a) & (loc[:, 1] < b)).sum() >= 8 else None for a, b in zip(edges[:-1], edges[1:])]
        # 髋端 = 局部 y 端点里离父骨（腿根）近的那端
        parent = model.bones[r.bone].parent
        hip = None
        if parent is not None and parent >= 0:
            pl = (np.array(model.bones[parent].location, float) - np.array(r.location, float)) @ Rm
            hip = '+h' if pl[1] > 0 else '-h'
        d = Rm @ np.array([c[0], 0.0, c[1]])
        size = [radius, float(r.size[1]), float(r.size[2])]
        out[ri] = dict(name=str(r.name), radius_before=float(r.size[0]), radius=radius,
                       shift=[float(x) for x in d], size=size,
                       location=[float(a + b) for a, b in zip(r.location, d)],
                       pcts=[float(x) for x in np.percentile(rad, [50, 75, 90])], profile=prof,
                       hip_end=hip, n=int(len(loc)))
    return out


def symmetrize(fits, model, pairs=(('左足', '右足'),), max_shift=0.5):
    """左右腿成对：一侧轴心偏移超过 max_shift×半径（权重被臀部顶点带偏，桃祈右腿偏 0.9）时
    用另一侧镜像（PMX X 取反）；两侧都正常时半径取均值、各自保留偏移。"""
    by = {f['name']: i for i, f in fits.items()}
    for a, b in pairs:
        if a not in by or b not in by:
            continue
        ia, ib = by[a], by[b]
        fa, fb = fits[ia], fits[ib]
        ok = {i: np.linalg.norm(f['shift']) <= max_shift * f['radius'] for i, f in ((ia, fa), (ib, fb))}
        if ok[ia] and ok[ib]:
            r = (fa['radius'] + fb['radius']) / 2
            for f in (fa, fb):
                f['radius'] = f['size'][0] = r
            continue
        if not ok[ia] and not ok[ib]:
            for i in (ia, ib):
                del fits[i]
            continue
        good, bad = (fa, fb) if ok[ia] else (fb, fa)
        ibad = ib if ok[ia] else ia
        bad['mirrored_from'] = good['name']; bad['shift_dropped'] = bad['shift']
        bad['shift'] = [-good['shift'][0], good['shift'][1], good['shift'][2]]
        bad['radius'] = bad['size'][0] = good['radius']
        bad['location'] = [float(x + d) for x, d in zip(model.rigids[ibad].location, bad['shift'])]
    return fits
