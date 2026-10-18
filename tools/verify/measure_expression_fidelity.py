"""量化表情烘焙的保真度：shapekey 真实位移 vs 烘焙后骨骼能重建的位移。

Blender 后台脚本，**不改任何文件**，只测量并写报告。用户报「表情有点淡」，
这个脚本给出「淡多少、淡在哪」的数字。

用法:
  blender.exe -b work/blenderProj/florence_poc.blend ^
      --python tools/verify/measure_expression_fidelity.py

## 为什么能离线量、不必进游戏

蒙皮是线性混合（LBS）: 顶点 = Σ wᵢ·Mᵢ·v。Blender 和 CK3 用同一个公式，
所以重建率、权重和、影响格数这些纯数值判据在两边一致。
（本地渲图看不见的是另一类问题：骨数不匹配导致 entity 不渲染、
alpha-to-coverage 的表现、光照 —— 那些必须进游戏 grep 日志。）

## 测什么

对每个语义槽:
  d_i  = shapekey 的真实位移（Basis → key_block）
  T    = 现算法选的主方向（位移最大顶点的方向）
  w_i  = clamp(dot(d_i,T)/|T|², 0, 1) / share   ← 复刻 bake 脚本的式子
  r_i  = w_i · T                                 ← 骨骼实际能给出的位移

  重建率 = Σ|r_i| / Σ|d_i|          （能做出原意的百分之几）
  反向丢弃 = #{i : dot(d_i,T) <= 0}  （被焊死在 head 上的顶点数）
  残差角 = angle(Σr, Σd)

同时测「若改用最小二乘主轴（SVD 第一主成分）而非最大位移顶点方向」
能提升到多少 —— 这是修法之一，先量收益再决定动不动。

以及影响格占用: 每顶点被几根 fx 骨影响。PDX 上限 4 格，head 占 1 格，
所以 fx 最多 3 格。已占满 3 格的顶点无法再加反向骨 —— 这是修法的硬约束。
"""
import json
import sys
from collections import Counter
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import moe_expression_contract as C  # noqa: E402

OUT = ROOT / "work" / "moe_anim_bake" / "fidelity_report.json"
# 洛茜/洁尔佩塔各有自己的 bake 目录：`-- --out-dir work/lau_anim_bake`
if "--" in sys.argv:
    _argv = sys.argv[sys.argv.index("--") + 1:]
    if "--out-dir" in _argv:
        OUT = Path(_argv[_argv.index("--out-dir") + 1]) / "fidelity_report.json"
MIN_DISP = 1e-4
WEIGHT_EPS = 1e-4
log_lines = []


def log(m):
    print(m)
    log_lines.append(str(m))


def head_mesh_objects():
    """取参与表情的网格。与 bake 脚本同源：带 shapekey 的网格对象。"""
    out = []
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        sk = o.data.shape_keys
        if sk and len(sk.key_blocks) > 1:
            out.append(o)
    return out


def principal_axis(deltas):
    """位移集合的第一主成分（最小二乘主轴），用 3x3 协方差的幂迭代求。

    不引 numpy：Blender 自带 numpy，但这里维度是 3，幂迭代 30 步足够，
    也免得依赖 Blender 的 numpy 版本。
    """
    cxx = cxy = cxz = cyy = cyz = czz = 0.0
    for d in deltas:
        cxx += d.x * d.x; cxy += d.x * d.y; cxz += d.x * d.z
        cyy += d.y * d.y; cyz += d.y * d.z; czz += d.z * d.z
    # 初值取最大位移方向，避免落在鞍点
    v = max(deltas, key=lambda d: d.length).normalized()
    for _ in range(30):
        nx = cxx * v.x + cxy * v.y + cxz * v.z
        ny = cxy * v.x + cyy * v.y + cyz * v.z
        nz = cxz * v.x + cyz * v.y + czz * v.z
        n = Vector((nx, ny, nz))
        if n.length < 1e-12:
            return v
        v = n.normalized()
    return v


def fit_scale(deltas, axis):
    """给定单位主轴，求使 Σ|w·axis - d| 最小的幅度（投影均值的最大值侧）。

    骨骼只能给一个平移量 T，各顶点靠权重 w∈[0,1] 缩放它，
    所以 |T| 应取投影的最大值（让权重铺满 [0,1]），与 bake 脚本一致。
    """
    m = 0.0
    for d in deltas:
        p = d.dot(axis)
        if p > m:
            m = p
    return m


def measure_slot(objs, keyname, share):
    """返回该槽在所有网格上的合并测量。"""
    idxs_all, deltas_all = [], []
    for o in objs:
        sk = o.data.shape_keys
        if not sk or keyname not in sk.key_blocks:
            continue
        basis = sk.key_blocks["Basis"].data
        kb = sk.key_blocks[keyname].data
        for i in range(len(basis)):
            d = kb[i].co - basis[i].co
            if d.length > MIN_DISP:
                idxs_all.append((o.name, i))
                deltas_all.append(d.copy())
    if not deltas_all:
        return None

    # --- 现算法：T = 位移最大顶点的方向 ---
    T_cur = max(deltas_all, key=lambda d: d.length).copy()
    t2 = T_cur.length_squared

    def eval_axis(T):
        """给定平移向量 T，算重建率/反向丢弃/残差角。"""
        tt = T.length_squared
        if tt < 1e-12:
            return None
        sum_d = Vector((0, 0, 0))
        sum_r = Vector((0, 0, 0))
        mag_d = mag_r = 0.0
        back = 0
        for d in deltas_all:
            w = d.dot(T) / tt
            if w <= 0:
                back += 1
                w = 0.0
            else:
                w = min(1.0, w) / share
            r = T * w
            sum_d += d
            sum_r += r
            mag_d += d.length
            mag_r += r.length
        rate = (mag_r / mag_d) if mag_d else 0.0
        ang = 0.0
        if sum_d.length > 1e-9 and sum_r.length > 1e-9:
            ang = sum_d.angle(sum_r) * 57.29577951308232
        return {
            "verts": len(deltas_all),
            "back_discarded": back,
            "rebuild_rate": round(rate, 4),
            "resid_angle_deg": round(ang, 2),
            "T_len": round(T.length, 4),
        }

    cur = eval_axis(T_cur)

    # --- 候选修法 A：最小二乘主轴 ---
    ax = principal_axis(deltas_all)
    T_pca = ax * fit_scale(deltas_all, ax)
    pca = eval_axis(T_pca)

    # --- 候选修法 B：正反双骨（正向一根 + 反向一根）---
    # 反向骨承接 dot(d,T)<=0 的那批顶点，主轴取它们自己的主成分。
    neg = [d for d in deltas_all if d.dot(T_pca) <= 0]
    twoway = None
    if neg:
        ax2 = principal_axis(neg)
        T2 = ax2 * fit_scale(neg, ax2)
        # 正向骨仍按 PCA 轴，只算正半空间；反向骨算负半空间
        mag_d = sum(d.length for d in deltas_all)
        mag_r = 0.0
        for d in deltas_all:
            w1 = d.dot(T_pca) / T_pca.length_squared if T_pca.length_squared > 1e-12 else 0
            if w1 > 0:
                mag_r += (T_pca * (min(1.0, w1) / share)).length
            else:
                w2 = d.dot(T2) / T2.length_squared if T2.length_squared > 1e-12 else 0
                if w2 > 0:
                    mag_r += (T2 * (min(1.0, w2) / share)).length
        twoway = {
            "rebuild_rate": round((mag_r / mag_d) if mag_d else 0.0, 4),
            "neg_verts": len(neg),
        }

    # --- 候选修法 C：反向骨 + 双侧合并（share 强制为 1）---
    # 双侧形态键旧做法把权重 /2 分给 _l/_r 两骨，幅度直接减半。
    # 合并成一根骨给满权重后，share=1。这里把 B 重算一遍看叠加收益。
    combo = None
    if neg and share > 1:
        mag_d = sum(d.length for d in deltas_all)
        mag_r = 0.0
        ax2 = principal_axis(neg)
        T2 = ax2 * fit_scale(neg, ax2)
        for d in deltas_all:
            w1 = d.dot(T_pca) / T_pca.length_squared if T_pca.length_squared > 1e-12 else 0
            if w1 > 0:
                mag_r += (T_pca * min(1.0, w1)).length      # share=1
            else:
                w2 = d.dot(T2) / T2.length_squared if T2.length_squared > 1e-12 else 0
                if w2 > 0:
                    mag_r += (T2 * min(1.0, w2)).length     # share=1
        combo = {"rebuild_rate": round((mag_r / mag_d) if mag_d else 0.0, 4)}
    elif share == 1 and twoway:
        combo = dict(twoway)      # 非双侧键，C 等于 B

    return {"current": cur, "pca": pca, "twoway": twoway, "combo": combo,
            "share": share, "shapekey": keyname}


def main():
    log("=" * 68)
    log("表情烘焙保真度测量（只读，不改文件）")
    log("=" * 68)

    objs = head_mesh_objects()
    log(f"\n带 shapekey 的网格: {[o.name for o in objs]}")

    keys = [k for o in objs for k in (o.data.shape_keys.key_blocks.keys()
                                      if o.data.shape_keys else [])]
    scheme, _ = C.detect_naming_scheme(keys)
    bindings, missing = C.resolve_bindings(keys, scheme)
    log(f"命名体系: {scheme}   绑定 {len(bindings)} 槽   缺 {len(missing)} 槽")

    share_count = Counter(bindings.values())

    log("\n" + "-" * 68)
    log(f"{'语义槽':<20}{'顶点':>6}{'反向丢':>7}{'现重建':>8}"
        f"{'PCA':>7}{'+反向骨':>9}{'+合并双侧':>11}{'share':>6}{'残差角':>8}")
    log("-" * 78)

    rows = {}
    for slot, keyname in sorted(bindings.items()):
        r = measure_slot(objs, keyname, share_count[keyname])
        if not r:
            continue
        rows[slot] = r
        c, p, t, cb = r["current"], r["pca"], r["twoway"], r["combo"]
        log(f"{slot:<20}{c['verts']:>6}{c['back_discarded']:>7}"
            f"{c['rebuild_rate']*100:>7.1f}%{p['rebuild_rate']*100:>6.1f}%"
            f"{(t['rebuild_rate']*100 if t else 0):>8.1f}%"
            f"{(cb['rebuild_rate']*100 if cb else 0):>10.1f}%"
            f"{r['share']:>6}{c['resid_angle_deg']:>7.1f}°")

    # ---- 影响格占用：加反向骨的硬约束 ----
    log("\n" + "-" * 68)
    log("fx 影响格占用（PDX 上限 4，head 占 1，故 fx 最多 3）")
    log("-" * 68)
    fx_names = set(C.SEMANTIC_SLOTS.values())
    slot_hist = Counter()
    combos = Counter()
    for o in objs:
        gidx = {g.index: g.name for g in o.vertex_groups}
        for v in o.data.vertices:
            got = [gidx[g.group] for g in v.groups
                   if gidx.get(g.group) in fx_names and g.weight > WEIGHT_EPS]
            slot_hist[len(got)] += 1
            if len(got) >= 3:
                combos[" + ".join(sorted(got))] += 1
    for n in sorted(slot_hist):
        log(f"  占 {n} 格: {slot_hist[n]:>6} 顶点")
    full = sum(c for n, c in slot_hist.items() if n >= 3)
    log(f"  已占满(>=3): {full} 顶点 —— 这些顶点无法再加反向骨")
    log("\n  占满的组合 top10:")
    for k, n in combos.most_common(10):
        log(f"    {n:>5}  {k}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "naming_scheme": scheme,
        "slots": rows,
        "influence_slots": dict(slot_hist),
        "full_combos": dict(combos.most_common(30)),
        "full_vert_count": full,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n报告: {OUT}")
    (OUT.parent / "fidelity_log.txt").write_text(
        "\n".join(log_lines), encoding="utf-8")


main()
