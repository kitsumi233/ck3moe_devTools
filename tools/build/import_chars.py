# -*- coding: utf-8 -*-
"""批量接入角色：按 moe_chars 登记表把一个或多个角色从 PMX 推到「动画齐备、可被
build_all 部署」的状态。**不含物理**（用户 2026-09-06：批量角色先不烘物理）。

每个角色四步，产物存在就跳过（--force 重做）：
  1 mesh    blender -b --python tools/model/build_florence_poc.py -- --model <slug>
            → work/<slug>/{moe_head,moe_torso}.mesh + <slug>_report.json + blenderProj/<slug>.blend
  2 expr    blender -b blenderProj/<slug>.blend --python tools/model/bake_moe_expressions.py --
              --out-dir work/<ck>_anim_bake --out-blend blenderProj/<ck>_expression_rig.blend
            → 7 表情 + 2 动作 .anim、带 fx 骨的 head/torso mesh、bake_report.json
  3 torso   build_all_anims.py --part torso   → 345 条 moe_body_*.anim
  4 head    graft_all_head_anims.py           → 357 条 moe_head_*.anim
跑完后执行 `python tools/build_all.py` 部署登记（它按 moe_chars.active() 自动纳入所有就绪角色）。

并行：步 1/2 是单线程 Blender，默认同时跑 --blender-jobs 3 个角色；步 3 内部已是多进程，
角色之间串行。日志逐角色写 work/logs/import_<ck>_<步>.log。

    python tools/build/import_chars.py --ck nag            # 一个
    python tools/build/import_chars.py --pending           # 所有未就绪角色
    python tools/build/import_chars.py --pending --only mesh,expr   # 只做 Blender 两步
"""
import argparse
import concurrent.futures as cf
from functools import lru_cache
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/（moe_chars 登记表）
import moe_chars as X   # noqa: E402

BLENDER = Path(r"D:\steam\steamapps\common\Blender\blender.exe")
LOGS = ROOT / "work" / "logs"
_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
STEPS = ("mesh", "expr", "torso", "head")
MIN_FREE_GIB = 20.0


def _run(cmd, log: Path, cwd=ROOT):
    free = shutil.disk_usage(ROOT).free / 1024**3
    if free < MIN_FREE_GIB:
        raise RuntimeError(f"导入已停止：磁盘仅余 {free:.2f} GiB，须保留 {MIN_FREE_GIB:.2f} GiB")
    t0 = time.time()
    status_file = log.with_suffix('.status.json')
    status = dict(status='running', command=cmd, started=t0)
    status_file.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
    with open(log, "w", encoding="utf-8", errors="replace") as f:
        r = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=str(cwd), env=_ENV,
                             text=True, encoding="utf-8", errors="replace")
        while True:
            try:
                rc = r.wait(timeout=5)
                break
            except subprocess.TimeoutExpired:
                free = shutil.disk_usage(ROOT).free / 1024**3
                if free >= MIN_FREE_GIB:
                    continue
                # Stop only this stage's process tree, including its workers.
                # A failure marker prevents partial files from satisfying resume.
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(r.pid), '/T', '/F'],
                                   stdout=f, stderr=subprocess.STDOUT)
                else:
                    r.terminate()
                r.wait()
                rc = 99
                status['reason'] = f'low disk: {free:.2f} GiB; reserve {MIN_FREE_GIB:.2f} GiB'
                f.write('\n' + status['reason'] + '\n')
                break
    status.update(status='complete' if rc == 0 else 'incomplete', returncode=rc, finished=time.time())
    status_file.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
    return rc, time.time() - t0


def _stage_incomplete(c, stage):
    status = LOGS / f"import_{c['ck']}_{stage}.status.json"
    if not status.exists():
        return False  # Existing imports predate stage markers.
    try:
        return json.loads(status.read_text(encoding='utf-8'))['status'] != 'complete'
    except (OSError, ValueError, KeyError):
        return True


def _tail(log: Path, n=3):
    try:
        return " | ".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])[:300]
    except OSError:
        return ""


def done_mesh(c):
    if _stage_incomplete(c, 'mesh'):
        return False
    w = X.work_dir(c["ck"])
    return (w / "moe_torso.mesh").is_file() and (w / "moe_head.mesh").is_file() and X.report_path(c["ck"]).is_file()


def done_expr(c):
    if _stage_incomplete(c, 'expr'):
        return False
    b = X.bake_dir(c["ck"])
    try:
        report = json.loads((b / "bake_report.json").read_text(encoding="utf-8"))
        outputs = report["export"]
        return (bool(outputs) and all(row.get("ok") for row in outputs.values())
                and all((b / name).is_file() and (b / name).stat().st_size > 0
                        for name in ("moe_head.mesh", "moe_torso.mesh", "moe_torso_idle_calm.anim"))
                and Path(report["output_blend"]).is_file())
    except (OSError, ValueError, KeyError):
        return False


@lru_cache(maxsize=2)
def _animation_names(part):
    import moe_anim_catalog as catalog
    return tuple("moe_" + base + ".anim" for base in catalog.Catalog(part).bases)


def _done_anims(c, part):
    if _stage_incomplete(c, part):
        return False
    b = X.bake_dir(c["ck"])
    return all((b / name).is_file() and (b / name).stat().st_size > 0
               for name in _animation_names(part))


def done_torso(c):
    return _done_anims(c, "torso")


def done_head(c):
    return _done_anims(c, "head")


def step_mesh(c, force):
    inv = ROOT / "work" / "pmx_skeleton_inventory" / f"{c['slug']}.json"
    if not inv.is_file():
        return False, f"缺盘点报告 {inv.name}（先跑 tools/model/inventory_gfl2_batch.py）"
    if done_mesh(c) and not force:
        return True, "已有"
    log = LOGS / f"import_{c['ck']}_mesh.log"
    rc, dt = _run([str(BLENDER), "-b", "--python", str(ROOT / "tools/model/build_florence_poc.py"),
                   "--", "--model", c["slug"]], log)
    ok = rc == 0 and done_mesh(c)
    return ok, f"{dt:.0f}s " + ("" if ok else f"exit {rc}: {_tail(log)}")


def step_expr(c, force):
    if done_expr(c) and not force:
        return True, "已有"
    blend = ROOT / "work" / "blenderProj" / (("florence_poc" if c["slug"] == "gfl2_florence" else c["slug"]) + ".blend")
    if not blend.is_file():
        return False, f"缺 {blend.name}"
    log = LOGS / f"import_{c['ck']}_expr.log"
    rc, dt = _run([str(BLENDER), "-b", str(blend), "--python", str(ROOT / "tools/model/bake_moe_expressions.py"),
                   "--", "--out-dir", str(X.bake_dir(c["ck"])),
                   "--out-blend", str(ROOT / "work" / "blenderProj" / f"{c['ck']}_expression_rig.blend")], log)
    ok = rc == 0 and done_expr(c)
    return ok, f"{dt:.0f}s " + ("" if ok else f"exit {rc}: {_tail(log)}")


# 这些游戏的模型脚尖内扣，torso 重定向补原版站立外展（L 24.6° / R 20.2°），并按 PMX 回転付与表驱动
# 足調整/肩調整 等付与骨（不驱动则大腿根/肩头皮肤钉在骨盆/锁骨上），并让 spine_up 修正带 rest 相对朝向
# （不带则胸颈前倾；gfl2/endfield 也有此问题，用户 10-13 定只改鸣潮）；其余游戏不传，产物不变
FOOT_TURNOUT_GAMES = ("wuwa", "wuwa_2")


def step_torso(c, force, jobs):
    if done_torso(c) and not force:
        return True, "已有"
    d = X.bake_dir(c["ck"]); log = LOGS / f"import_{c['ck']}_torso.log"
    rc, dt = _run([sys.executable, str(ROOT / "tools/anim/build_all_anims.py"), "--part", "torso",
                   "--jobs", str(jobs), "--out-dir", str(d), "--rest-mesh", str(d / "moe_torso.mesh"),
                   "--out-prefix", "moe_"]
                  + (["--foot-turnout", "--spine-up-rest", "--inherit-rot", str(X.report_path(c["ck"]))]
                     if c["game"] in FOOT_TURNOUT_GAMES else []), log)
    ok = rc == 0 and done_torso(c)
    return ok, f"{dt:.0f}s " + ("" if ok else f"exit {rc}: {_tail(log)}")


def step_head(c, force):
    if done_head(c) and not force:
        return True, "已有"
    d = X.bake_dir(c["ck"]); log = LOGS / f"import_{c['ck']}_head.log"
    rc, dt = _run([sys.executable, str(ROOT / "tools/anim/graft_all_head_anims.py"),
                   "--out-dir", str(d), "--head-mesh", str(d / "moe_head.mesh"), "--out-prefix", "moe_"], log)
    ok = rc == 0 and done_head(c)
    return ok, f"{dt:.0f}s " + ("" if ok else f"exit {rc}: {_tail(log)}")


STEP_FN = {"mesh": step_mesh, "expr": step_expr, "torso": step_torso, "head": step_head}


def _blender_stage(c, steps, force):
    """一个角色的 Blender 两步（可与其他角色并行）。"""
    out = []
    for st in ("mesh", "expr"):
        if st not in steps:
            continue
        ok, msg = STEP_FN[st](c, force)
        out.append((st, ok, msg))
        if not ok:
            break
    return c["ck"], out


def main():
    global MIN_FREE_GIB
    ap = argparse.ArgumentParser()
    ap.add_argument("--ck", action="append", help="角色短键，可多次")
    ap.add_argument("--pending", action="store_true", help="所有未就绪（无烘焙 mesh）的角色")
    ap.add_argument("--only", default=",".join(STEPS), help="只做这些步（逗号分隔）")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--blender-jobs", type=int, default=3)
    ap.add_argument("--anim-jobs", type=int, default=12,
                    help="torso 重定向的并行作业数（默认 12，避免按逻辑处理器数启动过多进程）")
    ap.add_argument("--min-free-gib", type=float, default=20,
                    help="每步开始前的磁盘余量下限（默认保留 20 GiB）")
    a = ap.parse_args()
    if a.min_free_gib < 0:
        ap.error("--min-free-gib must be nonnegative")
    MIN_FREE_GIB = a.min_free_gib
    steps = [s for s in STEPS if s in a.only.split(",")]
    cks = list(a.ck or [])
    if a.pending:
        cks += [c["ck"] for c in X.CHARS if not X.ready(c["ck"]) and c["ck"] not in cks]
    if not cks:
        ap.error("给 --ck 或 --pending")
    chars = [X.BY_CK[ck] for ck in cks]
    LOGS.mkdir(parents=True, exist_ok=True)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"接入 {len(chars)} 个角色 {cks}，步骤 {steps}，Blender 并行 {a.blender_jobs}")
    failed = {}
    t0 = time.time()

    # ---- 阶段 A：Blender 两步并行
    if "mesh" in steps or "expr" in steps:
        with cf.ThreadPoolExecutor(max_workers=max(1, a.blender_jobs)) as ex:
            for ck, out in ex.map(lambda c: _blender_stage(c, steps, a.force), chars):
                for st, ok, msg in out:
                    print(f"  [{ck}] {st:5} {'ok' if ok else '!!'} {msg}", flush=True)
                    if not ok:
                        failed[ck] = f"{st}: {msg}"
    # ---- 阶段 B：动画两步逐角色串行（内部多进程）
    for c in chars:
        if c["ck"] in failed:
            continue
        for st in ("torso", "head"):
            if st not in steps:
                continue
            if st == "torso":
                ok, msg = STEP_FN[st](c, a.force, a.anim_jobs)
            else:
                ok, msg = STEP_FN[st](c, a.force)
            print(f"  [{c['ck']}] {st:5} {'ok' if ok else '!!'} {msg}", flush=True)
            if not ok:
                failed[c["ck"]] = f"{st}: {msg}"
                break
    print(f"\n[import_chars] 用时 {time.time()-t0:.0f}s  成功 {len(chars)-len(failed)}  失败 {len(failed)}")
    for ck, why in failed.items():
        print(f"  !! {ck}: {why}")
    ready = [c["ck"] for c in chars if X.ready(c["ck"])]
    print(f"  现在就绪 {len(ready)}/{len(chars)}；下一步：python tools/build_all.py")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
