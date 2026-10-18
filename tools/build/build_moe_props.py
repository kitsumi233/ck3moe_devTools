# -*- coding: utf-8 -*-
"""给 moe 各 group 生成手持道具（accessory）支持。

用户 2026-08-28 第 5 项 (a)：「播放比如翻书之类的动作时只有动作却没书」。

## 根因（三层都缺）

道具要三层才挂得上：
  1. `animations.txt` 的 `portrait_modifier_pack = paged_book`（原版已有，
     我们的 type 靠 `moe_flo = male` 别名继承 —— 这层本来就通）
  2. modifierpack → `animations_props = fp4_paged_book_prop` → portrait_modifier
     的 `dna_modifiers = { accessory = { gene = animated_props
     template = fp4_paged_book } }`（原版已有）
  3. **那个 gene 必须存在于角色所在的 portrait group，且模板里要有本 group 的
     type 条目** ← 我们缺的是这一层

原版 `animated_props` 等基因的模板逐 type 穷举（`male` / `female = male` /
`boy` / `girl`），186 个模板里**没有一个有 `default` 兜底**，我们的
`moe_flo` / `moe_lau` / `moe_per` 全不在列。所以引擎查不到条目 → 不挂道具，
**且不报错**。

## 做法

在我们自己的 group 容器里定义**同名基因 + 同名模板**（gene 名可与原版重名，
命名空间按 group 隔离 —— 与 color_genes 同一套机制）。这样原版那一整套
modifierpack / portrait_modifier 不用改一个字就生效。

accessory 用**原版挂点名** `bn_l_prop` / `bn_r_prop`，这两根骨由
`tools/add_prop_bones.py` 追加进我们的八份 mesh（2026-08-29）。

## 为什么不能换成自己的骨名（走过的弯路）

一度重映射到 `bn_m_l_wrist` / `bn_m_r_wrist`（我们四个角色都有）。**不行**：
accessory 数据库是**全局**的，引擎拿每个 `node` 去校验**所有**身体 mesh，
原版身体没有 `bn_m_*` 骨 → 实机刷 432 条
`invalid node 'bn_m_r_wrist' for portrait accessory '...'`。挂点名只能用
原版身体也有的名字，所以正解是给我们的 mesh 加骨，不是改 accessory。

`ground_joint`（马匹/战车 38 个）与 `bn_h_head_mid`（1 个）仍不接：那些整匹
站在地上，挂到角色手上荒谬。它们走「查得到但 type 块留空」。
"""
import sys as _sys
from pathlib import Path as _P
_sys.path[:0] = [str(_P(__file__).resolve().parents[2] / "tools" / _d) for _d in ("lib", "anim")]   # 子目录化后底座不在脚本同目录
import moe_out as O          # 产物归属：谁写谁声明
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GAME = Path(r"E:\SteamLibrary\steamapps\common\Crusader Kings III\game")

# type 名与 group 名按约定从角色键推（moe_<ck> → moe_<ck>_g），角色清单以
# moe_out.GAME_OF 为唯一事实源。**别再手抄一份**：2026-09-06 加艾尔黛拉时这里
# 漏了她，props 步骤零报错地少写两份文件，直到 finalize 自检才抓到。
GROUP = "moe"
import moe_chars as X
# Catalog readers import only the vanilla parsers. Resolve project readiness
# when building, otherwise every retarget subprocess audits all release roles.
TYPES = {}

# 我们接手的挂点。`ground_joint`（马匹/战车 38 个）与 `bn_h_head_mid`（1 个）
# 不在表里，走「查得到但 type 块留空」。
#
# **prop 骨现在只存在于 torso mesh**（2026-09-03 修正）：head + torso 两个 entity
# 都有 prop 骨时引擎挂到 head 那份，而 head 动画不驱动手臂 → 道具错位（书飘在
# 体侧）。`bake_moe_expressions.py` 的 prop 骨构建已移至 `add_torso_court_roots()`
# 函数内（与 ground_joint/body_root 同一个钩子），只在 torso 导出时添加。
# **挂点名必须与原版身体一致**：accessory 库是全局的，引擎拿 node 名去校验
# 所有身体 mesh，用我们自己的 `bn_m_*` 骨名会在原版身体上全部报 `invalid node`。
#
# **等 mesh 带上 prop 骨之后，把值改成恒等映射**（键=值）。下面的判定按
# **实际 torso mesh** 决定用哪套名字 —— 骨数必须严格等于动画骨数，猜错一边
# 就是整个 entity 不渲染。
_VANILLA_NODES = {"bn_l_prop", "bn_r_prop"}
_FALLBACK = {"bn_l_prop": "bn_m_l_wrist", "bn_r_prop": "bn_m_r_wrist"}

# 三个道具基因。**基因名必须自造，不能与原版重名。**
#
# 一度以为「gene 的命名空间按 portrait group 隔离」（照 color_genes 类推），
# 实机否证：基因名是**全局**的。同名时两份定义按名字合并，合并后的模板归
# `human`，于是我们 group 的 accessory 被静默跳过。判据是那条报错的措辞——
#     accessory group [props_right:ep1_mena_book_big] has accessory
#     moe_flo_ep1_mena_book_big_01 which is only scripted to work in
#     portrait group human not moe_flo_g
# 它把**原版的** gene:template 名和**我们的** accessory 名写在同一行，
# 说明两边已经按名字并到一起了。
#
# 代价：原版那 242 个 `animations_props` item 不能原样复用（它们写死
# `gene = props_left`），必须自造 portrait_modifier 容器（write_modifier_container()）；
# .modifierpack 则由核心整份覆盖并把我们的键并入每个原版块（write_modifierpack_override()），
# 逐角色的 40_moe_props_<ck>.modifierpack 2026-09-06 退役（trash/20260906_per_ck_modifierpacks）。
PROP_GENES = ("animated_props", "props_left", "props_right")
# CK3 标识符通常是小写，但原版弓道具确实有 `bowBlunt` 这类大写片段。
# 只匹配 [a-z] 会把名字静默截断成另一个、并不存在的 modifier 名。
IDENT = r'[A-Za-z0-9_]+'


def gene_name(ck, vanilla_gene):
    """原版基因名 → 我们的自造名。`props_left` → `moe_flo_props_left`。"""
    return f"{GROUP}_{ck}_{vanilla_gene}"


def _node_map():
    """现役 mesh 有 prop 骨就用原版名，没有就退回腕骨。"""
    import moe_paths as _MP
    import pdx_parse as _PP
    for p in sorted(_MP.BASE_DIRS[0].rglob("*_torso.mesh")) \
            + sorted(_MP.BASE_DIRS[0].rglob("moe_torso.mesh")):
        got = set()
        for pp, _n, _k, _v in _PP.walk(_PP.parse(p.read_bytes())):
            if "/skeleton/" in pp:
                got.add(pp.split("/skeleton/", 1)[1].split("[")[0])
        if _VANILLA_NODES <= got:
            print(f"  挂点用原版名（{p.name} 已有 prop 骨）")
            return {n: n for n in _VANILLA_NODES}
        print(f"  !! {p.name} 没有 prop 骨 —— 挂点退回腕骨，"
              f"实机会刷 432 条 invalid node。修法：重烘该角色的表情"
              f"（bake_moe_expressions.py 会建 prop 骨）并重生成她的 .anim")
        return dict(_FALLBACK)
    return dict(_FALLBACK)


NODE_MAP = {}


def parse_accessories():
    """props.txt → {accessory 名: (node, entity, 额外字段行)}。

    **按花括号配平取 entity 块，不要用「entity 名后面紧跟 `}`」的正则**：原版
    武器类条目在 entity 名之后还有 `game_entity_override = weapon` 一行，紧跟
    `}` 的写法会把它们**整批漏掉**（匕首/佩剑 8 类，表现是模板留空、道具不挂）。
    额外字段要原样带上，不然武器换装逻辑丢了。
    """
    txt = (GAME / "gfx/portraits/accessories/props.txt").read_text(
        encoding="utf-8-sig", errors="replace")
    out = {}
    for m in re.finditer(rf'^({IDENT}) = \{{', txt, re.M):
        name = m.group(1)
        body = _balanced(txt, m.end() - 1)
        if body is None:
            continue
        em = re.search(r'entity = \{', body)
        if em is None:
            continue
        eb = _balanced(body, em.end() - 1)
        if eb is None:
            continue
        tm = re.search(r'required_tags = "([^"]*)"', eb)
        nm = re.search(r'node = "([^"]+)"', eb)
        am = re.search(r'entity = "([^"]+)"', eb)
        if not (tm and nm and am):
            continue
        # required_tags 非空的是按体型/文化分支的，同名 accessory 会有多条
        # entity 行。只取无标签那条（通用版），多条时后面的不覆盖前面。
        if tm.group(1) or name in out:
            continue
        extra = [ln.strip() for ln in eb.split("\n")
                 if ln.strip() and not re.match(
                     r'(required_tags|node|entity)\s*=', ln.strip())]
        out[name] = (nm.group(1), am.group(1), extra)
    return out


def parse_genes():
    """扫 common/genes/*.txt，取出 accessory_genes 里的基因与模板。

    返回 [(gene 名, 模板名, index, [accessory 名...])]。

    **只认 `male = {}` 块里的条目**：原版其余 type 都写 `female = male` 这种
    别名，内容一致。我们只需要一份内容，再挂到自己的 type 名下。
    """
    rows = []
    for f in sorted((GAME / "common/genes").glob("*.txt")):
        txt = f.read_text(encoding="utf-8-sig", errors="replace")
        # accessory_genes 容器可以在顶层，也可以嵌在 special_genes 里。
        # 两种缩进都要认，所以不按缩进定位、直接找容器名再按花括号配平。
        for cm in re.finditer(r'\baccessory_genes\s*=\s*\{', txt):
            body = _balanced(txt, cm.end() - 1)
            if body is None:
                continue
            for gm in re.finditer(rf'\n\s*({IDENT})\s*=\s*\{{', body):
                gname = gm.group(1)
                gbody = _balanced(body, gm.end() - 1)
                if gbody is None:
                    continue
                for tm in re.finditer(rf'\n\s*({IDENT})\s*=\s*\{{', gbody):
                    tname = tm.group(1)
                    tbody = _balanced(gbody, tm.end() - 1)
                    if tbody is None:
                        continue
                    im = re.search(r'\bindex\s*=\s*(\d+)', tbody)
                    mm = re.search(r'\bmale\s*=\s*\{', tbody)
                    if not (im and mm):
                        continue
                    male = _balanced(tbody, mm.end() - 1) or ""
                    accs = re.findall(rf'\d+\s*=\s*({IDENT})', male)
                    # **空模板也要收**（`male = { }`）。原版每个道具基因的 index 0
                    # 就是这种空模板（`no_props`），漏掉它会让下游走「补一个
                    # index 0」的兜底、把名字造成 `moe_no_prop` —— 而原版 DNA 与
                    # portrait_modifier 都按 `no_props` 这个**名字**查模板，改了名
                    # 就查不到。留空与「不存在」是两件事，别用 `if accs` 合并。
                    rows.append((gname, tname, int(im.group(1)), accs))
    return rows


def _balanced(s, i):
    """s[i] 是 '{'，返回配平的内部文本（不含两端花括号）。"""
    if i >= len(s) or s[i] != "{":
        return None
    d = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            d += 1
        elif s[j] == "}":
            d -= 1
            if d == 0:
                return s[i + 1:j]
    return None


def main():
    global TYPES, NODE_MAP
    TYPES = {c["type"]: c["group"] for c in X.active()}
    NODE_MAP = _node_map()
    acc = parse_accessories()
    rows = parse_genes()
    print(f"原版 props.txt accessory {len(acc)} 个；"
          f"accessory_genes 里的模板 {len(rows)} 条")

    # 我们只接手持类（NODE_MAP 认识的挂点）。但**其余模板也要定义、type 块留
    # 空** —— 原版的 portrait_modifier 写死了 `gene = X template = Y`，模板缺失
    # 时引擎查不到条目，行为未验证（可能刷屏报错）。定义成「查得到但不挂东西」
    # 最稳：马匹/战车那类是 `ground_joint`（整匹站在地上），挂到腕骨会很荒谬，
    # 所以刻意不给 accessory。
    keep, used_acc, empty = [], {}, 0
    for gname, tname, idx, accs in rows:
        if gname not in PROP_GENES:
            continue
        hit = [a for a in accs if a in acc and acc[a][0] in NODE_MAP]
        keep.append((gname, tname, idx, hit))
        if hit:
            for a in hit:
                used_acc[a] = acc[a]
        else:
            empty += 1
    print(f"三个道具基因的模板 {len(keep)} 条："
          f"给 accessory 的 {len(keep) - empty} 条、留空的 {empty} 条"
          f"（ground_joint 类）；涉及 accessory {len(used_acc)} 个")
    if not used_acc:
        sys.exit("没解析出任何手持道具 —— 解析器与原版格式不符，别静默产出空文件")

    # ---- 我们的 accessory：复用原版 entity，挂点也照原版
    #
    # **一个 group 一份文件、accessory 名带 group 前缀，且文件放进该 group 所属的
    # 模型子 mod。**
    #
    # 为什么名字要带 group 前缀：一个 accessory 只能属于一个 portrait group，
    # 三个 group 共用同一批名字时只有一份能生效（同名 accessory 全局唯一，
    # 后加载的整份顶掉前一份）。基因侧引用的也是带前缀的名字。
    #
    # 为什么文件要跟着 group 走（而不是全放核心）：group 由模型子 mod 的
    # `common/portrait_types/` 定义，accessory 的 group 归属靠「引用它的基因所在
    # group」推出来 —— 放在核心时实机刷 678 条
    #   accessory group [props_right:ep1_mena_book_big] has accessory
    #   moe_ep1_mena_book_big_01 which is only scripted to work in
    #   portrait group human not moe_per_g
    # 表现是道具一个都不挂（用户报的「翻书没有书」）。
    #
    # **归属靠每个 accessory 块里的 `portrait_group` 显式声明**，不靠「引用它的
    # 基因在哪个 group」推断 —— 上面那 678 条报错的真正含义是「你没声明，所以
    # 我按缺省算成 human」。
    #
    # 位置只有这一处对（2026-08-29 逐个实机否证）：
    #   - 写在**文件顶层** → 后面每条定义都报
    #     `Unexpected token: moe_flo_afr_marshal_sword_01`（648 条），整份解析
    #     失败，紧接着 678 条 `has an invalid accessory`。
    #   - 不写 → 678 条 `only scripted to work in portrait group human`。
    #   - 写在 accessory 块内、与 `entity` 平级 → 对。
    # 原版 accessory 文件里这个字段零出现，因为原版只有 human 一个 group，
    # 缺省即正确；自定义 group 必须显式写。
    for _t, _g in sorted(TYPES.items()):
        _pfx = f"{GROUP}_{_t.split('_', 1)[1]}"      # moe_flo / moe_lau / moe_per
        lines = [
            f"# {_g} 的手持道具 accessory。生成自 tools/build/build_moe_props.py，不要手改。",
            "#",
            "# 复用原版的 entity（模型/贴图/道具自身动画都是原版那份），挂点也**照抄",
            f"# 原版的 {' / '.join(sorted(NODE_MAP))}** —— accessory 库是全局的，引擎拿",
            "# node 名去校验所有身体 mesh，换成我们自己的骨名会在原版身体上全部报",
            "# `invalid node`（实测 432 条）。这两根骨由 bake_moe_expressions.py",
            "# 建进我们的八份 mesh（父骨=腕骨、绑定朝向=单位矩阵，与原版一致）。",
            "#",
            f"# 名字带 group 前缀 `{_pfx}_`：一个 accessory 只能属于一个 portrait",
            "# group，三个 group 共用同一批名字时只有一份生效。",
            "#",
            "# **每个 accessory 块里都要写 `portrait_group`**，与 `entity` 平级。",
            "# 这是 accessory 的 group 归属的**唯一**来源 —— 不写就全落进 human，",
            "# 实机刷 678 条 `only scripted to work in portrait group human not",
            f"# {_g}`，道具一个都不挂。",
            "#",
            "# 位置很讲究：写在**文件顶层**会让后面每条定义都报",
            "# `Unexpected token`、整份解析失败（实测 648 条）。原版 accessory",
            "# 文件里这个字段零出现，因为原版只有 human 一个 group，缺省就对。",
            "",
        ]
        for a, (node, ent, extra) in sorted(used_acc.items()):
            lines.append(f'{_pfx}_{a} = {{')
            lines.append(f'\tportrait_group = {_g}')
            if extra:
                # 武器类还有 `game_entity_override = weapon`，必须原样带上。
                lines.append('\tentity = {')
                lines.append(f'\t\trequired_tags = "" node = "{NODE_MAP[node]}" '
                             f'entity = "{ent}"')
                for ln in extra:
                    lines.append(f'\t\t{ln}')
                lines.append('\t}')
            else:
                lines.append(f'\tentity = {{\trequired_tags = ""\t\t'
                             f'node = "{NODE_MAP[node]}"\t\tentity = "{ent}" }}')
            lines.append("}")
        _ck = _t.split('_', 1)[1]
        _w(f"gfx/portraits/accessories/40_{GROUP}_props_{_ck}.txt",
           "\n".join(lines) + "\n", mod=O.models_of(_ck))

    # ---- 每个 group 一份基因容器
    for t, g in TYPES.items():
        ck = t[len(f"{GROUP}_"):]
        # 按 gene 归并：同一个 gene 下的多个模板要写在一个块里
        by_gene = {}
        for gname, tname, idx, accs in keep:
            # 自定义 portrait group 下的基因模板：
            # 只包含真正挂 accessory 的模板，并从 index 1 开始；没有原版 human
            # gene 那种 index 0 空模板。空 type 块会让 CK3 静默丢掉整根自造 gene，
            # 最后只在 modifier 加载阶段报 `could not find gene`。
            if accs:
                by_gene.setdefault(gname, []).append((tname, idx, accs))
        # **`accessory_genes` 嵌在 `special_genes` 里，`portrait_group` 写在
        # `accessory_genes` 的首行。** 这个位置基因**确实注册进了我们的 group**
        # （实机判据：没有任何 `Persistent portrait info missing gene props_right`）。
        #
        # 另两种写法都实机否证过（2026-08-29 同一次会话里先后试的，别再试）：
        #
        # 1. `portrait_group` 提到 `special_genes` 首行
        #    → `Unexpected token: portrait_group, near line: 18`，解析退回 human，
        #      紧接着 714 条 `Type moe_flo is not part of the human group`，
        #      连原版 DNA 都开始报 `Unknown props_right gene accessory group
        #      no_props`、modifier 报 `could not find template`。
        #
        # 2. `accessory_genes` 写成顶层容器（照 color_genes 的形状）
        #    → 不报解析错，但多出 18 条
        #      `Persistent portrait info missing gene animated_props/props_left/
        #      props_right` —— 基因根本没进我们的 group，比现在更糟。
        #
        # 留下的 678 条 `only scripted to work in portrait group human` 不是这一层
        # 的问题（三种写法都有），是 accessory **定义**侧的 group 归属，见
        # `_accessory_header()`。
        out = [_gene_header(g, ck), "special_genes = {", "\taccessory_genes = {",
               f"\t\tportrait_group = {g}", ""]
        for gname, tpls in sorted(by_gene.items()):
            # **基因名自造**，不与原版重名（否则全局合并、归 human）。
            custom_gname = gene_name(ck, gname)
            out.append(f"\t\t{custom_gname} = {{")
            # 自定义 portrait group 的 accessory gene 必须自己声明 gene group：
            # 容器顶层写 `portrait_group = anime` 之外，
            # 每个基因内都还要写 `group = anime_portrait_group`。
            # 漏这一行不会报语法错，而是在加载 portrait modifier 时整组变成
            # `could not find gene`，非常像文件没加载。
            out.append(f"\t\t\tgroup = {g}_portrait_group")
            for tname, idx, accs in sorted(tpls, key=lambda x: x[1]):
                out.append(f"\t\t\t{tname} = {{")
                out.append(f"\t\t\t\tindex = {idx}")
                out.append(f"\t\t\t\t{t} = {{")
                for a in accs:
                    # accessory 名带 **group 前缀**（`moe_flo_xxx`）：一个
                    # accessory 只能属于一个 portrait group，三个 group 各有
                    # 自己那份定义，同名会互相顶掉。见上面写 accessory 的那段。
                    out.append(f"\t\t\t\t\t1 = {GROUP}_{ck}_{a}")
                out.append("\t\t\t\t}")
                # 同 group 的另外三个 sex×age type（儿童、忽略性别用的异性成人/儿童）
                # 复用本性别成人那份，写法照原版 `female = male` / `boy = male`。
                for alias, _sex, _age in X.portrait_types(X.by_type(t))[1:]:
                    out.append(f"\t\t\t\t{alias} = {t}")
                out.append("\t\t\t}")
            out.append("\t\t}")
        out += ["\t}", "}", ""]
        # special gene 必须跟 portrait group 在同一个模型包：核心包加载时，
        # 后置模型包里的 moe_*_g 尚未定义，这个 gene 容器会被静默跳过。
        # 注意“同包”本身还不够，每个 gene 也必须写 `group = ...`（见上面）。
        _w(f"common/genes/42_{GROUP}_genes_props_{ck}.txt", "\n".join(out),
           mod=O.models_of(ck))

    print(f"写出 {len(TYPES)} 份 accessory + {len(TYPES)} 份基因容器"
          f"（每 group {len(by_gene)} 个基因 / {len(keep)} 个模板）")

    # ---- 我们的 portrait_modifier 容器 + .modifierpack
    # 容器名与 pack 名都要自造（与基因名同理，都是全局唯一的）。
    prop_items = parse_prop_items()
    print(f"\n原版手持道具 item {len(prop_items)} 个（animations_props 容器）")
    # 道具三层链的清单由 tools/build/survey_props_chain.py 从原版实读。**缺了就当场
    # 生成，不再靠人手跑**（2026-09-05）：它以前住在不入库的 cache/ 里，被当天
    # 的清理扫进 trash 后这一步静默跳过，animations.txt 少了 1662 行道具注入
    # （翻书/持弓全坏），而构建照报成功。
    prop_chain = ROOT / "cache" / "props_chain.json"
    if not prop_chain.exists():
        import subprocess
        prop_chain.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "build" / "survey_props_chain.py")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0 or not r.stdout.strip():
            raise SystemExit(f"**survey_props_chain.py 失败**，拒绝在没有道具链的情况下"
                             f"写产物：\n{r.stderr[-800:]}")
        prop_chain.write_text(r.stdout, encoding="utf-8")
        print(f"  已重新生成 {prop_chain.relative_to(ROOT)}")
    tpl_names = {tn for _g, tn, _i, accs in keep if accs}
    for t, g in sorted(TYPES.items()):
        ck = t[len(f"{GROUP}_"):]
        nc = write_modifier_container(ck, g, prop_items, tpl_names)
        print(f"  {ck}: 容器 {nc} 项")
    nb, nskip = write_modifierpack_override(prop_chain)
    print(f"  animations.modifierpack（核心覆盖）：{nb} 个块并入 {len(TYPES)} 个 group 的键，"
          f"跳过 {nskip} 个不在 item 表里的引用")


def parse_prop_items():
    """原版 `00_animation_props.txt` → {item 名: [(gene, template), ...]}。

    只保留引用了三个道具基因的 item（242 个）。这些 item 就是原版
    `.modifierpack` 与 `animations.txt` 实际引用的东西，我们要逐个造一份
    指向自造基因名的副本。
    """
    txt = (GAME / "gfx/portraits/portrait_modifiers/00_animation_props.txt"
           ).read_text(encoding="utf-8-sig", errors="replace")
    out = {}
    for m in re.finditer(rf'^\t({IDENT})\s*=\s*\{{', txt, re.M):
        body = _balanced(txt, m.end() - 1)
        if body is None:
            continue
        refs = re.findall(
            rf'gene\s*=\s*({IDENT})[\s\S]{{0,120}}?template\s*=\s*({IDENT})',
            body)
        if any(g in PROP_GENES for g, _ in refs):
            out[m.group(1)] = refs
    return out


def write_modifier_container(ck, g, items, tpl_names):
    """写我们自己的 portrait_modifier 容器（对应原版 `animations_props`）。

    **容器名也必须自造**：容器名同样是全局的，用 `animations_props` 会**整份
    替换**原版那个容器 —— 实测放一个只含 1 项的同名容器进去，原版另外 236 项
    全部报 `Unknown portrait modifier`。

    所有原版 item 名都输出；没有可挂模板的 item 生成空 `dna_modifiers`，作为
    no-op 占位。animations.txt / modifierpack 仍会引用这些名字，完全省略会把
    “刻意不挂马匹/战车”变成 `Unknown portrait modifier`。
    """
    lines = [
        f"# {ck} 的道具 portrait_modifier 容器（group {g}）。",
        "# 生成自 tools/build/build_moe_props.py，不要手改。",
        "#",
        f"# 容器名自造 `{GROUP}_{ck}_props`：容器名是**全局**的，叫",
        "# `animations_props` 会整份替换原版那个容器（实测原版 236 项会全部报",
        "# `Unknown portrait modifier`）。",
        "#",
        "# **顶层必须写 `portrait_group`**。不写时容器按默认 human group 解析，",
        "# 随后所有 `gene = moe_<ck>_*` 都报 `could not find gene`；要在",
        "# portrait_modifier 容器顶层写 `portrait_group = anime`。item 名虽是",
        "# 全局引用，dna_modifier 的 gene",
        "# 查找仍以容器所属 portrait group 为准。",
        "#",
        "# `usage = none` 照原版：这些 item 不参与随机，只靠显式引用触发。",
        "",
        f"{GROUP}_{ck}_props = {{",
        f"\tportrait_group = {g}",
        "\tusage = none",
        "",
    ]
    n = 0
    for iname, refs in sorted(items.items()):
        ours = [(gn, tn) for gn, tn in refs
                if gn in PROP_GENES and tn in tpl_names]
        n += 1
        lines.append(f"\t{GROUP}_{ck}_{iname} = {{")
        lines.append("\t\tdna_modifiers = {")
        for gn, tn in ours:
            lines.append("\t\t\taccessory = {")
            lines.append("\t\t\t\tmode = add")
            lines.append(f"\t\t\t\tgene = {gene_name(ck, gn)}")
            lines.append(f"\t\t\t\ttemplate = {tn}")
            lines.append("\t\t\t\tvalue = 1.0")
            lines.append("\t\t\t}")
        lines.append("\t\t}")
        lines.append("\t}")
    lines += ["}", ""]
    _w(f"gfx/portraits/portrait_modifiers/40_{GROUP}_props_{ck}.txt",
       "\n".join(lines), mod=O.models_of(ck))
    return n


_NL = "\n"


def _balanced_end(s, i):
    """s[i] 是 '{'，返回配平的那个 '}' 的下标。"""
    d = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            d += 1
        elif s[j] == "}":
            d -= 1
            if d == 0:
                return j
    return None


def write_modifierpack_override(prop_chain):
    """核心按原路径覆盖整份 `animations.modifierpack`：原版每个 portrait_modifier 块里，
    在 `animations_props = X` 之后并入全部就绪 group 的 `moe_<ck>_props = moe_<ck>_X`。

    为什么不再写逐角色的 `40_moe_props_<ck>.modifierpack` + 在 animations.txt 旁追加
    `portrait_modifier_pack = moe_<ck>_X`：实机（2026-09-06 晚）走 pack 路径的翻书/读书/写字
    全部空手，而同一天合并进原版块的 item 路径有道具 —— 原版 154 个带 pack 的变体每个只有
    1 行，追加的行与「块只取一块」同一种丢法。原版那一行 `portrait_modifier_pack = X` 不动，
    X 的定义整份覆盖后自带我们的键，原版角色照旧（多出的键在 human group 里查不到就跳过，
    与 animations.txt 里合并进块的 item 键同一机制）。

    返回 (并入的块数, 跳过的引用数)。跳过的是 X 不在我们 item 表里的（原版 12 个 bowBlunt
    附件，容器里本来就没造它们）。
    """
    import json
    if not prop_chain.exists():
        raise SystemExit(f"**缺 {prop_chain}** —— 道具 pack 会整段丢失，拒绝写产物")
    items = set(json.load(prop_chain.open(encoding="utf-8"))["items"])
    cks = [t[len(f"{GROUP}_"):] for t in sorted(TYPES)]
    src = GAME / "gfx/portraits/portrait_animations/animations.modifierpack"
    t = src.read_text(encoding="utf-8-sig", errors="replace")
    out, pos, nb, skipped = [], 0, 0, []
    for m in re.finditer(r"^(\t+)portrait_modifier\s*=\s*\{", t, re.M):
        if m.start() < pos:
            continue
        end = _balanced_end(t, m.end() - 1)
        if end is None:
            raise SystemExit("animations.modifierpack 花括号不配平，拒绝写产物")
        body = t[m.end():end]
        am = re.search(rf"animations_props\s*=\s*({IDENT})", body)
        # 闭合括号所在行的行首：我们的键插在它前面，缩进比块头多一级
        ls = t.rfind(_NL, 0, end) + 1
        out.append(t[pos:ls])
        if am and am.group(1) in items:
            nb += 1
            ind = m.group(1) + "\t"
            out.append("".join(f"{ind}{GROUP}_{ck}_props = {GROUP}_{ck}_{am.group(1)}{_NL}"
                               for ck in cks))
        elif am:
            skipped.append(am.group(1))
        pos = ls
    out.append(t[pos:])
    header = _NL.join([
        "# 原版 animations.modifierpack 的**整份覆盖**。生成自 tools/build/build_moe_props.py，不要手改。",
        "#",
        "# 原版内容一字不改，只在每个 portrait_modifier 块的 `animations_props = X` 之后并入",
        f"# 我们各 group 的 `{GROUP}_<ck>_props = {GROUP}_<ck>_X`（容器与 item 都是自造名，见",
        "# 40_moe_props_<ck>.txt）。为什么合并而不是旁边追加 pack 行：一个变体的",
        "# portrait_modifier_pack 与 portrait_modifier 都只取一份，追加的静默丢失（2026-09-06 实机）。",
        "",
    ])
    _w("gfx/portraits/portrait_animations/animations.modifierpack", header + "".join(out), mod=O.CORE)
    return nb, len(skipped)

def _gene_header(g, ck):
    return (f"# {ck} 的手持道具基因（group {g}）。"
            f"生成自 tools/build/build_moe_props.py，不要手改。\n"
            "#\n"
            f"# **基因名自造**（`{GROUP}_{ck}_props_left` 等），不与原版重名。\n"
            "# 一度以为命名空间按 portrait_group 隔离（照 color_genes 类推），\n"
            "# 实机否证：基因名是**全局**的，同名会按名字合并、合并后归 human，\n"
            "# 我们 group 的 accessory 被静默跳过。判据是报错措辞把原版的\n"
            "# gene:template 名和我们的 accessory 名写在同一行。\n"
            "#\n"
            "# 代价：原版的 portrait_modifier 容器与 .modifierpack 写死了\n"
            "# `gene = props_left`，不能复用，必须连它们一起自造。\n"
            "#\n"
            "# `portrait_group` 写在 `special_genes { accessory_genes { ... } }`\n"
            "# 内层容器首行；每个自造 gene 还必须写自己的 `group =\n"
            f"# {g}_portrait_group`。后者缺失时 CK3 不报语法错，却会在读取\n"
            "# portrait modifier 时说 `could not find gene`。\n"
            "#\n"
            f"# type 键必须是本 group 的 type 名（{ck} → {[k for k,v in TYPES.items() if v==g][0]}）。\n"
            "# 写 male / female 会报 `Type male is not part of the <group> group`。\n")


def _w(rel, text, *, mod):
    """写产物；调用者必须显式声明归属，避免重建某个包时静默丢文件。"""
    mid = mod
    p = O.root(mid) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    # 带 BOM：CK3 的文本解析对 UTF-8 BOM 有依赖，缺了会在含中文注释时报错。
    p.write_text(text, encoding="utf-8-sig")
    print(f"  写入 [{mid}] {rel}  ({len(text.splitlines())} 行)")


if __name__ == "__main__":
    main()
