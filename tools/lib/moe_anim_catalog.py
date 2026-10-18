"""完整动画清单：原版 state 表 → 我们要生成哪些 .anim、每个用哪个原版源。

为什么需要这个模块:
  先前 register_body_anims.py 手写了 17 条身体动作的映射表，够跑通验证，
  但**远不是完整覆盖** —— 原版 animations.txt 引用 368 个 torso state /
  288 个 head state，缺一个引擎就报
      Unknown animation state 'X' for entity 'moe_torso_entity'
  实测缺口 313（torso）/ 248（head），日志里刷了 5128 条。
  手写表补不完，必须**以原版 .asset 的 state 表为权威清单**自动生成。

## 核心手法：state 表照抄，.anim 按「基名」折叠

原版 female_body.asset 有 715 个 state，指向 676 个 animation id。
但这 676 个里绝大多数是**体型/编号变体**：

    body_AI_bold  body_AI_boldFat  body_AI_boldFatDwarf  body_AI_bold_2 ...

萌系角色只有一种体型（没有 Fat/Dwarf/Pregnant 的 blend shape，
`build_moe_mod.py` 也没注册这些 attribute），这些变体没有意义。
所以：

  * **state 表照原版全列** —— 引擎按名字请求，少一个就报 Unknown state
  * **.anim 只做「基名」那一份** —— 变体 state 全部指向同一个基名动画

折叠后 torso 404 → 实际能解析到源文件 400，head 395 → 393。
体积从 246 MB（原版 torso 全量）降到我们这边约 50 MB。

## 源文件优先取女性，缺了才用男性

我们的角色是女性体型，female_body 的动作姿态更贴。但原版把大量新动作
（DLC 的日式/中式动作、王座厅演出）只放在 male_body 目录，由
`common_body` 动画集共享给两性 —— 所以 350/400 个基名的源在 male_body。
这不是问题：重定向是按**骨名**做的（BONE_MAP），两边骨名完全一致
（都是 female_body_rig_01: 前缀，或会被 main() 规范化过去）。

## 头身配对必须从 .asset 实读，不能猜

同一个 state 名在 female_head.asset 与 female_body.asset 里各自指向不同的
animation id，这个对应关系是**引擎实际会同时请求的那一对**。
按名字猜（head_emotion_happy ↔ body_emotion_happy）会漏掉大量不同名的配对
（实测 animations.txt 里 371 组 (head,torso) 有 87 组不同名）。
拿错配对的后果是头与躯干脊椎摆幅不一致 → 头脱开躯干（最坏 21.5 单位）。
"""
import re
from functools import lru_cache
from pathlib import Path

GAME = Path(r"E:\SteamLibrary\steamapps\common\Crusader Kings III\game"
            r"\gfx\models\portraits")

# 体型/编号变体后缀。折叠掉它们就得到「基名」。
#
# 顺序无关（用 sub 全局替换），但**必须包含 Fat2/Fat3** ——
# 原版有 body_frontEnd_pregnantFat2 这种双重变体，漏了会多出一个假基名。
# 变体可以带前导下划线（原版两种写法都有: body_emotion_angryFat 与
# body_emotion_angry_Fat）。不吃掉那个下划线会留下 `body_emotion_angry_`
# 这种尾巴，与真基名 `body_emotion_angry` 分裂成两个，白多做一半文件。
_VARIANT = re.compile(r"_?(Fat\d?|Dwarf|Pregnant|Serious)+")
_TRAIL_NUM = re.compile(r"_\d+$")

# 我们的动画文件名前缀。基名本身已带 body_/head_ 段
# （原版 id 就是 body_AI_bold / head_emotion_happy），所以拼出来是
#   moe_body_AI_bold.anim / moe_head_emotion_happy.anim
# 与先前手写的 moe_body_happy.anim 命名不同 —— 完整版一律走系统命名，
# 让 state 表能机械地从原版推出来，不再需要人工对照表。
OUT_PREFIX = "moe_"

# 折叠时**不能**丢的尾数字：这些基名本身以数字结尾，不是变体编号。
# 漏掉会把 throneRoom_bow1 / bow2 / bow3 三个不同动作折成一个。
KEEP_TRAILING_DIGIT = re.compile(
    r"(bow|cheer|conversation|kneel|curtsey|applaud|ruler|rulerChild|messenger|"
    r"oneHandedPassive|twoHandedPassive|writerIdle|writerTransitionIdle|"
    r"writerTransitionWriting|writerWriting|Aggressive|loss|testCase|"
    r"chancellor|infant)\d+$")


@lru_cache(maxsize=8192)
def fold(anim_id):
    """animation id → 基名。

    body_AI_boldFatDwarf_2 → body_AI_bold
    body_throneRoom_bow1_3 → body_throneRoom_bow1   （bow1 的 1 是动作编号）
    """
    s = _VARIANT.sub("", anim_id)
    if not KEEP_TRAILING_DIGIT.search(s):
        s = _TRAIL_NUM.sub("", s)
    else:
        # 形如 bow1_3：先去掉 _3，保留 bow1
        s = re.sub(r"(?<=\d)_\d+$", "", s)
    return s


def _read(rel):
    p = GAME / rel
    return p.read_text(encoding="utf-8-sig", errors="replace") if p.is_file() else ""


def _id_to_file(text):
    """.asset 文本 → {animation id: 文件名}。additive_animation 不收。"""
    return dict(re.findall(r'(?<!additive_)animation\s*=\s*\{\s*id\s*=\s*"([^"]+)"'
                           r'\s*type\s*=\s*"([^"]+)"', text))


def _states(text):
    """.asset 文本 → [(state 名, animation id, 整行)]，保持原顺序（含重复）。"""
    out = []
    for m in re.finditer(r'^[ \t]*state\s*=\s*\{([^}]*)\}', text, re.M):
        body = m.group(1)
        n = re.search(r'name\s*=\s*"([^"]+)"', body)
        a = re.search(r'animation\s*=\s*"([^"]+)"', body)
        if n and a:
            out.append((n.group(1), a.group(1), body))
    return out


class Catalog:
    """一个部件（torso / head）的完整动画清单。

    属性:
      states   [(state 名, 原版 anim id, 原版 state 行体)]，顺序照原版
      bases    {基名: (源目录, 源文件名)}  —— 要重定向/移植的清单
      id_of    {原版 anim id: 我们的 anim id}
      file_of  {我们的 anim id: 我们的文件名}
    """

    def __init__(self, part):
        self.part = part
        if part == "torso":
            own, sib = ("female_body", "female_body.asset"), ("male_body", "male_body.asset")
        elif part == "head":
            own, sib = ("female_head", "female_head.asset"), ("male_head", "male_head.asset")
        else:
            raise ValueError(part)

        t_own = _read(f"{own[0]}/{own[1]}")
        t_sib = _read(f"{sib[0]}/{sib[1]}")
        self.states = _states(t_own)
        self._map_own, self._dir_own = _id_to_file(t_own), own[0]
        self._map_sib, self._dir_sib = _id_to_file(t_sib), sib[0]

        used = [aid for _, aid, _ in self.states]
        self.bases = {}
        self.unresolved = []
        for b in sorted({fold(x) for x in used}):
            hit = self._resolve(b, used)
            if hit:
                self.bases[b] = hit
            else:
                self.unresolved.append(b)

        # 原版 id → 我们的 id。解析不到源的基名回落到 idle，
        # **不能留空**：state 指向不存在的 animation id 同样报错。
        self.id_of = {}
        for aid in used:
            b = fold(aid)
            if b not in self.bases:
                b = self._fallback(b)
            self.id_of[aid] = OUT_PREFIX + b
        self.file_of = {OUT_PREFIX + b: OUT_PREFIX + b + ".anim" for b in self.bases}

    def _resolve(self, base, used):
        """基名 → (源目录, 文件名)。优先精确同名、优先女性目录。"""
        cands = [base] + sorted(x for x in used if fold(x) == base)
        for table, d in ((self._map_own, self._dir_own), (self._map_sib, self._dir_sib)):
            for c in cands:
                f = table.get(c)
                if f and (GAME / d / f).is_file():
                    return (d, f)
        return None

    def _fallback(self, base):
        """解析不到源文件时退到哪个基名。

        退到 idle 而不是丢弃：state 必须指向一个存在的 animation id，
        否则报的是 Unknown animation 而不是「这个动作没做」。
        """
        idle = f"{self.part_prefix}_idle"
        return idle if idle in self.bases else next(iter(self.bases))

    @property
    def part_prefix(self):
        return "body" if self.part == "torso" else "head"


def pair_head_to_torso(torso_bases=None):
    """{head 基名: torso 基名} —— 从两个 .asset 的 state 表实读同名 state。

    同一个 state 名下，head 侧与 torso 侧各指向自己的 animation id，
    这一对就是引擎会同时请求的组合。一个 head 基名可能配到多个 torso 基名
    （不同变体 state），取出现次数最多的那个。

    传 torso_bases（Catalog("torso").bases）时启用两层兜底，补上 25 个
    实读配不到的 head 基名:

      1. **按名字直推** head_X → body_X。补 6 个 —— 这些是 female_head.asset
         注册了 state、female_body.asset 没注册的（如 emotion_happy_start），
         但 body_ 侧的动画文件与基名都在，只是 state 表漏了。
      2. **左右/近义换名**。原版婚礼动作按站位分左右，两个 asset 各注册一侧:
             head_wedding_bride_right_idle ↔ body_wedding_bride_left_idle
         同一个动作的镜像位，脊椎摆幅一致，配上比退 idle 好得多。

    剩下配不上的（Aggressive 系列 14 个 + horse_idle / page_flipping_loop）
    是原版 female_body.asset 根本没注册对应 state 的 —— 由调用方退 body_idle。
    它们的 head 侧动画仍然照做，因为 head state 表要列它们。
    """
    import collections
    # **一个 state 名可以注册多个变体动画**（原版的随机变体机制，例如
    # state 'wedding_happy_cry_loop' 同时指向 loop1/loop2/loop3）。
    # 2026-08-21 踩过：这里原本写 {n: aid for ...}，dict 推导让后者覆盖前者，
    # 只剩 loop3 —— loop2 从 h.values() 里整个消失，连兜底循环都遍历不到它，
    # 于是它退用了 body_idle 的脊椎（242 帧），而它自己的 torso 有 322 帧，
    # 头身从此差 80 帧。所以按 state 名聚成**列表**，不能覆盖。
    hl = collections.defaultdict(list)
    for n, aid, _ in _states(_read("female_head/female_head.asset")):
        hl[n].append(aid)
    tl = collections.defaultdict(list)
    for n, aid, _ in _states(_read("female_body/female_body.asset")):
        tl[n].append(aid)
    votes = collections.defaultdict(collections.Counter)
    for name, haids in hl.items():
        taids = tl.get(name) or []
        if not taids:
            continue
        tfold = {fold(a): a for a in taids}
        for i, haid in enumerate(haids):
            # 同名多变体要按**名字**对位（head_X ↔ body_X），不能按下标：
            # 两个 .asset 的变体注册顺序不同（state 'emotion_angry_entry' 的
            # head 侧是 var/var2/base，torso 侧是 base/var/var2），按下标配
            # 会整体错位一格，把 head_..._start 配到 body_..._start_var2。
            # 变体本来就是引擎各自随机挑一个播，下标之间没有语义对应关系。
            hb = fold(haid)
            want = "body_" + hb[len("head_"):] if hb.startswith("head_") else None
            taid = tfold.get(want)
            if taid is None:
                # 名字对不上（head 有该变体、torso 没有）就退到 torso 的
                # 第一个变体 —— 它是 state 的默认动画。
                taid = taids[0]
            votes[fold(haid)][fold(taid)] += 1
    # **同名优先，票数其次**。原版 Fat 变体的命名两侧不对称：
    #     head_emotion_sadness_start_Fat  ↔  body_emotion_sadness_Fat（没有 _start）
    # 折叠后 head_emotion_sadness_start 拿到 3 票：1 票同名的
    # body_emotion_sadness_start，2 票（Fat / FatDwarf 兜底到 taids[0]）给
    # body_emotion_sadness。多数决选错 → 头嫁接的是 loop 那条的脊椎（321 帧），
    # 躯干播的却是 start（241 帧），颈根错位 3.6~4.7 单位（2026-09-02，三个
    # 角色各 1 组）。只要同名的 body 基名真的存在（拿到过票，或在 torso 清单
    # 里），它就是引擎在 `_entry` state 下真正配对的那条，直接用。
    out = {}
    for hb, c in votes.items():
        same = "body_" + hb[len("head_"):] if hb.startswith("head_") else None
        if same and (same in c or (torso_bases and same in torso_bases)):
            out[hb] = same
        else:
            out[hb] = c.most_common(1)[0][0]

    all_haids = {fold(a) for aids in hl.values() for a in aids}
    if torso_bases:
        # 只补真实存在的 torso 基名，避免指向不存在的产物
        for hb in all_haids:
            if hb in out:
                continue
            core = hb[len("head_"):]
            for guess in (f"body_{core}",
                          f"body_{core.replace('_right', '_left')}",
                          f"body_{core.replace('_left', '_right')}",
                          f"body_{core}_gallop"):
                if guess in torso_bases:
                    out[hb] = guess
                    break
    return out


# ---------------------------------------------------------------- 表情选源
# 情绪与说话是独立语义，不能再让 talk 抢走 happy_teaching 的开心。
# 按下划线/驼峰/数字分词，仅显式 * 词根做前缀匹配。具体复合语义优先。
EXPR_KEYWORDS = [
    ("moe_happy.anim", ["happy_cry"]),
    ("moe_angry.anim", ["objection", "argu*", "scold*", "shout*"]),
    ("moe_laugh.anim", ["laugh*", "ecstasy", "schadenfreude", "manic"]),
    ("moe_flirty.anim", ["flirt*", "coy*", "love"]),
    ("moe_shame.anim", ["shame", "ashamed", "embarrass*"]),
    ("moe_disgust.anim", ["disgust*", "disapprov*", "dismiss*", "eyeroll"]),
    ("moe_boredom.anim", ["bored*", "exhausted"]),
    ("moe_thinking.anim", ["think*", "ponder*", "rational", "scheme", "eavesdrop"]),
    ("moe_fear.anim", ["fear*", "coward*", "paranoia", "stayback", "yield"]),
    ("moe_worry.anim", ["worr*", "stress*", "nervous", "beg", "arrest", "dungeon"]),
    ("moe_sadness.anim", [
        "sad*", "grief", "cry", "wail*", "loss", "losing", "pain", "sick",
        "slightlylosing*", "stronglylosing*",
        "wounded*", "poison", "delirium", "cough", "shiver", "disappoint*",
    ]),
    ("moe_happy.anim", [
        "happy", "cheer*", "celebrate*", "victory", "win", "winning",
        "chesscertainwin", "slightlywinning*", "stronglywinning*",
        "toast", "peekaboo", "danc*", "applaud*", "hero_flex",
    ]),
    ("moe_smile.anim", [
        "smil*", "content", "admiration", "interested", "compassionate",
        "forgiving", "cocky", "chesscocky", "wedding", "reception", "newborn", "drink*",
        "storytelling",
    ]),
    ("moe_angry.anim", [
        "angry", "anger", "rage", "menacing", "threatening", "aggressive*",
        "assassin", "callous", "vengeful", "cynical", "zealous", "irrational",
        "onehandedaggressive*", "twohandedaggressive*",
    ]),
    ("moe_shock.anim", [
        "shock*", "disbelief", "stunned", "eccentric", "surpris*",
    ]),
]
NEUTRAL_EXPR = "moe_idle_calm.anim"
TALK_KEYWORDS = (
    "conversation*", "conversing", "talk*", "storytelling", "messenger*",
    "chancellor", "teaching", "argu*", "scold*", "shout*", "lecture*",
    "announc*", "speech", "preach*", "debat*", "objection",
)
READING_KEYWORDS = ("reading", "writing", "writer*", "page_flipping")

# 旧中性源还带原生 neck 呼吸。它属于既有头身/物理驱动合同，不应随着新增
# thinking/smile 等面部选源改变。这里刻意保留旧 substring 判定（包括 win 等
# 旧的宽匹配），只决定 native 层是否需要 idle_calm donor，不能用于新情绪分派。
_LEGACY_NONNEUTRAL_WORDS = (
    "conversation", "conversing", "talking", "storytelling", "messenger",
    "chancellor", "teaching", "argue", "scold", "shout", "lecture", "announce", "speech", "preach",
    "happy", "laugh", "flirt", "cheer", "ecstasy", "admiration", "love", "celebrate", "victory",
    "win", "content", "schadenfreude", "toast", "drink", "wedding", "peekaboo", "dancing",
    "applaud", "interested", "chariot_happy", "hero_flex", "manic",
    "sad", "grief", "worry", "stress", "cry", "wail", "shame", "beg", "loss", "pain", "sick",
    "wounded", "poison", "delirium", "cough", "shiver", "dungeon", "arrest", "yield", "exhausted",
    "coward", "disappoint", "nervous", "prostration", "kneel", "praying", "prayer",
    "angry", "anger", "rage", "disgust", "disapprov", "dismiss", "menacing", "threatening",
    "aggressive", "assassin", "callous", "vengeful", "cynical", "paranoia", "scheme", "eyeroll",
    "boredom", "stayback", "zealous", "irrational",
    "shock", "fear", "disbelief", "stunned", "shocked", "newborn", "eccentric", "surprise",
)


def native_breathing_for(head_base):
    """原先选用中性源的动作保留它的 native 呼吸，独立于新的面部情绪。"""
    low = str(head_base).lower()
    return not any(word in low for word in _LEGACY_NONNEUTRAL_WORDS)


def _semantic_tokens(name):
    name = re.sub(r"([a-z])([A-Z])", r"\1_\2", str(name))
    return re.findall(r"[a-z]+", name.lower())


def _has_keyword(tokens, keyword):
    parts = keyword.lower().split("_")
    return any(all(token.startswith(part[:-1]) if part.endswith("*")
                   else token == part
                   for token, part in zip(tokens[i:i + len(parts)], parts))
               for i in range(len(tokens) - len(parts) + 1))


def _emotion_source(head_base):
    tokens = _semantic_tokens(head_base)
    for source, keywords in EXPR_KEYWORDS:
        if any(_has_keyword(tokens, key) for key in keywords):
            return source
    return NEUTRAL_EXPR


def _phase_hint(name):
    tokens = set(_semantic_tokens(name))
    if tokens & {"exit", "end", "stop"}:
        return "exit"
    if tokens & {"entry", "start"}:
        return "entry"
    if tokens & {"loop", "looping", "idle"}:
        return "loop"
    return None


_EXPRESSION_PHASES = None


def _expression_phases():
    """读取真实 state 转移；自跳转和多 state 循环都属于持续表演。"""
    global _EXPRESSION_PHASES
    if _EXPRESSION_PHASES is not None:
        return _EXPRESSION_PHASES
    try:
        states = _states(_read("female_head/female_head.asset"))
    except FileNotFoundError:
        return {}                     # 独立配方检查可在没有游戏安装的机器运行
    graph = {}
    for name, _, body in states:
        graph.setdefault(name, set()).update(re.findall(r'next_state\s*=\s*"([^"]+)"', body))

    def cycles_to(start):
        pending, seen = list(graph.get(start, ())), set()
        while pending:
            node = pending.pop()
            if node == start:
                return True
            if node not in seen:
                seen.add(node)
                pending.extend(graph.get(node, ()))
        return False

    cyclic = {name for name in graph if cycles_to(name)}
    choices = {}
    for name, aid, body in states:
        explicit_loop = bool(re.search(r"\blooping\s*=\s*yes\b", body))
        phase = ("loop" if explicit_loop or name in cyclic else
                 _phase_hint(name) or _phase_hint(aid) or "oneshot")
        choices.setdefault(fold(aid).lower(), set()).add(phase)
    # 同一源常被 entry 和正式 state 共用；选循环，避免每圈重新中性起步。
    _EXPRESSION_PHASES = {
        base: next(p for p in ("loop", "entry", "exit", "oneshot") if p in phases)
        for base, phases in choices.items()
    }
    return _EXPRESSION_PHASES


def performance_for(head_base):
    """嫁接计划：情绪源、说话层、视线任务及 state 时序分开。

    expression 始终是情绪源；中性说话是 idle_calm + talk=True。
    phase 从原版真实 state 图判断，名字仅为未注册源提供回退。
    """
    source = _emotion_source(head_base)
    tokens = _semantic_tokens(head_base)
    phase = _expression_phases().get(fold(str(head_base)).lower())
    if phase is None:
        phase = _phase_hint(head_base) or ("loop" if "emotion" in tokens else "oneshot")
    return {
        "expression": source,
        "emotion": "neutral" if source == NEUTRAL_EXPR else source[4:-5],
        "talk": any(_has_keyword(tokens, key) for key in TALK_KEYWORDS),
        "native_breathing": native_breathing_for(head_base),
        "phase": phase,
        "gaze": "reading" if any(_has_keyword(tokens, key) for key in READING_KEYWORDS) else None,
    }


def expr_for(head_base):
    """head 基名 → 情绪源；需要说话/时序的调用方必须用 performance_for。"""
    return _emotion_source(head_base)


# ---------------------------------------------------------------- 手持道具
# 「这条 torso 动画播的时候手里有没有东西、在哪只手」—— 双手关系 IK 的依据
# （见 retarget_vanilla_anim.py 的 HAND IK 段）。数据来源全是原版实读：
#   animations.txt / animations.modifierpack  state → animations_props item
#   00_animation_props.txt                    item  → (gene, template)
#   common/genes + accessories/props.txt      template → accessory → node
# 不按名字猜（`bow_idle` 里的弓在**左**手，`holding_hu` 的笏在右手，猜不出来）。
_HAND_PROPS = None


@lru_cache(maxsize=1)
def _bp():
    """懒导入 tools/build/build_moe_props（解析 .txt 基因/道具表的函数住在那边）。

    lib/ 的调用方只把 lib/ 与 anim/ 放进 sys.path，build/ 不在其中；这里自己补路径，
    别要求每个调用脚本都知道 catalog 内部依赖谁。2026-09-06 重组后艾尔黛拉
    345 条重定向全数 ModuleNotFoundError 就是这条漏掉的。
    """
    import sys
    sys.path.append(str(Path(__file__).resolve().parents[1] / "build"))   # audit_repo 按这一行认目录
    import build_moe_props as BP
    return BP
_SRC_TO_BASE = None


def _enclosing_block(text, pos):
    """返回包住 text[pos] 的最内层 `{...}` 的 (左花括号下标, 内部文本)。"""
    depth = 0
    i = pos
    while i >= 0:
        c = text[i]
        if c == "}":
            depth += 1
        elif c == "{":
            if depth == 0:
                break
            depth -= 1
        i -= 1
    if i < 0:
        return None, None
    BP = _bp()
    return i, BP._balanced(text, i)


def _direct_props(body, pack_items):
    """块 body 的**直接**子项里引用的 props item 集合。

    只认直接子块 `portrait_modifier = { animations_props = X }` 与直接赋值
    `portrait_modifier_pack = P`。嵌套的 state 子块一律跳过 —— `chaplain` 的
    默认姿势块里嵌着 `council_chaplain_task = { … portrait_modifier_pack =
    open_book }`，整块正则搜会把那本书认到 `council_chaplain` 头上（它其实空手）。
    """
    its = set()
    tok = re.compile(r'([A-Za-z0-9_]+)\s*=\s*')
    i = 0
    while True:
        m = tok.search(body, i)
        if not m:
            return its
        name, j = m.group(1), m.end()
        if j < len(body) and body[j] == "{":
            BP = _bp()
            inner = BP._balanced(body, j)
            if inner is None:
                return its
            if name == "portrait_modifier":
                its.update(re.findall(r'animations_props\s*=\s*([A-Za-z0-9_]+)',
                                      inner))
            i = j + len(inner) + 2
        else:
            v = re.match(r'"?([A-Za-z0-9_]+)"?', body[j:])
            if name == "portrait_modifier_pack" and v:
                its.update(pack_items.get(v.group(1), ()))
            i = j + (v.end() if v else 1)


def hand_prop_nodes():
    """{torso 基名: frozenset({'l', 'r'})} —— 只收挂在腕骨 prop 点上的道具。

    ground_joint / 头饰类挂点不算手。同一基名被多个 state 引用时取并集。
    """
    global _HAND_PROPS
    if _HAND_PROPS is not None:
        return _HAND_PROPS
    BP = _bp()
    acc = BP.parse_accessories()                  # accessory → (node, entity, extra)
    items = BP.parse_prop_items()                 # item → [(gene, template)]
    node_of_tpl = {}
    for gname, tpl, _idx, accs in BP.parse_genes():
        if gname in BP.PROP_GENES:
            node_of_tpl.setdefault(tpl, set()).update(
                acc[a][0] for a in accs if a in acc)
    hand_of_node = {"bn_l_prop": "l", "bn_r_prop": "r"}

    def hands_of_item(item):
        hs = set()
        for g, t in items.get(item, []):
            if g in BP.PROP_GENES:
                for nd in node_of_tpl.get(t, ()):
                    if nd in hand_of_node:
                        hs.add(hand_of_node[nd])
        return hs

    pa = BP.GAME / "gfx/portraits/portrait_animations"
    atxt = (pa / "animations.txt").read_text(encoding="utf-8-sig", errors="replace")
    mpack = (pa / "animations.modifierpack").read_text(encoding="utf-8-sig",
                                                       errors="replace")
    pack_items = {}
    for m in re.finditer(r'^\s*([A-Za-z0-9_]+)\s*=\s*\{', mpack, re.M):
        body = BP._balanced(mpack, m.end() - 1)
        if body:
            its = re.findall(r'animations_props\s*=\s*([A-Za-z0-9_]+)', body)
            if its:
                pack_items[m.group(1)] = its

    # animations.txt 的 `torso = "X"` 写的是 **state 名**，不是 animation id；
    # state → 基名要经 .asset 的 state 表（Catalog.states）折叠，不能直接 fold。
    bases_of_state = {}
    for sn, aid, _body in Catalog("torso").states:
        bases_of_state.setdefault(sn, set()).add(fold(aid))
    out = {}
    for m in re.finditer(r'torso\s*=\s*"([^"]+)"', atxt):
        # 两种写法：A `animation = { head torso }` 的父块是 state 块；
        # B `default = { head torso }`（默认 state 的简写）的父块是 pose 块，
        # portrait_modifier 与它平级。两种情况都是「包住 torso 行的块」的父块。
        inner, _ = _enclosing_block(atxt, m.start())
        if inner is None:
            continue
        outer, body = _enclosing_block(atxt, inner - 1)
        if body is None:
            continue
        its = _direct_props(body, pack_items)
        hs = set()
        for it in its:
            hs |= hands_of_item(it)
        if hs:
            for b in bases_of_state.get(m.group(1), ()):
                out[b] = frozenset(out.get(b, frozenset()) | hs)
    _HAND_PROPS = out
    return out


def base_of_source(src_name):
    """原版源文件名（如 female_body_praying.anim）→ torso 基名；不在清单里返回 None。"""
    global _SRC_TO_BASE
    if _SRC_TO_BASE is None:
        _SRC_TO_BASE = {}
        c = Catalog("torso")
        for b, (_d, f) in c.bases.items():
            _SRC_TO_BASE.setdefault(f, b)
        # 同一基名的男性源文件也认（清单优先女性目录，男性文件不一定在表里）
        for b, (_d, f) in c.bases.items():
            _SRC_TO_BASE.setdefault(f.replace("female_", "male_"), b)
    return _SRC_TO_BASE.get(Path(src_name).name)


if __name__ == "__main__":
    import io
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for part in ("torso", "head"):
        c = Catalog(part)
        print(f"\n=== {part}")
        print(f"  原版 state 行 {len(c.states)}"
              f"（unique {len({n for n, _, _ in c.states})}）")
        print(f"  折叠后基名 {len(c.bases)}，解析不到源 {len(c.unresolved)}")
        if c.unresolved:
            print(f"    {c.unresolved}")
        tot = sum((GAME / d / f).stat().st_size for d, f in c.bases.values())
        fem = sum(1 for d, _ in c.bases.values() if d.startswith("female"))
        print(f"  源体积 {tot/1e6:.0f} MB（女性源 {fem} / 男性源 "
              f"{len(c.bases) - fem}）")
    p = pair_head_to_torso()
    print(f"\n配对 head→torso {len(p)} 条，示例:")
    for k in list(sorted(p))[:8]:
        print(f"  {k:<40} → {p[k]}")
    import collections
    ec = collections.Counter(expr_for(b) for b in Catalog("head").bases)
    print(f"\n表情源分布: {dict(ec)}")
