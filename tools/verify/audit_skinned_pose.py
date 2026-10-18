"""按真实 .mesh + .anim 做 CPU 蒙皮，横向审计角色包的几何问题（纯 numpy，不需要 Blender）。

为什么要它：新批次的饰品错位、手丢失、指骨异常、尺寸不对这几类问题在渲图里
都要一个个角色去看；这里直接把引擎会做的 LBS 算出来，按骨头给数字：

  size      bind 下 torso+head 顶点包围盒高度（原版约 150；二次元 130~150 属正常）
  detach    饰品骨相对「最近的语义核心祖先骨」的变换，动画帧 vs bind 的偏差。
            无物理的挂件应当刚性跟随父链；偏差大 = 挂点没跟身体走
  fly       蒙皮后顶点离 bind 位置超过身高 0.6 倍的比例（飞走/塌到原点 = 看起来丢失）
  finger    指骨局部旋转相对 bind 的最大角度（>100° 基本是轴向错了）

用法:
  python tools/verify/audit_skinned_pose.py --ck lin tep zfy --anim idle dance_loop
  python tools/verify/audit_skinned_pose.py --game endfield --json out.json
"""
import argparse
import glob
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools/lib")]
import moe_chars as X  # noqa: E402
import pdx_parse as P  # noqa: E402

CORE = re.compile(r"^(bn_m_(root|center|center_2|groove|groove_2|waist|pelvis|spine_low|spine_1|spine_up|neck|head"
                  r"|[lr]_(hip|knee|ankle|toe|shoulder|arm|elbow|wrist|legD|kneeD|ankleD)))$")
FINGER = re.compile(r"^bn_m_[lr]_(thumb|index|middle|ring|pinky)")


def grouped(path):
    g = {}
    for node, key, _k, v in P.walk(P.parse(Path(path).read_bytes())):
        g.setdefault(node, {})[key] = v
    return g


def read_mesh(path):
    g = grouped(path)
    bones = []
    for node, v in g.items():
        if "/skeleton/" in node and "ix" in v and "tx" in v:
            ib = np.eye(4)
            ib[:3] = np.asarray(v["tx"], float).reshape(4, 3).T      # 列主序
            bones.append((v["ix"][0], node.rsplit("/", 1)[1], v.get("pa", [-1])[0], ib))
    bones.sort()
    names = [b[1] for b in bones]
    parent = [b[2] for b in bones]
    ib = np.asarray([b[3] for b in bones])
    parts = []
    for node, v in g.items():
        if "p" not in v or "tri" not in v:
            continue
        sk = g.get(node + "/skin", {})
        w = sk.get("bones", [0])[0]
        p = np.asarray(v["p"], float).reshape(-1, 3)
        if not w:
            parts.append(dict(node=node, p=p, ix=None, w=None, tri=len(v["tri"]) // 3))
            continue
        parts.append(dict(node=node, p=p, tri=len(v["tri"]) // 3,
                          ix=np.asarray(sk["ix"], int).reshape(len(p), w),
                          w=np.asarray(sk["w"], float).reshape(len(p), w)))
    return names, parent, ib, parts


def read_anim(path, names):
    g = grouped(path)
    meta = g["/info"]
    F = int(meta["sa"][0])
    rows = [(k[len("/info/"):], v) for k, v in g.items() if k.startswith("/info/") and k.count("/") == 2]
    an = [r[0] for r in rows]
    if an != names:
        return None, f"骨表不一致 anim={len(an)} mesh={len(names)}"
    ch = {}
    for c, wd, d in (("t", 3, [0, 0, 0]), ("q", 4, [0, 0, 0, 1]), ("s", 1, [1])):
        base = np.asarray([v.get(c, d)[:wd] for _, v in rows], float)
        data = np.broadcast_to(base, (F,) + base.shape).copy()
        anim = [i for i, (_, v) in enumerate(rows) if c in (v.get("sa") or [""])[0]]
        s = np.asarray(g.get("/samples", {}).get(c, []), float)
        if anim:
            data[:, anim] = s.reshape(F, len(anim), wd)
        ch[c] = data
    return (F, ch), None


def qmat(q):
    x, y, z, w = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    m = np.empty(q.shape[:-1] + (3, 3))
    m[..., 0, 0] = 1 - 2 * (y * y + z * z); m[..., 0, 1] = 2 * (x * y - z * w); m[..., 0, 2] = 2 * (x * z + y * w)
    m[..., 1, 0] = 2 * (x * y + z * w); m[..., 1, 1] = 1 - 2 * (x * x + z * z); m[..., 1, 2] = 2 * (y * z - x * w)
    m[..., 2, 0] = 2 * (x * z - y * w); m[..., 2, 1] = 2 * (y * z + x * w); m[..., 2, 2] = 1 - 2 * (x * x + y * y)
    return m


def world(ch, f, parent):
    n = len(parent)
    L = np.tile(np.eye(4), (n, 1, 1))
    L[:, :3, :3] = qmat(ch["q"][f]) * ch["s"][f][:, :1, None]
    L[:, :3, 3] = ch["t"][f]
    W = np.empty_like(L)
    for i in range(n):
        W[i] = W[parent[i]] @ L[i] if parent[i] >= 0 else L[i]
    return W


def skin(parts, D):
    out = []
    for pt in parts:
        p = np.column_stack((pt["p"], np.ones(len(pt["p"]))))
        if pt["ix"] is None:
            out.append(pt["p"].copy()); continue
        r = np.zeros_like(pt["p"])
        for c in range(pt["ix"].shape[1]):
            ix, w = pt["ix"][:, c], pt["w"][:, c]
            m = w > 0
            r[m] += np.einsum("nij,nj->ni", D[ix[m]], p[m])[:, :3] * w[m, None]
        out.append(r)
    return out


def rot_deg(a, b):
    r = a[:3, :3].T @ b[:3, :3]
    return math.degrees(math.acos(max(-1, min(1, (np.trace(r) - 1) / 2))))


def model_dir(c):
    hits = glob.glob(str(ROOT / f"mod/moe_models_*/gfx/models/portraits/moe/models/{c['prefix']}torso.mesh"))
    return Path(hits[0]).parent if hits else None


def anim_path(c, part, base):
    d = model_dir(c)
    sub = c["ck"] if c["prefix"] != "moe_" else "flo"
    p = d.parent / sub / "anims" / f"{c['prefix']}{part}_{base}.anim"
    return p if p.is_file() else None


def audit(c, anims):
    d = model_dir(c)
    if d is None:
        return dict(ck=c["ck"], error="无 mesh")
    names, parent, ib, parts = read_mesh(d / f"{c['prefix']}torso.mesh")
    bind = np.linalg.inv(ib)                       # 骨的 bind 世界矩阵
    allp = np.concatenate([p["p"] for p in parts])
    hn, hp, hib, hparts = read_mesh(d / f"{c['prefix']}head.mesh")
    hallp = np.concatenate([p["p"] for p in hparts])
    both = np.concatenate([allp, hallp])
    height = float(both[:, 1].max() - both[:, 1].min())
    res = dict(ck=c["ck"], name=c["name_cn"], game=c["game"], bones=len(names),
               height=round(height, 2), y=[round(float(both[:, 1].min()), 2), round(float(both[:, 1].max()), 2)],
               torso_verts=len(allp), head_verts=len(hallp), anims={})
    idx = {n: i for i, n in enumerate(names)}

    def core_anc(i):
        j = parent[i]
        while j >= 0 and not CORE.match(names[j]):
            j = parent[j]
        return j

    # 每个 submesh 的主导骨
    dom = []
    for pt in parts:
        if pt["ix"] is None:
            dom.append(None); continue
        dom.append(pt["ix"][np.arange(len(pt["p"])), pt["w"].argmax(1)])
    for base in anims:
        ap = anim_path(c, "body", base)
        if ap is None:
            res["anims"][base] = "缺文件"; continue
        a, err = read_anim(ap, names)
        if err:
            res["anims"][base] = err; continue
        F, ch = a
        frames = sorted({0, F // 3, 2 * F // 3, F - 1})
        det, fly, fing = {}, [], {}
        for f in frames:
            W = world(ch, f, parent)
            for i, n in enumerate(names):
                if CORE.match(n) or n.startswith(("ground_joint", "body_root", "bn_h_", "bn_sp_", "camera", "bn_l_prop", "bn_r_prop")):
                    continue
                k = core_anc(i)
                if k < 0:
                    continue
                rel_b = np.linalg.inv(bind[k]) @ bind[i]
                rel_a = np.linalg.inv(W[k]) @ W[i]
                dt = float(np.linalg.norm(rel_a[:3, 3] - rel_b[:3, 3]))
                dr = rot_deg(rel_b, rel_a)
                if FINGER.match(n):
                    fing[n] = max(fing.get(n, 0), dr)
                else:
                    o = det.get(n, (0, 0))
                    det[n] = (max(o[0], dt), max(o[1], dr))
            D = W @ ib
            sk = skin(parts, D)
            for pt, s in zip(parts, sk):
                far = np.linalg.norm(s - pt["p"], axis=1) > 0.6 * height
                fly.append(float(far.mean()))
        worst = sorted(det.items(), key=lambda kv: -kv[1][0])[:12]
        res["anims"][base] = dict(
            frames=F,
            detach_top=[(n, round(t, 2), round(r, 1)) for n, (t, r) in worst],
            finger_max=[(n, round(v, 1)) for n, v in sorted(fing.items(), key=lambda kv: -kv[1])[:6]],
            fly_frac_max=round(max(fly) if fly else 0, 4),
        )
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ck", nargs="*")
    ap.add_argument("--game", nargs="*")
    ap.add_argument("--anim", nargs="*", default=["idle"])
    ap.add_argument("--json")
    a = ap.parse_args()
    cs = [c for c in X.CHARS if (not a.ck or c["ck"] in a.ck) and (not a.game or c["game"] in a.game)]
    out = []
    for c in cs:
        r = audit(c, a.anim)
        out.append(r)
        print(json.dumps(r, ensure_ascii=False))
        sys.stdout.flush()
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
