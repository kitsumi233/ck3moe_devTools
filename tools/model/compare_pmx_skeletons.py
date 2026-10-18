"""汇总全部 PMX 骨架报告，做跨模型比较，判断能否抽象通用二次元骨架。

纯 Python，不需要 Blender。

输出:
  work/pmx_skeleton_inventory/comparison.json
  work/pmx_skeleton_inventory/SKELETON_REPORT.md

比较维度（按 HANDOFF 要求，不只看骨名）:
  1. MMD 标准核心骨覆盖率
  2. 父级签名一致率（同名骨的 parent 是否相同）
  3. 左右对称率
  4. 关键骨段长度归一化差异（除以身高，消除缩放）
  5. rest 骨向量方向差异
  6. deform 权重覆盖
  7. 系列内 / 跨系列聚类

用法: python tools/model/compare_pmx_skeletons.py
"""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INV = ROOT / "work" / "pmx_skeleton_inventory"

# slug -> 系列。武器不参与人体骨架比较。
SERIES = {
    "endfield_eldera": "终末地",
    "endfield_perlica": "终末地",
    "endfield_laurentina": "终末地",
    "endfield_fluorite": "终末地",
    "gfl2_asteria": "少前2",
    "gfl2_faelynn": "少前2",
    "gfl2_florence": "少前2",
    "gfl2_cheyanne": "少前2",
    "gfl2_sakura": "少前2",
    "wuwa_baizhi": "鸣潮",
    "wuwa_rover_f": "鸣潮",
    "wuwa_yangyang": "鸣潮",
}
WEAPONS = {"weapon_gohei", "weapon_ofuda"}

# 用于父级签名与长度比较的核心骨
CORE = [
    "全ての親", "センター", "グルーブ", "腰",
    "上半身", "上半身2", "首", "頭",
    "左肩", "左腕", "左ひじ", "左手首",
    "右肩", "右腕", "右ひじ", "右手首",
    "下半身",
    "左足", "左ひざ", "左足首", "左つま先",
    "右足", "右ひざ", "右足首", "右つま先",
]

# 归一化后比较的骨段（用身高 z_span 归一）
MEASURED_SEGMENTS = [
    ("腰", "上半身"), ("上半身", "上半身2"), ("上半身2", "首"), ("首", "頭"),
    ("左肩", "左腕"), ("左腕", "左ひじ"), ("左ひじ", "左手首"),
    ("左足", "左ひざ"), ("左ひざ", "左足首"),
]


def load():
    out = {}
    for p in sorted(INV.glob("*.json")):
        if p.name == "comparison.json":
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  !! 读取失败 {p.name}: {e}")
            continue
        if not d.get("import_ok"):
            print(f"  !! 跳过导入失败的 {p.stem}")
            continue
        out[p.stem] = d
    return out


def main_armature(rep):
    """取骨骼最多的骨架作为主人体骨架（排除舌头等辅助骨架）。"""
    arms = rep.get("armatures", [])
    if not arms:
        return None
    return max(arms, key=lambda a: a.get("bone_count", 0))


def bone_map(arm):
    return {b["name"]: b for b in arm.get("bones", [])}


def norm(v):
    return math.sqrt(sum(c * c for c in v))


def sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def unit(v):
    n = norm(v)
    return [c / n for c in v] if n > 1e-9 else [0.0, 0.0, 0.0]


def angle_between(a, b):
    ua, ub = unit(a), unit(b)
    d = max(-1.0, min(1.0, sum(ua[i] * ub[i] for i in range(3))))
    return math.degrees(math.acos(d))


def main():
    reports = load()
    chars = {k: v for k, v in reports.items() if k not in WEAPONS}
    weapons = {k: v for k, v in reports.items() if k in WEAPONS}
    print(f"载入 {len(reports)} 份报告：角色 {len(chars)}，武器 {len(weapons)}")

    result = {
        "model_count": len(reports),
        "character_count": len(chars),
        "weapon_count": len(weapons),
        "weapons_excluded": sorted(weapons),
        "per_model": {},
        "series": {},
    }

    # ------------------------------------------------------------ 逐模型摘要
    rows = []
    for slug, rep in sorted(chars.items()):
        arm = main_armature(rep)
        if not arm:
            continue
        cov = arm["mmd_core_coverage"]
        sym = arm["symmetry"]
        skin = arm["skin_coverage"]
        cls = arm.get("class_counts", {})
        z = arm.get("z_span", 0) or 1.0
        entry = {
            "series": SERIES.get(slug, "?"),
            "model_name": rep.get("mmd_model_name", "") or rep.get("source_name"),
            "bone_count": arm["bone_count"],
            "max_depth": arm["max_depth"],
            "z_span": z,
            "core_ratio": cov["ratio"],
            "core_missing": cov["missing"],
            "deform_candidates": cls.get("deform_candidate", 0),
            "physics_bones": cls.get("physics", 0),
            "twist_bones": cls.get("twist", 0),
            "ik_bones": cls.get("ik", 0),
            "skinned_bones": skin["bones_with_weights"],
            "skin_ratio": skin["ratio"],
            "unpaired_left": sym["left_bones_without_pair"],
            "vertices": rep["geometry"]["total_vertices"],
            "polygons": rep["geometry"]["total_polygons"],
            "materials": rep["counts"]["materials"],
            "images": rep["counts"]["images"],
        }
        result["per_model"][slug] = entry
        rows.append((slug, entry))

    # ------------------------------------------------------------ 父级签名一致率
    # 对每根核心骨，统计各模型给出的 parent 是否一致
    parent_votes = {}
    for slug, rep in sorted(chars.items()):
        arm = main_armature(rep)
        if not arm:
            continue
        bm = bone_map(arm)
        for cb in CORE:
            b = bm.get(cb)
            if not b:
                continue
            parent_votes.setdefault(cb, {}).setdefault(b["parent"], []).append(slug)

    parent_consistency = {}
    for cb, votes in sorted(parent_votes.items()):
        total = sum(len(v) for v in votes.values())
        best_parent, best_models = max(votes.items(), key=lambda kv: len(kv[1]))
        parent_consistency[cb] = {
            "models_having_bone": total,
            "dominant_parent": best_parent,
            "dominant_count": len(best_models),
            "agreement": round(len(best_models) / total, 4) if total else 0,
            "variants": {str(k): sorted(v) for k, v in votes.items()},
        }
    result["parent_signature"] = parent_consistency

    # ------------------------------------------------------------ 归一化骨段长度
    seg_table = {}
    for slug, rep in sorted(chars.items()):
        arm = main_armature(rep)
        if not arm:
            continue
        bm = bone_map(arm)
        z = arm.get("z_span") or 1.0
        for a, b in MEASURED_SEGMENTS:
            ba, bb = bm.get(a), bm.get(b)
            key = f"{a}→{b}"
            if not ba or not bb:
                seg_table.setdefault(key, {})[slug] = None
                continue
            d = norm(sub(bb["head"], ba["head"])) / z
            seg_table.setdefault(key, {})[slug] = round(d, 5)

    seg_stats = {}
    for key, per in sorted(seg_table.items()):
        vals = [v for v in per.values() if v is not None]
        if len(vals) < 2:
            seg_stats[key] = {"values": per, "note": "样本不足"}
            continue
        mean = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
        seg_stats[key] = {
            "values": per,
            "present": len(vals),
            "mean": round(mean, 5),
            "stdev": round(sd, 5),
            "cv": round(sd / mean, 4) if mean > 1e-9 else None,  # 变异系数
            "min": round(min(vals), 5),
            "max": round(max(vals), 5),
            "spread_ratio": round(max(vals) / min(vals), 4) if min(vals) > 1e-9 else None,
        }
    result["normalized_segments"] = seg_stats

    # ------------------------------------------------------------ rest 骨向方向
    dir_table = {}
    for slug, rep in sorted(chars.items()):
        arm = main_armature(rep)
        if not arm:
            continue
        bm = bone_map(arm)
        for cb in CORE:
            b = bm.get(cb)
            if not b:
                continue
            v = sub(b["tail"], b["head"])
            if norm(v) < 1e-9:
                continue
            dir_table.setdefault(cb, {})[slug] = unit(v)

    dir_stats = {}
    for cb, per in sorted(dir_table.items()):
        slugs = sorted(per)
        if len(slugs) < 2:
            continue
        # 以第一个模型为参考，算其余模型的夹角
        ref = per[slugs[0]]
        angles = {s: round(angle_between(ref, per[s]), 2) for s in slugs[1:]}
        vals = list(angles.values())
        dir_stats[cb] = {
            "reference_model": slugs[0],
            "angles_deg": angles,
            "max_angle": round(max(vals), 2) if vals else 0,
            "mean_angle": round(sum(vals) / len(vals), 2) if vals else 0,
        }
    result["rest_direction"] = dir_stats

    # ------------------------------------------------------------ 系列聚类
    for series in sorted(set(SERIES.values())):
        members = [s for s in chars if SERIES.get(s) == series]
        if not members:
            continue
        bcs = [result["per_model"][m]["bone_count"] for m in members if m in result["per_model"]]
        cores = [result["per_model"][m]["core_ratio"] for m in members if m in result["per_model"]]
        # 系列内核心骨父级是否完全一致
        sigs = []
        for m in members:
            arm = main_armature(chars[m])
            if not arm:
                continue
            bm = bone_map(arm)
            sig = tuple((cb, bm[cb]["parent"]) for cb in CORE if cb in bm)
            sigs.append((m, sig))
        identical = len({s for _, s in sigs}) == 1 if sigs else False
        result["series"][series] = {
            "members": sorted(members),
            "count": len(members),
            "bone_count_range": [min(bcs), max(bcs)] if bcs else None,
            "core_ratio_range": [min(cores), max(cores)] if cores else None,
            "core_parent_signature_identical": identical,
            "distinct_signatures": len({s for _, s in sigs}) if sigs else 0,
        }

    # ------------------------------------------------------------ 结论
    all_core_full = all(e["core_ratio"] == 1.0 for e in result["per_model"].values())
    parent_full = [cb for cb, d in parent_consistency.items() if d["agreement"] == 1.0]
    parent_partial = [cb for cb, d in parent_consistency.items() if d["agreement"] < 1.0]
    high_var = [k for k, d in seg_stats.items()
                if isinstance(d.get("cv"), float) and d["cv"] > 0.15]
    big_dir = [k for k, d in dir_stats.items() if d["max_angle"] > 30]

    result["findings"] = {
        "all_models_full_mmd_core": all_core_full,
        "core_bones_with_unanimous_parent": sorted(parent_full),
        "core_bones_with_parent_conflict": sorted(parent_partial),
        "segments_with_high_variation_cv_gt_0.15": sorted(high_var),
        "core_bones_with_rest_dir_gt_30deg": sorted(big_dir),
    }

    out_json = INV / "comparison.json"
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {out_json}")

    # ------------------------------------------------------------ Markdown 报告
    md = []
    md.append("# PMX 骨架跨模型比较\n")
    md.append(f"样本：{len(chars)} 个角色模型（排除 {len(weapons)} 个武器：{', '.join(sorted(weapons))}）\n")
    md.append("由 `tools/model/compare_pmx_skeletons.py` 生成，数据源 `work/pmx_skeleton_inventory/*.json`。\n")

    md.append("\n## 逐模型概览\n")
    md.append("| 模型 | 系列 | 总骨 | deform | 物理 | 扭转 | 蒙皮骨 | 核心骨 | 深度 | 身高 | 顶点 | 面 |")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for slug, e in sorted(result["per_model"].items(), key=lambda kv: (kv[1]["series"], kv[0])):
        md.append(
            f"| `{slug}` | {e['series']} | {e['bone_count']} | {e['deform_candidates']} | "
            f"{e['physics_bones']} | {e['twist_bones']} | {e['skinned_bones']} | "
            f"{int(e['core_ratio']*25)}/25 | {e['max_depth']} | {e['z_span']:.2f} | "
            f"{e['vertices']:,} | {e['polygons']:,} |"
        )

    md.append("\n## MMD 核心骨覆盖\n")
    if all_core_full:
        md.append("**全部角色模型 25/25 完整覆盖 MMD 标准核心人体骨。**\n")
    else:
        md.append("存在缺失：\n")
        for slug, e in sorted(result["per_model"].items()):
            if e["core_ratio"] < 1.0:
                md.append(f"- `{slug}`：缺 {', '.join(e['core_missing'])}")

    md.append("\n## 父级签名一致性\n")
    md.append("同名核心骨在各模型中的 parent 是否一致。不一致说明层级结构有差异，")
    md.append("不能只按骨名映射。\n")
    md.append("| 核心骨 | 拥有该骨的模型数 | 主流 parent | 一致率 |")
    md.append("|---|---|---|---|")
    for cb in CORE:
        d = parent_consistency.get(cb)
        if not d:
            continue
        flag = "" if d["agreement"] == 1.0 else " ⚠"
        md.append(f"| `{cb}` | {d['models_having_bone']} | `{d['dominant_parent']}` | "
                  f"{d['agreement']*100:.0f}%{flag} |")
    if parent_partial:
        md.append("\n父级冲突详情：\n")
        for cb in sorted(parent_partial):
            d = parent_consistency[cb]
            md.append(f"- `{cb}`：")
            for parent, models in sorted(d["variants"].items(), key=lambda kv: -len(kv[1])):
                md.append(f"  - parent=`{parent}` → {', '.join(models)}")

    md.append("\n## 归一化骨段长度（除以身高）\n")
    md.append("变异系数 CV 越小说明各模型体型比例越接近。CV > 0.15 视为差异显著。\n")
    md.append("| 骨段 | 样本 | 均值 | 标准差 | CV | 最小 | 最大 | 极差比 |")
    md.append("|---|---|---|---|---|---|---|---|")
    for key, d in seg_stats.items():
        if "note" in d:
            md.append(f"| `{key}` | - | - | - | - | - | - | {d['note']} |")
            continue
        cv = d["cv"]
        flag = " ⚠" if isinstance(cv, float) and cv > 0.15 else ""
        md.append(f"| `{key}` | {d['present']} | {d['mean']} | {d['stdev']} | "
                  f"{cv}{flag} | {d['min']} | {d['max']} | {d['spread_ratio']} |")

    md.append("\n## rest 骨向方向差异\n")
    md.append("以某一模型为参考，其余模型同名骨的 head→tail 向量夹角。夹角大说明 rest pose 轴向不同，")
    md.append("直接套用动画会错。\n")
    md.append("| 核心骨 | 参考模型 | 平均夹角° | 最大夹角° |")
    md.append("|---|---|---|---|")
    for cb in CORE:
        d = dir_stats.get(cb)
        if not d:
            continue
        flag = " ⚠" if d["max_angle"] > 30 else ""
        md.append(f"| `{cb}` | `{d['reference_model']}` | {d['mean_angle']} | {d['max_angle']}{flag} |")

    md.append("\n## 系列聚类\n")
    md.append("| 系列 | 成员数 | 总骨范围 | 核心骨覆盖 | 核心父级签名完全一致 | 不同签名数 |")
    md.append("|---|---|---|---|---|---|")
    for s, d in sorted(result["series"].items()):
        cr = d["core_ratio_range"]
        md.append(f"| {s} | {d['count']} | {d['bone_count_range'][0]}–{d['bone_count_range'][1]} | "
                  f"{cr[0]:.2f}–{cr[1]:.2f} | {'是' if d['core_parent_signature_identical'] else '否'} | "
                  f"{d['distinct_signatures']} |")

    md.append("\n## 结论要点\n")
    md.append(f"- 核心骨全覆盖：{'是' if all_core_full else '否'}")
    md.append(f"- 父级完全一致的核心骨：{len(parent_full)}/{len(parent_consistency)}")
    if parent_partial:
        md.append(f"- 父级有冲突的核心骨：{', '.join(f'`{c}`' for c in sorted(parent_partial))}")
    if high_var:
        md.append(f"- 体型比例差异显著的骨段（CV>0.15）：{', '.join(f'`{k}`' for k in sorted(high_var))}")
    else:
        md.append("- 所有测量骨段的归一化长度 CV ≤ 0.15，体型比例接近")
    if big_dir:
        md.append(f"- rest 轴向差异 >30° 的核心骨：{', '.join(f'`{k}`' for k in sorted(big_dir))}")
    else:
        md.append("- 所有核心骨 rest 轴向差异 ≤30°")

    out_md = INV / "SKELETON_REPORT.md"
    out_md.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"已写入 {out_md}")


main()
