# -*- coding: utf-8 -*-
"""按正确顺序跑完整套构建，然后拆分 + 同步。

**为什么需要这个编排脚本**：`build_moe_mod.py` 开头 `shutil.rmtree(MOD)` 清空
整个 mod 目录，所以后面每一步的产物都会被它冲掉。手动按 README 的清单一条条
跑，漏一步就是**静默丢功能、零报错**：

    漏 build_moe_shader        → 所有材质退回单面剔除，裙摆内侧被看穿
    漏 build_big_portrait_gui  → 立绘放大没了
    漏 build_moe_outline_shader→ 描边没了，且 gui 的 shaderfile 指向不存在的文件
    漏 register_all_anims      → .asset 只剩 6 个 state，游戏刷几千条
                                 `Unknown state`，所有动作退回 idle

我 2026-08-28 这天就漏了 shader 和 register 两步 —— 前者被 split_mods 的
「moe_shader 无文件」暴露，后者是数 .asset 的 state 数才发现的。所以把顺序
固化成代码，不再依赖「记得看 README」。

用法：
    python tools/build_all.py              # 全流程（不含 Blender 那两步）
    python tools/build_all.py --no-sync    # 只到拆分，不装进 userdir
    python tools/build_all.py --from split # 从某一步开始（调试用）
"""
import argparse
import os
import subprocess
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 子进程的 UTF-8 靠下面 env 里的 PYTHONIOENCODING，**父进程自己也要设**：
# 少了这句时 12 步全跑完，却崩在最后一行 print 的 `✓` 上
# （`UnicodeEncodeError: 'gbk' codec`），看起来像整个构建失败。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

# 子进程的 stdout 一旦被重定向（我们这里必然被重定向），Python 就按系统
# codepage 编码 —— 中文 Windows 上是 gbk，日志里一个「✓」就抛
# UnicodeEncodeError 把那一步整个打死，而那一步的产物已经写完了，看起来像
# 「构建失败」其实只是打印失败。统一压成 utf-8，从源头断掉这一类。
_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

# 角色 → register_all_anims 的参数。**不能猜**：set-name 是
# `moe_common_<角色键>`，我曾按 `moe_<键>_portrait_animation_set` 猜过，脚本
# 打印「找不到 skeletal_animation_set」然后 exit 0，什么都没做。
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/（moe_chars 登记表）
import moe_release_sources as RELEASE_SOURCES
_ENV.update(RELEASE_SOURCES.pin_environment())
import moe_chars as X   # noqa: E402
# 就绪 = 有烘焙 mesh。芙洛伦的 state 表走无参 register（set 名 moe_common，历史遗留）。
ACTIVE = [c["ck"] for c in X.active()]
CHARS = [ck for ck in ACTIVE if ck != "flo"]
ANIM_CHARS = {ck: (X.bake_dir(ck), "moe_") for ck in ACTIVE}


def steps(no_sync, with_anims=False, jobs=12, semantic_motion="off", no_physics=False):
    """(步骤名, 命令, 说明)。顺序即依赖顺序。

    `with_anims` 打开动画重生成（约 15 分钟）。**默认关**：`build_moe_mod.py`
    会「暂存（不删）」已有的 `.anim`，所以日常改脚本/配置不必重跑。

    什么时候必须开：
      · 改了重定向/嫁接的算法或参数
      · 改了 `pdx_anim_write.py`（例如恒定通道删减）—— 旧产物不会自动受益
    """
    s = []
    if with_anims:
        # 动画先写入各自的 work/<角色>_anim_bake，之后 build_moe_mod 才会把
        # 新 mesh、表情源和完整 torso/head 动画一起部署到模型子 mod。若把这
        # 几步放在 mod 之后，生成出的新动画要等下一轮 build 才会生效。
        for ck, (out_dir, out_prefix) in ANIM_CHARS.items():
            s.append((f"anims_{ck}_torso",
                      [PY, "tools/anim/build_all_anims.py", "--part", "torso",
                       "--out-dir", str(out_dir),
                       "--rest-mesh", str(out_dir / "moe_torso.mesh"),
                       "--out-prefix", out_prefix,
                       "--semantic-motion", semantic_motion],
                      f"重定向 {ck} 的 345 条 torso 动画"))
            s.append((f"anims_{ck}_head",
                      [PY, "tools/anim/graft_all_head_anims.py",
                       "--out-dir", str(out_dir),
                       "--head-mesh", str(out_dir / "moe_head.mesh"),
                       "--out-prefix", out_prefix],
                      f"嫁接 {ck} 的 head 表情与脊椎动画"))

    s += [
        ("mod", [PY, "tools/build/build_moe_mod.py"],
         "生成 mod 文件（**清空并重写整个 mod 目录**）"),
        ("shader", [PY, "tools/build/build_moe_shader.py"],
         "双面 / 赛璐璐 shader 副本"),
        ("gui", [PY, "tools/build/build_big_portrait_gui.py"],
         "立绘放大相机 + gui 补丁"),
        ("outline", [PY, "tools/build/build_moe_outline_shader.py"],
         "立绘描边 shader（必须在 gui 之后：gui 引用它产出的文件）"),
        ("lighting", [PY, "tools/build/build_moe_lighting.py"],
         "肖像布光覆盖（脸上的暖红来自布光配置，不是 shader）"),
        ("props", [PY, "tools/build/build_moe_props.py"],
         "手持道具 accessory + 各 group 的道具基因"),
        ("cosplay_gui", [PY, "tools/build/build_moe_cosplay_gui.py"],
         "换装窗口 gui（模型槽位 + 姿势下拉）"),
        # 这里曾有一步 migrate —— 从单体目录把动画搬进各子 mod。单体目录
        # 已经退役（2026-08-29），动画就常驻在模型包里，不需要搬。
        # 模型包**不会被任何步骤清空**（主生成器只清核心），所以 1417 个
        # .anim 一直在；真要重做才跑 --with-anims。
        # previews 步骤 2026-09-06 退役：换装窗口左栏改成实时肖像，缩略图不再被任何 gui 引用。
    ]
    s += [
        ("register", [PY, "tools/anim/register_all_anims.py"],
         "写完整 state 表（芙洛伦）"),
    ]
    for ck in CHARS:
        s.append((f"register_{ck}",
                  [PY, "tools/anim/register_all_anims.py",
                   "--out-prefix", f"moe_{ck}_",
                   "--set-name", f"moe_common_{ck}",
                   "--head-entity", f"moe_{ck}_head_entity",
                   "--torso-entity", f"moe_{ck}_torso_entity"],
                  f"写完整 state 表（{ck}）"))
    # 产物现在由各生成器**直写** mod/<mod>/（见 moe_out.py），
    # 所以这里不再有「拆分」步骤。finalize 只补 descriptor、自检、装机。
    s.append(("finalize",
              [PY, "tools/build/finalize_mods.py", '--jobs', str(jobs)] + (["--no-sync"] if no_sync else [])
              + (["--no-physics"] if no_physics else []),
              "补 descriptor + 自检 + 装进隔离 userdir"))
    return s


def check_products():
    """跑完之后核对关键产物真的在。每条都是漏步骤后**静默**丢掉的东西。

    路径里带 mod 名 —— 直写之后每个产物有确定的归属，核对时要连归属一起
    核对。写对了内容但落错 mod 同样是失效（换装 `.gui` 落进放大立绘包时，
    只装核心 + 模型包的用户点交互后什么都不弹）。
    """
    sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]
    import moe_out as O
    import moe_cosplay_rules as CR

    # Keep UI artwork in the same ownership contract as the GUI generator.
    sys.path.insert(0, str(ROOT / "tools/build"))
    import build_moe_cosplay_gui as COSPLAY_GUI

    bad = []
    for mid, rel, why in (
            (O.SHADER, "gfx/FX/jomini/portrait.shader",
             "肖像双面/卡通 shader（漏 = 模型材质回落原版）"),
            (O.SHADER, "gfx/FX/court_scene.shader",
             "王座厅 Moe effect（漏 = 王座模式无场景光）"),
            (O.BIG, "gfx/FX/moe/moe_gui_portrait.shader", "立绘描边"),
            (O.BIG, "gui/shared/portraits.gui", "立绘放大"),
            (O.BIG, "gfx/portraits/cameras/30_moe_big_portrait_cameras.txt",
             "放大相机"),
            # 换装窗口挂在原版 hud_sidebars 里，不是独立顶层 window ——
            # 后者引擎从不实例化（见 build_moe_cosplay_gui 的 docstring）。
            (O.CORE, "gui/hud_sidebars.gui", "换装窗口（注入 hud_sidebars）"),
            (O.CORE, "gui/multiplayer_lobby.gui",
             "大厅入口按钮 + 窗口副本（注入 JominiMultiplayerLobby）"),
            # 两个自定义传统及其加成归可选文化包（2026-09-27）；核心只有无条件的文化门。
            (O.CORE, CR.CULTURE_OK_REL, "文化门（核心恒真，文化包同路径覆盖为传统判定）"),
            (O.UNITS, CR.ICON_REL, "cosplay 认同文化传统图标"),
            (O.UNITS, CR.HUMANOID_MERCENARIES_ICON_REL, "类人异族雇佣传统图标"),
            (O.UNITS, CR.TRADITION_REL, "cosplay 认同传统定义"),
            (O.UNITS, CR.CULTURE_OK_REL, "文化包的传统门"),
            (O.UNITS, CR.BEAUTY_OUTPUT, "最高美貌的文化吸引力加成"),
            (O.UNITS, CR.COURT_COST_OUTPUT, "仅宫廷服饰的 5% 开销调整"),
            *[(O.CORE, f"{COSPLAY_GUI.ART_REL}/{name}", "换装窗口美术")
              for name in COSPLAY_GUI.ART_FILES],
            (O.CORE, "gfx/portraits/environments/zz_moe_environments.txt",
             "肖像布光（漏 = 脸上暖红回来）"),
            # 一个 group 一份 accessory 定义，**落在定义该 group 的模型子 mod 里**
            # （同名 accessory 全局唯一，三个 group 共用一批名字只有一份生效）。
            *[(O.models_of(ck), f"gfx/portraits/accessories/40_moe_props_{ck}.txt", f"手持道具({ck})")
              for ck in ACTIVE],
            *[(O.models_of(ck), f"common/genes/42_moe_genes_props_{ck}.txt", f"手持道具基因({ck})")
              for ck in ACTIVE],
    ):
        if not (O.root(mid) / rel).is_file():
            bad.append(f"[{mid}] {rel} 缺失 —— {why}")

    asset = O.asset_path("gfl2")
    if asset.is_file():
        n = asset.read_text(encoding="utf-8-sig").count("state = {")
        # 芙洛伦一个角色的完整表约 1300 个 state。只有个位数说明 register
        # 没跑，表现是游戏刷几千条 Unknown state、所有动作退回 idle。
        # 阈值按**单个角色**定 —— asset 已按 mod 拆分，不再是三人合一份。
        if n < 500:
            bad.append(f"gfl2 的 asset 只有 {n} 个 state（应 >500）"
                       f"—— register_all_anims 没跑")
    else:
        bad.append(f"moe_models_gfl2 的 {O.asset_name('gfl2')} 缺失")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-sync", action="store_true")
    ap.add_argument("--with-anims", action="store_true",
                    help="连动画一起重生成（约 15 分钟）。改了 pdx_anim_write "
                         "或重定向算法时必须开，否则旧产物不会受益")
    ap.add_argument("--from", dest="start", help="从这一步开始（步骤名）")
    ap.add_argument("--no-physics", action="store_true",
                    help="原样传给 finalize_mods：装基础动画、不装物理（见 memory finalize-needs-no-physics-flag）")
    ap.add_argument('--jobs', type=int, default=os.cpu_count() or 12, help='Parallel copies and independent registration groups (default: CPU count)')
    ap.add_argument('--semantic-motion', choices=('off', 'gentle-v1'), default='off',
                    help='candidate upstream low-frequency semantic sway for regenerated torso animations; default off')
    a = ap.parse_args()
    if a.jobs < 1:
        ap.error('--jobs must be positive')
    if a.with_anims and RELEASE_SOURCES.resolve() is not None:
        ap.error('Selected release inputs are immutable; regenerate into a new explicit revision before adoption')
    _ENV['MOE_BUILD_JOBS'] = str(a.jobs)
    _ENV['PYTHONUNBUFFERED'] = '1'

    todo = steps(a.no_sync, a.with_anims, a.jobs, a.semantic_motion, a.no_physics)
    if a.start:
        names = [n for n, _, _ in todo]
        if a.start not in names:
            sys.exit(f"未知步骤 {a.start}，可选：{names}")
        todo = todo[names.index(a.start):]

    t0 = time.time()
    index = 0
    timings = {}
    output_lock = threading.Lock()

    def register_group(group):
        for position, name, command, description in group:
            started = time.time()
            result = subprocess.run(command, cwd=ROOT, env=_ENV, capture_output=True,
                                    text=True, encoding='utf-8', errors='replace')
            with output_lock:
                print(f'\n[{position}/{len(todo)}] {name} —— {description}')
                print(result.stdout, end='')
                if result.stderr:
                    print(result.stderr, end='', file=sys.stderr)
                timings[name] = time.time() - started
                print(f'[timing] {name}: {timings[name]:.1f}s', flush=True)
            if result.returncode:
                raise RuntimeError(f'{name} failed ({result.returncode}); deployment cancelled')

    while index < len(todo):
        name, cmd, desc = todo[index]
        if name == 'register' or name.startswith('register_'):
            groups = {}
            while index < len(todo):
                name, cmd, desc = todo[index]
                if name != 'register' and not name.startswith('register_'):
                    break
                ck = 'flo' if name == 'register' else name.removeprefix('register_')
                groups.setdefault(X.BY_CK[ck]['game'], []).append((index + 1, name, cmd, desc))
                index += 1
            print(f'[register] {len(groups)} independent assets; one writer per asset', flush=True)
            with ThreadPoolExecutor(max_workers=min(a.jobs, len(groups))) as pool:
                futures = [pool.submit(register_group, group) for group in groups.values()]
                for future in as_completed(futures):
                    future.result()
            continue
        i = index + 1
        print(f"\n{'='*66}\n[{i}/{len(todo)}] {name} —— {desc}\n{'='*66}")
        started = time.time()
        r = subprocess.run(cmd, cwd=ROOT, env=_ENV)
        timings[name] = time.time() - started
        print(f'[timing] {name}: {timings[name]:.1f}s', flush=True)
        if r.returncode != 0:
            sys.exit(f"\n**{name} 失败（退出码 {r.returncode}）**，"
                     f"后面的步骤没跑 —— 产物现在是残缺的，别拿去测。")
        index += 1

    bad = check_products()
    print(f"\n{'='*66}")
    if bad:
        for b in bad:
            print(f"  !! {b}")
        sys.exit("**产物自检失败**")
    print(f"产物自检 ✓   总耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
