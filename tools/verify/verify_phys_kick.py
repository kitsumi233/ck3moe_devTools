# -*- coding: utf-8 -*-
"""物理产物输出校验：按**骨链**统计踢击率，超阈值的链列出并给指纹。
用法：python tools/verify/verify_phys_kick.py <ck> [anims_dir] [--glob "*idle*"] [--warn 5] [--fail 20]
  anims_dir 默认 = 该角色物理包 anims 目录（moe_paths.phys_anims_dir）。
指标（链内按骨/帧合并，跨动画取最差）：
  kick%  = |Δ²θ| > 0.5°/帧 的帧占比（接触/限位硬弹回的指纹）
  sust%  = 踢击帧里相邻帧也是踢击帧的占比；只描述连续性，不能据此判定碰撞或求解参数根因
  p95/max = 帧间转角步长（度）
退出码：任一链 kick ≥ --fail 时 2，否则 0（链脚本可拿它把关）。
2026-09-09 起因：利塔拉单条 idle 0.56% 而全 idle 系 7.85%，奇塔发尾/飘带/裙子三处抖动是三个机制，
按组的均值看不出是哪条链、也分不出机制。"""
import sys, re, glob, argparse, json
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools/lib"), str(ROOT / "tools/anim")]
import retarget_vanilla_anim as RV
import moe_paths as MP

def qa(left, right): return np.degrees(2 * np.arccos(min(1.0, abs(float(np.dot(left, right))))))
def chain_of(n):
    m = re.match(r"(bn_m_)?(.*?)[_\d]*$", n)
    return (m.group(2) if m else n) or n

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ck"); ap.add_argument("anims_dir", nargs="?")
    ap.add_argument("--glob", default="*idle*"); ap.add_argument("--warn", type=float, default=5.0)
    ap.add_argument("--fail", type=float, default=20.0); ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--json", type=Path, help="Write machine-readable chain metrics")
    a = ap.parse_args()
    d = Path(a.anims_dir) if a.anims_dir else MP.phys_anims_dir(a.ck)
    files = sorted(glob.glob(str(d / f"*{a.glob}.anim")))
    if not files: raise SystemExit(f"**{d} 下没有匹配 {a.glob} 的 .anim**")
    rows = {}   # (chain, file) -> [kick, total, sust, sust_total, p95s, maxs, bones]
    base_dir = MP.require_base_anims(a.ck); n_skip = 0
    for p in files:
        m, b, s = RV.load_anim(p); F, ts, qs = RV.unpack_samples(m, b, s)
        if F < 8: continue
        # 对照无物理的基础动画：物理没改过的骨（原版动作自带的手/腕摆动）不算，否则乐器 idle 大面积假阳性
        bqs = {}
        bp = base_dir / Path(p).name
        if not bp.is_file():
            raise FileNotFoundError(f"Missing unsimulated comparison animation: {bp}")
        m2, b2, s2 = RV.load_anim(str(bp)); _, _, bqs = RV.unpack_samples(m2, b2, s2)
        for n, Q in qs.items():
            Q = np.asarray(Q)
            if n in bqs and np.asarray(bqs[n]).shape == Q.shape and np.allclose(Q, np.asarray(bqs[n]), atol=1e-6):
                n_skip += 1; continue
            norms = np.linalg.norm(Q, axis=1, keepdims=True)
            if not np.isfinite(Q).all() or (norms < 1e-8).any():
                raise ValueError(f'Invalid quaternion: {p} / {n}')
            Q = Q / norms
            th = np.array([qa(Q[i], Q[i - 1]) for i in range(1, F)]); d2 = np.abs(np.diff(th))
            if d2.max() < 1e-4: continue
            k = d2 > 0.5
            r = rows.setdefault((chain_of(n), Path(p).name), [0, 0, 0, 0, [], [], set()])
            r[0] += int(k.sum()); r[1] += len(k)
            r[2] += int((k[1:] & k[:-1]).sum()); r[3] += int(k[1:].sum())
            r[4].append(float(np.percentile(th, 95))); r[5].append(float(th.max())); r[6].add(n)
    # 按链聚合（跨文件取最差）
    agg = {}
    for (ch, f), r in rows.items():
        kick = 100 * r[0] / max(r[1], 1)
        cur = agg.setdefault(ch, dict(kick=0, sust=0, p95=0, mx=0, worst="", n=0, files=0))
        cur["files"] += 1; cur["n"] = max(cur["n"], len(r[6]))
        if kick > cur["kick"] or not cur["worst"]:
            cur.update(kick=kick, sust=100 * r[2] / max(r[3], 1), p95=max(r[4]), mx=max(r[5]), worst=f)
    bad = sorted((c for c in agg.items() if c[1]["kick"] >= a.warn), key=lambda c: -c[1]["kick"])
    print(f"[verify] {a.ck} {len(files)} 条 [{a.glob}]  物理骨链 {len(agg)}（剔除与基础动画相同的骨 {n_skip} 条）  超 {a.warn}% 的链 {len(bad)}")
    print(f"  {'链':22s} {'骨':>3s} {'kick%':>6s} {'sust%':>6s} {'p95':>6s} {'max':>6s}  指纹      最差文件")
    for ch, c in bad[:a.top]:
        fp = "连续颤动" if c["sust"] >= 50 else "间歇跳变"
        print(f"  {ch:22s} {c['n']:3d} {c['kick']:6.1f} {c['sust']:6.0f} {c['p95']:6.2f} {c['mx']:6.2f}  {fp}  {c['worst']}")
    worst = max((c["kick"] for c in agg.values()), default=0)
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(dict(character=a.ck, files=len(files), chains=agg),
                                     ensure_ascii=False, indent=2), encoding='utf-8')
    if worst >= a.fail:
        print(f"[verify] !! 最差链 {worst:.1f}% ≥ {a.fail}%"); return 2
    print(f"[verify] OK 最差链 {worst:.1f}%"); return 0

if __name__ == "__main__":
    sys.exit(main())
