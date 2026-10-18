"""生成**完整**动画集：把原版全部肖像动作/表情迁移到 moe 骨架。

背景：先前只手写迁移了 17 个身体动作 + 6 个表情，进游戏后日志刷 5128 条
    Unknown animation state 'X' for entity 'moe_torso_entity'
—— 原版 animations.txt 会请求 368 个 torso state / 288 个 head state，
缺一个就报一条，角色遇到这些情境时**直接不播动画**（僵在 rest 或上一个姿势）。

本脚本按 moe_anim_catalog 的清单批量生成:

  torso 侧  399 个基名 → tools/anim/retarget_vanilla_anim.py 逐个重定向
  head  侧  393 个基名 → tools/anim/graft_spine_into_head_anim.py 的手法，
                        表情通道 + 配对 torso 动作的脊椎通道

用法:
    python tools/anim/build_all_anims.py                 # 全量（约 15 分钟）
    python tools/anim/build_all_anims.py --limit 20      # 只做前 20 个（调试）
    python tools/anim/build_all_anims.py --part torso    # 只做一侧
    python tools/anim/build_all_anims.py --jobs 8        # 并行度（默认 CPU-2）
    python tools/anim/build_all_anims.py --skip-existing # 增量（跳过已存在的）
    python tools/anim/build_all_anims.py --semantic-motion gentle-v1
                                                        # 候选上游低频语义微动

## 为什么走子进程而不是 import

retarget_vanilla_anim.main() 是个 600 行的单函数，全局状态（TRANSLATION_SCALE）
在里面被写。改成可重入要动已经验证过的数值逻辑 —— 那是这个项目里最难验证对错
的部分（骨向修正、镜像轴、IK 关停都是靠渲图 A/B 定下来的）。
子进程隔离一次性绕开全部风险，代价只是每条多 0.15 秒启动开销。

## 顺序依赖

head 侧要读**配对的 torso 产物**取脊椎通道，所以 torso 必须先全部跑完。
--part head 单独跑时会检查 torso 产物是否齐全，缺了就报错而不是静默用 idle
（用 idle 的脊椎配 bow 的表情 = 头身不同步，这类错本地渲图很难看出来）。
"""
import argparse
import io
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import moe_paths as MP    # noqa: E402  资源路径解析（子 mod 感知）
import moe_anim_catalog as CAT  # noqa: E402

# stdout 转 utf-8。**用 reconfigure 不用 TextIOWrapper** —— 后者每次都新建一个
# 包装对象，多个模块各包一层时外层被 GC 会关掉共享的 buffer，于是后面任何
# print 都抛 `ValueError: I/O operation on closed file`（动画其实已经写完了，
# 只是收尾的 print 炸掉，看起来像整条失败）。reconfigure 原地改编码，天然幂等。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 单体目录已退役（2026-08-29）：产物按子 mod 直写 mod/<mod_id>/。
# MP.BASE 解析到第一个存在的模型包；**定位具体文件用 MP.find()**，
# 它会遍历全部子 mod（flo 在 gfl2 包、lau/per/eld 在 endfield 包）。
MOE = MP.BASE
RETARGET = ROOT / "tools" / "anim" / "retarget_vanilla_anim.py"
GRAFT = ROOT / "tools" / "anim" / "graft_all_head_anims.py"

# 重定向的模板：取 moe 的骨序与 rest。必须与 --out-dir 里的骨架骨数一致
# （芙洛伦 189 骨、洛茜 473、艾尔黛拉 704、洁尔佩塔 594 —— 骨数不匹配 =
# 引擎整段拒播 + entity 静默不渲染）。
#
# **模板名带角色前缀**，不能写死。`bake_moe_expressions.py` 产出的是
# `<pfx>idle_calm.anim`：芙洛伦的前缀正好是 `moe_`，所以写死 `moe_idle_calm.anim`
# 对她成立、对其余角色一律「缺模板」（实测洁尔佩塔在这里被拦下）。
TEMPLATE = "moe_idle_calm.anim"          # 兜底值；main() 里按 out_prefix 重算


def _template_name(out_prefix, torso=False):
    suffix = "torso_idle_calm.anim" if torso else "idle_calm.anim"
    return (out_prefix or "moe_") + suffix


def _run_retarget(args):
    """子进程：重定向一条。返回 (基名, ok, 耗时, 摘要或错误)。

    **模板名必须由参数传进来，不能读模块全局 `TEMPLATE`** ——
    `ProcessPoolExecutor` 的子进程是重新 import 本模块的，拿到的是模块级初值
    `moe_idle_calm.anim`，`main()` 里的 `global TEMPLATE` 重新赋值对它们不可见。
    实测洁尔佩塔 344 条全部报「找不到 moe_idle_calm.anim」，而 main() 的
    模板检查是通过的 —— 一个只在并行路径上出现的分叉。
    """
    base, src_dir, src_file, out_name, out_dir, rest_mesh, tpl, semantic_motion, foot_turnout, inherit_rot, spine_up_rest = args
    t0 = time.time()
    cmd = [sys.executable, str(RETARGET),
           "--src", f"{src_dir}/{src_file}",
           "--out", out_name,
           "--template", tpl]
    if out_dir:
        cmd += ["--out-dir", str(out_dir)]
    if rest_mesh:
        cmd += ["--rest-mesh", str(rest_mesh)]
    if semantic_motion != "off":
        cmd += ["--semantic-motion", semantic_motion]
    if foot_turnout:
        cmd += ["--foot-turnout"]
    if inherit_rot:
        cmd += ["--inherit-rot", str(inherit_rot)]
    if spine_up_rest:
        cmd += ["--spine-up-rest"]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(ROOT), env=env)
    dt = time.time() - t0
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip().splitlines()
        return (base, False, dt, tail[-1] if tail else f"exit {p.returncode}")
    # 从 stdout 里摘「写出 X 字节 fps= 帧=」那行做摘要
    summary = ""
    for ln in (p.stdout or "").splitlines():
        if ln.startswith("写出 "):
            summary = ln.strip()
    return (base, True, dt, summary)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=("torso", "head", "both"), default="both")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个（调试）")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--skip-existing", action="store_true",
                    help="已存在的输出跳过（增量重跑）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-dir", default=None,
                    help="模板与产物所在目录（默认按角色写 canonical 烘焙目录，使用通用 moe_ 前缀）。"
                         "换角色时指向该角色自己的目录。")
    ap.add_argument("--rest-mesh", default=None,
                    help="取骨序与父子链的 <角色>torso.mesh 路径。"
                         "**不传会按 --out-prefix 自动解析到该角色出货的那份**，"
                         "只有要用别的骨架时才需要显式给。")
    ap.add_argument("--out-prefix", default=None,
                    help="产物文件名前缀（默认 moe_anim_catalog.OUT_PREFIX，"
                         "即 'moe_'）。换角色时用不同前缀（如 'moe_lau_'），"
                         "这样即使产物最终落进同一个 mod 目录也不会与"
                         "芙洛伦的文件撞名。")
    ap.add_argument("--semantic-motion", choices=("off", "gentle-v1"), default="off",
                    help="在 torso 源阶段叠加经审核的低频语义微动；默认关闭，"
                         "开启后必须把对应 candidate source 与 head 一起审计")
    ap.add_argument("--foot-turnout", action="store_true",
                    help="透传 retarget 的 --foot-turnout（补原版站立脚尖外展，鸣潮用）；默认关")
    ap.add_argument("--inherit-rot", default=None, metavar="REPORT",
                    help="透传 retarget 的 --inherit-rot（按 PMX 回転付与表驱动付与骨，鸣潮用）；默认不给")
    ap.add_argument("--spine-up-rest", action="store_true",
                    help="透传 retarget 的 --spine-up-rest（spine_up 修正带 rest 相对朝向，修前倾，鸣潮用）；默认关")
    a = ap.parse_args()

    if a.out_prefix:
        CAT.OUT_PREFIX = a.out_prefix

    # 模板名按前缀重算 —— 写死 `moe_idle_calm.anim` 只对芙洛伦成立
    global TEMPLATE
    TEMPLATE = _template_name(CAT.OUT_PREFIX, torso=True)

    # **产物目录按角色解析，不能用固定的 `MP.BASE`**。资产拆成多个模型子 mod
    # 之后 `MP.BASE` 是第一个存在的基址（gfl2 包），拿它去找洛茜的模板必然
    # 「缺模板」而整步退出 —— 与 `register_all_anims` 当初同一类错。
    _ck = MP.char_of(TEMPLATE) or "flo"
    if a.out_dir:
        out_dir = Path(a.out_dir)
    else:
        import moe_chars as X
        out_dir = X.bake_dir(_ck)
        CAT.OUT_PREFIX = "moe_"
        TEMPLATE = _template_name(CAT.OUT_PREFIX, torso=True)
        if not a.rest_mesh:
            a.rest_mesh = out_dir / "moe_torso.mesh"
        print(f"  产物目录按角色解析：{_ck} → {out_dir}")

    # **rest mesh 也按角色解析**。它给的是骨序与父子链，必须与模板同一套骨；
    # `retarget_vanilla_anim` 的默认值是芙洛伦那份，拿去跑洛茜会报
    # `KeyError: 'bn_m_waistCancel_l'`（那是别的骨数版本独有的骨）。
    # 以前要手工传 `--rest-mesh <绝对路径>`，属于「每次都要记得」的步骤。
    if not a.rest_mesh:
        _pfx = CAT.OUT_PREFIX or "moe_"
        _rm = MP.models_dir(_ck) / f"{_pfx}torso.mesh"
        if _rm.is_file():
            a.rest_mesh = _rm
            print(f"  rest mesh 按角色解析：{_rm.name}")
        else:
            print(f"  !! 找不到 {_rm.name} —— 回落到 retarget 的默认 rest mesh，"
                  f"骨集不一致会报 KeyError")
    # **必须递归找**：资产改成按 `<角色>/anims/` 分层之后，模板不在 out_dir
    # 根下而在 `flo/anims/` 里。只查根目录会报「缺模板」把整步拦下 ——
    # 2026-08-28 就是这样让 `build_all.py --with-anims` 在第 6 步失败的。
    if not (out_dir / TEMPLATE).is_file():
        found = next(iter(sorted(out_dir.rglob(TEMPLATE))), None)
        if found is None:
            # 尚未按 torso-only 王座根契约重烘的角色仍使用旧共用模板。
            TEMPLATE = _template_name(CAT.OUT_PREFIX)
            found = next(iter(sorted(out_dir.rglob(TEMPLATE))), None)
        if found is not None:
            out_dir = found.parent
            print(f"  模板在子目录，产物目录改为 {out_dir}")
        else:
            have = sorted(p.name for p in out_dir.rglob("*idle_calm.anim"))
            raise SystemExit(f"缺模板 {TEMPLATE} —— 先跑 bake_moe_expressions.py"
                             f"（--out-dir {out_dir}）"
                             + (f"；递归找到的有 {have}" if have else ""))

    # ---------------------------------------------------------------- torso
    if a.part in ("torso", "both"):
        cat = CAT.Catalog("torso")
        jobs = []
        for base, (d, f) in sorted(cat.bases.items()):
            out = CAT.OUT_PREFIX + base + ".anim"
            if a.skip_existing and (out_dir / out).is_file():
                continue
            # 模板名跟着 job 走 —— 子进程读不到 main() 改过的全局，见
            # `_run_retarget` 的文档字符串
            jobs.append((base, d, f, out, out_dir, a.rest_mesh, TEMPLATE,
                         a.semantic_motion, a.foot_turnout, a.inherit_rot, a.spine_up_rest))
        if a.limit:
            jobs = jobs[:a.limit]
        print(f"=== torso: {len(jobs)} 条要重定向"
              f"（清单共 {len(cat.bases)}，并行 {a.jobs}）")
        if a.dry_run:
            for b, d, f, o, *_ in jobs[:20]:
                print(f"  {o:<44} ← {d}/{f}")
            print(f"  ... 共 {len(jobs)}")
        else:
            ok = fail = 0
            t0 = time.time()
            with ProcessPoolExecutor(max_workers=a.jobs) as ex:
                futs = {ex.submit(_run_retarget, j): j for j in jobs}
                for i, fu in enumerate(as_completed(futs), 1):
                    base, good, dt, msg = fu.result()
                    if good:
                        ok += 1
                        if i % 25 == 0 or i == len(jobs):
                            el = time.time() - t0
                            print(f"  [{i}/{len(jobs)}] {el:.0f}s "
                                  f"已用，ok={ok} fail={fail}  最近: {base}")
                    else:
                        fail += 1
                        print(f"  !! {base}: {msg}")
            print(f"  torso 完成 ok={ok} fail={fail} "
                  f"用时 {time.time() - t0:.0f}s")

    # ---------------------------------------------------------------- head
    if a.part in ("head", "both"):
        print(f"\n=== head: 交给 {GRAFT.name}（需读 torso 产物取脊椎）")
        if a.dry_run:
            print("  (dry-run 跳过)")
        else:
            cmd = [sys.executable, str(GRAFT)]
            if a.limit:
                cmd += ["--limit", str(a.limit)]
            if a.skip_existing:
                cmd.append("--skip-existing")
            if a.jobs:
                cmd += ["--jobs", str(a.jobs)]
            cmd += ["--out-dir", str(out_dir), "--out-prefix", CAT.OUT_PREFIX]
            if not a.out_dir:
                cmd += ["--head-mesh", str(out_dir / "moe_head.mesh")]
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            r = subprocess.run(cmd, cwd=str(ROOT), env=env)
            if r.returncode != 0:
                raise SystemExit(f"{GRAFT.name} 失败 exit {r.returncode}")

    n = len(list(out_dir.glob("*.anim")))
    tot = sum(p.stat().st_size for p in out_dir.glob("*.anim"))
    print(f"\n目录里现在 {n} 个 .anim，共 {tot/1e6:.1f} MB")


if __name__ == "__main__":
    main()
