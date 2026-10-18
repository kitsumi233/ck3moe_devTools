"""head 与 torso 两份动画对同名骨给出的世界姿态差（引擎按骨名共享姿态，差值 = 画面上会跳/错位的量）。

背景见记忆 ck3-shared-bone-names-across-entities-share-pose：同名骨两个 entity
谁的姿态赢不确定，差得多的部件（饰品、手、手指）会在两个位置之间跳，或整块挂错。

只比 torso 骨表里**有蒙皮顶点**的骨（或其祖先），因为画面上只看得见它们。

用法:
  python tools/verify/check_shared_bone_pose.py --ck lin tep zfy --anim idle emotion_laugh
  python tools/verify/check_shared_bone_pose.py --all --anim idle --summary
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools/verify"), str(ROOT / "tools/lib")]
import audit_skinned_pose as A  # noqa: E402
import moe_chars as X  # noqa: E402


def skinned(parts, n):
    used = np.zeros(n, bool)
    for p in parts:
        if p["ix"] is not None:
            used[np.unique(p["ix"][p["w"] > 0])] = True
    return used


def compare(c, base):
    d = A.model_dir(c)
    tn, tp, tib, tparts = A.read_mesh(d / f"{c['prefix']}torso.mesh")
    hn, hp, hib, hparts = A.read_mesh(d / f"{c['prefix']}head.mesh")
    ta = A.anim_path(c, "body", base); ha = A.anim_path(c, "head", base)
    if not ta or not ha:
        return None
    (tf, tch), e1 = A.read_anim(ta, tn)
    (hf, hch), e2 = A.read_anim(ha, hn)
    used_t = skinned(tparts, len(tn)); used_h = skinned(hparts, len(hn))
    hidx = {n: i for i, n in enumerate(hn)}
    worst = {}
    for f in sorted({0, tf // 2, tf - 1}):
        Wt = A.world(tch, min(f, tf - 1), tp)
        Wh = A.world(hch, min(f, hf - 1), hp)
        # attach 把 head 的 bn_m_root 贴到 torso 的 bn_m_root：两边都换到各自 root 坐标系再比
        Wt = np.linalg.inv(Wt[tn.index("bn_m_root")]) @ Wt
        Wh = np.linalg.inv(Wh[hn.index("bn_m_root")]) @ Wh
        for i, n in enumerate(tn):
            j = hidx.get(n)
            if j is None or not (used_t[i] or used_h[j]):
                continue
            dt = float(np.linalg.norm(Wt[i][:3, 3] - Wh[j][:3, 3]))
            if dt > worst.get(n, (0,))[0]:
                worst[n] = (dt, "torso" if used_t[i] else "", "head" if used_h[j] else "")
    return worst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ck", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--anim", nargs="*", default=["idle"])
    ap.add_argument("--thr", type=float, default=0.5)
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    cs = X.CHARS if a.all else [X.BY_CK[k] for k in a.ck]
    for c in cs:
        for base in a.anim:
            w = compare(c, base)
            if w is None:
                print(c["ck"], base, "缺动画"); continue
            bad = sorted(((n, v) for n, v in w.items() if v[0] > a.thr), key=lambda kv: -kv[1][0])
            line = f"{c['ck']:4} {c['game']:10} {base:16} 超 {a.thr} 的骨 {len(bad):3d}  最大 {bad[0][1][0] if bad else 0:7.2f}"
            print(line if a.summary else line + "\n   " + json.dumps([(n, round(v[0], 2), v[1] + v[2]) for n, v in bad[:25]], ensure_ascii=False))
            sys.stdout.flush()


if __name__ == "__main__":
    main()
