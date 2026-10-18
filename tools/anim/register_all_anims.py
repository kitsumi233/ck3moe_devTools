"""把**完整** state 表写进 moe_portrait.asset —— 完整覆盖原版全部肖像动画。

这是 register_body_anims.py 的完整版。差别只在覆盖面:

  register_body_anims  手写 17 条身体动作 + 6 表情 → 120 个 state
  本脚本               照抄原版 890/691 行 state → 每一个原版会请求的 state

## 为什么必须照抄原版 state 表（不能只列我们"做了"的）

引擎播动画的路径是:
    animations.txt 块 → (head = "X", torso = "Y") → 两个 entity 各找自己的
    state X / Y → state 里的 animation id → 动画集里的 .anim

任何一环缺名就报 `Unknown animation state 'X' for entity 'moe_torso_entity'`
并且**不播**（僵在上一姿势）。原版 animations.txt 会请求 368 个 torso state /
288 个 head state，先前只注册了 120，日志刷了 5128 条。

## state 行照抄原版的 looping / next_state / chance

不是简单复制文本，而是**逐字段照抄**:
  looping / next_state  决定循环行为。自跳转（looping = no + next_state 指向
                        自己）是原版模拟循环的手法，改成 looping = yes 会
                        改变时序语义
  chance                同名 state 有多份时的随机权重，原版靠它做变体轮播

我们只把 `animation = "原版 id"` 换成 `animation = "我们的 id"`。
变体 id（Fat / Dwarf / _2）折叠到同一个基名 —— state 名保持原样，
所以引擎照旧能找到名字，只是几个 state 共享同一份动画。

## 与 register_body_anims.py 的关系

本脚本**接管**了它的全部职责（那 17 条身体动作在完整清单里都有对应基名）。
两者标记块不同（本脚本用 register_all_anims.py BEGIN），不会互相冲掉，但
同时跑会产生重复 state 名。构建流程里只跑本脚本。
"""
import argparse
import io
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import moe_anim_catalog as CAT  # noqa: E402

# stdout 转 utf-8。**用 reconfigure 不用 TextIOWrapper** —— 后者每次都新建一个
# 包装对象，多个模块各包一层时外层被 GC 会关掉共享的 buffer，于是后面任何
# print 都抛 `ValueError: I/O operation on closed file`（动画其实已经写完了，
# 只是收尾的 print 炸掉，看起来像整条失败）。reconfigure 原地改编码，天然幂等。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import moe_paths as MP        # 资源目录的单点定义

# 单体目录已退役；asset 由 _asset_for() 按动画集名定位到正确的子 mod。
# 资源根。.anim 在它下面的 flo/anims、lau/anims 里，
# 所以扫描用 rglob 而不是 glob。
ANIM_DIR = MP.BASE


def _asset_for(set_name):
    """含指定动画集的那份 `.asset`。

    `.asset` **按模型子 mod 各出一份、文件名还按游戏区分**（flo 在 gfl2 包的
    `moe_portrait_gfl2.asset`，lau/per 在 endfield 包的
    `moe_portrait_endfield.asset`）。所以既不能用固定的 `MP.BASE`，也不能用
    固定的文件名 —— 注册 lau 时会去 gfl2 那份里找 `moe_common_lau`，报
    「找不到 skeletal_animation_set」然后什么都不做（退出码 1）。

    按**动画集名实际出现在哪一份**里定位，找不到返回 None。
    """
    return MP.asset_containing(f'name = "{set_name}"')

PART_ENTITY = {"head": "moe_head_entity", "torso": "moe_torso_entity"}
SET_NAME = "moe_common"

BEGIN = "\t# >>> register_all_anims.py BEGIN {}"
END = "\t# <<< register_all_anims.py END {}"

# 先前 register_body_anims.py 写的块。本脚本接管后必须**整块删掉** ——
# 留着会与完整表产生同名 state（引擎取哪个未定义），也会引用被折叠掉的
# 旧文件名（moe_body_happy.anim 已经换成 moe_body_emotion_happy.anim）。
OLD_MARKS = [
    ("\t# >>> register_body_anims.py BEGIN anims",
     "\t# <<< register_body_anims.py END anims"),
    ("\t# >>> register_body_anims.py BEGIN moe_head_entity",
     "\t# <<< register_body_anims.py END moe_head_entity"),
    ("\t# >>> register_body_anims.py BEGIN moe_torso_entity",
     "\t# <<< register_body_anims.py END moe_torso_entity"),
]

# build_moe_mod.py 在 entity 段里手写了 18 个 state（idle / emotion_shock /
# throneRoom_bow1 各带 Fat/Dwarf 变体）。完整表里有其中 16 个同名的 ——
# 同名 state 出现两次，引擎取哪个**未定义**，而两份指向不同动画
# （手写的 emotion_shock 指 moe_idle_calm，完整表指 moe_body_emotion_shock）。
# 所以注册完整表前先删掉这些手写行。
#
# 例外：`default_state = "idle"` 那一行不是 state，别删。
# 另外手写的 idleFatDwarf / idleFatDwarf_entry 在原版 state 表里没有
# （原版只到 idleFat），完整表不会覆盖，删掉后就没人定义它们了 ——
# 但也没人请求（animations.txt 不引用），删掉是安全的。
HAND_WRITTEN_PREFIXES = ("idle", "emotion_shock", "throneRoom_bow")


def strip_hand_written(seg):
    """删掉 entity 段里 build_moe_mod 手写的 state 行（保留标记块内的）。"""
    out, n = [], 0
    for ln in seg.splitlines():
        m = re.match(r'\tstate = \{ name = "([^"]+)"', ln)
        if m and m.group(1).startswith(HAND_WRITTEN_PREFIXES):
            n += 1
            continue
        out.append(ln)
    return "\n".join(out), n


def pad(s, n):
    return s + " " * max(1, n - len(s))


def strip_block(text, begin, end):
    pat = re.compile(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", re.S)
    return pat.sub("", text)


def splice(text, begin, end, payload, anchor_re):
    """幂等替换标记块；没有标记时插到 anchor_re 最后一次匹配之后。"""
    pat = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.S)
    block = begin + "\n" + payload + "\n" + end
    if pat.search(text):
        return pat.sub(lambda _m: block, text)
    ms = list(re.finditer(anchor_re, text, re.M))
    if ms:
        at = ms[-1].end()
        return text[:at] + "\n" + block + text[at:]
    # 锚点不存在（删完手写 state 后 entity 段里一行都不剩）→ 插到
    # default_state 之后。它是 entity 段里必然存在的一行，且必须在 state 前面。
    m = re.search(r'^\tdefault_state = .*$', text, re.M)
    if m:
        return text[:m.end()] + "\n" + block + text[m.end():]
    raise SystemExit(f"找不到插入锚点 {anchor_re}，也没有 default_state")


def build_state_lines(part, cat, have_files, missing):
    """原版 state 表 → 我们的 state 行。返回 (行列表, 用到的 anim id 集合)。"""
    lines, used = [], set()
    seen = set()
    for name, vanilla_id, body in cat.states:
        our_id = cat.id_of.get(vanilla_id)
        f = cat.file_of.get(our_id)
        if not f or f not in have_files:
            missing.add(our_id or vanilla_id)
            continue
        # 同名 + 同动画的重复行只留一份。原版靠重复 + chance 做变体轮播，
        # 但变体折叠后它们指向同一份动画，留着只是白刷 state 表。
        key = (name, our_id)
        if key in seen:
            continue
        seen.add(key)
        used.add(our_id)

        extra = []
        m = re.search(r'chance\s*=\s*(\d+)', body)
        if m:
            extra.append(f"chance = {m.group(1)}")
        # looping / next_state **只在原版写了的时候才写**。
        # 补默认值是有害的：原版 `state = { name = "none" animation = ... }`
        # 故意两个字段都不写（"none" 是 default_state 的静止姿势），
        # 硬加 looping = no 会改变引擎对该 state 的处理。
        m = re.search(r'looping\s*=\s*(yes|no)', body)
        if m:
            extra.append(f"looping = {m.group(1)}")
        m = re.search(r'next_state\s*=\s*"([^"]+)"', body)
        if m:
            extra.append(f'next_state = "{m.group(1)}"')
        m = re.search(r'state_time\s*=\s*([\d.]+)', body)
        if m:
            extra.append(f"state_time = {m.group(1)}")
        lines.append(f'\tstate = {{ name = {pad(chr(34) + name + chr(34), 52)}'
                     f'animation = {pad(chr(34) + our_id + chr(34), 46)}'
                     f'{" ".join(extra)} }}')
    return lines, used


def collapse_variants(payload, prefix, have=None):
    """同一个 state 名只留**一个**动画，且 head / torso 选同一个基名。

    ## 为什么必须做（用户 2026-08-28 报的「anger 一半时间头身对不上」）

    原版靠**重复同名 state 行**做变体轮播：`emotion_angry_entry` 写三遍，
    分别指向 `_start` / `_start_var` / `_start_var2`，引擎在这几条里随机挑。

    问题是 **head 与 torso 是两个独立 entity，各自独立随机**。原版能这么写
    是因为它两侧的变体是配套设计的（挑第几个都对得上）；我们的头部动画是
    `graft_all_head_anims.py` 从**同名**身体动画嫁接来的，只有两侧挑到
    **同一个基名**时脊椎才一致。

    实测 anger：head 侧三个变体、torso 侧折叠后只剩 `_start` 一个 ——
    head 恰好抽中 `_start` 才对得上，所以约 1/3 的时间正常、2/3 脱节。
    这正是「有一半时间对得上，另一半对不上」。

    happy（两侧各 3 变体）/ shock（各 2）/ wedding_happy_cry_loop（各 3）
    有同样的隐患：即使两侧变体集合**完全相同**，独立随机也会各挑各的。
    所以判据不是「两侧集合是否一致」，而是「同名 state 是否只有一个动画」。

    ## 修法

    每个 state 名只留一条：优先选**两侧都有**的基名（保证一致），多个候选
    时取排序最前的（`_start` < `_start_var` < `_start_var2`，天然偏向无后缀
    的原始动作）。代价是失去变体轮播的随机性 —— 我们一个角色只有一具 mesh，
    本来也用不上体型变体，随机性换同步是划算的。

    副作用（正向）：折叠后 `_var` / `_var2` 不再被引用，`all_used` 自然不
    声明它们，省掉一批 animation id。

    注意 `next_state` 可能随变体不同（`throneRoom_ruler1` vs `ruler2`）——
    两侧都按同一规则选，选出来的 `next_state` 也就一致，不会错位。
    """
    pat = re.compile(r'\tstate = \{ name = "([^"]+)"\s+animation = "([^"]+)"')

    def base(aid, part):
        pfx = f"{prefix}head_" if part == "head" else f"{prefix}body_"
        return aid[len(pfx):] if aid.startswith(pfx) else aid

    # 每个 state 名下、每侧出现过的基名（保持出现顺序）
    seen = {"head": {}, "torso": {}}
    for part in ("head", "torso"):
        for st, aid in pat.findall(payload[part]):
            seen[part].setdefault(st, [])
            b = base(aid, part)
            if b not in seen[part][st]:
                seen[part][st].append(b)

    # 决定每个 state 名保留哪个基名。
    #
    # **候选必须两侧都真有文件**。只看「state 表里两侧都出现过这个基名」是不够
    # 的：`event_newborn_entry` 两侧都写着 `event_newborn_start`，但盘上只有
    # `<pfx>head_event_newborn_start.anim`，没有 body 那份（原版躯干在这个
    # state 播的是别的动画，由 reconcile_pairs 特案处理）。不查存在性就会写出
    # 指向不存在文件的引用 —— 引擎报 missing，而 `collapse_variants` 自己
    # 一声不响。实测就是这么把 3 个包各刷出一条 `animation 指向不存在的文件`。
    def exists(base):
        if have is None:
            return True
        return (f"{prefix}head_{base}.anim" in have
                and f"{prefix}body_{base}.anim" in have)

    # `keep[state][part]` = 该部件保留的基名。**两侧没有共同基名时各留自己的**，
    # 不把一侧的基名强加给另一侧 —— 那是 `reconcile_pairs` 的职责，它会查文件
    # 存在性并按「优先改躯干」的优先级来调。
    #
    # 曾经无条件写成 `sorted(hb or tb)[0]`，于是 `event_newborn_entry`
    # （torso 播 `body_event_newborn`、head 播 `head_event_newborn_start`，
    # 两侧基名本就不同）被强行统一到 head 那个基名，写出指向
    # `moe_body_event_newborn_start.anim` 的引用 —— 那个文件不存在，
    # 三个包各刷一条 `animation 指向不存在的文件`。
    keep = {}
    for st in set(seen["head"]) | set(seen["torso"]):
        hb, tb = seen["head"].get(st, []), seen["torso"].get(st, [])
        both = sorted(set(hb) & set(tb))
        ok = [b for b in both if exists(b)]
        shared = (ok or both)
        if shared:
            keep[st] = {"head": shared[0], "torso": shared[0]}
        else:
            keep[st] = {"head": (sorted(hb) or [None])[0],
                        "torso": (sorted(tb) or [None])[0]}

    # 逐部件重写：命中保留基名的第一条留下，其余同名行整条删掉
    stats = {"dropped": 0, "rewritten": 0}
    for part in ("head", "torso"):
        pfx = f"{prefix}head_" if part == "head" else f"{prefix}body_"
        kept, out = set(), []
        for line in payload[part].splitlines():
            m = pat.match(line)
            if not m:
                out.append(line)
                continue
            st, aid = m.group(1), m.group(2)
            if st in kept:
                stats["dropped"] += 1
                continue
            base = keep[st][part]
            if base is None:      # 这一侧本来就没有这个 state，不该走到这
                kept.add(st)
                out.append(line)
                continue
            want = pfx + base
            if aid != want:
                line = line.replace(f'animation = "{aid}"',
                                    f'animation = {pad(chr(34) + want + chr(34), len(aid) + 2)}',
                                    1)
                stats["rewritten"] += 1
            kept.add(st)
            out.append(line)
        payload[part] = "\n".join(out)

    multi = {s for s in keep
             if len(seen["head"].get(s, [])) > 1 or len(seen["torso"].get(s, [])) > 1}
    if multi:
        print(f"  变体折叠：{len(multi)} 个 state 名有多变体，"
              f"删 {stats['dropped']} 行、改 {stats['rewritten']} 行动画引用")
        for s in sorted(multi)[:6]:
            k = keep[s]
            shown = k["head"] if k["head"] == k["torso"] else f"head={k['head']} torso={k['torso']}"
            print(f"    {s}: head={seen['head'].get(s, [])} "
                  f"torso={seen['torso'].get(s, [])} → {shown}")
    return stats["dropped"] + stats["rewritten"]


def reconcile_pairs(payload, have, prefix):
    """强制 head 与 torso 在同一个 state 上播**同名**动画。

    ## 为什么必须做

    原版的 head/torso state 表在同一个 state 名下可以指向**不同名**的动画
    （它自己的两份动画是配套设计的）。我们的头部动画则是靠
    `graft_all_head_anims.py` 从**同名**身体动画嫁接脊椎通道来的 ——
    嫁接源与 state 表的实际配对不是同一个文件时，头和躯干的脊椎就不一致，
    表现是**动画过程中头和身子分离**（用户 2026-08-28 报的 anger 就是这个）。

    实测 515 组配对里 9 组异名，脊椎差 7.48~23.49°；其余 506 组同名的差
    ~0.04°。9 组全是原版给胖子/矮胖子准备的体型变体（`_var2` / `Fat` /
    `FatDwarf`）—— 我们一个角色只有一具 mesh，用不上这种区分，所以统一到
    同名是无损的。

    **多变体的情况先由 `collapse_variants` 处理掉**（同名 state 只留一条），
    这个函数只管剩下的一对一异名配对。顺序反了的话下面 `parse()` 的 dict
    会让同名多变体只剩最后一个，判不出「另一侧其实还有别的变体」。

    ## 修法与优先级

    1. 躯干改播 `<prefix>body_<头的基名>`（存在就用）—— 优先改躯干，因为
       头部动画带表情通道（morph 代理骨），换掉会丢表情。
    2. 否则头改播 `<prefix>head_<躯干的基名>`。
    3. 两者都不存在就留着不动，并打印出来 —— 静默跳过会让下次有人以为
       修全了。
    """
    pat = re.compile(r'(\tstate = \{ name = )("[^"]+")(\s+animation = )("[^"]+")')

    def parse(part):
        out = {}
        for m in pat.finditer(payload[part]):
            out[m.group(2).strip('"')] = m.group(4).strip('"')
        return out

    h, t = parse("head"), parse("torso")
    fixes = {"head": {}, "torso": {}}
    unresolved = []
    for st, tid in t.items():
        hid = h.get(st)
        if not hid:
            continue
        hbase = hid[len(f"{prefix}head_"):] if hid.startswith(f"{prefix}head_") else None
        tbase = tid[len(f"{prefix}body_"):] if tid.startswith(f"{prefix}body_") else None
        if hbase is None or tbase is None or hbase == tbase:
            continue
        want_t = f"{prefix}body_{hbase}"
        want_h = f"{prefix}head_{tbase}"
        if want_t + ".anim" in have:
            fixes["torso"][st] = (tid, want_t)
        elif want_h + ".anim" in have:
            fixes["head"][st] = (hid, want_h)
        else:
            unresolved.append((st, hid, tid))

    for part in ("head", "torso"):
        if not fixes[part]:
            continue
        def sub(m, _p=part):
            st = m.group(2).strip('"')
            if st in fixes[_p]:
                _old, new = fixes[_p][st]
                return f'{m.group(1)}{m.group(2)}{m.group(3)}{pad(chr(34) + new + chr(34), len(m.group(4)))}'
            return m.group(0)
        payload[part] = pat.sub(sub, payload[part])

    n = sum(len(v) for v in fixes.values())
    if n:
        print(f"  头身配对协调：改了 {n} 个 state"
              f"（躯干 {len(fixes['torso'])} / 头 {len(fixes['head'])}）")
        for part in ("torso", "head"):
            for st, (old, new) in sorted(fixes[part].items())[:12]:
                print(f"    [{part}] {st}: {old} → {new}")
    if unresolved:
        print(f"  !! 还有 {len(unresolved)} 组异名配对没法统一（两侧同名文件都不存在）：")
        for st, hid, tid in unresolved[:6]:
            print(f"    {st}: head={hid} torso={tid}")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--anim-dir", default=None,
                    help="要扫描 .anim 文件是否存在的目录（默认 ANIM_DIR，"
                         "即芙洛伦的 mod 目录；两个角色的 .anim 最终都放在"
                         "同一个 mod 目录下，只是文件名前缀不同）。")
    ap.add_argument("--out-prefix", default=None,
                    help="产物文件名前缀（默认 moe_anim_catalog.OUT_PREFIX，"
                         "即 'moe_'）。换角色时用不同前缀（如 'moe_lau_'），"
                         "避免 id/文件名与芙洛伦已注册的撞名。")
    ap.add_argument("--set-name", default=SET_NAME,
                    help=f"要写入的 skeletal_animation_set 名字（默认 {SET_NAME}）。")
    ap.add_argument("--torso-set-name", default=None,
                    help="torso 使用的独立动画集；省略时若 asset 含 "
                         "<set-name>_torso 则自动采用，否则与 head 共用。")
    ap.add_argument("--head-entity", default=PART_ENTITY["head"],
                    help=f"head entity 名字（默认 {PART_ENTITY['head']}）。")
    ap.add_argument("--torso-entity", default=PART_ENTITY["torso"],
                    help=f"torso entity 名字（默认 {PART_ENTITY['torso']}）。")
    a = ap.parse_args()

    # **按目标动画集定位 asset**，不要用固定基址：asset 已按模型子 mod
    # 各出一份，用 MP.BASE 注册 lau 会在 gfl2 那份里找不到它的动画集。
    global ASSET
    _hit = _asset_for(a.set_name)
    if _hit is None:
        sys.exit(f"**没有任何 .asset 含动画集 {a.set_name}** —— "
                 f"找过 {[p.name for p in MP.assets()]}。"
                 f"先跑 build_moe_mod.py 生成骨架块。")
    ASSET = _hit
    print(f"目标 asset: {ASSET}")

    # 动画目录默认取该 asset 同目录 —— 引用是**相对 .asset 所在目录**解析的，
    # 扫别的 mod 的目录会算出错误的相对路径。
    anim_dir = Path(a.anim_dir) if a.anim_dir else ASSET.parent
    if a.out_prefix:
        CAT.OUT_PREFIX = a.out_prefix
    part_entity = {"head": a.head_entity, "torso": a.torso_entity}

    # **必须 rglob**：.anim 在 flo/anims、lau/anims 子目录里。用 glob 只扫
    # 一层会得到空集，于是下面 1404 条 state 全被判成「缺产物」、state 表
    # 整个空掉 —— 而日志只会说「缺 N 个产物」，看起来像动画还没生成。
    #
    # have 保持**裸名**集合：build_state_lines 与 moe_anim_catalog.file_of
    # 两侧都按裸名比对，改语义会牵连更多地方。另建 rel_of 表，在写 .asset
    # 引用时查出带子目录的相对路径。
    anim_files = sorted(anim_dir.rglob("*.anim"))
    have = {p.name for p in anim_files}
    rel_of = {p.name: p.relative_to(anim_dir).as_posix() for p in anim_files}
    if len(rel_of) != len(anim_files):
        raise SystemExit(
            "**有同名 .anim 分布在不同子目录** —— 裸名映射会互相覆盖，"
            "而引擎按裸名建索引本身也不允许重名")
    print(f"磁盘上有 {len(have)} 个 .anim（{anim_dir} 递归）")

    txt = ASSET.read_text(encoding="utf-8", errors="replace")
    torso_set_name = a.torso_set_name or (
        f"{a.set_name}_torso"
        if re.search(r'\bname\s*=\s*"' + re.escape(a.set_name) + r'_torso"', txt)
        else a.set_name)
    set_of_part = {"head": a.set_name, "torso": torso_set_name}
    if torso_set_name != a.set_name:
        print(f"  动画集按部件拆分: head={a.set_name}, torso={torso_set_name}")
    for b, e in OLD_MARKS:
        if b in txt:
            txt = strip_block(txt, b, e)
            print(f"  删掉旧块 {b.strip()[6:]}")

    anim_lines, state_payload = [], {}
    all_used = set()
    for part in ("torso", "head"):
        cat = CAT.Catalog(part)
        missing = set()
        lines, used = build_state_lines(part, cat, have, missing)
        state_payload[part] = "\n".join(lines)
        all_used |= used
        print(f"  {part}: {len(lines)} 行 state，引用 {len(used)} 个动画"
              f"{f'，缺 {len(missing)} 个产物' if missing else ''}")
        if missing:
            print(f"    缺: {sorted(missing)[:8]}")

    # 两个 payload 都建好之后再协调配对。**必须在这里**，不能放到
    # build_state_lines 里 —— 那个函数一次只看一个部位，判不出「另一边播的是
    # 什么」。协调可能把某个动画引用换掉，所以要在下面收集 all_used 之前做。
    # 用 CAT.OUT_PREFIX 不用 a.out_prefix —— 后者默认 None（芙洛伦走默认值），
    # 而 CAT.OUT_PREFIX 已在上面被 --out-prefix 覆盖过，两条路都对。
    #
    # **先折叠多变体再做一对一配对**：reconcile_pairs 用 dict 存 state→动画，
    # 同名多变体会只剩最后一个，于是它看不见「head 其实还挂着两个别的变体」，
    # anger 那三个 state 就这么被判成「两侧已一致」放过去了。
    collapse_variants(state_payload, CAT.OUT_PREFIX, have)
    reconcile_pairs(state_payload, have, CAT.OUT_PREFIX)
    used_by_part = {
        part: set(re.findall(r'animation = "([^"]+)"', state_payload[part]))
        for part in ("head", "torso")
    }
    anim_lines_by_set = {}
    for part in ("head", "torso"):
        set_name = set_of_part[part]
        lines = anim_lines_by_set.setdefault(set_name, [])
        already = {re.search(r'id = "([^"]+)"', ln).group(1) for ln in lines}
        for aid in sorted(used_by_part[part] - already):
            f = aid + ".anim"
            # 引用要带子目录（相对 .asset 所在目录）。查不到就是产物缺失，
            # 此时退回裸名 —— 引擎会报 missing，比静默写错路径好排查。
            ref = rel_of.get(f, f)
            lines.append(f'\tanimation = {{ id = {pad(chr(34) + aid + chr(34), 56)}'
                         f'type = "{ref}" }}')
    print("  动画集: " + ", ".join(
        f"{name}={len(lines)} 条" for name, lines in anim_lines_by_set.items()))

    if a.dry_run:
        print("\n(dry-run) 前 5 条 animation / 每部件前 3 行 state:")
        for name, lines in anim_lines_by_set.items():
            for ln in lines[:5]:
                print(f"[{name}] {ln}")
        for part in ("torso", "head"):
            for ln in state_payload[part].splitlines()[:3]:
                print(f"[{part}] {ln}")
        return

    # 备份写到 work/ 而不是 mod 目录 —— 放在 mod 里会被 finalize_mods.py 一起
    # 部署进游戏。引擎不认这个后缀所以不会报错，但它是无用负载，
    # 而且看起来像一份「另一个版本的 asset」，排查时容易误读。
    bak = (ROOT / "work" / "asset_backups"
           / f"{ASSET.name}.before_{a.set_name}")
    bak.parent.mkdir(parents=True, exist_ok=True)
    if not bak.exists():
        shutil.copy2(ASSET, bak)
        print(f"  备份 → work/asset_backups/{bak.name}")

    # skeletal_animation_set 段也要**先切段再替换**，跟 entity 段同一手法 ——
    # 两个角色各有一个同名（skeletal_animation_set）块，不切段直接用全局
    # anchor_re 会插到「文件里最后一条 animation 行」之后，落进错误的 set。
    for set_name, anim_lines in anim_lines_by_set.items():
        m = re.search(r'skeletal_animation_set\s*=\s*\{\s*\n\s*name\s*=\s*"'
                      + re.escape(set_name) + r'"', txt)
        if not m:
            raise SystemExit(f"找不到 skeletal_animation_set {set_name}")
        nxt = re.search(r'\n(skeletal_animation_set|pdxmesh|entity)\s*=\s*\{',
                        txt[m.end():])
        end = m.end() + (nxt.start() if nxt else len(txt) - m.end())
        seg = txt[m.start():end]
        seg = splice(seg, BEGIN.format("anims"), END.format("anims"),
                     "\n".join(anim_lines), r'^\tanimation = \{ id = .*$')
        txt = txt[:m.start()] + seg + txt[end:]

    for part in ("head", "torso"):
        ent = part_entity[part]
        entity_variants = [ent]
        child_ent = ent + "_child"
        if re.search(r'entity\s*=\s*\{\s*\n\s*name\s*=\s*"'
                     + re.escape(child_ent) + r'"', txt):
            entity_variants.append(child_ent)
        for entity_name in entity_variants:
            # 只在该 entity 段内替换：先切段，改完拼回。
            m = re.search(r'entity\s*=\s*\{\s*\n\s*name\s*=\s*"'
                          + re.escape(entity_name) + r'"', txt)
            if not m:
                raise SystemExit(f"找不到 entity {entity_name}")
            nxt = re.search(r'\nentity\s*=\s*\{', txt[m.end():])
            end = m.end() + (nxt.start() if nxt else len(txt) - m.end())
            seg = txt[m.start():end]
            # 先删手写 state（会与完整表同名），再插完整表。
            # 顺序反了的话手写行会留在标记块前面，同名冲突照旧。
            seg, nrm = strip_hand_written(seg)
            if nrm:
                print(f"  {entity_name}: 删掉 {nrm} 行手写 state（完整表已覆盖）")
            seg2 = splice(seg, BEGIN.format(entity_name), END.format(entity_name),
                          state_payload[part], r'^\tstate = \{.*$')
            txt = txt[:m.start()] + seg2 + txt[end:]

    ASSET.write_text(txt, encoding="utf-8")
    n = len(txt.splitlines())
    st = len(re.findall(r'^\tstate = \{', txt, re.M))
    an = len(re.findall(r'^\tanimation = \{', txt, re.M))
    print(f"\n写出 {ASSET.name}: {n} 行，{st} 个 state，{an} 条 animation")


if __name__ == "__main__":
    main()
