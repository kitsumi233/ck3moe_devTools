"""校验完整动画集：引用完整性 + 骨数一致 + 循环接缝。

三类问题各自的症状（都不会被本地渲图发现，只能靠这个脚本或游戏日志）:

  1. **state 指向不存在的 animation id** → Unknown animation，该 state 不播
  2. **animation id 指向不存在的 .anim 文件** → 引擎加载失败
  3. **.anim 骨数 ≠ mesh 骨数** → `PlayAnimation failed for mesh "X"`，
     而且**整个 entity 不渲染**（不是降级播放）。踩过一次：idle 指向 167 骨
     的动画，躯干直接消失，画面只剩一个头。见 memory:
     ck3-bone-count-mismatch-kills-entity

用法:
    python tools/verify/verify_all_anims.py
    python tools/verify/verify_all_anims.py --seams   # additionally 检查循环接缝
"""
import argparse
import pathlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import moe_paths as MP    # noqa: E402  资源路径解析（子 mod 感知）
import pdx_parse as P  # noqa: E402
# check_anim_loop_seam 在 --seams 分支里 import，它会在 import 时重绑
# sys.stdout —— 若本模块也包了 wrapper，那个 wrapper 会变成「已关闭的文件」
# （ValueError: I/O operation on closed file）。所以这里**不包 wrapper**，
# 靠 PYTHONIOENCODING=utf-8 解决编码。这个坑在本项目踩过三次。

# 单体目录已退役（2026-08-29）：产物按子 mod 直写 mod/<mod_id>/。
# MP.BASE 解析到第一个存在的模型包；**定位具体文件用 MP.find()**，
# 它会遍历全部子 mod（flo 在 gfl2 包、lau/per/eld 在 endfield 包）。
MOE = MP.BASE
# asset 不再是固定的一份 —— main() 遍历 MP.assets()，每份传给 check_asset()。


def anim_bone_count(path):
    tree = P.parse(path.read_bytes())
    for pp, name, _k, val in P.walk(tree):
        if name == "j" and "/info" in pp:
            return val[0]
    return None


def _mesh_bone_names(path):
    tree = P.parse(path.read_bytes())
    names = set()
    for pp, _name, _k, _val in P.walk(tree):
        if "/skeleton/" in pp:
            names.add(pp.split("/skeleton/", 1)[1].split("[")[0])
    return names


def _mesh_bone_parents(path):
    """返回最终 mesh 的 {骨名: 父骨名}，用于校验 Royal Court 根契约。"""
    tree = P.parse(path.read_bytes())
    rows = {}
    for pp, name, _kind, val in P.walk(tree):
        if "/skeleton/" not in pp or name not in ("ix", "pa"):
            continue
        bone = pp.split("/skeleton/", 1)[1].split("[")[0]
        values = val if isinstance(val, list) else [val]
        if values:
            rows.setdefault(bone, {})[name] = values[0]
    by_index = {row["ix"]: bone for bone, row in rows.items() if "ix" in row}
    return {
        bone: by_index.get(row.get("pa", -1))
        for bone, row in rows.items()
        if "ix" in row
    }


def _anim_info(path):
    """返回 .anim 的 ({骨名: 通道串}, {骨名: rest t})。"""
    tree = P.parse(path.read_bytes())
    channels = {}
    rest_t = {}
    for pp, name, _kind, val in P.walk(tree):
        if not (pp.startswith("/info/") and pp.count("/") == 2 and name == "sa"):
            continue
        bone = pp.split("/info/", 1)[1].split("[")[0]
        values = val if isinstance(val, list) else [val]
        channels[bone] = values[0] if values and isinstance(values[0], str) else ""
    for pp, name, _kind, val in P.walk(tree):
        if not (pp.startswith("/info/") and pp.count("/") == 2 and name == "t"):
            continue
        bone = pp.split("/info/", 1)[1].split("[")[0]
        values = val if isinstance(val, list) else [val]
        if len(values) >= 3:
            rest_t[bone] = tuple(float(x) for x in values[:3])
    return channels, rest_t


def mesh_bone_count(path):
    return len(_mesh_bone_names(path))


# head 与 torso 两个 entity 共有的脊椎链。head 靠 attach 骑在 torso 的
# bn_m_root 上，root 以上这几根的 rest/帧数据在两侧动画里必须逐字一致，
# 否则头相对躯干整体错位（见 check_asset 第 3b 步）。
PAIR_SPINE = ("bn_m_center", "bn_m_groove", "bn_m_waist",
              "bn_m_spine_low", "bn_m_spine_up", "bn_m_neck")
PAIR_SPINE_DEG = 0.5      # rest 四元数夹角容差
PAIR_SPINE_T = 0.05       # rest 平移容差（模型单位）


def _anim_rest(path):
    """{骨名: (通道串, rest_t(3), rest_q(4))}。"""
    tree = P.parse(path.read_bytes())
    out = {}
    for pp, name, _k, val in P.walk(tree):
        if not (pp.startswith("/info/") and pp.count("/") == 2):
            continue
        bone = pp.split("/info/", 1)[1].split("[")[0]
        d = out.setdefault(bone, {})
        d[name] = val
    res = {}
    for bone, d in out.items():
        sa = d.get("sa") or [""]
        res[bone] = (sa[0] if isinstance(sa[0], str) else "",
                     tuple(d.get("t") or (0.0, 0.0, 0.0))[:3],
                     tuple(d.get("q") or (0.0, 0.0, 0.0, 1.0))[:4])
    return res


def _check_pair_spine_rest(ents, id2file, mesh_paths):
    """同名 state 下 head/torso 动画的脊椎 rest 是否一致。返回问题数。"""
    import math
    heads = {e: d for e, d in ents.items() if e.endswith("head_entity")}
    torsos = {e: d for e, d in ents.items() if e.endswith("torso_entity")}
    if not heads or not torsos:
        return 0
    bad = 0
    cache = {}

    def rest_of(f):
        if f not in cache:
            p = MP.find(f, missing_ok=True)
            cache[f] = _anim_rest(p) if p.is_file() else None
        return cache[f]

    print("\nhead/torso 配对脊椎 rest 校验:")
    for he, hd in sorted(heads.items()):
        # 同一角色的 torso entity：前缀相同（moe_lau_head_entity <-> moe_lau_torso_entity）
        te = he[:-len("head_entity")] + "torso_entity"
        td = torsos.get(te)
        if td is None:
            continue
        hs = dict(hd["states"])
        pairs = set()
        for st, taid in td["states"]:
            haid = hs.get(st)
            if haid and haid in id2file and taid in id2file:
                pairs.add((id2file[haid], id2file[taid]))
        off = []
        for hf, tf in sorted(pairs):
            H, T = rest_of(hf), rest_of(tf)
            if H is None or T is None:
                continue
            for bn in PAIR_SPINE:
                if bn not in H or bn not in T:
                    continue
                hsa, ht, hq = H[bn]
                tsa, tt, tq = T[bn]
                dq = math.degrees(2 * math.acos(min(1.0, abs(sum(
                    x * y for x, y in zip(hq, tq))))))
                dt = math.dist(ht, tt)
                # **只比 rest 值，不比通道有无**。通道有无天然不对称：
                # bn_m_neck 的 q 只在 head 侧（表情点头）；write_anim 会压掉
                # 恒定通道，body 里一条不动的 t 到 head 侧就消失了 —— 两种
                # 都是 rest 相等的合法情形。第一版把 sa 也比了，993 组里
                # 604 组假阳性，全是 Δq 0.00° / Δt 0.000。
                if dq > PAIR_SPINE_DEG or dt > PAIR_SPINE_T:
                    off.append((hf, tf, bn, dq, dt, hsa, tsa))
                    break
        if off:
            bad += len(off)
            print(f"  !! {he} <-> {te}: {len(off)}/{len(pairs)} 组配对脊椎 rest "
                  f"不一致（head 动画不是从当前 torso 动画嫁接的 —— 重跑 "
                  f"graft_all_head_anims.py）")
            for hf, tf, bn, dq, dt, hsa, tsa in off[:5]:
                print(f"     {Path(hf).name} vs {Path(tf).name}: {bn} "
                      f"Δq {dq:.2f}° Δt {dt:.3f} sa '{hsa}'/'{tsa}'")
        else:
            print(f"  {he} <-> {te}: {len(pairs)} 组配对脊椎 rest 一致 OK")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seams", action="store_true")
    ap.add_argument("--seam-deg", type=float, default=5.0)
    ap.add_argument("--asset", action="append", metavar="NAME",
                    help="check only the named asset; repeat to select several assets")
    a = ap.parse_args()

    # **每份 `.asset` 都要查**。以前固定查 `MP.BASE` 那一份，于是 lau/per 所在
    # 的 endfield 包从来没被校验过 —— 而「骨数≠mesh」这类问题会让 entity
    # 整体不渲染，本地渲图看不见。
    assets = MP.assets()
    if a.asset:
        requested = set(a.asset)
        available = {p.name for p in assets}
        unknown = requested - available
        if unknown:
            print(f"!! unknown asset(s): {sorted(unknown)}")
            return 2
        assets = [p for p in assets if p.name in requested]
    if not assets:
        print("!! 一份 .asset 都没有 —— 先跑 build_moe_mod.py")
        return 1
    bad = 0
    for i, p in enumerate(assets):
        # 路径形如 mod/<mod_id>/gfx/models/portraits/moe/<name>.asset
        print(f"\n{'='*62}\n[{i+1}/{len(assets)}] {p.name}"
              f"  ({p.parents[4].name})\n{'='*62}")
        bad += check_asset(p, a)
    print(f"\n{'[PASS] 全部通过' if bad == 0 else f'!! 合计 {bad} 处问题'}")
    return 1 if bad else 0


def check_asset(ASSET, a):
    txt = ASSET.read_text(encoding="utf-8-sig", errors="replace")

    # ---- animation id → 文件
    id2file = dict(re.findall(r'animation = \{ id = "([^"]+)"\s*type = "([^"]+)"', txt))
    # ---- 每个 entity 的 state → animation id
    ents = {}
    for m in re.finditer(r'entity\s*=\s*\{\s*\n\s*name\s*=\s*"([^"]+)"', txt):
        nxt = re.search(r'\nentity\s*=\s*\{', txt[m.end():])
        end = m.end() + (nxt.start() if nxt else len(txt) - m.end())
        seg = txt[m.start():end]
        pm = re.search(r'pdxmesh\s*=\s*"([^"]+)"', seg)
        ents[m.group(1)] = {
            "mesh_id": pm.group(1) if pm else None,
            "states": re.findall(r'state = \{ name = "([^"]+)"\s*'
                                 r'animation = "([^"]+)"', seg),
            "default": (re.search(r'default_state = "([^"]+)"', seg) or
                        [None, None])[1] if re.search(r'default_state', seg) else None,
        }
    # ---- pdxmesh id → 文件
    mesh2file = {}
    for m in re.finditer(r'pdxmesh\s*=\s*\{\s*\n\s*name\s*=\s*"([^"]+)"\s*\n\s*'
                         r'file\s*=\s*"([^"]+)"', txt):
        mesh2file[m.group(1)] = m.group(2)

    print(f"asset: animation id {len(id2file)}，entity {len(ents)}，"
          f"pdxmesh {len(mesh2file)}")

    bad = 0

    # 1. state → animation id 存在？
    for ent, d in ents.items():
        miss = sorted({aid for _, aid in d["states"] if aid not in id2file})
        print(f"\n{ent}: state {len(d['states'])}，"
              f"default_state = {d['default']}")
        if miss:
            bad += len(miss)
            print(f"  !! {len(miss)} 个 state 指向不存在的 animation id: {miss[:8]}")
        # next_state 也要存在
        names = {n for n, _ in d["states"]}
        nxts = set(re.findall(r'next_state = "([^"]+)"',
                             txt[txt.find(f'"{ent}"'):]))
        # default_state 必须有定义
        if d["default"] and d["default"] not in names:
            bad += 1
            print(f"  !! default_state '{d['default']}' 没有对应 state")

    # 2. animation id → 文件存在？
    missf = sorted({f for f in id2file.values() if not MP.find(f, missing_ok=True).is_file()})
    if missf:
        bad += len(missf)
        print(f"\n!! {len(missf)} 个 animation 指向不存在的文件: {missf[:8]}")
    else:
        print(f"\n全部 {len(id2file)} 个 animation 的 .anim 文件都在")

    # 3. 骨数一致？按 entity 绑定的 mesh 分别核对 head / torso。
    #
    # 新架构允许同一角色的 head 和 torso 使用不同骨架（例如 flo 的
    # head=200、torso=202），所以不能再按角色把两件 mesh 合并成一个基准。
    # entity 的 state 表才是权威映射：它决定哪些 animation 实际会被该 mesh 播放。
    print("\n骨数校验:")
    mesh_n = {}
    mesh_paths = {}
    for mid, f in sorted(mesh2file.items()):
        p = MP.find(f, missing_ok=True)
        if not p.is_file():
            bad += 1
            print(f"  !! {mid}: 缺文件 {f}")
            continue
        mesh_paths[mid] = p
        mesh_n[mid] = mesh_bone_count(p)
        print(f"  {mid:<20} {f:<20} {mesh_n[mid]} 骨")

    anim_counts = {}
    for f in sorted({f for f in id2file.values()}):
        p = MP.find(f, missing_ok=True)
        if p.is_file():
            anim_counts[f] = anim_bone_count(p)
    from collections import Counter
    print(f"  .anim 骨数分布: {dict(Counter(anim_counts.values()))}")

    # 判据是**严格相等**，不要放宽成「anim 骨 ⊆ mesh 骨」。引擎原话：
    #   Animation "X" does not have the same amount of joints as the mesh it is
    #   trying to animate. ( Animation 189, Mesh 198 )
    # 差一根就让那件 mesh 上**每一条**动画被拒播、整个 entity 不渲染。
    # （目录里躺着的 59 骨原版动画是**没人引用的遗留文件** —— female_body.asset
    # 引用的是 `_1`/`_2` 变体，全是 134 骨，与它的 mesh 相等。别拿那些文件当
    # 「引擎允许少骨」的证据，我拿它当过一次，差点把 191 骨 mesh 配 189 骨动画
    # 装进游戏。）
    for ent, d in sorted(ents.items()):
        mid = d["mesh_id"]
        ref = mesh_n.get(mid)
        if ref is None:
            continue

        # Royal Court 不会把 bn_m_groove 当 ruler 的标准摆位根。曾经洛茜和
        # 洁尔佩塔的“错误 torso mesh + 与它骨数一致的错误动画”一起通过了旧
        # 校验：两者都缺 ground_joint/body_root，只有芙洛伦实际能坐王座。
        # 所以这里必须查父链语义，不能只数骨头。
        if ent.endswith("_torso_entity") or ent == "moe_torso_entity":
            parents = _mesh_bone_parents(mesh_paths[mid])
            expected = {
                "ground_joint": None,
                "body_root": "ground_joint",
                "bn_m_root": "body_root",
            }
            root_errors = [
                f"{bone} 父骨={parents.get(bone)!r}，应为 {parent!r}"
                for bone, parent in expected.items()
                if bone not in parents or parents.get(bone) != parent
            ]
            if root_errors:
                bad += len(root_errors)
                print(f"  !! {ent}: Royal Court torso 根契约错误: "
                      + "; ".join(root_errors))
            else:
                print(f"  {ent}: Royal Court 根链 "
                      "ground_joint -> body_root -> bn_m_root OK")

        files = sorted({id2file[aid] for _state, aid in d["states"]
                        if aid in id2file})
        mine = {f: anim_counts[f] for f in files if f in anim_counts}
        off = sorted(f for f, n in mine.items() if n != ref)
        if off:
            bad += len(off)
            print(f"  !! {ent}: {len(off)} 个 .anim 骨数 ≠ mesh {ref}"
                  f": {off[:5]}")
        else:
            print(f"  {ent}: 全部 {len(mine)} 个实际动画为 {ref} 骨，"
                  f"与自身 mesh 一致")
        dist = Counter(mine.values())
        if len(dist) > 1:
            bad += 1
            print(f"  !! {ent} 的实际 .anim 骨数分布 {dict(dist)} 有多个值"
                  f" —— 可能混进了别的 entity 产物")

        # 六条 ruler 动画必须把场景摆位写到字面 body_root，并把 10 单位座高
        # 校准写到真正驱动 mesh 的 bn_m_root。退回 bn_m_groove 虽能离线摆出
        # 坐姿，却不会满足 Royal Court 的 ruler locator 契约。
        if ent.endswith("_torso_entity") or ent == "moe_torso_entity":
            ruler_files = [f for f in files if "throneRoom_ruler" in f]
            idle_file = next((f for f in files if f.endswith("torso_idle_calm.anim")), None)
            if idle_file is None:
                idle_file = next((f for f in files if f.endswith("idle_calm.anim")), None)
            idle_rest = {}
            if idle_file:
                idle_path = MP.find(idle_file, missing_ok=True)
                if idle_path.is_file():
                    _, idle_rest = _anim_info(idle_path)
            for f in ruler_files:
                p = MP.find(f, missing_ok=True)
                if not p.is_file():
                    continue
                channels, rest_t = _anim_info(p)
                errors = []
                if "body_root" not in channels and "body_root" not in rest_t:
                    errors.append("body_root 不在动画骨架中")
                if "bn_m_root" not in rest_t:
                    errors.append("bn_m_root 不在动画骨架中")
                if "t" in channels.get("bn_m_groove", ""):
                    errors.append("bn_m_groove 仍带旧摆位 t 通道")
                if idle_rest.get("bn_m_root") and rest_t.get("bn_m_root"):
                    dy = rest_t["bn_m_root"][1] - idle_rest["bn_m_root"][1]
                    if abs(dy + 10.0) > 1e-3 or any(
                            abs(rest_t["bn_m_root"][i] - idle_rest["bn_m_root"][i]) > 1e-3
                            for i in (0, 2)):
                        errors.append(f"bn_m_root 相对 idle 的局部位移不是 (0,-10,0)，实际 "
                                      f"({rest_t['bn_m_root'][0]-idle_rest['bn_m_root'][0]:.3f},"
                                      f"{dy:.3f},"
                                      f"{rest_t['bn_m_root'][2]-idle_rest['bn_m_root'][2]:.3f})")
                if errors:
                    bad += len(errors)
                    print(f"  !! {ent}/{f}: " + "; ".join(errors))
            if ruler_files:
                print(f"  {ent}: 已检查 {len(ruler_files)} 条 ruler 根通道")

        # head mesh 不得含道具挂点骨（bn_l_prop / bn_r_prop）
        # 这两根骨只应存在于 torso。如果 head 也有，accessory 会挂到 head 那份，
        # 而 head 动画不驱动手臂，骨停在 T 字手位 → 手持道具错位（书飘在体侧）。
        # 2026-08-29 已确诊一次未落地修法，2026-09-03 再次实机遇到，现加硬闸。
        if ent.endswith("_head_entity") or ent == "moe_head_entity":
            parents = _mesh_bone_parents(mesh_paths[mid])
            prop_bones_in_head = [b for b in ("bn_l_prop", "bn_r_prop") if b in parents]
            if prop_bones_in_head:
                bad += len(prop_bones_in_head)
                print(f"  !! {ent}: head mesh 意外含道具挂点骨 {prop_bones_in_head}"
                      f" —— 必须只存在于 torso，否则道具会挂到 head 那份而错位")
            else:
                print(f"  {ent}: head mesh 不含道具挂点骨 OK")

    # 3b. head/torso 同名 state 配对的**脊椎 rest 必须一致**。
    #
    # 骨数判据抓不住这个：洛茜/洁尔佩塔 2026-09-02 的 head 动画是 08-29 的旧
    # 产物（475/596 骨 = 当前 head mesh 骨数，引擎零报错），而 torso 动画已按
    # 新根链重定向 —— 旧版把身体前倾写在 bn_m_groove（19~22°），新版 groove
    # 归零、姿态挪到 bn_m_spine_low。两侧总角度差不多，但支点高度差 12 单位，
    # 头在 root 局部系里整体前移 3~4 单位，所有动画（含 idle）侧面看脖子撕开。
    #
    # 只比 rest（第 0 帧）就够：嫁接器把脊椎的 rest 与全部帧都从配对 body
    # 逐帧拷贝，rest 一致 ⇒ 帧也一致；rest 不一致 ⇒ 一定是拿错了 body 源。
    # 逐帧 FK 版是 `check_head_torso_sync.py --char <ck>`，这里只做闸。
    bad += _check_pair_spine_rest(ents, id2file, mesh_paths)

    # 4. 循环接缝（自跳转的 state 才有意义）
    if a.seams:
        import check_anim_loop_seam as S
        # 只查**自跳转**的动画（looping = no + next_state 指向自己）。
        # 引擎在这个接缝处**不做插值**，首尾姿势差多少就跳多少。
        # looping = yes 的同理，一并收。
        selfloop = set()
        for m in re.finditer(r'state = \{ name = "([^"]+)"\s*animation = '
                             r'"([^"]+)"([^}]*)\}', txt):
            name, aid, rest = m.groups()
            nx = re.search(r'next_state = "([^"]+)"', rest)
            loops = "looping = yes" in rest or (nx and nx.group(1) == name)
            if loops:
                f = id2file.get(aid)
                if f:
                    selfloop.add(f)
        print(f"\n自跳转/循环的动画 {len(selfloop)} 个，检查首尾接缝 "
              f"(阈值 {a.seam_deg}°):")
        worst = []
        for f in sorted(selfloop):
            p = MP.find(f, missing_ok=True)
            if not p.is_file():
                continue
            try:
                r = S.analyze(p)
            except Exception as e:
                print(f"  !! {f}: {e}")
                continue
            if r and "skip" not in r:
                worst.append((r["max_deg"], r["max_dist"], f, r.get("top_q")))
        worst.sort(reverse=True)
        nb = sum(1 for d, *_ in worst if d > a.seam_deg)
        for d, dist, f, top in worst[:15]:
            bone = top[0][1] if top else ""
            print(f"  {d:>7.2f}° {dist:>7.3f}  {f:<48} {bone}")
        print(f"  超阈值 {nb}/{len(worst)}（这些在循环接缝处会可见跳变）")

    print(f"  小结: {'[PASS] 通过' if bad == 0 else f'!! {bad} 处问题'}")
    return bad


if __name__ == "__main__":
    sys.exit(main())
