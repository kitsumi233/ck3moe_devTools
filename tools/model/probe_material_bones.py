"""探测每个材质的主导骨骼，判定它该挂 head 还是 torso/clothes 实体。

为什么需要:
  阿斯缇亚有 `Cth1-Hairpin`（发夹）和 `Cth1-Goggles`（护目镜）两个
  **头部穿戴物**。材质名带 `Cth`（衣服前缀）会被合同判成 clothes，
  但 CK3 的衣服 entity 靠 `shared_pose_entity = torso` 继承**躯干**姿势 ——
  挂在头骨上的东西放进 clothes 实体，头一转就会脱开。

  芙洛伦没有头部穿戴物（clothes 全是 Cloth1A-C/Cloth2A-B/Socks），
  所以这是阿斯缇亚引入的新情况，靠材质名判不出来，必须看实际蒙皮。

判据: 该材质顶点的权重按骨骼汇总，看主导骨落在头链还是躯干链。
  头链 = 頭 / 首 及其子孙（MMD 日文骨名）

用法:
  blender.exe -b --python tools/model/probe_material_bones.py -- --model gfl2_asteria
"""
import io
import json
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
# stdout 转 utf-8。**用 reconfigure 不用 TextIOWrapper** —— 后者每次都新建一个
# 包装对象，多个模块各包一层时外层被 GC 会关掉共享的 buffer，于是后面任何
# print 都抛 `ValueError: I/O operation on closed file`（动画其实已经写完了，
# 只是收尾的 print 炸掉，看起来像整条失败）。reconfigure 原地改编码，天然幂等。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
MODEL = "gfl2_asteria"
for _i, _a in enumerate(_argv):
    if _a == "--model" and _i + 1 < len(_argv):
        MODEL = _argv[_i + 1]

inv = json.loads((ROOT / "work" / "pmx_skeleton_inventory" / f"{MODEL}.json")
                 .read_text(encoding="utf-8"))
PMX = Path(inv["source_file"])

print(f"[probe] {MODEL}\n  {PMX}")

bpy.ops.wm.read_factory_settings(use_empty=True)
try:
    bpy.ops.preferences.addon_enable(module="bl_ext.user_default.mmd_tools")
except Exception:
    pass
bpy.ops.mmd_tools.import_model(filepath=str(PMX), types={"MESH", "ARMATURE"},
                               scale=1.0, clean_model=False, log_level="ERROR")

arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
meshes = [o for o in bpy.data.objects if o.type == "MESH" and o.data.polygons]
print(f"  骨 {len(arm.data.bones)}  网格 {len(meshes)}")

# ---- 头链：頭/首 的全部子孙
HEAD_ROOTS = ["頭", "首"]
head_chain = set()


def collect(b):
    head_chain.add(b.name)
    for c in b.children:
        collect(c)


for rn in HEAD_ROOTS:
    b = arm.data.bones.get(rn)
    if b:
        collect(b)
print(f"  头链骨 {len(head_chain)} 根（根 {HEAD_ROOTS}）")

rows = []
for obj in meshes:
    gname = {i: vg.name for i, vg in enumerate(obj.vertex_groups)}
    # 材质 → {骨名: 权重和}
    per_mat = {}
    # 顶点 → 材质集合（按面归属）
    vmat = {}
    for poly in obj.data.polygons:
        mi = poly.material_index
        m = obj.data.materials[mi] if mi < len(obj.data.materials) else None
        if not m:
            continue
        for vi in poly.vertices:
            vmat.setdefault(vi, set()).add(m.name)
    for vi, mats in vmat.items():
        v = obj.data.vertices[vi]
        for g in v.groups:
            if g.weight <= 1e-6:
                continue
            bn = gname.get(g.group)
            # mmd_tools 会建 `mmd_edge_scale` / `mmd_vertex_order` 两个
            # **非骨骼**顶点组来存描边宽度和顶点序。它们每个顶点都有值，
            # 不筛掉会把所有比例稀释到 ~50%，头链占比全部落在 49% 附近，
            # 于是「头 vs 躯干」完全分不出来（第一版就是这么错的）。
            if bn is None or bn not in arm.data.bones:
                continue
            for mn in mats:
                per_mat.setdefault(mn, {})
                per_mat[mn][bn] = per_mat[mn].get(bn, 0.0) + g.weight
    for mn, acc in per_mat.items():
        tot = sum(acc.values())
        if tot <= 0:
            continue
        h = sum(w for b, w in acc.items() if b in head_chain)
        top = sorted(acc.items(), key=lambda kv: -kv[1])[:3]
        rows.append((mn, tot, h / tot, top))

print(f"\n{'材质':<22}{'头链占比':>9}  主导骨（前3）")
print("-" * 78)
for mn, tot, ratio, top in sorted(rows, key=lambda r: -r[2]):
    tops = ", ".join(f"{b}:{w / tot:.0%}" for b, w in top)
    flag = "→HEAD" if ratio >= 0.5 else ""
    print(f"{mn:<22}{ratio:>8.1%}  {tops}  {flag}")

out = ROOT / "work" / "pmx_skeleton_inventory" / f"{MODEL}_material_bones.json"
out.write_text(json.dumps(
    {mn: {"head_ratio": round(r, 4),
          "top_bones": [[b, round(w / t, 4)] for b, w in tp]}
     for mn, t, r, tp in rows}, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\n  写出 {out}")
