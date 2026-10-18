"""量 head 实体与 torso 实体在身体动作下的脱节量（绝对单位）。

## 问题

head 与 torso 是两个**独立 entity**，各播自己的 animation，靠 portrait group
的 `attach` 对接。实测通道（tools/anim/anim_channels.py）：

    表情 anim  8 个 t 通道，全是 fx 代理骨；脊椎/颈/头**全静态**
    身体 anim  53 个 q 通道，含 bn_m_spine_low / bn_m_spine_up
               但**不含** bn_m_neck / bn_m_head / bn_m_root

我们的 attach 写的是 `bn_m_root → bn_m_root`，而 `bn_m_root` 在身体动作里
是静态的。于是 torso 的脊椎倾了 18~60°，head 实体却按自己那份（脊椎静止的）
表情动画摆位 —— **头留在原地，脖子从躯干上错开**。

## 原版是怎么做的（这是修法的依据）

原版 `male_head.mesh` 只有 61 骨（`head_root` + 颈 + 脸），**根本没有脊椎**。
group 的 attach 写 `bn_sp_thoracic → head_root`，而 `bn_sp_thoracic`
**恰好是 torso 动画驱动的骨**（实测 `female_body_idle.anim` 里它带 q 通道，
`bn_sp_cervical` 反而是静态）。所以 attach 是**动态**的：头骑在被动画驱动的
胸椎上，躯干一倾，头跟着倾。

我们把 attach 设成同名骨互贴（bn_m_root→bn_m_root）是为了解决坐标系问题
（head.mesh 顶点已是世界坐标），代价就是挂到了一根不动的骨上。

## 判据

用 mesh 里的 rest 绑定矩阵做 FK，逐帧算 `bn_m_neck` 的世界位置：
  - torso 侧：脊椎按身体 anim 驱动
  - head  侧：脊椎保持 rest（表情 anim 不驱动它）
两者之差就是屏幕上看得见的接缝错位（单位与网格一致，头高约 60）。

用法:
  python tools/verify/check_head_torso_sync.py                 # 芙洛伦
  python tools/verify/check_head_torso_sync.py --char lau       # 洛茜（每个角色单独跑）
  python tools/verify/check_head_torso_sync.py --anim moe_body_laugh.anim --verbose
"""
import argparse
import io
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import pdx_parse as P  # noqa: E402
import moe_paths as MP        # noqa: E402  分层目录的路径解析（必须在 sys.path 之后）

# stdout 转 utf-8。**用 reconfigure 不用 TextIOWrapper** —— 后者每次都新建一个
# 包装对象，多个模块各包一层时外层被 GC 会关掉共享的 buffer，于是后面任何
# print 都抛 `ValueError: I/O operation on closed file`（动画其实已经写完了，
# 只是收尾的 print 炸掉，看起来像整条失败）。reconfigure 原地改编码，天然幂等。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 按角色解析（--char）。**曾写死芙洛伦**：ANIM_DIR = MP.BASE、
# MESH = moe_torso.mesh、asset_of("flo")、moe_head_entity —— 于是洛茜/洁尔佩塔
# 的 head 动画停在旧 torso 版本、331 组配对全部错位 3~54 单位时，这个脚本
# 一直报「全 0.000」（2026-09-02）。角色相关的量全部由 _setup() 填。
CHAR = "flo"
ANIM_DIR = None
MESH = None
HEAD_ENTITY = TORSO_ENTITY = None


def _setup(ck):
    """把角色相关的全局量按 ck 填好。前缀规则与 moe_paths.CHARACTERS 一致。"""
    global CHAR, ANIM_DIR, MESH, HEAD_ENTITY, TORSO_ENTITY
    import moe_out as _O
    CHAR = ck
    ANIM_DIR = _O.asset_of(ck).parent
    pfx = MP.CHARACTERS[ck]["prefix"]           # flo → "moe_"，lau → "moe_lau_"
    MESH = MP.find(f"{pfx}torso.mesh")
    HEAD_ENTITY, TORSO_ENTITY = f"{pfx}head_entity", f"{pfx}torso_entity"

PROBE = ["bn_m_neck", "bn_m_head"]
SPINE = ("bn_m_spine_low", "bn_m_spine_up", "bn_m_groove", "bn_m_center")


# ---------------------------------------------------------------- 线代
def m_ident():
    return [1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0, 0, 0, 0]   # 3x3 行主 + t


def m_mul(a, b):
    """a·b，两者都是 [r00..r22, tx,ty,tz]（行主 3x3 + 平移）。"""
    ar, at = a[:9], a[9:]
    br, bt = b[:9], b[9:]
    r = [0.0] * 9
    for i in range(3):
        for j in range(3):
            r[i * 3 + j] = sum(ar[i * 3 + k] * br[k * 3 + j] for k in range(3))
    t = [at[i] + sum(ar[i * 3 + k] * bt[k] for k in range(3)) for i in range(3)]
    return r + t


def m_inv(m):
    """刚体逆（旋转正交 + 平移）。PDX 的 tx 就是刚体+可能带缩放，这里按刚体处理。"""
    r, t = m[:9], m[9:]
    ri = [r[0], r[3], r[6], r[1], r[4], r[7], r[2], r[5], r[8]]
    ti = [-sum(ri[i * 3 + k] * t[k] for k in range(3)) for i in range(3)]
    return ri + ti


def q_to_m(q):
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
            2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
            2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y),
            0.0, 0.0, 0.0]


def m_pos(m):
    return (m[9], m[10], m[11])


def dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


# ---------------------------------------------------------------- 读取
def read_mesh_skeleton(path):
    """→ [{name, ix, pa, inv_bind}]，按 mesh 骨序。"""
    tree = P.parse(Path(path).read_bytes())
    bones, cur = [], None
    for p, name, _k, val in P.walk(tree):
        if "/skeleton/" not in p:
            continue
        bn = p.split("/skeleton/", 1)[1].split("[")[0]
        if cur is None or cur["name"] != bn:
            cur = {"name": bn}
            bones.append(cur)
        cur[name] = val
    out = []
    for b in bones:
        tx = b.get("tx") or []
        # PDX tx = 12 float，列主 3x3 + 平移，且是**逆绑定**矩阵
        if len(tx) >= 12:
            inv = [tx[0], tx[3], tx[6], tx[1], tx[4], tx[7],
                   tx[2], tx[5], tx[8], tx[9], tx[10], tx[11]]
        else:
            inv = m_ident()
        out.append({"name": b["name"], "ix": (b.get("ix") or [0])[0],
                    "pa": (b.get("pa") or [-1])[0], "inv": inv})
    out.sort(key=lambda d: d["ix"])
    return out


def read_anim(path):
    tree = P.parse(Path(path).read_bytes())
    fps = nf = None
    bones, cur = [], None
    samples = {}
    for p, name, _k, val in P.walk(tree):
        if p == "/info":
            if name == "fps":
                fps = val[0]
            elif name == "sa":
                nf = val[0]
        elif p.startswith("/info/") and p.count("/") == 2:
            bn = p.split("/info/", 1)[1].split("[")[0]
            if cur is None or cur["name"] != bn:
                cur = {"name": bn}
                bones.append(cur)
            cur[name] = val
        elif p == "/samples":
            samples[name] = val
    return fps, nf, bones, samples


def _pairs_from_asset():
    """从 .asset 读出「torso 播的 .anim → head 播的 .anim」配对。

    同名 state 在 head 与 torso 各定义一次，各指自己的 animation id；
    再由 animation id 查到 .anim 文件名。这样得到的配对与引擎实际行为一致。
    """
    import re
    import moe_out as _O
    asset = _O.asset_of(CHAR)
    txt = asset.read_text(encoding="utf-8", errors="replace")
    id2file = dict(re.findall(
        r'animation = \{ id = "([^"]+)"\s+type = "([^"]+)"', txt))

    def seg(part):
        m = re.search(r'entity\s*=\s*\{\s*\n\s*name\s*=\s*"' + part + r'"', txt)
        if not m:
            return ""
        nxt = re.search(r'\nentity\s*=\s*\{', txt[m.end():])
        return txt[m.start():m.end() + (nxt.start() if nxt
                                        else len(txt) - m.end())]

    def states(part):
        return dict(re.findall(
            r'state = \{\s*name = "([^"]+)"\s*animation = "([^"]+)"', seg(part)))

    h, b = states(HEAD_ENTITY), states(TORSO_ENTITY)
    out = {}
    for name, bid in b.items():
        hid = h.get(name)
        if not hid:
            continue
        bf, hf = id2file.get(bid), id2file.get(hid)
        if bf and hf and (ANIM_DIR / hf).is_file():
            out[bf] = ANIM_DIR / hf
    return out


def _head_of(pairs, body_path):
    """按裸名查配对的 head 动画。

    `.asset` 里的引用带子目录（`flo/anims/moe_body_X.anim`），所以 `pairs`
    的键是带路径的。下游拿到的是 `Path`，用 `.name` 直接查会**全部落空**，
    于是 head 侧回落到「脊椎保持 rest」的假设，把同步良好的动画报成
    8~9 单位错位 —— 这个假警报和 `--anim` 那个是同一个键不匹配的坑，
    在两处各犯了一次。
    """
    want = Path(body_path).name
    for k, v in pairs.items():
        if Path(k).name == want:
            return v
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--anim", default=None)
    ap.add_argument("--char", default="flo", choices=sorted(MP.CHARACTERS),
                    help="角色短名（flo / lau / per ...）。默认 flo；"
                         "**每个角色都要单独跑一次**，只查芙洛伦不能代表别人。")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    _setup(a.char)

    skel = read_mesh_skeleton(MESH)
    byname = {b["name"]: b for b in skel}
    print(f"mesh 骨架 {MESH.name}: {len(skel)} 骨")

    # rest 世界矩阵 = 逆绑定矩阵之逆
    rest_w = {b["name"]: m_inv(b["inv"]) for b in skel}
    # rest 局部 = 父世界⁻¹ · 自身世界
    rest_l = {}
    for b in skel:
        pw = (m_inv(skel[b["pa"]]["inv"]) if b["pa"] >= 0 else m_ident())
        rest_l[b["name"]] = m_mul(m_inv(pw), rest_w[b["name"]])

    for nm in PROBE:
        if nm in rest_w:
            p = m_pos(rest_w[nm])
            print(f"  rest {nm:<14} 世界位置 ({p[0]:.2f}, {p[1]:.2f}, {p[2]:.2f})")

    files = None   # 由下面按 .asset 的配对决定

    # head 侧播哪个动画，**从 .asset 实读**，不猜。
    #
    # 为什么必须读 asset: 配对关系写在 .asset 的 state 里（head 与 torso
    # 各有一份同名 state，各指自己的 animation）。用文件名猜配对会漏掉
    # 没有对应身体动作的那些（emotion_shock 的 torso 播 moe_idle_calm），
    # 而那正是最容易残留脱节的地方。
    HEAD_FOR = _pairs_from_asset()
    if a.anim:
        # 键是**带子目录的相对路径**（`flo/anims/moe_body_X.anim`），而参数
        # 一般给裸名。曾经写成 `k == a.anim` —— 过滤后 0 组，然后下游回落到
        # 「假设头侧脊椎保持 rest」，报出 179.91° 的**假警报**，把排查带偏了
        # 一整轮。所以这里按裸名 / 子串两种方式匹配，且**匹配不到就退出**，
        # 不允许静默继续。
        want = a.anim
        HEAD_FOR = {k: v for k, v in HEAD_FOR.items()
                    if Path(k).name == want or want in k}
        if not HEAD_FOR:
            raise SystemExit(
                f"**--anim {want} 没匹配到任何配对** —— 键形如 "
                f"`flo/anims/moe_body_X.anim`，给裸名或路径片段都行。"
                f"不继续跑：0 组会让下游回落到 rest 假设、报出假警报。")
    files = sorted(ANIM_DIR / k for k in HEAD_FOR)
    print(f"\n从 .asset 读出 {len(HEAD_FOR)} 组 head/torso 配对")

    print(f"\n{'anim':<28}{'脊椎最大摆幅':>13}{'颈错位':>10}{'头错位':>10}")
    print("-" * 62)
    rows = []
    for f in files:
        fps, nf, abones, samples = read_anim(f)
        qs = samples.get("q") or []
        ts = samples.get("t") or []
        qb = [b["name"] for b in abones if "q" in ((b.get("sa") or [""])[0] or "")]
        tb = [b["name"] for b in abones if "t" in ((b.get("sa") or [""])[0] or "")]
        # 动画里的 rest（第 0 帧）局部
        anim_rest_q = {b["name"]: tuple(b["q"][:4]) for b in abones if b.get("q")}
        anim_rest_t = {b["name"]: tuple(b["t"][:3]) for b in abones if b.get("t")}

        # head 侧：有配套头部动画就按它的脊椎走（按归一化时间对齐帧）
        #
        # **q 和 t 都要读**。第一版只读 q、给 t 传 `{}`，于是 bn_m_groove 的
        # t 通道（呼吸起伏）在 head 侧被丢掉，量出来 2.6 单位的「残留错位」
        # 其实是**判据自己造的**。识别信号很明确: moe_body_praying 残留恰好
        # 0.000，而它是唯一 groove 只有 q、没有 t 的身体动作。
        hq, ht = {}, {}
        hp = _head_of(HEAD_FOR, f)
        if hp is None:
            # 静默回落到 rest 假设就是假警报的来源，宁可显式报出来。
            print(f"  !! {f.name} 查不到配对的 head 动画 —— 这一行的错位量"
                  f"是「假设头侧脊椎保持 rest」算的，不能当真")
        head_rest_q, head_rest_t = {}, {}
        if hp:
            _hf, hnf, hbones, hsamples = read_anim(hp)
            hqs = hsamples.get("q") or []
            hts = hsamples.get("t") or []
            head_rest_q = {b["name"]: tuple(b["q"][:4]) for b in hbones if b.get("q")}
            head_rest_t = {b["name"]: tuple(b["t"][:3]) for b in hbones if b.get("t")}
            hqb = [b["name"] for b in hbones
                   if "q" in ((b.get("sa") or [""])[0] or "")]
            htb = [b["name"] for b in hbones
                   if "t" in ((b.get("sa") or [""])[0] or "")]
            for fr2 in range(hnf or 0):
                base2 = fr2 * len(hqb) * 4
                if base2 + len(hqb) * 4 <= len(hqs):
                    for i, bn in enumerate(hqb):
                        hq.setdefault(bn, []).append(
                            tuple(hqs[base2 + i * 4: base2 + i * 4 + 4]))
                base3 = fr2 * len(htb) * 3
                if base3 + len(htb) * 3 <= len(hts):
                    for i, bn in enumerate(htb):
                        ht.setdefault(bn, []).append(
                            tuple(hts[base3 + i * 3: base3 + i * 3 + 3]))

        worst_spine = 0.0
        worst = {nm: 0.0 for nm in PROBE}
        for fr in range(nf or 0):
            qbase, tbase = fr * len(qb) * 4, fr * len(tb) * 3
            if qbase + len(qb) * 4 > len(qs):
                break
            frame_q = {}
            for i, bn in enumerate(qb):
                frame_q[bn] = tuple(qs[qbase + i * 4: qbase + i * 4 + 4])
            frame_t = {}
            for i, bn in enumerate(tb):
                if tbase + i * 3 + 3 <= len(ts):
                    frame_t[bn] = tuple(ts[tbase + i * 3: tbase + i * 3 + 3])

            # 脊椎摆幅（相对动画自己的 rest）
            for bn in SPINE:
                if bn in frame_q and bn in anim_rest_q:
                    d = abs(sum(x * y for x, y in zip(frame_q[bn], anim_rest_q[bn])))
                    worst_spine = max(worst_spine,
                                      math.degrees(2 * math.acos(
                                          max(-1.0, min(1.0, d)))))

            # head 侧该帧的脊椎（按归一化时间取配套头部动画的对应帧）
            head_q, head_t = {}, {}
            if nf and nf > 1:
                for src, dst in ((hq, head_q), (ht, head_t)):
                    for bn, seq in src.items():
                        if seq:
                            j = round(fr / (nf - 1) * (len(seq) - 1))
                            dst[bn] = seq[max(0, min(j, len(seq) - 1))]

            # FK：torso 侧用身体动画驱动；head 侧用配套头部动画（若无则保持 rest）
            def fk(qsrc, tsrc):
                w = {}
                for b in skel:
                    nm = b["name"]
                    loc = rest_l[nm]
                    if nm in qsrc:
                        rm = q_to_m(qsrc[nm])
                        t = (tsrc.get(nm) or (loc[9], loc[10], loc[11]))
                        loc = rm[:9] + [t[0], t[1], t[2]]
                    elif nm in tsrc:
                        t = tsrc[nm]
                        loc = loc[:9] + [t[0], t[1], t[2]]
                    pw = w.get(skel[b["pa"]]["name"]) if b["pa"] >= 0 else m_ident()
                    w[nm] = m_mul(pw or m_ident(), loc)
                return w

            # **没有通道的骨要用该 .anim 自己的 rest，不是 mesh 的 rest**。
            # 引擎播放时每根骨的姿态 = anim rest 叠采样；写出器会把恒定通道
            # 压掉，所以「无通道」≠「停在 mesh rest」。第一版两侧都用 mesh rest
            # 兜底，芙洛伦 reception_bride_right_idle 的 head 侧 spine_up 恒定
            # 被压掉后回落到 mesh rest，报出 7.665 单位的假错位（2026-09-02）。
            wa = fk({**anim_rest_q, **frame_q}, {**anim_rest_t, **frame_t})
            wr = fk({**head_rest_q, **head_q}, {**head_rest_t, **head_t})
            # **在 attach 骨 bn_m_root 的局部系里比**，不比世界坐标。
            # attach 把 head 的 bn_m_root 钉在 torso 的 bn_m_root 上，root 以下
            # 的东西（ground_joint → body_root 的宫廷摆位平移，只在 torso 动画里）
            # 对头身相对位置没有影响。按世界坐标比会把 body_root 的 t 通道算成
            # 3.2 单位「错位」，而那是假的（2026-09-02，加根链之后才出现）。
            ra = m_inv(wa["bn_m_root"]) if "bn_m_root" in wa else m_ident()
            rr = m_inv(wr["bn_m_root"]) if "bn_m_root" in wr else m_ident()
            for nm in PROBE:
                if nm in wa and nm in wr:
                    pa_ = m_pos(m_mul(ra, wa[nm]))
                    pr_ = m_pos(m_mul(rr, wr[nm]))
                    worst[nm] = max(worst[nm], dist(pa_, pr_))

        rows.append((f.name, worst_spine, worst[PROBE[0]], worst[PROBE[1]]))
        print(f"{f.name:<28}{worst_spine:>12.2f}°"
              f"{worst[PROBE[0]]:>10.3f}{worst[PROBE[1]]:>10.3f}")

    if rows:
        print("-" * 62)
        print(f"{'最大':<28}{max(r[1] for r in rows):>12.2f}°"
              f"{max(r[2] for r in rows):>10.3f}{max(r[3] for r in rows):>10.3f}")
        print("\n错位单位与网格一致（头部 Y 跨度约 60）。"
              "attach 挂在静态骨上时，这就是脖子接缝的错开量。")


if __name__ == "__main__":
    main()
