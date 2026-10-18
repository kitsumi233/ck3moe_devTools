"""量 .anim 的首尾帧姿势差，判断「自跳转循环」会不会跳变。

## 为什么需要它

CK3 里非 idle 的 state 都是这个写法（见 register_body_anims.py）:

    state = { name = "X" animation = "a" looping = no next_state = "X" }

`looping = no` + `next_state` 指回自己 —— 用自跳转模拟循环。
引擎在这里**不做插值过渡**，直接从尾帧切到首帧。
所以首尾帧姿势差多少，画面上就跳变多少。

`looping = yes` 的 state 由引擎内部循环，不吃这个亏。

## 判据

对每根带 q 通道的骨，算首帧与尾帧四元数的夹角（度）。
报最大值与超阈值的骨名 —— 这些就是跳变的来源。

平移通道同理，报绝对距离（单位与模型一致，这里约等于厘米）。

用法:
  python tools/verify/check_anim_loop_seam.py <anim...>
  python tools/verify/check_anim_loop_seam.py <anim> --list --deg 5
"""
import io
import math
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[2] / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import pdx_parse as P  # noqa: E402

# stdout 转 utf-8。**用 reconfigure 不用 TextIOWrapper** —— 后者每次都新建一个
# 包装对象，多个模块各包一层时外层被 GC 会关掉共享的 buffer，于是后面任何
# print 都抛 `ValueError: I/O operation on closed file`（动画其实已经写完了，
# 只是收尾的 print 炸掉，看起来像整条失败）。reconfigure 原地改编码，天然幂等。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def kids(node):
    if not isinstance(node, dict):
        return {}
    ch = node.get("_children") or []
    return ch if isinstance(ch, dict) else {n: s for n, s in ch}


def seq(node, key):
    v = (node.get("_props", {}) if isinstance(node, dict) else {}).get(key)
    if v is None:
        return None
    if isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], str):
        vals = v[1]
        return list(vals) if isinstance(vals, (list, tuple)) else [vals]
    return list(v) if isinstance(v, (list, tuple)) else [v]


def scalar(node, key, default=None):
    vals = seq(node, key)
    return vals[0] if vals else default


def quat_angle_deg(a, b):
    """两个四元数代表的朝向夹角（度）。q 与 -q 同朝向，取绝对值。"""
    d = abs(sum(x * y for x, y in zip(a, b)))
    d = max(-1.0, min(1.0, d))
    return math.degrees(2.0 * math.acos(d))


def analyze(path):
    root = P.parse(Path(path).read_bytes())
    top = kids(root)
    if "info" not in top or "samples" not in top:
        return None
    info, samples = top["info"], top["samples"]
    frames = scalar(info, "sa") or 0
    if frames < 2:
        return {"file": Path(path).name, "frames": frames, "skip": "帧数<2"}

    # info 下逐骨的通道串，决定它在 samples 里占不占位
    bones = []
    for bname, bnode in kids(info).items():
        sa = seq(bnode, "sa") or []
        s = sa[0] if sa and isinstance(sa[0], str) else ""
        bones.append((bname, s))

    sq = seq(samples, "q") or []
    st = seq(samples, "t") or []
    q_bones = [b for b, s in bones if "q" in s]
    t_bones = [b for b, s in bones if "t" in s]

    # samples 布局: 每帧依次排完所有带该通道的骨
    def frame_slice(buf, per, nbone, fi):
        base = fi * nbone * per
        return buf[base:base + nbone * per]

    worst_q, worst_t = [], []
    if q_bones and len(sq) >= frames * len(q_bones) * 4:
        f0 = frame_slice(sq, 4, len(q_bones), 0)
        fN = frame_slice(sq, 4, len(q_bones), frames - 1)
        for i, b in enumerate(q_bones):
            a = f0[i * 4:i * 4 + 4]
            c = fN[i * 4:i * 4 + 4]
            if len(a) == 4 and len(c) == 4:
                worst_q.append((quat_angle_deg(a, c), b))
    if t_bones and len(st) >= frames * len(t_bones) * 3:
        f0 = frame_slice(st, 3, len(t_bones), 0)
        fN = frame_slice(st, 3, len(t_bones), frames - 1)
        for i, b in enumerate(t_bones):
            a = f0[i * 3:i * 3 + 3]
            c = fN[i * 3:i * 3 + 3]
            if len(a) == 3 and len(c) == 3:
                d = math.dist(a, c)
                worst_t.append((d, b))

    worst_q.sort(reverse=True)
    worst_t.sort(reverse=True)
    return {
        "file": Path(path).name, "frames": frames,
        "q_bones": len(q_bones), "t_bones": len(t_bones),
        "max_deg": worst_q[0][0] if worst_q else 0.0,
        "max_dist": worst_t[0][0] if worst_t else 0.0,
        "top_q": worst_q[:8], "top_t": worst_t[:8],
    }


def main():
    argv = sys.argv[1:]
    want_list = "--list" in argv
    thr = 3.0
    # 先把 --deg 及其值摘掉，否则那个数字会被当成文件名
    if "--deg" in argv:
        i = argv.index("--deg")
        thr = float(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    args = [a for a in argv if not a.startswith("--")]

    rows = []
    for a in args:
        r = analyze(a)
        if r:
            rows.append(r)
    rows.sort(key=lambda r: -r.get("max_deg", 0))

    print(f"{'文件':<30}{'帧':>4}{'q骨':>5}{'首尾最大角差':>14}{'最大位移差':>12}")
    print("-" * 68)
    for r in rows:
        if r.get("skip"):
            print(f"{r['file']:<30}{r['frames']:>4}  {r['skip']}")
            continue
        flag = "  ⚠ 会跳变" if r["max_deg"] > thr else ""
        print(f"{r['file']:<30}{r['frames']:>4}{r['q_bones']:>5}"
              f"{r['max_deg']:>13.2f}°{r['max_dist']:>12.3f}{flag}")
        if want_list and r["top_q"]:
            for d, b in r["top_q"]:
                if d > thr:
                    print(f"      {b:<34}{d:>8.2f}°")

    bad = [r for r in rows if r.get("max_deg", 0) > thr]
    print(f"\n{len(bad)}/{len(rows)} 个动画首尾差超 {thr}°，"
          f"自跳转循环时会在接缝处跳变。")
    print("修法：要么让动画首尾姿势一致（回到起始 pose），"
          "要么改用 looping = yes 让引擎内部循环。")


if __name__ == "__main__":
    main()
