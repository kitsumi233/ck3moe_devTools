"""批量生成完整的 head 侧动画：表情通道 + 配对 torso 动作的脊椎通道。

这是 graft_spine_into_head_anim.py 的批量版。手法完全一致（直接 import 它的
build()），区别只在清单来源：

  单条版  手写 BODY_FILE_TO_BASE（17 条）
  本脚本  moe_anim_catalog.Catalog("head").bases（393 条）+
          pair_head_to_torso() 实读的配对关系

## 为什么 head 必须一对一配 torso 动作

head 与 torso 是两个独立 entity，各播自己的 animation。头部实体通过 attach
挂在 torso 的 bn_m_root 上，而 root 在我们的动画里是**静态**的 —— 躯干的
弯腰/转身全在 spine 链上。所以 head 侧不移植该动作**自己的**脊椎通道，
头就会留在原地不跟着躯干走。实测最坏 grief 头错位 21.5 单位（36% 头高）。

配对关系从两个 .asset 的同名 state 实读（catalog.pair_head_to_torso），
不按名字猜 —— animations.txt 里 371 组 (head,torso) 有 87 组名字不同。

情绪、说话、视线与入场/循环/退出分别由 catalog.performance_for 分派。
源曲线在合成阶段重采样，身体的帧数、帧率和共享骨通道始终逐帧复制。

用法:
    python tools/anim/graft_all_head_anims.py
    python tools/anim/graft_all_head_anims.py --limit 20 --dry-run
    python tools/anim/graft_all_head_anims.py --skip-existing
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import moe_paths as MP    # noqa: E402  资源路径解析（子 mod 感知）
import moe_anim_catalog as CAT              # noqa: E402
# 共用合成器使用 stdout.reconfigure，不建立会关闭共享 buffer 的 wrapper。
import graft_spine_into_head_anim as G

# 单体目录已退役（2026-08-29）：产物按子 mod 直写 mod/<mod_id>/。
# MP.BASE 解析到第一个存在的模型包；**定位具体文件用 MP.find()**，
# 它会遍历全部子 mod（flo 在 gfl2 包、lau/per/eld 在 endfield 包）。
MOE = MP.BASE


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-existing", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--jobs", type=int, default=0, help="（保留，本脚本单进程）")
    ap.add_argument("--out-dir", default=None,
                    help="head/torso .anim 所在目录（默认按角色写 canonical 烘焙目录，使用通用 moe_ 前缀）。")
    ap.add_argument("--head-mesh", default=None,
                    help="该角色的 head mesh 文件名（如 moe_per_head.mesh）。"
                         "它决定「哪些骨算 head 骨」—— 写死芙洛伦那份会给"
                         "别的角色筛错骨集。走 moe_paths.find 解析。")
    ap.add_argument("--out-prefix", default=None,
                    help="产物文件名前缀（默认 moe_anim_catalog.OUT_PREFIX，"
                         "即 'moe_'）。必须与生成 torso 产物时用的前缀一致，"
                         "否则这里找不到配对的 body .anim。")
    a = ap.parse_args()

    if a.out_prefix:
        CAT.OUT_PREFIX = a.out_prefix

    # 产物目录**按角色解析**，不能从 MP.BASE 往下 rglob —— BASE 只指向第一个
    # 存在的模型包（gfl2），而洛茜的动画在 endfield 包里，rglob 一条都找不到，
    # 于是回落到 BASE 根目录、每个文件都 FileNotFoundError。
    # `build_all_anims.py` 有同一个坑，两处都按角色解析。
    if a.out_dir:
        moe = Path(a.out_dir)
    else:
        _ck = MP.char_of(CAT.OUT_PREFIX or "moe_") or "flo"
        import moe_chars as X
        moe = X.bake_dir(_ck)
        CAT.OUT_PREFIX = "moe_"
        if not a.head_mesh:
            a.head_mesh = str(moe / "moe_head.mesh")
        print(f"  产物目录按角色解析：{_ck} → {moe}")

    def _pfx(name):
        """把 catalog 里的 flo 名换成当前角色的名。

        catalog 写的是 `moe_angry.anim` 这种**不带角色前缀**的名字 ——
        芙洛伦的前缀正好是 `moe_`，所以对她恰好成立。其余角色的文件是
        `moe_per_angry.anim`，不换就一个都找不到、静默回落到同样不存在的
        中性表情，产物是垃圾。
        """
        cur = CAT.OUT_PREFIX or "moe_"
        return (cur + name[len("moe_"):]) if name.startswith("moe_") else name

    cat = CAT.Catalog("head")
    # 传 torso 清单启用配对兜底（按名字直推 + 左右换名），把实读配不到的
    # 25 个降到 19 个。剩下的是原版 female_body.asset 真没注册 state 的。
    pair = CAT.pair_head_to_torso(CAT.Catalog("torso").bases)

    # mesh 在 models/ 而不是动画目录，且名字带角色前缀 —— 裸名走 MP.find。
    #
    # **`--head-mesh` 给的是真实路径时直接用，不能再喂给 MP.find**。
    # `build_all.py --with-anims` 传的是 `work/lau_anim_bake/moe_head.mesh`，
    # 而 MP.find 看到带 `/` 的输入只在各子 mod 基址下拼路径，于是抛
    # FileNotFoundError —— torso 那一步已经跑完、head 这一步半路死掉，
    # 产物目录里留下的是**旧** head 动画。旧 head 骨数恰好等于新 mesh，引擎
    # 零报错，表现是洛茜/洁尔佩塔所有动画（含 idle）头比身子靠前 3~4 单位、
    # 侧面看脖子撕开（2026-09-02）。
    _hm = a.head_mesh or _pfx("moe_head.mesh")
    _hm_path = Path(_hm)
    if not _hm_path.is_file():
        _hm_path = MP.find(_hm)
    keep = G.head_relevant_bones(_hm_path)
    print(f"head mesh: {_hm_path}")
    print(f"head.mesh 相关骨 {len(keep)} 根（蒙皮骨 + 祖先链）")

    jobs, no_torso, no_expr = [], [], []
    for base in sorted(cat.bases):
        out = moe / (CAT.OUT_PREFIX + base + ".anim")
        if a.skip_existing and out.is_file():
            continue
        tb = pair.get(base)
        # 配不到 torso 的（原版 head 有、torso 没有的 state，多是 Aggressive 系列）
        # 退到 body_idle 的脊椎。这比不生成好：state 必须有动画可播。
        body = None
        if tb:
            cand = moe / (CAT.OUT_PREFIX + tb + ".anim")
            if cand.is_file():
                body = cand
            else:
                no_torso.append((base, tb))
        if body is None:
            body = moe / (CAT.OUT_PREFIX + "body_idle.anim")
            if not body.is_file():
                # 表情源 fallback：idle_calm 不带角色前缀（来自 bake_moe_expressions）
                body = moe / _pfx("moe_idle_calm.anim")
        # 表情源在模型包里带角色前缀（构建拷贝时改名），必须过 _pfx（2026-09-06 lau/per/eld 撞上）
        performance = CAT.performance_for(base)
        expr = moe / _pfx(performance["expression"])
        if not expr.is_file():
            no_expr.append(base)
        jobs.append((expr, body, out, base, performance))
    if a.limit:
        jobs = jobs[:a.limit]

    if no_torso:
        print(f"  !! {len(no_torso)} 个 head 基名配到的 torso 产物不存在，"
              f"退用 body_idle 脊椎（前 6: {no_torso[:6]}）")
    if no_expr:
        print(f"  !! {len(no_expr)} 个缺表情源，需先重烘表情: {no_expr[:6]}")

    # 同一轮 bake 的完整源是必要输入；缺源或骨表不一致会拒绝该动作。
    life = moe / _pfx(G.LIFE_EXPR)
    reading = moe / _pfx(G.READING_EXPR)
    talk = moe / _pfx("moe_talk.anim")
    for nm, pth in (("生命层", life), ("阅读视线", reading)):
        if not pth.is_file():
            print(f"  !! 缺{nm}源 {pth.name}：依赖此源的动作将拒绝生成，"
                  f"先重跑 bake_moe_expressions.py")
    print(f"=== head: {len(jobs)} 条要生成（清单共 {len(cat.bases)}）")
    ok = fail = 0
    t0 = time.time()
    for i, (expr, body, out, base, performance) in enumerate(jobs, 1):
        try:
            msg, err = G.build(expr, body, out, keep, a.dry_run, life, reading,
                               talk_path=talk, performance=performance,
                               native_path=moe / _pfx(CAT.NEUTRAL_EXPR))
        except (OSError, ValueError) as exc:
            msg, err = None, str(exc)
        if msg:
            ok += 1
            if a.dry_run or i % 25 == 0 or i == len(jobs):
                print(f"  [{i}/{len(jobs)}] {msg}")
        else:
            fail += 1
            print(f"  !! {base}: {err}")
    print(f"  head 完成 ok={ok} fail={fail} 用时 {time.time() - t0:.0f}s")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
