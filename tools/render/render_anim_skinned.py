"""在 Blender 里带蒙皮渲染 .anim，用来查穿插与皮肤撕裂。

## 为什么骨架线框不够

线框能看出姿势与比例问题（肘角、腕的落点），但看不出：
  - **穿插**：前臂插进躯干、手穿过大腿 —— 骨骼线可以贴很近却不相交
  - **皮肤撕裂**：肩窝、腹股沟、肘内侧的权重在大角度下崩开
  - 未蒙皮到语义骨的部件（头发、裙摆）被带飞

## 为什么不用 io_pdx_mesh 的导入器

我们的 `moe_torso.mesh` 里有 **27 个同名 `mesh` 节点，其中 19 个是空的**
（只有 8 个带 `p`/`tri`）。导入器把子节点按名字塞进 dict，同名兄弟互相覆盖，
于是它拿到一个没有 `p` 属性的空节点直接崩：

    AttributeError: 'PDXData' object has no attribute 'p'

这是我们文件的结构特征，不是插件 bug，所以绕开导入器，用自研 `pdx_parse`
直接建网格与蒙皮。顺带也避开了导出器那个浮点噪声问题。

## 判据

除了看图，还输出**自穿插三角对数**（BVH 自相交）。撕裂和穿插都会让它上升；
拿 rest 帧当基线比较，只看增量。

用法（必须用 Blender 跑）:
  "D:/steam/.../blender.exe" -b --python tools/render/render_anim_skinned.py -- \
      --frames 0,20,40,60 --views front,three --tag idle_ik
"""
import argparse
import math
import sys
from pathlib import Path

import bpy
import mathutils

ROOT = Path(__file__).resolve().parents[2]
SHOT = ROOT / "work" / "shot"
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import pdx_parse                      # noqa: E402
import retarget_vanilla_anim as R     # noqa: E402


def argv_after():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def kids(n):
    ch = n.get("_children") or []
    return list(ch.items()) if isinstance(ch, dict) else list(ch)


def props(n):
    return n.get("_props") or {}


def read_mesh(path):
    """返回 (submeshes, bone_names)。submesh = dict(p, tri, bones, ix, w)。"""
    tree = pdx_parse.parse(Path(path).read_bytes())
    subs, bones = [], []
    for _, obj in kids(tree):
        for _, mnode in kids(obj):
            for _, cn in kids(mnode):
                pr = props(cn)
                if "ix" in pr and "tx" in pr:          # 骨骼节点
                    bones.append((pr["ix"][1][0], _))
                if "p" not in pr:
                    continue
                sk = None
                for _, g in kids(cn):
                    gp = props(g)
                    if "bones" in gp and "ix" in gp:
                        sk = (gp["bones"][1][0], gp["ix"][1], gp["w"][1])
                subs.append({"p": pr["p"][1], "tri": pr["tri"][1], "skin": sk})
    return subs


def bone_table(path):
    """返回按 ix 排序的骨名列表 + {骨名: 父骨名}。"""
    tree = pdx_parse.parse(Path(path).read_bytes())
    by_ix, par = {}, {}
    def walk(nd):
        for name, ch in kids(nd):
            pr = props(ch)
            if "ix" in pr:
                by_ix[pr["ix"][1][0]] = name
                par[name] = pr.get("pa", [None, [-1]])[1][0]
            walk(ch)
    walk(tree)
    names = [by_ix[i] for i in sorted(by_ix)]
    return names, {n: (by_ix.get(p) if p >= 0 else None) for n, p in par.items()}


def build_baked(meshpath, animpath, frames, extra_meshes=None, mesh_anims=None):
    """自己做 linear blend skinning，把每帧的顶点位置直接烘成 shape key。

    `mesh_anims`: {mesh 路径: 该 mesh 用的 anim 路径}。默认所有 mesh 共用
    `animpath`。**渲 head/torso 同步问题时必须用它** —— 引擎里 head 与 torso
    是独立 entity、各播自己的 animation，用一份 anim 驱动两件 mesh
    渲出来永远是同步的，看不出脱节（我第一次就这么渲，白渲了一轮）。

    为什么不用 Blender 的骨架蒙皮：pose bone 的 `rotation_quaternion` 是相对
    **edit bone 自身坐标系**的量，而 edit bone 的朝向由 head→tail 决定，
    跟 PDX 的局部帧不是一回事。照搬 PDX 的 q 会得到完全错乱的姿势
    （实测渲出来人是倒过来对折的），而同一份 .anim 的骨架线框是正常的。

    换成自研 FK + LBS：蒙皮矩阵 = M_now(bone) · M_rest(bone)⁻¹，
    与引擎做的事一致，且复用了已经逐帧校验过的 world_rest/posed 代码路径。
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    names, parent_of = bone_table(meshpath)
    subs = read_mesh(meshpath)
    for s in subs:
        s["_src"] = meshpath
    for extra in (extra_meshes or []):
        # head / torso / clothes 三件**骨序完全相同**（189 根，逐一对齐，
        # 由 tools/model/bake_moe_expressions.py 一次导出保证），
        # 所以可以共用同一套骨架与权重索引，直接把 submesh 拼进来。
        n2, _ = bone_table(extra)
        assert n2 == names, f"{extra} 骨序与主 mesh 不一致，不能共用权重索引"
        got = read_mesh(extra)
        for s in got:
            s["_src"] = extra
        subs += got

    # 每个 mesh 各自的动画（默认共用 animpath）
    anim_for = {}
    srcs = [meshpath] + list(extra_meshes or [])
    for sp in srcs:
        anim_for[sp] = (mesh_anims or {}).get(sp, animpath)

    posed = {}       # anim 路径 → (F, pose_at 函数, restWP, restWQ)
    for ap_ in set(anim_for.values()):
        meta, abones, samp = R.load_anim(ap_)
        F_, ts_, qs_ = R.unpack_samples(meta, abones, samp)
        arest_ = {b["name"]: b for b in abones}
        rWP, rWQ = R.world_rest(arest_, parent_of)
        posed[ap_] = (F_, arest_, ts_, qs_, rWP, rWQ)
        print(f"[skin] {Path(ap_).name}: 帧 {F_}")

    F = min(v[0] for v in posed.values())
    print(f"[skin] submesh {len(subs)}  骨 {len(names)}  共同帧数 {F}")

    def make_pose(arest, ts, qs):
        order = []

        def emit(n):
            if n in order:
                return
            p = parent_of.get(n)
            if p and p in arest:
                emit(p)
            order.append(n)
        for n in arest:
            emit(n)

        def pose_at(f):
            WP, WQ = {}, {}
            for n in order:
                b = arest[n]
                t = ts[n][f] if (n in ts and f < len(ts[n])) else tuple(b["t"])
                q = (R.q_norm(qs[n][f]) if (n in qs and f < len(qs[n]))
                     else R.q_norm(b["q"]))
                p = parent_of.get(n)
                if p and p in WQ:
                    o = R.q_apply(WQ[p], t)
                    WP[n] = tuple(WP[p][i] + o[i] for i in range(3))
                    WQ[n] = R.q_norm(R.q_mul(WQ[p], q))
                else:
                    WP[n], WQ[n] = tuple(t), q
            return WP, WQ
        return pose_at

    pose_fn = {ap_: make_pose(v[1], v[2], v[3]) for ap_, v in posed.items()}

    objs = []
    for si, s in enumerate(subs):
        p, tri = s["p"], s["tri"]
        nv = len(p) // 3
        verts = [(p[i*3], p[i*3+1], p[i*3+2]) for i in range(nv)]
        faces = [tuple(tri[i:i+3]) for i in range(0, len(tri), 3)]
        me = bpy.data.meshes.new(f"sub{si}")
        me.from_pydata(verts, [], faces)
        me.update()
        ob = bpy.data.objects.new(f"sub{si}", me)
        bpy.context.collection.objects.link(ob)
        objs.append(ob)
        ob.shape_key_add(name="rest", from_mix=False)

        infl, ix, w = (s["skin"] or (0, [], []))
        my_anim = anim_for[s["_src"]]
        pose_at = pose_fn[my_anim]
        restWP, restWQ = posed[my_anim][4], posed[my_anim][5]
        for f in frames:
            WP, WQ = pose_at(f)
            kb = ob.shape_key_add(name=f"f{f}", from_mix=False)
            if not infl:
                continue
            for vi in range(nv):
                v = verts[vi]
                acc = [0.0, 0.0, 0.0]
                tw = 0.0
                for k in range(infl):
                    bi = ix[vi*infl+k]
                    wt = w[vi*infl+k] if w else 0.0
                    if bi < 0 or wt <= 0.0 or bi >= len(names):
                        continue
                    bn = names[bi]
                    if bn not in restWQ or bn not in WQ:
                        continue
                    # 顶点 → 骨骼 rest 局部 → 用当前世界变换前推
                    loc = R.q_apply(R.q_conj(restWQ[bn]),
                                    [v[j] - restWP[bn][j] for j in range(3)])
                    nw = R.q_apply(WQ[bn], loc)
                    for j in range(3):
                        acc[j] += (WP[bn][j] + nw[j]) * wt
                    tw += wt
                if tw > 1e-6:
                    kb.data[vi].co = mathutils.Vector([c / tw for c in acc])
    return objs, F, frames


def build(meshpath, animpath):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    names, parent_of = bone_table(meshpath)
    subs = read_mesh(meshpath)
    print(f"[skin] submesh {len(subs)}  骨 {len(names)}")

    # ---- 骨架：用 .anim 的 rest（与动画同一参考系）
    meta, abones, samp = R.load_anim(animpath)
    F, ts, qs = R.unpack_samples(meta, abones, samp)
    arest = {b["name"]: b for b in abones}
    WP, WQ = R.world_rest(arest, parent_of)

    arm_d = bpy.data.armatures.new("moe")
    arm = bpy.data.objects.new("moe_rig", arm_d)
    bpy.context.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    ebones = {}
    for n in names:
        if n not in WP:
            continue
        eb = arm_d.edit_bones.new(n)
        head = mathutils.Vector(WP[n])
        # tail 朝子骨；没有子骨就沿骨自身局部 +Z 给一点长度
        ch = [c for c, p in parent_of.items() if p == n and c in WP]
        if ch:
            tail = mathutils.Vector(WP[ch[0]])
            if (tail - head).length < 1e-4:
                tail = head + mathutils.Vector((0, 0, 1))
        else:
            tail = head + mathutils.Vector(R.q_apply(WQ[n], (0, 0, 1.5)))
        eb.head, eb.tail = head, tail
        ebones[n] = eb
    for n, eb in ebones.items():
        p = parent_of.get(n)
        if p in ebones:
            eb.parent = ebones[p]
    bpy.ops.object.mode_set(mode="OBJECT")

    # ---- 网格 + 蒙皮
    objs = []
    for si, s in enumerate(subs):
        p, tri = s["p"], s["tri"]
        verts = [(p[i * 3], p[i * 3 + 1], p[i * 3 + 2]) for i in range(len(p) // 3)]
        faces = [tuple(tri[i:i + 3]) for i in range(0, len(tri), 3)]
        me = bpy.data.meshes.new(f"sub{si}")
        me.from_pydata(verts, [], faces)
        me.update()
        ob = bpy.data.objects.new(f"sub{si}", me)
        bpy.context.collection.objects.link(ob)
        objs.append(ob)
        if not s["skin"]:
            continue
        infl, ix, w = s["skin"]
        vg = {}
        for vi in range(len(verts)):
            for k in range(infl):
                bi = ix[vi * infl + k]
                wt = w[vi * infl + k] if w else 0.0
                if bi < 0 or wt <= 0.0 or bi >= len(names):
                    continue
                bn = names[bi]
                if bn not in ebones:
                    continue
                g = vg.get(bn) or ob.vertex_groups.new(name=bn)
                vg[bn] = g
                g.add([vi], wt, "REPLACE")
        md = ob.modifiers.new("arm", "ARMATURE")
        md.object = arm

    # ---- 把采样烘成关键帧
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="POSE")
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, F
    for f in range(F):
        for n in names:
            if n not in arest or n not in arm.pose.bones:
                continue
            pb = arm.pose.bones[n]
            t = ts[n][f] if n in ts else tuple(arest[n]["t"])
            q = qs[n][f] if n in qs else arest[n]["q"]
            # PDX (x,y,z,w) → Blender (w,x,y,z)；转成 basis 相对 rest
            mq = mathutils.Quaternion((q[3], q[0], q[1], q[2]))
            rq = arest[n]["q"]
            rest_q = mathutils.Quaternion((rq[3], rq[0], rq[1], rq[2]))
            pb.rotation_mode = "QUATERNION"
            pb.rotation_quaternion = rest_q.inverted() @ mq
            pb.location = mathutils.Vector(t) - mathutils.Vector(arest[n]["t"])
            pb.keyframe_insert("rotation_quaternion", frame=f + 1)
            pb.keyframe_insert("location", frame=f + 1)
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm, objs, F


def setup_render(res=(560, 900)):
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.image_settings.file_format = "PNG"
    sh = sc.display.shading
    sh.light = "STUDIO"
    sh.color_type = "SINGLE"
    sh.single_color = (0.80, 0.77, 0.74)
    sh.show_cavity = True      # 空腔阴影能把撕裂缝隙显出来
    sh.cavity_type = "BOTH"
    sc.display.viewport_aa = "FXAA"


def fit_camera(objs, view, margin=1.15):
    """摆正交相机。

    注意：PDX 数据是 **Y-up**（脚踝 Y≈10、头 Y≈140），而 Blender 场景是
    Z-up。按 Blender 惯例摆相机会把人渲成倒过来的（踩过这个坑）。
    这里显式把「上方向」当作 +Y，用 track-to 约束对准中心，避免手写欧拉角。
    """
    pts = []
    for ob in objs:
        for c in ob.bound_box:
            pts.append(ob.matrix_world @ mathutils.Vector(c))
    xs = [p.x for p in pts]; ys = [p.y for p in pts]; zs = [p.z for p in pts]
    ctr = mathutils.Vector(((min(xs)+max(xs))/2, (min(ys)+max(ys))/2,
                            (min(zs)+max(zs))/2))
    dx, dy, dz = max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs)
    d = max(dx, dy, dz) * 3 + 10

    # 视线方向（从相机指向中心）。up = +Y。
    if view == "front":
        eye = ctr + mathutils.Vector((0, 0, -d))      # 沿 -Z 看向 +Z
        span = (dx, dy)
    elif view == "side":
        eye = ctr + mathutils.Vector((d, 0, 0))
        span = (dz, dy)
    else:
        eye = ctr + mathutils.Vector((d*0.7, dy*0.05, -d*0.7))
        span = (max(dx, dz), dy)

    cd = bpy.data.cameras.new("c"); cd.type = "ORTHO"
    cd.ortho_scale = max(span) * margin
    cam = bpy.data.objects.new("c", cd)
    bpy.context.collection.objects.link(cam)
    cam.location = eye
    fwd = (ctr - eye).normalized()
    up = mathutils.Vector((0, 1, 0))
    if abs(fwd.dot(up)) > 0.99:
        up = mathutils.Vector((0, 0, 1))
    right = fwd.cross(up).normalized()
    up2 = right.cross(fwd).normalized()
    # 相机默认朝 -Z 看，+Y 为上
    m = mathutils.Matrix((right, up2, -fwd)).transposed().to_4x4()
    m.translation = eye
    cam.matrix_world = m
    bpy.context.scene.camera = cam


def self_isect(ob):
    from mathutils.bvhtree import BVHTree
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    me.calc_loop_triangles()
    vs = [ev.matrix_world @ v.co for v in me.vertices]
    ts = [tuple(t.vertices) for t in me.loop_triangles]
    tree = BVHTree.FromPolygons(vs, ts, all_triangles=True, epsilon=0.0)
    n = sum(1 for a, b in tree.overlap(tree) if a != b) // 2
    ev.to_mesh_clear()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mesh", default=str(ROOT / "work/florence_poc/moe_torso.mesh"))
    ap.add_argument("--mesh2", default=None, action="append",
                    help="额外 mesh，可重复给（如 --mesh2 head --mesh2 clothes），"
                         "与主 mesh 共用骨架")
    # 默认动画按角色解析（单体 mod 已退役，写死路径会解析到空目录）。
    import moe_paths as _MP
    _def_anim = _MP.find("moe_idle_calm.anim", missing_ok=True)
    ap.add_argument("--anim", default=str(_def_anim) if _def_anim else None,
                    required=_def_anim is None)
    ap.add_argument("--frames", default="0,20,40,60")
    ap.add_argument("--views", default="front,three")
    ap.add_argument("--tag", default="skin")
    ap.add_argument("--anim-for", default=None, action="append",
                    help="给某个 mesh 单独指定动画: --anim-for <mesh路径>=<anim路径>。"
                         "渲 head/torso 同步问题必须用它，否则两件 mesh 共用一份"
                         "动画，渲出来永远同步、看不出脱节。")
    a = ap.parse_args(argv_after())

    mesh_anims = {}
    for spec in (a.anim_for or []):
        k, _, v = spec.partition("=")
        if not v:
            raise SystemExit(f"--anim-for 需要 <mesh>=<anim> 形式，收到 {spec!r}")
        mesh_anims[k] = v

    want = [int(x) for x in a.frames.split(",")]
    objs, F, frames = build_baked(a.mesh, a.anim, want, a.mesh2, mesh_anims)
    frames = [f for f in frames if f < F]
    print(f"[skin] 帧数 {F}  mesh 对象 {len(objs)}")
    setup_render()
    sc = bpy.context.scene
    body = max(objs, key=lambda o: len(o.data.polygons))

    def show(f):
        """把所有 submesh 切到该帧的 shape key（其余权重归零）。"""
        for ob in objs:
            sk = ob.data.shape_keys
            if not sk:
                continue
            for kb in sk.key_blocks:
                kb.value = 1.0 if kb.name == f"f{f}" else 0.0

    print(f"\n[穿插] 主体 {body.name} ({len(body.data.polygons)} 面)")
    print(f"{'帧':<7}{'自相交三角对':>14}")
    for f in frames:
        show(f)
        print(f"{f:<7}{self_isect(body):>14}")

    SHOT.mkdir(parents=True, exist_ok=True)
    show(frames[0])
    for view in a.views.split(","):
        for o in [x for x in bpy.data.objects if x.type == "CAMERA"]:
            bpy.data.objects.remove(o, do_unlink=True)
        fit_camera(objs, view)
        for f in frames:
            show(f)
            out = SHOT / f"{a.tag}_{view}_f{f}.png"
            sc.render.filepath = str(out)
            bpy.ops.render.render(write_still=True)
            print(f"[渲出] {out.name}")
    print("[skin] DONE")


if __name__ == "__main__":
    main()
