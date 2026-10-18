# -*- coding: utf-8 -*-
"""换装窗口的姿势清单 —— 从原版 animations.txt **全部顶层块**自动派生。

用户 2026-09-06：「现在换装窗口的可选动作太少，能不能把所有动画都加进来？做个滚动条
给动画选择」。原来 build_moe_mod.POSES 是手写的 9 条；这里改成读原版文件：每个顶层块
（`idle` / `chancellor` / `page_flipping` / `jockey_idle` …，共 248 个）就是一个可选姿势。

每个姿势取三样东西：
  · **块名**   —— 中栏实时预览 `Character.GetAnimatedPortrait(env, cam, '块名', size)`
                  的第三个参数就是它（[[ck3-portrait-animation-four-name-layers]]）。
  · **默认 state** —— 块内 `default = { head = "X" torso = "X" }` 的 X，写进 idle 块的
                  姿势变体 `animation = { head = X torso = X }`。原版 head/torso 在 build_moe_mod
                  第二处改动里已统一同名，这里只取 head 名。
  · **道具行** —— default 子块**直接层**的 `portrait_modifier_pack = …` /
                  `portrait_modifier = { … }`，逐字抄进变体。道具是跟 pack 走的不是跟动画走的
                  （翻书没书、持弓没弓都是漏了这层）；随后 build_moe_mod 第四处改动会在这些
                  引用旁追加 `moe_<ck>_` 版本，所以只要抄原文就自动接上我们的道具。
                  `animations = xxx_decal` 之类是原版 group 的容器名，我们 group 查不到会静默
                  跳过，无害。

**筛掉的块**：默认 state 是 `idle` / `idle_entry` 的非 idle 块（pregnant / clutching_toy /
instrument_*：渲出来与默认站姿一模一样，只是多一条噪声）、`bind_pose`（T 字位）。
另外 state 必须存在于原版 female_body.asset —— 我们的 state 表就是照它抄的
（[[moe-full-anim-set-from-vanilla-state-table]]），不在的话头身都播不出来。

分组 = 块前面最近的一条横幅注释（`# Council Animations` / `# Child` / `# Weapon Animations`…），
用作滚动列表里的小标题。

姿势的 flag / scripted_gui / loc key 都以块名为键：`moe_pose_<块名>`、
`moe_set_pose_<块名>`、`moe_cosplay.0002.<块名>`。
"""
import re
from pathlib import Path

import moe_anim_catalog as _AC

# moe_anim_catalog.GAME 指到 game/gfx/models/portraits，往上三级才是 game 根
GAME_ROOT = Path(_AC.GAME).parents[2]
ANIM_TXT = GAME_ROOT / "gfx" / "portraits" / "portrait_animations" / "animations.txt"
BODY_ASSET_DIRS = ("female_body", "male_body")

# 与默认站姿等价、或者不是「姿势」的块
SKIP_STATES = {"idle", "idle_entry"}
SKIP_BLOCKS = {"bind_pose"}

# 横幅 → 中/英分组名。键是横幅原文去空白后的小写。
GROUP_NAMES = {
    "default personality animations": ("待机", "Idle"),
    "council animations":             ("议会", "Council"),
    "frontend animations":            ("前端", "Frontend"),
    "personality animations (for events)": ("性格", "Personality"),
    "emotion animations (for events)":     ("情绪", "Emotion"),
    "misc animations":                ("杂项", "Misc"),
    "child":                          ("孩童", "Child"),
    "sick":                           ("病痛", "Sick"),
    "book":                           ("读写", "Book"),
    "war":                            ("战争", "War"),
    "drinking":                       ("饮酒", "Drinking"),
    "misc":                           ("杂项", "Misc"),
    "hunting animations":             ("狩猎", "Hunting"),
    "wedding animations":             ("婚礼", "Wedding"),
    "weapon animations":              ("武器", "Weapon"),
    "courteous animations":           ("礼仪", "Courteous"),
    "horse animations":               ("骑乘", "Horse"),
    "instrument animations":          ("乐器", "Instrument"),
    "ability":                        ("状态", "Ability"),
    "bookmark characters":            ("书签", "Bookmark"),
}

# 块名 → 中文名。缺的回落到英文自动名（下划线转空格、首字母大写）。
NAME_CN = {
    "idle": "默认站姿", "chancellor": "宰相", "steward": "总管", "spymaster": "间谍首领",
    "chaplain": "宫廷神父", "marshal": "元帅", "marshal_random_weapon": "元帅（随机武器）",
    "marshal_axe": "元帅（斧）", "marshal_dagger": "元帅（匕首）", "marshal_mace": "元帅（锤）",
    "marshal_shield": "元帅（盾）", "minister_works": "工部", "grand_marshal": "大元帅",
    "frontend_center_idle": "前端·中", "frontend_left_idle": "前端·左", "frontend_right_idle": "前端·右",
    "personality_honorable": "正直", "personality_dishonorable": "奸诈", "personality_bold": "勇敢",
    "personality_coward": "怯懦", "personality_greedy": "贪婪", "personality_content": "知足",
    "personality_vengeful": "记仇", "personality_forgiving": "宽恕", "personality_rational": "理性",
    "personality_irrational": "冲动", "personality_compassionate": "仁慈", "personality_callous": "冷酷",
    "personality_zealous": "虔诚", "personality_cynical": "怀疑",
    "admiration": "钦佩", "anger": "愤怒", "beg": "乞求", "boredom": "无聊", "crying": "哭泣",
    "delirium": "神志不清", "disappointed": "失望", "disapproval": "不赞成", "disbelief": "难以置信",
    "disgust": "厌恶", "dismissal": "不屑", "eccentric": "古怪", "ecstasy": "狂喜", "fear": "恐惧",
    "flirtation": "撩拨", "flirtation_left": "撩拨（左）", "grief": "悲痛", "happiness": "开心",
    "interested": "感兴趣", "interested_left": "感兴趣（左）", "laugh": "大笑", "love": "爱慕",
    "manic": "狂躁", "nervous": "紧张", "pain": "疼痛", "paranoia": "疑神疑鬼", "poison": "中毒",
    "rage": "暴怒", "sadness": "悲伤", "schadenfreude": "看好戏", "scheme": "密谋", "shame": "羞愧",
    "shock": "震惊", "stress": "压力", "stunned": "呆住", "wailing": "哀号", "wedding_happy_cry": "喜极而泣",
    "worry": "忧虑", "newborn": "抱婴", "peekaboo": "躲猫猫", "child_hobby_horse": "竹马",
    "clutching_ball": "抱球", "clutching_doll": "抱娃娃", "go_to_your_room": "回房间去",
    "sick": "生病", "cough": "咳嗽", "shiver": "发抖", "sick_stomach": "胃痛", "severelywounded": "重伤",
    "loss_1": "失落", "page_flipping": "翻书", "writing": "书写", "reading": "阅读",
    "stressed_teacher": "焦躁的老师", "happy_teacher": "开心的老师", "throne_room_writer": "王座厅书记",
    "thinking": "思考", "emotion_thinking_scepter": "持杖思考", "pondering": "沉思",
    "prisonhouse": "软禁", "prisondungeon": "地牢", "war_attacker": "进攻方", "war_defender": "防守方",
    "war_over_tie": "战争平局", "war_over_win": "战争胜利", "war_over_loss": "战争失败",
    "drink": "饮酒", "drink_goblet": "举杯饮", "toast": "敬酒", "toast_goblet": "举杯敬酒",
    "wedding_drunk": "婚宴醉酒", "acknowledging": "致意", "betting": "下注", "bribing": "行贿",
    "chess_certain_win": "棋局稳赢", "chess_cocky": "棋局得意", "dancing": "跳舞", "dancing_plague": "舞蹈狂",
    "debating": "辩论", "eavesdrop": "偷听", "eyeroll": "翻白眼", "fanning_coyly": "羞怯扇风",
    "fanning": "扇风", "hero_flex": "英雄亮相", "lantern": "提灯", "obsequious_bow": "谄媚鞠躬",
    "physician": "医师", "prayer": "祈祷", "scepter": "持杖", "serving_tray": "端托盘", "stayback": "退后",
    "storyteller": "讲故事", "survey": "眺望", "survey_staff": "持杖眺望", "holding_staff": "持手杖",
    "shepherd_with_sheep": "牧羊", "hunting_horn": "吹号角", "hunting_carcass_start": "提猎物",
    "hunting_falcon": "驯鹰", "wedding_groom_right": "新郎（右）", "wedding_bride_left": "新娘（左）",
    "wedding_priest": "主婚神父", "reception_groom_left": "宴会新郎（左）", "reception_bride_right": "宴会新娘（右）",
    "wedding_objection_start": "婚礼异议", "assassin": "刺客", "aggressive_unarmed": "徒手威慑",
    "sword_coup_degrace": "剑·终结", "wrestling_victory": "摔角胜利", "sword_yield_start": "剑·投降",
    "wrestling_yield_start": "摔角投降", "wooden_sword_yield_start": "木剑投降", "throne_room_wooden_sword": "王座厅木剑",
    "celebrate_wooden_sword": "木剑庆祝", "aggressive_wooden_sword": "木剑威慑", "marshal_wooden_sword": "元帅木剑",
    "wooden_sword_coup_degrace": "木剑·终结", "random_weapon_coup_degrace": "随机武器·终结",
    "random_weapon_aggressive": "随机武器威慑", "random_weapon_celebrate": "随机武器庆祝",
    "random_weapon_yield": "随机武器投降", "inspect_weapon": "检视武器", "menacing": "威吓",
    "menacing_wooden_sword": "木剑威吓", "threatening": "威胁", "hunting_knife_start": "猎刀",
    "crossbow": "弩", "bow_idle": "持弓", "hunting_shortbow_rest_arrow_default": "短弓·放松·箭",
    "hunting_shortbow_rest_bluntarrow_default": "短弓·放松·钝箭", "hunting_shortbow_aim_arrow_default": "短弓·瞄准·箭",
    "hunting_shortbow_aim_bluntarrow_default": "短弓·瞄准·钝箭", "hunting_longbow_rest_arrow_default": "长弓·放松·箭",
    "hunting_longbow_rest_bluntarrow_default": "长弓·放松·钝箭", "hunting_longbow_aim_arrow_default": "长弓·瞄准·箭",
    "hunting_longbow_aim_bluntarrow_default": "长弓·瞄准·钝箭",
    "throne_room_one_handed_passive_1": "王座厅单手持械 1", "throne_room_one_handed_passive_2": "王座厅单手持械 2",
    "throne_room_one_handed_passive_3": "王座厅单手持械 3", "throne_room_two_handed_passive_1": "王座厅双手持械",
    "throne_room_two_handed_passive_shield": "王座厅持盾", "jodan_no_kamae": "上段之构", "chudan_no_kamae": "中段之构",
    "gedan_no_kamae": "下段之构", "hasso_no_kamae": "八相之构", "waki_gamae": "胁构", "relaxed_spear": "持矛放松",
    "throne_room_ruler": "御座", "throne_room_ruler_3": "御座 3", "throne_room_ruler_hu": "御座（笏）",
    "throne_room_ruler_jp": "御座（和）", "emperor": "帝王", "holding_hu": "持笏", "throne_room_chancellor": "王座厅宰相",
    "throne_room_messenger_1": "信使 1", "throne_room_messenger_2": "信使 2", "throne_room_messenger_3": "信使 3",
    "holding_scrolls": "持卷轴", "throne_room_conversation_1": "交谈 1", "throne_room_conversation_2": "交谈 2",
    "throne_room_conversation_3": "交谈 3", "throne_room_conversation_4": "交谈 4", "throne_room_cheer_1": "欢呼 1",
    "throne_room_cheer_2": "欢呼 2", "throne_room_applaud_1": "鼓掌", "throne_room_kneel_1": "跪 1",
    "throne_room_kneel_2": "跪 2", "seiza": "正坐", "sitting": "坐", "throne_room_curtsey_1": "屈膝礼",
    "throne_room_bow_1": "鞠躬 1", "throne_room_bow_2": "鞠躬 2", "throne_room_bow_3": "鞠躬 3", "prostration": "叩拜",
    "gongshou": "拱手", "gongshou_with_hu": "持笏拱手", "wu_shu_bao_quan_li": "抱拳礼",
    "jockey_lance_tilted": "骑枪竖持", "jockey_lance_couched_gallop": "骑枪冲锋", "jockey_gallop": "疾驰",
    "jockey_idle": "骑马待机", "jockey_victory": "骑马胜利", "jockey_loss": "骑马失利", "jockey_walk": "骑马慢行",
    "jockey_wave": "骑马挥手", "standing_horse": "牵马", "horse_archer_idle": "骑射待机", "horse_archer_aggressive": "骑射进攻",
    "horse_surveying": "骑马眺望", "horse_exhausted": "骑马疲惫", "horse_conversing_right": "骑马交谈（右）",
    "horse_conversing_left": "骑马交谈（左）", "horse_sword_shield": "骑马剑盾", "chariot_neutral": "战车",
    "chariot_happy": "战车·高兴", "chariot_shocked": "战车·震惊", "chariot_w_horses_neutral": "驷车",
    "chariot_w_horses_happy": "驷车·高兴", "chariot_w_horses_shocked": "驷车·震惊",
    "shawm_active": "吹肖姆管", "shawm_idle": "持肖姆管", "qanun_active": "弹卡龙琴", "qanun_idle": "持卡龙琴",
    "guqin_active": "弹古琴", "guqin_idle": "抚古琴", "lute_active": "弹琉特琴", "lute_idle": "持琉特琴",
    "chifonie_active": "摇轮琴", "chifonie_idle": "持轮琴", "alto_flute_active": "吹长笛", "alto_flute_idle": "持长笛",
    "incapable": "失能", "dead": "死亡", "map_worry": "地图·忧虑", "map_ecstasy": "地图·狂喜",
    "map_dismissal": "地图·不屑", "map_disbelief": "地图·难以置信", "map_rage": "地图·暴怒", "map_love": "地图·爱慕",
    "map_disgust": "地图·厌恶", "map_schadenfreude": "地图·看好戏", "map_fear": "地图·恐惧",
    # idle 块内拆出的变体（2026-09-08 晚）
    "holding_hu_2": "持笏 2", "holding_hu_3": "持笏 3",
    # 2026-10-01 漏解析的 43 个块（块头带行尾注释 / `名= {`）
    "mental_anguish": "精神痛苦", "holding_crutch": "拄拐", "thinking_all": "思考（全）",
    "contemplating": "冥想", "deliberation": "斟酌", "crowning": "加冕", "condemnation": "谴责",
    "japanese_war_fan": "军配团扇", "pointing": "指点", "villain": "反派", "zealous_superior": "狂信上位",
    "acknowledging_crozier": "持牧杖致意", "divine_gesture_1": "神圣手势 1", "divine_gesture_2": "神圣手势 2",
    "divine_gesture_3": "神圣手势 3", "divine_gesture_4": "神圣手势 4", "divine_ecstasy": "神圣狂喜",
    "divine_ecstasy_kneeling": "跪地神圣狂喜", "holding_crozier": "持牧杖", "prayer_muslim": "礼拜",
    "prayer_crossing": "画十字祈祷", "prayer_standing": "站立祈祷", "prayer_standing_eyes": "站立祈祷（睁眼）",
    "preaching": "布道", "preaching_no_book": "布道（无书）", "thurible": "提香炉", "situla": "持圣水桶",
    "priest_good": "善良祭司", "priest_corrupt": "腐败祭司", "whipping": "鞭笞", "self_flagellation": "自笞",
    "sword_shield_aggressive": "剑盾威慑", "aggressive_axe": "斧·进攻", "aggressive_mace": "锤·进攻",
    "aggressive_sword": "剑·进攻", "aggressive_dagger": "匕首·进攻", "aggressive_spear": "矛·进攻",
    "aggressive_hammer": "战锤·进攻", "celebrate_axe": "斧·庆祝", "celebrate_mace": "锤·庆祝",
    "celebrate_sword": "剑·庆祝", "celebrate_dagger": "匕首·庆祝", "celebrate_spear": "矛·庆祝",
    "celebrate_hammer": "战锤·庆祝", "spiritual_fulfillment": "心灵满足",
    # 2026-10-01 变体/孤立 state 拆出的 synthetic 姿势（名字参照 tools/physics/action_labels_cn.json）
    "chancellor_council_chancellor_asia": "宰相·东方", "spymaster_council_spymaster_asia": "间谍首领·东方",
    "chaplain_council_chaplain_task": "神道祈祷", "frontend_center_idle_frontend_middle_infant": "主菜单·婴儿",
    "frontend_center_idle_frontend_middle_infant_2": "主菜单·婴儿 2",
    "frontend_center_idle_frontend_middle_child": "主菜单·孩童",
    "frontend_center_idle_frontend_middle_pregnant": "主菜单·孕期", "personality_honorable_pregnant": "孕期",
    "crying_infant": "婴儿哭泣", "crying_child": "孩童哭泣", "drink_goblet_japanese_drink": "和式饮酒",
    "horse_archer_aggressive_2": "骑射进攻 2", "extra_ai_cynical_asia_action": "待机·怀疑·东方（动作）",
    "extra_kagura_dance": "神乐舞", "extra_throneroom_ruler1_entry": "御座 1",
    "extra_throneroom_rulerchild1_entry": "幼主御座",
    "defeated": "待机·战败", "character_murchad": "待机·穆尔哈德",
}

_BANNER = re.compile(r"^#[\s#]*([A-Za-z][A-Za-z ()/]*?)[\s#]*$")
# 块头允许行尾注释与等号前无空格（2026-10-01：原来写死 `名 = {\n`，原版 302 个顶层块里
# `crowning = { #Introduced in ACH`、`japanese_war_fan= {` 这类 43 个被静默漏掉，
# 其中 20 多个动作已烘焙却在换装窗口里选不到）。
_BLOCK = re.compile(r"^([a-z_0-9]+)\s*=\s*\{[ \t]*(?:#[^\n]*)?\n(.*?)^\}", re.M | re.S)
_DEFAULT = re.compile(r'default = \{\s*head = "([^"]+)"\s*torso = "([^"]+)"')
# default 子块直接层（两个 tab 缩进）的道具行
# 块内可能带 `trigger = { NOT = { ... } }`（原版 chancellor），按「同缩进闭合」取整块；
# trigger 在 _parse_props 里丢掉（moe 角色靠 flag 选姿势，原版的角色排除条件不适用）。
# 旧写法 `[^}]*\}` 停在 trigger 的第一个 `}`，chancellor 的道具行整条丢失。
_PROPS = re.compile(r"^\t\t(portrait_modifier_pack = \w+|portrait_modifier = \{.*?^\t\t\})",
                    re.M | re.S)
# 变体（default 子块直接层的 `名 = { ... }`，两 tab 缩进）及其内部三 tab 缩进的道具行
_VARIANT = re.compile(r"^\t\t(\w+) = \{\n(.*?)^\t\t\}", re.M | re.S)
_VANIM = re.compile(r'animation = \{\s*head = "([^"]+)"')
_VANIM2 = re.compile(r'animation = \{\s*head = "([^"]+)"\s*torso = "([^"]+)"')
# idle 块内性格待机变体的中文名：AI_<性格>[_asia]
_AI_CN = {"bold": "勇敢", "callous": "冷酷", "compassionate": "仁慈", "content": "知足", "coward": "怯懦",
          "cynical": "怀疑", "dishonorable": "奸诈", "forgiving": "宽恕", "greedy": "贪婪", "honorable": "正直",
          "irrational": "冲动", "rational": "理性", "vengeful": "记仇", "zealous": "虔诚"}


def _idle_var_names(vname, st):
    m = re.match(r"AI_([a-z]+?)(_asia)?$", vname)
    if m:
        cn = "待机·" + _AI_CN.get(m.group(1), m.group(1)) + ("·东方" if m.group(2) else "")
        en = "Idle " + m.group(1).capitalize() + (" (Asia)" if m.group(2) else "")
        return cn, en
    return NAME_CN.get(vname) or NAME_CN.get(st) or ("待机·" + _en_name(vname)), "Idle " + _en_name(vname)


def _parse_props(raw_props):
    props = []
    for raw in raw_props:
        raw = re.sub(r"\s+", " ", raw).strip()
        if raw.startswith("portrait_modifier_pack"):
            props.append(("pack", raw.split("=")[1].strip()))
        else:
            inner = raw[raw.index("{") + 1: raw.rindex("}")]
            inner = re.sub(r"trigger = \{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}", "", inner)
            pairs = re.findall(r"(\w+) = (\w+)", inner)
            props.append(("modifier", pairs))
    return props
_VPROPS = re.compile(r"^\t\t\t(portrait_modifier_pack = \w+|portrait_modifier = \{[^}]*\})", re.M | re.S)


def _en_name(block):
    return " ".join(w.capitalize() for w in block.split("_"))


def _body_states():
    out = set()
    for bd in BODY_ASSET_DIRS:
        p = Path(_AC.GAME) / bd
        if p.is_dir():
            for f in p.glob("*.asset"):
                out |= set(re.findall(r'state\s*=\s*\{\s*name\s*=\s*"([^"]+)"',
                                      f.read_text(encoding="utf-8-sig", errors="replace")))
    return out


def load(text=None):
    """→ [dict(key, state, group, group_cn, group_en, cn, en, props)]，顺序照原版。
    props 是道具行列表：("pack", 名) / ("modifier", [(容器, 值)...])。skipped 附在返回列表的 .skipped 上。"""
    if text is None:
        if not ANIM_TXT.is_file():
            raise SystemExit(f"**找不到原版 animations.txt**: {ANIM_TXT}")
        text = ANIM_TXT.read_text(encoding="utf-8-sig")
    states = _body_states()
    # 先按行扫横幅，记每个块起始行号所属的分组
    group_at = {}
    cur = ("其他", "Other")
    pos = 0
    line_groups = []
    for line in text.split("\n"):
        m = _BANNER.match(line)
        if m and m.group(1).strip():
            key = re.sub(r"\s+", " ", m.group(1).strip()).lower()
            if key in GROUP_NAMES:
                cur = GROUP_NAMES[key]
        line_groups.append((pos, cur))
        pos += len(line) + 1

    def group_of(off):
        g = line_groups[0][1]
        for p, gg in line_groups:
            if p > off:
                break
            g = gg
        return g

    out = []
    skipped = []
    for m in _BLOCK.finditer(text):
        name, body = m.group(1), m.group(2)
        d = _DEFAULT.search(body)
        if not d:
            skipped.append((name, "no default"))
            continue
        st = d.group(1)
        if name in SKIP_BLOCKS or (name != "idle" and st in SKIP_STATES):
            skipped.append((name, f"state {st}"))
            continue
        if states and st not in states:
            skipped.append((name, f"state {st} not in body .asset"))
            continue
        raw_props = _PROPS.findall(body)
        # 默认层只有表情贴花（steward：`animations = council_steward_decal`）不算有道具，
        # 照样去变体里找道具行。
        if not any("animations_props" in r or "portrait_modifier_pack" in r for r in raw_props):
            # default 层没道具 → 找 animation 与默认 state 相同的变体（85 个块这么写：翻书/书写/
            # 全部武器/弓/笏都在这里）。优先无 weight 的（无条件生效），否则取第一个。
            cands = []
            for vm in _VARIANT.finditer(body):
                vb = vm.group(2)
                a = _VANIM.search(vb)
                if a and a.group(1) == st:
                    vp = _VPROPS.findall(vb)
                    if vp:
                        cands.append(("weight" in vb, vp))
            if cands:
                cands.sort(key=lambda c: c[0])
                raw_props = cands[0][1]
        # 道具行解析成结构：("pack", 名) 或 ("modifier", [(容器, 值), ...])。
        # 消费方要重新排成**多行**写法 —— build_moe_mod 第四处改动追加我们版本的正则
        # 要求 `portrait_modifier = {` 后换行、同缩进闭合，折成一行会漏接道具。
        props = _parse_props(raw_props)
        g_cn, g_en = group_of(m.start())
        out.append(dict(key=name, state=st, group_cn=g_cn, group_en=g_en,
                        cn=NAME_CN.get(name, _en_name(name)), en=_en_name(name),
                        props=props, synthetic=False))
    # 2026-09-08 用户「所有 idle 缩在一个选项里，能拆开吗」：原版 idle 块内的加权变体，凡其 state 没被任何
    # 顶层块用到的（实测 11 个：9 个 AI_*_asia + 持笏 2/3），各自成一个姿势（键 idle_<变体名小写>，归「待机」组）。
    # 键不是原版块名 → synthetic=True，build_moe_mod 要在 animations.txt 末尾补同名顶层块给中栏预览用。
    _mi = re.search(r"^idle = \{\n(.*?)^\}", text, re.M | re.S)
    if _mi:
        used = {q["state"] for q in out}
        g_cn, g_en = group_of(_mi.start())
        for vm in _VARIANT.finditer(_mi.group(1)):
            vname, vb = vm.group(1), vm.group(2)
            a = _VANIM2.search(vb)
            if not a or a.group(1) != a.group(2):
                skipped.append((f"idle/{vname}", "head!=torso 或无 animation"))
                continue
            st = a.group(1)
            if st in SKIP_STATES or re.search(r"Fat|Dwarf|Infant|pregnant", st, re.I):
                skipped.append((f"idle/{vname}", f"state {st} 体型变体/默认"))
                continue
            if st in used:
                skipped.append((f"idle/{vname}", f"state {st} 已有姿势"))
                continue
            if states and st not in states:
                skipped.append((f"idle/{vname}", f"state {st} not in body .asset"))
                continue
            used.add(st)
            cn, en = _idle_var_names(vname, st)
            out.append(dict(key=f"idle_{vname.lower()}", state=st, group_cn=g_cn, group_en=g_en,
                            cn=cn, en=en, props=_parse_props(_VPROPS.findall(vb)), synthetic=True))
    out.extend(_extra_poses(text, out, skipped))
    out = _Poses(out)
    out.skipped = skipped
    return out


# ---- 补齐到烘焙清单（2026-10-01 用户「gui 里的动作不如烘焙数量多」）
#
# 烘焙按 female_body.asset 的 state 表走（331 个基名），姿势按 animations.txt 的块走，两套命名空间
# （[[ck3-portrait-animation-four-name-layers]]）。块的默认 state 覆盖不到的烘焙动作有两类：
#   A. 只作为**其他块的变体**出现：文化/宗教/年龄条件挑中的 `council_chancellor_asia`、神道 `kami_prayer`、
#      `japanese_drink`、`crying_infant` 等。原版只在满足条件的角色上随机到，玩家点不到。
#   B. **不被任何块引用**的 state：`kagura_dance`、`throneRoom_ruler1_entry`（ruler 块只用 ruler2）等。
# 两类都做成 synthetic 姿势（build_moe_mod 在 animations.txt 末尾补同名顶层块给中栏预览）。
# **不单独开按钮**的：被 next_state 自动接上的 _loop/_action/_2 段（点入口就会播到），以及 bind_pose（T 字位）。
EXTRA_GROUP = ("杂项", "Misc")
# B 类 state 借哪个块的分组与道具行（坐姿要 seated_skirt / no_cloak，不借就是坐在空气里裙子穿模）
EXTRA_LIKE = {"throneRoom_ruler1_entry": "throne_room_ruler",
              "throneRoom_rulerChild1_entry": "throne_room_ruler",
              "AI_cynical_asia_action": "personality_cynical",
              "kagura_dance": "dancing"}
_BODY_NOISE = re.compile(r"Fat|Dwarf")
_STATE_LINE = re.compile(r'state\s*=\s*\{([^}\n]*)\}')


def _state_graph():
    """female_body.asset → ({state: {折叠基名}}, {state: {next_state}})。注释行不算。"""
    anims, nxt = {}, {}
    p = Path(_AC.GAME) / "female_body" / "female_body.asset"
    if not p.is_file():
        return anims, nxt
    for line in p.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _STATE_LINE.search(line)
        if not m:
            continue
        n = re.search(r'name\s*=\s*"([^"]+)"', m.group(1))
        a = re.search(r'animation\s*=\s*"([^"]+)"', m.group(1))
        if not (n and a):
            continue
        anims.setdefault(n.group(1), set()).add(_AC.fold(a.group(1)))
        x = re.search(r'next_state\s*=\s*"([^"]+)"', m.group(1))
        if x:
            nxt.setdefault(n.group(1), set()).add(x.group(1))
    return anims, nxt


def _head_states():
    p = Path(_AC.GAME) / "female_head" / "female_head.asset"
    if not p.is_file():
        return set()
    return set(re.findall(r'^[ \t]*state\s*=\s*\{\s*name\s*=\s*"([^"]+)"',
                          p.read_text(encoding="utf-8-sig", errors="replace"), re.M))


def reachable_bases(poses, anims=None, nxt=None):
    """姿势集合经 next_state 闭包能播到的全部动画基名（去掉 body_ 前缀，与烘焙清单同名）。"""
    if anims is None:
        anims, nxt = _state_graph()
    seen, stack = set(), [p["state"] for p in poses]
    while stack:
        s = stack.pop()
        if s not in seen:
            seen.add(s)
            stack.extend(nxt.get(s, ()))
    return {b[5:] if b.startswith("body_") else b for s in seen for b in anims.get(s, ())}


def _extra_poses(text, out, skipped):
    anims, nxt = _state_graph()
    if not anims:
        return []
    heads = _head_states()
    blocks = {m.group(1) for m in _BLOCK.finditer(text)}
    keys = {q["key"] for q in out}
    extra = []

    def reach():
        return reachable_bases(out + extra, anims, nxt)

    def ok_state(st, where):
        if _BODY_NOISE.search(st) or st in SKIP_STATES or st in SKIP_BLOCKS:
            return False
        if st not in anims or (heads and st not in heads):
            skipped.append((where, f"state {st} 头或身 .asset 缺"))
            return False
        return True

    def new_key(k):
        k = re.sub(r"[^a-z0-9_]", "_", k.lower())
        assert k not in keys and k not in blocks, f"synthetic 姿势键撞名：{k}"
        keys.add(k)
        return k

    # A. 各块内的变体：state 与 head/torso 同名、且其动画基名还没被任何姿势播到
    got = reach()
    for m in _BLOCK.finditer(text):
        bname, body = m.group(1), m.group(2)
        if bname == "idle" or bname in SKIP_BLOCKS:
            continue   # idle 的变体上面已经拆过
        g_cn, g_en = next((q["group_cn"], q["group_en"]) for q in out if q["key"] == bname) \
            if bname in keys else EXTRA_GROUP
        for vm in _VARIANT.finditer(body):
            vname, vb = vm.group(1), vm.group(2)
            a = _VANIM2.search(vb)
            if not a or a.group(1) != a.group(2):
                continue
            st = a.group(1)
            if not ok_state(st, f"{bname}/{vname}"):
                continue
            new_bases = {b[5:] for b in anims[st]} - got
            if not new_bases:
                continue
            k = new_key(f"{bname}_{vname}" if not vname.lower().startswith(bname) else vname)
            cn = NAME_CN.get(k) or (NAME_CN.get(bname, _en_name(bname)) + "·" + _en_name(vname))
            extra.append(dict(key=k, state=st, group_cn=g_cn, group_en=g_en, cn=cn,
                              en=f"{_en_name(bname)} ({_en_name(vname)})",
                              props=_parse_props(_VPROPS.findall(vb)), synthetic=True))
            got = reach()

    # B. 不被任何块引用、也不是别的 state 的 next_state 落点的入口 state
    referenced = set(re.findall(r'(?:head|torso) = "([^"]+)"', text))
    targets = {n for s, ns in nxt.items() for n in ns if n != s}
    for st in sorted(anims):
        if st in referenced or st in targets or not ok_state(st, f"<state>/{st}"):
            continue
        if not ({b[5:] for b in anims[st]} - got):
            continue
        k = new_key("extra_" + st)
        like = next((q for q in out if q["key"] == EXTRA_LIKE.get(st)), None)
        g_cn, g_en = (like["group_cn"], like["group_en"]) if like else EXTRA_GROUP
        extra.append(dict(key=k, state=st, group_cn=g_cn, group_en=g_en,
                          cn=NAME_CN.get(k, _en_name(st)), en=_en_name(st),
                          props=list(like["props"]) if like else [], synthetic=True))
        got = reach()
    return extra


class _Poses(list):
    skipped = ()


def props_text(props, ind):
    """把 props 排成 animations.txt 变体里的多行写法（ind 是变体内容的缩进）。"""
    out = []
    for kind, v in props:
        if kind == "pack":
            out.append(f"{ind}portrait_modifier_pack = {v}\n")
        else:
            out.append(f"{ind}portrait_modifier = {{\n"
                       + "".join(f"{ind}\t{k} = {val}\n" for k, val in v)
                       + f"{ind}}}\n")
    return "".join(out)


def synthetic_block_text(pose):
    """synthetic 姿势在 animations.txt 里的顶层块：块名 = 键，默认 state = 变体的 state，道具行原样。
    结构照原版 `scepter = { default = { default = {...} portrait_modifier = {...} } }`。"""
    return (f'{pose["key"]} = {{\n\tdefault = {{\n'
            f'\t\tdefault = {{ head = "{pose["state"]}" torso = "{pose["state"]}" }}\n'
            + props_text(pose["props"], "\t\t") + "\t}\n}\n")


def group_key(group_en):
    """英文组名 → loc / gui 变量里用的小写键（`Emotion` → `emotion`）。"""
    return re.sub(r"[^a-z0-9]+", "_", group_en.lower()).strip("_")


def groups(poses):
    """→ [(group_cn, group_en, [poses])] 保持首次出现顺序。"""
    order, bucket = [], {}
    for p in poses:
        k = (p["group_cn"], p["group_en"])
        if k not in bucket:
            order.append(k)
            bucket[k] = []
        bucket[k].append(p)
    return [(k[0], k[1], bucket[k]) for k in order]


if __name__ == "__main__":
    ps = load()
    print(f"{len(ps)} 个姿势，跳过 {len(ps.skipped)}：{ps.skipped}")
    for gcn, gen, items in groups(ps):
        print(f"[{gcn}/{gen}] {len(items)}: " + " ".join(p["key"] for p in items))
    miss = [p["key"] for p in ps if p["key"] not in NAME_CN]
    print("无中文名:", miss)
    nprops = sum(1 for p in ps if p["props"])
    print(f"带道具行 {nprops}")
    syn = [p for p in ps if p["synthetic"]]
    print(f"idle 块拆出的 synthetic 姿势 {len(syn)}: " + " ".join(f'{p["key"]}({p["cn"]})' for p in syn))