# -*- coding: utf-8 -*-
"""把一条**写回后的 .anim** 加载到骨架上，用于在 Blender GUI 里看成品效果。

## 为什么需要这个（别用 --save-blend-after 的产物看网格）

`bake_hair_bullet.py --save-blend-after` 存的 blend 里：
  - 刚体（250 个）带着已烘的 point_cache，**会动**
  - 但 `rig.pose.bones` 的约束数是 **0** —— `simulate()` 是直接读刚体的
    `matrix_world` 采样的，不通过约束驱动骨骼
所以 GUI 打开只看到碰撞盒在飞、人物站着不动。那个 blend 适合看**装配与刚体
运动**，不适合看成品。

成品是写回后的 `.anim`。把它逐骨灌成关键帧，看到的就是游戏里的表现
（游戏读的是同一个文件）。

与 `bake_hair_bullet.drive_rig_from_anim` 的区别：那个**故意只驱动身体骨**
（物理骨留给 Bullet 解算）。这里要驱动**全部骨**，包括头发/裙子 —— 它们的
通道正是物理烘焙写进去的东西。

用法：
    blender.exe work/blenderProj/<短名>_expression_rig.blend --python tools/render/open_anim_viewer.py \\
        -- --anim <pfx>body_stayback_start
"""
import argparse
import sys
from pathlib import Path

import bpy
import mathutils

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import moe_paths as MP                      # noqa: E402
import retarget_vanilla_anim as RV           # noqa: E402


def a2b_quat(q):
    """.anim 的 (x,y,z,w) → Blender 的 (w,x,y,z)。"""
    return (q[3], q[0], q[1], q[2])


def a2b_vec(v):
    return mathutils.Vector((v[0], v[1], v[2]))


def _frame_and_play(rig):
    """把视口对准人物并开始播放。

    默认视口看的是场景原点附近，而我们的 mesh 是**世界坐标**的（见
    [[moe-mesh-is-world-space-so-scale-breaks-attach]]），人物离原点很远，
    不取景的话画面里只有一角。
    """
    meshes = [o for o in bpy.data.objects
              if o.type == "MESH" and not o.rigid_body]
    for o in bpy.data.objects:
        o.select_set(False)
    for o in meshes or [rig]:
        o.select_set(True)
    bpy.context.view_layer.objects.active = (meshes or [rig])[0]
    for w in bpy.context.window_manager.windows:
        for ar in w.screen.areas:
            if ar.type != "VIEW_3D":
                continue
            rgn = next((r for r in ar.regions if r.type == "WINDOW"), None)
            with bpy.context.temp_override(window=w, area=ar, region=rgn):
                try:
                    bpy.ops.view3d.view_selected()
                    bpy.ops.view3d.view_axis(type="FRONT")
                    bpy.ops.view3d.view_selected()
                except Exception as e:                       # noqa: BLE001
                    print(f"[view] 取景失败（{e}）—— 手动按小键盘 . 对准")
    try:
        bpy.ops.screen.animation_play()
        print("[view] 已开始播放（空格暂停）")
    except Exception as e:                                   # noqa: BLE001
        print(f"[view] 自动播放失败（{e}）—— 手动按空格")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--anim", required=True, help="基名（不带 .anim）或路径")
    ap.add_argument("--no-translation", action="store_true",
                    help="只灌旋转。裙子的运动大半在位移里，一般不要加")
    argv = sys.argv
    a = ap.parse_args(argv[argv.index("--") + 1:] if "--" in argv else [])

    p = Path(a.anim)
    if not p.is_file():
        p = MP.find(a.anim if a.anim.endswith(".anim") else a.anim + ".anim")
    print(f"[view] {p}")

    meta, bones, samp = RV.load_anim(p)
    F, ts, qs = RV.unpack_samples(meta, bones, samp)
    rest = {b["name"]: b for b in bones}

    rig = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    if rig.animation_data is None:
        rig.animation_data_create()
    act = bpy.data.actions.new(f"view_{p.stem}")
    rig.animation_data.action = act
    pb = rig.pose.bones

    # **驱动全部骨**（含物理骨）—— 物理烘焙写进去的通道就在它们身上
    n_q = n_t = 0
    for n, b in rest.items():
        if n not in pb:
            continue
        pbn = pb[n]
        pbn.rotation_mode = "QUATERNION"
        rq = mathutils.Quaternion(a2b_quat(RV.q_norm(b["q"])))
        rt = a2b_vec(b.get("t", (0.0, 0.0, 0.0)))
        has_q, has_t = n in qs, (n in ts and not a.no_translation)
        n_q += has_q
        n_t += has_t
        for f in range(F):
            q = RV.q_norm(qs[n][f]) if has_q else RV.q_norm(b["q"])
            pbn.rotation_quaternion = rq.inverted() @ mathutils.Quaternion(
                a2b_quat(q))
            pbn.keyframe_insert("rotation_quaternion", frame=f)
            if has_t:
                # 平移的 basis 是「当前 t 减去 rest t」，再转到骨局部空间。
                # 骨局部 = rest 朝向的逆 × 世界增量（.anim 的 t 与 q 共用
                # 父骨当帧参考系，见 [[anim-q-and-t-share-parent-current-frame]]）
                d = a2b_vec(ts[n][f]) - rt
                pbn.location = rq.inverted() @ d
                pbn.keyframe_insert("location", frame=f)

    scn = bpy.context.scene
    scn.frame_start, scn.frame_end = 0, max(F - 1, 1)
    scn.frame_set(0)
    fps = meta.get("fps", [30.0])[0]
    scn.render.fps = max(1, int(round(fps)))
    if not bpy.app.background:
        _frame_and_play(rig)
    print(f"[view] {F} 帧 fps={fps:.3f}  骨 {len(rest)}  "
          f"带旋转通道 {n_q}  带位移通道 {n_t}")
    if n_q < 100:
        print(f"[view] !! 只有 {n_q} 根骨带旋转通道 —— 这条 .anim 很可能"
              f"**还没烘物理**（干净的重定向产物只有几十根身体骨有通道）")


main()
