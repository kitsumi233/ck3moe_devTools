"""校验烘焙出的 .anim 是否真的能被 CK3 吃下。纯二进制解析，不依赖 Blender。

用法:
  python tools/verify/verify_moe_anim.py work/moe_anim_bake

检查项都是踩过或差点踩到的坑，不是形式主义:

1. 代理骨在不在、通道非空
   导出器可能静默丢掉「没有实际运动」的骨。若代理骨通道为空，游戏里就是
   表情完全不动，而 Blender 里看着好好的。

2. 附属骨通道非空（MMD 付与親/IK 烘焙结果）
   bn_m_l_armTwist / bn_m_l_legD / bn_m_waistCancel_l 这类骨有蒙皮权重，
   靠 Blender 约束求值驱动。约束不进 .anim，必须已被烘成显式关键帧。
   一旦为空，游戏里这些骨不动而周围骨在动，网格会撕裂。
   这是整条管线里最容易静默出错的地方。

3. samples 长度自洽
   samples/t 长度必须 = 带 t 通道骨数 × 3 × 帧数，q 同理 ×4。
   长度不符说明导出器写坏了，游戏读到的是错位数据（表现为骨乱飞）。

4. 首帧 == info 里的 rest
   已实测原版是这个规律（采样是绝对局部变换，非增量）。
   若我们的产物不满足，说明写入基准不同，叠加时会整体偏移。

5. 循环动画首尾闭合
   idle 每次循环接缝处会跳帧，根因就是首尾帧不一致。

6. 骨名无命名空间前缀
   原版带 female_head_rig_01: 前缀，我们的不该有；有的话游戏对不上骨。
"""
import io
import sys
from pathlib import Path

# stdout 转 utf-8。**用 reconfigure 不用 TextIOWrapper** —— 后者每次都新建一个
# 包装对象，多个模块各包一层时外层被 GC 会关掉共享的 buffer，于是后面任何
# print 都抛 `ValueError: I/O operation on closed file`（动画其实已经写完了，
# 只是收尾的 print 炸掉，看起来像整条失败）。reconfigure 原地改编码，天然幂等。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path[:0] = [str(Path(__file__).resolve().parents[2] / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import pdx_parse  # noqa: E402
import moe_expression_contract as C  # noqa: E402

# 关于「附属骨必须有通道否则撕裂」这条担忧 —— 实测证明在本管线里不成立，
# 这里记下为什么，免得以后又照传闻加回错误的检查。
#
# 查 florence_poc.blend: **整个骨架 167 根骨，带约束的有 0 根**。
# MMD 的付与親/IK 在 PMX 导入阶段就已经没了，不存在「约束未烘焙」的风险。
#
# 更关键的是层级方向。实测:
#   bn_m_l_arm → bn_m_l_armTwist → bn_m_l_elbow → bn_m_l_wristTwist
# armTwist 是 elbow 的**父骨**，不是跟随它的从骨。子骨有通道而父骨没有，
# 是完全正常的 FK：elbow 的局部旋转不需要父骨配合，父骨静止即维持 rest。
# 撕裂只会发生在「同一块皮被两根骨拉扯而它们相对关系被破坏」时，
# 而纯 FK 的父子链天然保持相对关系。
#
# 第一版把方向写反（armTwist 跟随 elbow），于是 greet_bow 报了假撕裂。
# 更早一版则因为导出器的浮点噪声给每根骨都塞了假通道，反而「通过」了 ——
# 一个假阴性掩盖了一个假阳性。修掉噪声后假阳性才浮现。
#
# 真正需要警惕的静默失败已由下面两条检查覆盖:
#   1. 配方要求的骨是否真被驱动（expected_bones）
#   2. 通道存在但数值恒定（constant_channels）
# 这两条抓的是「烘焙漏了」，比骨骼拓扑猜测可靠。
#
# 骨骼蒙皮权重实测（moe_torso.mesh，供参考）:
#   legD 2055/1798（3760/3541 权重项）· armTwist 95/95（185/180）
#   waistCancel_l/r 权重和 0 → 纯控制骨，无蒙皮
# 所以这里**不设**附属骨通道检查。
REST_TOL = 1e-3
LOOP_TOL = 1e-4


def load(path):
    return pdx_parse.parse(Path(path).read_bytes())


# pdx_parse 的节点形状（实测，不是猜的）:
#   节点 = {"_props": {名: 值列表}, "_children": [(子名, 子节点), ...]}
#   _children 是**元组列表**而非 dict —— 因为 PDX 允许同名重复节点，
#   用 dict 会丢掉后面的。叶节点可能完全没有 "_children" 键。
def kids(node):
    """子节点列表 → dict。.anim 里骨名不重复，可以安全转。"""
    if not isinstance(node, dict):
        return {}
    ch = node.get("_children") or []
    if isinstance(ch, dict):
        return ch
    return {name: sub for name, sub in ch}


def props(node):
    return node.get("_props", {}) if isinstance(node, dict) else {}


# 属性值形状（实测）: ("类型标签", [值...])，如 ('int', [90])、('float', [30.0])、
# ('str', [])。类型标签必须剥掉才能拿到数值。
def seq(node, key):
    """取属性的值列表，剥掉类型标签。不存在返回 None。"""
    v = props(node).get(key)
    if v is None:
        return None
    if isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], str):
        vals = v[1]
        return list(vals) if isinstance(vals, (list, tuple)) else [vals]
    return list(v) if isinstance(v, (list, tuple)) else [v]


def scalar(node, key, default=None):
    """取单值属性。"""
    vals = seq(node, key)
    if not vals:
        return default
    return vals[0]


def analyze(path):
    """返回 (报告 dict, 错误列表)。"""
    root = load(path)
    errs, warns = [], []
    top = kids(root)
    if "info" not in top or "samples" not in top:
        return {"file": path.name, "fatal": "缺少 info 或 samples"}, ["结构不对"]

    info, samples = top["info"], top["samples"]
    fps = scalar(info, "fps")
    frames = scalar(info, "sa")
    njoint = scalar(info, "j")

    # 逐骨读通道字符串与 rest 值
    bones = {}
    order = []
    for bname, bnode in kids(info).items():
        # 骨节点的 sa 是通道字符串（'tq'/'t'/'q'/...），空 = 该骨无动画通道。
        # 注意与 info 自身的 sa（帧数, int）同名但含义完全不同。
        sa_vals = seq(bnode, "sa") or []
        sa = sa_vals[0] if sa_vals else ""
        if not isinstance(sa, str):
            sa = ""
        bones[bname] = {
            "sa": sa,
            "t": seq(bnode, "t"),
            "q": seq(bnode, "q"),
            "s": seq(bnode, "s"),
        }
        order.append(bname)

    n_t = sum(1 for b in bones.values() if "t" in b["sa"])
    n_q = sum(1 for b in bones.values() if "q" in b["sa"])
    n_s = sum(1 for b in bones.values() if "s" in b["sa"])

    st = seq(samples, "t") or []
    sq = seq(samples, "q") or []
    ss = seq(samples, "s") or []

    rep = {
        "file": path.name, "fps": fps, "frames": frames, "joints": njoint,
        "bones_in_info": len(bones),
        "chan_counts": {"t": n_t, "q": n_q, "s": n_s},
        "sample_lens": {"t": len(st), "q": len(sq), "s": len(ss)},
    }

    # --- 检查 3: samples 长度自洽 ---
    exp_t, exp_q, exp_s = n_t * 3 * frames, n_q * 4 * frames, n_s * frames
    rep["expected_lens"] = {"t": exp_t, "q": exp_q, "s": exp_s}
    for lbl, got, exp in (("t", len(st), exp_t), ("q", len(sq), exp_q),
                          ("s", len(ss), exp_s)):
        if got != exp:
            errs.append(f"samples/{lbl} 长度 {got} != 预期 {exp}"
                        f"（{lbl}骨数×每骨×帧数）")

    if njoint != len(bones):
        warns.append(f"info/j={njoint} 但 info 下有 {len(bones)} 个骨节点")

    # --- 检查 6: 骨名无命名空间前缀 ---
    ns = [b for b in bones if ":" in b]
    if ns:
        errs.append(f"{len(ns)} 个骨名带命名空间前缀，如 {ns[0]}")

    # --- 检查 1&2: 关键骨在场且通道非空 ---
    def channel_ok(bname):
        b = bones.get(bname)
        return b is not None and b["sa"] != ""

    # 代理骨：只要求「在骨架里」。某个表情不用某根代理骨时它没通道是对的
    # （happy 里 decal_anger 当然不该动），所以空通道不算错，只报数。
    proxy_present = [b for b in C.PROXY_BONES if b in bones]
    proxy_missing = [b for b in C.PROXY_BONES if b not in bones]
    proxy_active = [b for b in proxy_present if channel_ok(b)]
    rep["proxy"] = {"present": len(proxy_present), "missing": proxy_missing,
                    "active": proxy_active}

    # --- 检查 4: 首帧 == rest ---
    # samples 按帧交错: 第 f 帧的 t 值在 [f*n_t*3 : (f+1)*n_t*3]
    t_bones = [b for b in order if "t" in bones[b]["sa"]]
    q_bones = [b for b in order if "q" in bones[b]["sa"]]
    bad_rest = []
    if frames and len(st) == exp_t:
        for i, b in enumerate(t_bones):
            rest = bones[b]["t"] or []
            got = st[i * 3:(i + 1) * 3]
            if len(rest) == 3 and len(got) == 3:
                if max(abs(a - c) for a, c in zip(rest, got)) > REST_TOL:
                    bad_rest.append(f"{b}.t")
    if frames and len(sq) == exp_q:
        for i, b in enumerate(q_bones):
            rest = bones[b]["q"] or []
            got = sq[i * 4:(i + 1) * 4]
            if len(rest) == 4 and len(got) == 4:
                if max(abs(a - c) for a, c in zip(rest, got)) > REST_TOL:
                    bad_rest.append(f"{b}.q")
    rep["first_frame_ne_rest"] = bad_rest[:8]
    rep["first_frame_ne_rest_count"] = len(bad_rest)
    if bad_rest:
        warns.append(f"{len(bad_rest)} 个通道首帧 != info rest（原版规律是相等）")

    # --- 检查 5: 首尾闭合（仅循环动画）---
    def frame_slice(arr, per, count, f):
        base = f * count * per
        return arr[base:base + count * per]

    loop_gap = None
    if frames and frames > 1:
        gaps = []
        if len(st) == exp_t and n_t:
            a = frame_slice(st, 3, n_t, 0)
            z = frame_slice(st, 3, n_t, frames - 1)
            if len(a) == len(z) and a:
                gaps.append(max(abs(x - y) for x, y in zip(a, z)))
        if len(sq) == exp_q and n_q:
            a = frame_slice(sq, 4, n_q, 0)
            z = frame_slice(sq, 4, n_q, frames - 1)
            if len(a) == len(z) and a:
                gaps.append(max(abs(x - y) for x, y in zip(a, z)))
        loop_gap = max(gaps) if gaps else None
    rep["first_last_gap"] = round(loop_gap, 6) if loop_gap is not None else None

    # --- 运动量：通道存在但数值恒定 = 实际没动 ---
    still = []
    if len(st) == exp_t and frames > 1:
        for i, b in enumerate(t_bones):
            vals = [st[f * n_t * 3 + i * 3 + k]
                    for f in range(frames) for k in range(3)]
            if max(vals) - min(vals) < 1e-6:
                still.append(f"{b}.t")
    if len(sq) == exp_q and frames > 1:
        for i, b in enumerate(q_bones):
            vals = [sq[f * n_q * 4 + i * 4 + k]
                    for f in range(frames) for k in range(4)]
            if max(vals) - min(vals) < 1e-6:
                still.append(f"{b}.q")
    rep["constant_channels"] = len(still)
    rep["moving_channels"] = (n_t + n_q) - len(still)
    if rep["moving_channels"] == 0:
        errs.append("所有通道数值恒定 —— 这个动画实际什么都不动")

    rep["_channel_ok"] = {b: channel_ok(b) for b in bones}
    rep["_all_bones"] = set(bones)
    rep["errors"] = errs
    rep["warnings"] = warns
    return rep, errs


def expected_bones(stem):
    """这个动画**应该**驱动哪些骨。用来抓「烘焙漏了」的静默失败。

    表情: 配方里出现过的语义槽 → 代理骨（跳过模型没有的槽）。
    动作: 配方里出现的语义骨名。
    """
    name = stem[4:] if stem.startswith("moe_") else stem
    if name in C.EXPRESSIONS:
        slots = {s for _, d in C.EXPRESSIONS[name] for s in d}
        return name, "expression", {C.SEMANTIC_SLOTS[s] for s in slots
                                    if s in C.SEMANTIC_SLOTS}
    if name in C.ACTIONS:
        return name, "action", {b for _, d in C.ACTIONS[name]["keys"] for b in d}
    return name, "unknown", set()


def main():
    d = Path(sys.argv[1] if len(sys.argv) > 1 else "work/moe_anim_bake")
    files = sorted(d.glob("*.anim"))
    if not files:
        print(f"{d} 下没有 .anim")
        return 1

    loop_names = {f"moe_{k}" for k, v in C.ACTIONS.items() if v["looping"]}
    total_err = 0
    print(f"校验 {len(files)} 个 .anim（目录 {d}）")
    print("=" * 78)
    for p in files:
        rep, errs = analyze(p)
        if "fatal" in rep:
            print(f"\n{p.name}  致命: {rep['fatal']}")
            total_err += 1
            continue
        cc = rep["chan_counts"]
        print(f"\n{p.name}")
        print(f"  fps={rep['fps']} 帧数={rep['frames']} 骨数={rep['joints']}"
              f"  通道 t={cc['t']} q={cc['q']} s={cc['s']}")
        print(f"  samples 长度 {rep['sample_lens']} / 预期 {rep['expected_lens']}"
              + ("  ✓" if rep["sample_lens"] == rep["expected_lens"] else "  ✗"))
        pr = rep["proxy"]
        print(f"  代理骨 在骨架 {pr['present']}/{len(C.PROXY_BONES)}"
              f"  本动画驱动 {len(pr['active'])} 根"
              + (f"  缺失 {pr['missing']}" if pr["missing"] else ""))
        # 配方要求的骨是否都真被驱动了 —— 抓「烘焙静默漏骨」
        nm, kind, want = expected_bones(p.stem)
        if want:
            active = set(rep["proxy"]["active"]) | {
                b for b in want if b in rep["_channel_ok"]}
            got = {b for b in want if rep["_channel_ok"].get(b)}
            miss = sorted(want - got)
            # 模型没有的形态键对应的骨压根不会建，属预期跳过
            absent = sorted(b for b in miss if b not in rep["_all_bones"])
            real_miss = sorted(b for b in miss if b in rep["_all_bones"])
            print(f"  配方要求 {len(want)} 骨，实际驱动 {len(got)}"
                  + (f"  预期跳过(模型无此键) {absent}" if absent else "")
                  + (f"  ✗漏驱动 {real_miss}" if real_miss else "  ✓"))
            if real_miss:
                rep["errors"].append(f"配方要求但未驱动: {real_miss}")
        print(f"  有运动的通道 {rep['moving_channels']}"
              f"，恒定 {rep['constant_channels']}")
        if rep["first_frame_ne_rest_count"]:
            print(f"  首帧≠rest 的通道 {rep['first_frame_ne_rest_count']} 个"
                  f" 例 {rep['first_frame_ne_rest'][:4]}")
        else:
            print("  首帧 == info rest ✓")
        g = rep["first_last_gap"]
        stem = p.stem
        if stem in loop_names:
            ok = g is not None and g <= LOOP_TOL
            print(f"  循环闭合 首尾差={g}" + ("  ✓" if ok else "  ✗ 会跳帧"))
            if not ok:
                rep["errors"].append(f"循环动画首尾不闭合，差 {g}")
        else:
            print(f"  （非循环）首尾差={g}")
        for e in rep["errors"]:
            print(f"  ✗ {e}")
        for w in rep["warnings"]:
            print(f"  · {w}")
        total_err += len(rep["errors"])

    print("\n" + "=" * 78)
    print("全部通过 ✓" if total_err == 0 else f"共 {total_err} 项错误")
    return 0 if total_err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
