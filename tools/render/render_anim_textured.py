"""带材质渲染部署的 .anim —— 用来肉眼确认头发/裙子物理。

## 与 render_anim_skinned.py 的分工

`render_anim_skinned.py` 是**灰模**诊断器：查穿插与撕裂，不需要贴图，
所以它丢掉 UV 与 material。但用户要确认的是「头发摆得对不对、裙子在不在」，
灰模 + 只渲 torso（头发在 head.mesh 里）根本看不到头发，于是这里补一版：

  1. 读 `u0` 建 UV，读 material 的 `diff` 挂贴图（.png 名 → 实际是 .dds）
  2. **head + torso 一起渲**，各自配对自己的 .anim
     （`[[ck3-head-torso-need-paired-anims]]`：共用一份 anim 渲出来永远同步，
      看不出脱节；两件 mesh 必须各播自己那份）
  3. 蒙皮复用 render_anim_skinned 的自研 LBS（已逐帧校验过的代码路径）

## 半透明材质怎么处理

`portrait_hair_alpha` / `portrait_attachment_alpha_to_coverage` 这些在 CK3 里
是 alpha-to-coverage，Blender EEVEE 没有等价物。这里统一用 BLEND +
`alpha_threshold`，只为**看清形状**，不追求还原引擎观感
（`[[ck3-alpha-to-coverage-kills-gradients]]`：真要定案只能进游戏 A/B）。

用法:
  blender.exe -b --python tools/render/render_anim_textured.py -- \
      --anim-head moe_idle_calm --anim-torso moe_body_idle \
      --frames 0,20,40 --tag idle
"""
import argparse
import importlib.util
import math
import sys
from pathlib import Path

import bpy
import mathutils
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "work" / "preview_textured"
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import moe_paths as MP    # noqa: E402  资源路径解析（子 mod 感知）
# 单体目录已退役（2026-08-29）：产物按子 mod 直写 mod/<mod_id>/。
# MP.BASE 解析到第一个存在的模型包；**定位具体文件用 MP.find()**，
# 它会遍历全部子 mod（flo 在 gfl2 包、lau/per/eld 在 endfield 包）。
D = MP.BASE
# **必须在 sys.path.insert 之后**：Blender 跑脚本时 cwd 不含 tools/，
# 放在文件顶部会 ModuleNotFoundError。

import pdx_parse                       # noqa: E402
import retarget_vanilla_anim as R      # noqa: E402


def _load_skinned():
    """复用 render_anim_skinned 的 bone_table —— 那套遍历已经验证过。"""
    path = ROOT / "tools" / "render" / "render_anim_skinned.py"
    spec = importlib.util.spec_from_file_location("_ras", path)
    m = importlib.util.module_from_spec(spec)
    sys.modules["_ras"] = m
    # 它在 import 时不会跑 main（有 __main__ 保护）
    spec.loader.exec_module(m)
    return m


def kids(n):
    ch = n.get("_children") or []
    return list(ch.items()) if isinstance(ch, dict) else list(ch)


def props(n):
    return n.get("_props") or {}


def read_mesh_full(path):
    """比 render_anim_skinned.read_mesh 多读 u0(UV) 与 material(贴图名)。"""
    tree = pdx_parse.parse(Path(path).read_bytes())
    subs = []
    for _, obj in kids(tree):
        for _, mnode in kids(obj):
            for _, cn in kids(mnode):
                pr = props(cn)
                if "p" not in pr:
                    continue          # 空 mesh 节点（这个文件里有 19 个）
                sk, diff, shader = None, None, None
                for n4, gn in kids(cn):
                    gp = props(gn)
                    if n4 == "skin" and "bones" in gp:
                        sk = (gp["bones"][1][0], gp["ix"][1], gp["w"][1])
                    if n4 == "material":
                        if "diff" in gp:
                            diff = gp["diff"][1][0]
                        if "shader" in gp:
                            shader = gp["shader"][1][0]
                subs.append({"p": pr["p"][1], "tri": pr["tri"][1],
                             "u0": pr.get("u0", [None, None])[1],
                             "skin": sk, "diff": diff, "shader": shader})
    return subs


def _walk_bone_nodes(mesh_path):
    """遍历 .mesh 的骨节点，yield (骨名, 该节点的 _props)。"""
    tree = pdx_parse.parse(Path(mesh_path).read_bytes())

    def rec(nd):
        for name, ch in kids(nd):
            pr = props(ch)
            if "ix" in pr and "tx" in pr:
                yield name, pr
            yield from rec(ch)
    yield from rec(tree)


def _tx_to_bind(pr):
    """tx（12 float）→ (Rm 3×3, pm 3)，蒙皮公式里的「绑定空间」那一半。

    实测定下的读法（2026-10-15，逐候选跑「绑定姿态应还原原顶点」判据）：

        out = R_pose · (Rm · v + pm) + p_pose
        Rm  = tx[:9] 按**列主序**读（reshape(3,3).T）
        pm  = tx[9:12]

    判据结果（wcq，人高 ~146 单位）：本读法还原误差 4.33，其余三种读法
    都在 150~213 —— 差两个数量级，不是噪声。
    """
    tx = pr["tx"][1]
    Rm = np.array(tx[:9], float).reshape(3, 3).T
    pm = np.array(tx[9:12], float)
    return Rm, pm


def find_texture(diff_name):
    """material 里记的是 .png，实际部署的是 .dds。"""
    if not diff_name:
        return None
    stem = Path(diff_name).stem
    for ext in (".dds", ".png"):
        # missing_ok：MMD 模型会带 toon01/toon02.bmp 这类**引擎侧不需要**的
        # 渐变贴图（mmd_tools 导入时自带），它们不在部署目录里。
        # find() 默认抛错是对的（分类漏了一种命名比文件真缺失更常见），
        # 但渲预览图时缺一张 toon 图不该中断整次渲染。
        p = MP.find(f"{stem}{ext}", missing_ok=True)
        if p.exists():
            return p
    return None


def make_material(sub, idx):
    name = f"mat{idx}_{Path(sub['diff'] or 'none').stem}"
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if bsdf is None:
        return mat
    # 卡渲模型不需要金属/高光反射，压掉免得盖住形状
    for key, val in (("Metallic", 0.0), ("Roughness", 0.75)):
        if key in bsdf.inputs:
            bsdf.inputs[key].default_value = val
    tex_path = find_texture(sub["diff"])
    if tex_path is None:
        return mat
    try:
        img = bpy.data.images.load(str(tex_path), check_existing=True)
    except RuntimeError:
        return mat
    tn = nt.nodes.new("ShaderNodeTexImage")
    tn.image = img
    nt.links.new(tn.outputs["Color"], bsdf.inputs["Base Color"])

    shader = (sub.get("shader") or "")
    if "alpha" in shader or "coverage" in shader:
        # 头发/眼睛遮罩这类：用 alpha 抠形状。CK3 用 alpha-to-coverage，
        # EEVEE 无等价物，这里只求看清轮廓（见模块 docstring）。
        nt.links.new(tn.outputs["Alpha"], bsdf.inputs["Alpha"])
        mat.blend_method = "BLEND"
        mat.show_transparent_back = False
    return mat


def build(frames, head_anim, torso_anim, prefix="moe_"):
    """建 head+torso 两件 mesh，各配自己的 anim，逐帧烘 shape key。

    prefix 决定用哪个角色的 mesh（moe_ / moe_lau_ / moe_eld_ / moe_per_）。
    """
    ras = _load_skinned()
    bpy.ops.wm.read_factory_settings(use_empty=True)

    torso_mesh = MP.find(f"{prefix}torso.mesh")
    head_mesh = MP.find(f"{prefix}head.mesh")
    main_mesh = torso_mesh
    names, parent_of = ras.bone_table(main_mesh)

    plan = [(torso_mesh, torso_anim), (head_mesh, head_anim)]
    subs = []
    for mp, ap in plan:
        # 每件 mesh 的蒙皮索引指向**它自己的**骨表：head 比 torso 少 4 根
        # torso 专属骨（ground_joint/body_root/两根 prop 骨），且 head 从 bn_m_root
        # 起、torso 从 ground_joint 起，两表既不相等也不是前缀。原来要求两表
        # 相同并统一用 torso 表解索引，在 prop 骨加进 torso（2026-09-03）后就对
        # 所有角色失效，只是预览图一直复用旧图没重渲，到艾尔黛拉槽位 4 才炸。
        # head 的骨名都在 torso 表里，父骨映射仍用 torso 那份即可。
        n2, _ = ras.bone_table(mp)
        assert set(n2) <= set(names), (
            f"{mp.name} 有 torso 表里没有的骨: {sorted(set(n2) - set(names))[:5]}")
        for s in read_mesh_full(mp):
            s["_src"], s["_anim"], s["_names"] = mp, ap, n2
            subs.append(s)

    posed = {}
    for ap in {p[1] for p in plan}:
        meta, abones, samp = R.load_anim(ap)
        F_, ts_, qs_ = R.unpack_samples(meta, abones, samp)
        arest = {b["name"]: b for b in abones}
        # 只用来建 FK 的骨序与无通道骨的兜底朝向；**不再当蒙皮基准**（见下面 BIND 的注释）。
        rWP, rWQ = R.world_rest(arest, parent_of)
        posed[ap] = (F_, arest, ts_, qs_, rWP, rWQ)
        print(f"[tex] {ap.name}: {F_} 帧")

    # head 的骨链必须走 **body 动画的 FK**。
    #
    # 2026-10-15：head 的 .anim 骨表**缺 ground_joint / body_root**（它的第一根骨
    # 就是 bn_m_root），于是 head 自己的 FK 少一层躯干变换，世界位置整体偏高
    # （实测 bn_m_neck 在 head 空间是 Y=127.3，在 body 空间是 Y=50.7）。
    # 原来两件 mesh 各播自己那份、各做各的 FK，站姿下恰好没暴露（两者的 rest
    # 都近似绑定姿态），一换坐姿 head 就飘到躯干上方 70 多个单位。
    #
    # 正确语义：head 只**额外交付面部骨通道**（表情/眼球/下颌那几根），
    # 其余骨——包括整条身体链——一律听 body 的。所以这里是**真覆盖**：
    # body 有的骨一律用 body 的 t/q，head 只在 body 没有该骨的通道时补进来。
    _body_anim = torso_anim
    if head_anim != _body_anim and _body_anim in posed and head_anim in posed:
        b_arest, b_ts, b_qs = posed[_body_anim][1], posed[_body_anim][2], posed[_body_anim][3]
        h_arest, h_ts, h_qs = posed[head_anim][1], posed[head_anim][2], posed[head_anim][3]
        m_ts = dict(b_ts)
        m_qs = dict(b_qs)
        extra_t = extra_q = 0
        for nm in h_ts:
            if nm not in m_ts:
                m_ts[nm] = h_ts[nm]
                extra_t += 1
        for nm in h_qs:
            if nm not in m_qs:
                m_qs[nm] = h_qs[nm]
                extra_q += 1
        size = min(posed[head_anim][0], posed[_body_anim][0])
        print(f"[tex] head 骨链改走 body FK（head 额外交付 t+{extra_t} q+{extra_q} 根）")
        posed[head_anim] = (size, b_arest, m_ts, m_qs,
                            posed[_body_anim][4], posed[_body_anim][5])

    # 蒙皮基准必须是 **mesh 的绑定姿态**，不能是 .anim 自带的 rest。
    #
    # 2026-10-15 发现（渲染与「姿态保持型」动画的冲突）：`.anim` 的 info 块 rest
    # 就是**该文件第 0 帧**，而坐姿这类动画整个 201 帧都是坐姿、第 0 帧即坐姿。
    # 拿它当基准算 M_now·M_rest⁻¹，delta 恒为单位阵 → 输出永远是 mesh 的绑定站姿，
    # 无论动画是什么样子。idle 因为站姿≈绑定姿态，一直看着「正常」，这个 bug 才
    # 长期没暴露；到坐姿/躺姿才显形（[[pdx-anim-rest-is-frame0-not-bindpose]]）。
    # 正确做法与引擎/`moe_bullet_bake.mesh_bind` 一致：用 .mesh 每根骨的 tx
    # （列主序**逆**绑定矩阵 12 float）作基准。
    BIND = {}
    for name, nd in _walk_bone_nodes(torso_mesh):
        BIND[name] = _tx_to_bind(nd)
    print(f"[tex] 绑定姿态 {len(BIND)} 骨（mesh={torso_mesh.name}）")
    F = min(v[0] for v in posed.values())
    frames = [f for f in frames if f < F]
    print(f"[tex] submesh {len(subs)}  共同帧数 {F}  渲 {len(frames)} 帧")

    def make_pose(arest, ts, qs):
        """FK：与 render_anim_skinned.build_baked 里那份同一套算法。
        那里它是 build_baked 的内嵌函数、取不到，只好照抄一份。
        改动时两边要一起改。"""
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

    pose_fn = {ap: make_pose(v[1], v[2], v[3]) for ap, v in posed.items()}

    objs = []
    for si, s in enumerate(subs):
        p, tri = s["p"], s["tri"]
        nv = len(p) // 3
        verts = [(p[i * 3], p[i * 3 + 1], p[i * 3 + 2]) for i in range(nv)]
        faces = [tuple(tri[i:i + 3]) for i in range(0, len(tri), 3)]
        me = bpy.data.meshes.new(f"s{si}")
        me.from_pydata(verts, [], faces)
        me.update()

        u0 = s["u0"]
        if u0 and len(u0) >= nv * 2:
            uv = me.uv_layers.new(name="UVMap")
            for loop in me.loops:
                vi = loop.vertex_index
                # PDX 的 V 与 Blender 相反
                uv.data[loop.index].uv = (u0[vi * 2], 1.0 - u0[vi * 2 + 1])

        ob = bpy.data.objects.new(f"s{si}", me)
        bpy.context.collection.objects.link(ob)
        me.materials.append(make_material(s, si))
        objs.append(ob)
        ob.shape_key_add(name="rest", from_mix=False)

        infl, ix, w = (s["skin"] or (0, [], []))
        names = s["_names"]                      # 本件 mesh 自己的骨表
        pa = pose_fn[s["_anim"]]
        # 蒙皮基准：mesh 的逆绑定矩阵（不是动画的 rest，见上面 BIND 的注释）。
        # head 缺 torso 专属骨，但 head 自己的骨名都在 torso 表里、tx 也一致，
        # 所以共用同一份 BIND，缺的骨反正不会被 head 的蒙皮索引引用到。
        for f in frames:
            WP, WQ = pa(f)
            kb = ob.shape_key_add(name=f"f{f}", from_mix=False)
            if not infl:
                continue
            for vi in range(nv):
                v = verts[vi]
                acc, tw = [0.0, 0.0, 0.0], 0.0
                for k in range(infl):
                    bi = ix[vi * infl + k]
                    wt = w[vi * infl + k] if w else 0.0
                    if bi < 0 or wt <= 0.0 or bi >= len(names):
                        continue
                    bn = names[bi]
                    if bn not in BIND or bn not in WQ:
                        continue
                    Rm, pm = BIND[bn]
                    loc = Rm @ np.asarray(v, float) + pm
                    nw = R.q_apply(WQ[bn], loc)
                    for j in range(3):
                        acc[j] += (WP[bn][j] + nw[j]) * wt
                    tw += wt
                if tw > 1e-6:
                    kb.data[vi].co = mathutils.Vector([c / tw for c in acc])
    return objs, frames


def setup(res, transparent=False):
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.resolution_x, sc.render.resolution_y = res
    # 透明胶片给换装窗口的槽位预览图用：按钮叠在窗口底图上，
    # 不透明背景会切出一块灰方块。
    sc.render.film_transparent = transparent
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA" if transparent else "RGB"
    for o in [o for o in bpy.data.objects if o.type == "LIGHT"]:
        bpy.data.objects.remove(o, do_unlink=True)
    # 灯也要按 Y-up 布（见 fit_camera 的注释）：SUN 默认照 -Z，
    # 绕 X 转 90° 让它照 -Y 之外的方向，再用 Y 分量拉出高低差。
    for nm, rot, en in (("k", (math.radians(118), math.radians(28), 0.0), 4.5),
                        ("f", (math.radians(105), math.radians(-58), 0.0), 2.8),
                        ("b", (math.radians(70), math.radians(180), 0.0), 2.2)):
        ld = bpy.data.lights.new(nm, "SUN")
        ld.energy = en
        ob = bpy.data.objects.new(nm, ld)
        sc.collection.objects.link(ob)
        ob.rotation_euler = rot
    if sc.world is None:
        sc.world = bpy.data.worlds.new("W")
    sc.world.use_nodes = True
    sc.world.node_tree.nodes["Background"].inputs[1].default_value = 1.0


def fit_camera(objs, azim, upper=True):
    """取上半身（头发垂到腰，全身框会把人缩得太小看不清发丝）。

    **这个 .anim/.mesh 空间是 Y-up**，不是 Z-up：实测 rest 下 bn_m_head 在
    (0, 124.27, 1.51)、bn_m_root 在原点，即"高度"沿 +Y、身体前后沿 Z。
    第一版照抄了 render_physics_sheet 的 Z-up 取景（那边是 Blender 骨架空间，
    头顶 Z≈144），于是相机绕着错误的轴转、人躺着出画。
    这里统一按 Y 为竖直轴、绕 Y 转方位角。
    """
    dg = bpy.context.evaluated_depsgraph_get()
    lo = mathutils.Vector((1e9,) * 3)
    hi = mathutils.Vector((-1e9,) * 3)
    for o in objs:
        eo = o.evaluated_get(dg)
        me = eo.to_mesh()
        for v in me.vertices:
            wv = o.matrix_world @ v.co
            for i in range(3):
                lo[i] = min(lo[i], wv[i])
                hi[i] = max(hi[i], wv[i])
        eo.to_mesh_clear()
    height = hi.y - lo.y
    if upper:
        ctr = mathutils.Vector(((lo.x + hi.x) / 2,
                                hi.y - height * 0.26,
                                (lo.z + hi.z) / 2))
        span = height * 0.42
    else:
        ctr = (lo + hi) / 2
        span = height * 0.55
    for o in [x for x in bpy.data.objects if x.type == "CAMERA"]:
        bpy.data.objects.remove(o, do_unlink=True)
    cd = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", cd)
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    cd.type = "ORTHO"
    cd.ortho_scale = span * 2.3
    a = math.radians(azim)
    # 绕竖直轴(Y)转：正面朝 -Z 看过来
    cam.location = ctr + mathutils.Vector((math.sin(a) * span * 4, 0.0,
                                           -math.cos(a) * span * 4))
    # 显式构相机朝向，不用 to_track_quat('-Z','Y')：那个 up 参数是按
    # **Blender 的 Z-up 世界**解的，而这个模型 Y 才是竖直轴（实测 mesh
    # Y 跨 0→148 是身高，Z 只有 22 的厚度），于是人被摆成横躺的。
    # 手搭正交基：forward = 看向中心，up = 世界 +Y（模型的竖直方向）。
    fwd = (ctr - cam.location).normalized()
    up = mathutils.Vector((0.0, 1.0, 0.0))
    right = fwd.cross(up).normalized()
    up2 = right.cross(fwd).normalized()
    # 相机看向自身 -Z，+X 向右、+Y 向上
    m = mathutils.Matrix((right, up2, -fwd)).transposed().to_4x4()
    cam.rotation_euler = m.to_euler()


VIEWS = {"front": 0, "q35": -35, "side": -70}


def main():
    argv = sys.argv
    ap = argparse.ArgumentParser()
    ap.add_argument("--anim-head", default="moe_idle_calm")
    ap.add_argument("--anim-torso", default="moe_body_idle")
    ap.add_argument("--anim-dir", default=None,
                    help="从别处读 .anim（调参对比用；mesh 仍从部署目录读）")
    ap.add_argument("--frames", default="0,20,40,60")
    ap.add_argument("--views", default="q35")
    ap.add_argument("--tag", default="tex")
    ap.add_argument("--res", default="620x820")
    ap.add_argument("--full-body", action="store_true")
    ap.add_argument("--transparent", action="store_true",
                    help="透明背景（换装窗口的槽位预览图要这个）")
    ap.add_argument("--prefix", default="moe_",
                    help="角色前缀，决定用哪套 mesh："
                         "moe_(芙洛伦) / moe_lau_(洛茜) / moe_eld_(艾尔黛拉) / "
                         "moe_per_(洁尔佩塔)")
    a = ap.parse_args(argv[argv.index("--") + 1:] if "--" in argv else [])

    want = [int(x) for x in a.frames.split(",")]
    adir = Path(a.anim_dir) if a.anim_dir else D

    # 动画默认名带的是芙洛伦前缀。换角色时 `--prefix moe_lau_` 已经指明了
    # 是哪套 mesh，动画名也跟着换 —— 否则会拿芙洛伦的动画去驱动洛茜的骨架，
    # 骨表不同就直接崩（不是「看起来怪」而是抛异常）。
    # 但明确给了 --anim-dir 时，用户已经指定了文件所在目录，名字按传入值
    # 原样用（bake 目录的文件通常还带着 moe_ 前缀，不应再拼一次角色前缀）。
    if a.prefix != "moe_" and a.anim_head.startswith("moe_") and not a.anim_dir:
        a.anim_head = a.prefix + a.anim_head[len("moe_"):]
    if a.prefix != "moe_" and a.anim_torso.startswith("moe_") and not a.anim_dir:
        a.anim_torso = a.prefix + a.anim_torso[len("moe_"):]

    # 动画目录是分层的（moe/<角色>/anims/），顶层已经没有 .anim。
    # 这是同一个坑第三次发作（build_all_anims、graft_all_head_anims 已修）。
    def _find(name):
        p = adir / f"{name}.anim"
        if p.is_file():
            return p
        hit = next(iter(sorted(adir.rglob(f"{name}.anim"))), None)
        if hit is None and not a.anim_dir:
            # D = MP.BASE 只是第一个存在的模型包（gfl2）；eld/lau/per 的动画在
            # endfield 包里，要靠 MP.find 遍历全部子 mod（2026-09-06 槽位 4 预览渲图
            # 因此失败）。显式给了 --anim-dir 时尊重它，不越界找。
            hit = MP.find(f"{name}.anim", missing_ok=True)
        if hit is None:
            raise SystemExit(f"**找不到动画** {name}.anim（在 {adir} 及全部模型包下找过）")
        return hit

    objs, frames = build(want, _find(a.anim_head), _find(a.anim_torso),
                         a.prefix)
    w, h = (int(x) for x in a.res.split("x"))
    setup((w, h), a.transparent)

    def show(f):
        for ob in objs:
            sk = ob.data.shape_keys
            if not sk:
                continue
            for kb in sk.key_blocks:
                kb.value = 1.0 if kb.name == f"f{f}" else 0.0

    OUT.mkdir(parents=True, exist_ok=True)
    sc = bpy.context.scene
    for view in a.views.split(","):
        show(frames[0])
        fit_camera(objs, VIEWS.get(view, 0), upper=not a.full_body)
        for f in frames:
            show(f)
            out = OUT / f"{a.tag}_{view}_f{f:04d}.png"
            sc.render.filepath = str(out)
            bpy.ops.render.render(write_still=True)
    print(f"[tex] 渲了 {len(frames) * len(a.views.split(','))} 张 → {OUT}")


if __name__ == "__main__":
    main()
