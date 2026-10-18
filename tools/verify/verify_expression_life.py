"""量表情源与 head 产物里「活」的信号：眼球位移、眨眼次数、嘴张幅、接缝。

为什么单独成工具：2026-09-05 用户实机报「几个表情都很不明显，尤其没有眼球
动画」，而此前的 verify（骨数、接缝、头身同步）全部 PASS —— 那些判据量的
是「不会崩」，不是「看得见」。这里量的是**画面上能不能看到**的量，全部换算
成模型单位后与几何余量比较：

  眼球横向位移 / 虹膜宽    < 0.15 就是「没动」（原版 reading 转 13.5°）
  眨眼峰值 / 全闭          < 0.8 就是「没眨」
  下颌位移（模型单位）     talk 类 < 0.6 就是「嘴没张」（原版 storytelling ~1.1）

用法:
  python tools/verify/verify_expression_life.py                # 三角色 bake 目录
  python tools/verify/verify_expression_life.py --products     # 再抽查部署的 head 产物
"""
import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from graft_spine_into_head_anim import read_anim, unpack   # noqa: E402
import moe_paths as MP                                       # noqa: E402

BAKES = {"flo": ("moe_anim_bake", "moe_"),
         "lau": ("lau_anim_bake", "moe_lau_"),
         "per": ("per_anim_bake", "moe_per_")}

# 三模型虹膜盘宽（实测 mesh），眼球位移除以它得到「移动了几个虹膜」
IRIS_W = {"flo": 1.76, "lau": 2.15, "per": 2.08}


def parent_frames(mesh_path):
    """{骨名: 父骨的 bone→world 3x3}。.anim 的 t 在父骨局部系，方向判断必须
    换到世界系（PDX：X 右、Y 上、Z 深度，脸朝 -Z）。tx 是列主序的逆绑定矩阵。"""
    import numpy as np
    import pdx_parse as P
    tree = P.parse(Path(mesh_path).read_bytes())
    names, tx, pa = [], {}, {}
    for pp, k, _kd, v in P.walk(tree):
        if "/skeleton/" not in pp:
            continue
        bn = pp.split("/skeleton/", 1)[1].split("[")[0]
        if k == "ix":
            names.append((int(v[0]), bn))
        elif k == "tx":
            tx[bn] = np.array(v[:12], float).reshape(4, 3).T[:, :3]
        elif k == "pa":
            pa[bn] = int(v[0])
    byix = dict(names)
    out = {}
    for bn in tx:
        p = byix.get(pa.get(bn, -1))
        out[bn] = np.linalg.inv(tx[p]) if p in tx else np.eye(3)
    return out


def t_stats(A, pick, frames=None):
    """{骨: (峰值|Δt|, 峰值Δ向量(世界系), 峰数)}，pick(骨名)->bool。"""
    _Q, T = unpack(A)
    rest = {b["name"]: b for b in A["bones"]}
    out = {}
    for bn, seq in T.items():
        if not pick(bn):
            continue
        r = (rest[bn].get("t") or [0, 0, 0])[:3]
        d = [tuple(a - b for a, b in zip(s, r)) for s in seq]
        if frames is not None and bn in frames:
            R = frames[bn]
            d = [tuple(float(x) for x in R @ v) for v in d]
        mag = [math.sqrt(sum(c * c for c in v)) for v in d]
        i = max(range(len(mag)), key=mag.__getitem__)
        half = max(mag) * 0.5
        peaks = sum(1 for k in range(1, len(mag) - 1)
                    if mag[k] > half and mag[k] >= mag[k - 1] and mag[k] >= mag[k + 1])
        out[bn] = (mag[i], d[i], peaks)
    return out


def report_source(ck, d, pfx):
    rep = json.loads((d / "bake_report.json").read_text(encoding="utf-8"))
    eyes = set(rep.get("eye_bones") or [])
    frames = parent_frames(d / "moe_head.mesh")
    print(f"\n== {ck}  eye_bones={sorted(eyes)}  gain={rep.get('slot_gain')}")
    bad = 0
    for name in ("life", "reading_gaze", "talk", "angry", "happy", "sadness", "shock"):
        f = d / f"moe_{name}.anim"
        if not f.is_file():
            print(f"  {name:<13} 缺文件")
            bad += 1
            continue
        A = read_anim(f)
        st = t_stats(A, lambda b: b in eyes or b.startswith("bn_m_fx_"), frames)
        eye = max((v for b, v in st.items() if b in eyes), key=lambda v: v[0], default=None)
        blink = max((v for b, v in st.items() if "lid_close" in b and not b.endswith("_rev")),
                    key=lambda v: v[0], default=None)
        jaw = st.get("bn_m_fx_jaw")
        brow = max((v for b, v in st.items() if "brow" in b), key=lambda v: v[0], default=None)
        line = f"  {name:<13} {A['nf']:>4}帧"
        if eye:
            ex, ey, ez = eye[1]
            line += (f"  眼球峰 {eye[0]:.3f}（{eye[0] / IRIS_W[ck]:.2f} 虹膜; "
                     f"世界 x{ex:+.2f} 上{ey:+.2f} 深{ez:+.2f}）")
            if abs(ez) > 0.3 * eye[0] + 1e-6:
                line += " !!深度轴有分量"
                bad += 1
            if name in ("life", "reading_gaze") and eye[0] / IRIS_W[ck] < 0.15:
                line += " !!没动"
                bad += 1
        else:
            line += "  眼球 无通道"
            if name in ("life", "reading_gaze"):
                line += " !!"
                bad += 1
        if blink:
            line += f"  眨眼峰 {blink[0]:.2f}×{blink[2]}次"
            if name == "life" and blink[2] < 3:
                line += " !!太少"
                bad += 1
        elif name == "life":
            line += "  眨眼 无通道 !!"
            bad += 1
        if jaw:
            line += f"  下颌峰 {jaw[0]:.2f}×{jaw[2]}次"
            if name == "talk" and jaw[0] < 0.6:
                line += " !!嘴没张"
                bad += 1
        if brow:
            line += f"  眉峰 {brow[0]:.2f}"
        print(line)
    return bad


def report_products(ck, pfx, samples):
    print(f"\n== {ck} 产物抽查")
    d = MP.anims_dir(ck)
    eyes = set(json.loads((ROOT / "work" / BAKES[ck][0] / "bake_report.json")
                          .read_text(encoding="utf-8")).get("eye_bones") or [])
    frames = parent_frames(ROOT / "work" / BAKES[ck][0] / "moe_head.mesh")
    bad = 0
    for base in samples:
        f = d / f"{pfx}head_{base}.anim"
        if not f.is_file():
            print(f"  {base:<32} 缺产物 !!")
            bad += 1
            continue
        A = read_anim(f)
        st = t_stats(A, lambda b: b in eyes or "lid_close" in b or b == "bn_m_fx_jaw",
                     frames)
        eye = max((v for b, v in st.items() if b in eyes), key=lambda v: v[0], default=None)
        blink = max((v for b, v in st.items() if "lid_close" in b and not b.endswith("_rev")),
                    key=lambda v: v[0], default=None)
        jaw = st.get("bn_m_fx_jaw")
        # 接缝：首尾帧眼球/眼睑/下颌差
        _Q, T = unpack(A)
        seam = max((math.dist(T[b][0], T[b][-1]) for b in st if b in T), default=0.0)
        line = (f"  {base:<32} {A['nf']:>4}帧 "
                f"眼球 {eye[0]:.3f}×{eye[2]}次" if eye else f"  {base:<32} {A['nf']:>4}帧 眼球 无 !!")
        if not eye:
            bad += 1
        if blink:
            line += f"  眨眼 {blink[0]:.2f}×{blink[2]}"
        if jaw:
            line += f"  下颌 {jaw[0]:.2f}×{jaw[2]}"
        line += f"  首尾差 {seam:.3f}"
        if seam > 0.05:
            line += " !!接缝"
            bad += 1
        print(line)
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--products", action="store_true")
    ap.add_argument("--chars", default="flo,lau,per")
    a = ap.parse_args()
    bad = 0
    for ck in a.chars.split(","):
        sub, pfx = BAKES[ck]
        d = ROOT / "work" / sub
        if not (d / "bake_report.json").is_file():
            print(f"\n== {ck} 无 bake_report")
            bad += 1
            continue
        bad += report_source(ck, d, pfx)
        if a.products:
            bad += report_products(ck, pfx, [
                "reading_loop", "idle", "throneRoom_conversation1",
                "emotion_angry", "emotion_happy", "AI_bold", "writing"])
    print(f"\n{'[PASS]' if bad == 0 else f'!! {bad} 处问题'}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
