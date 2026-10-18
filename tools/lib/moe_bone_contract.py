"""二次元 deform 语义骨架合同：日文 MMD 骨名 → ASCII 语义名。

存在两个硬约束逼出这个模块:
  1. PDX .mesh/.anim 用 latin-1 编码写骨名，日文骨名直接写入会抛
     UnicodeEncodeError。必须转 ASCII。
  2. CK3 动画按骨名索引，所以命名必须稳定、跨模型一致，才能让一套动画
     驱动不同角色。

命名规范沿用 CK3 风格 `bn_` 前缀，但用自己的语义空间避免与原版 61/134 骨混淆:
  bn_m_*  = moe 骨骼（m 表示 moe）
  _l/_r   = 左右

跨模型差异（依据 work/pmx_skeleton_inventory/comparison.json）已在映射里吸收:
  - 终末地多插 センター2 / グルーブ2 一层
  - 少前2 与鸣潮多 腰キャンセル左/右
  - 上半身2 的 parent 在三系列分别是 上半身 / 上半身1 / 上半身3
  - 少前2 全系列无 つま先（脚趾）
"""

# ---------------------------------------------------------------- 语义核心骨
# (语义名, MMD 日文名候选列表, 语义 parent)
# parent 用语义名，链路收缩在解析时完成 —— 源骨架里的中间辅助骨
# （センター2 / グルーブ2 / 腰キャンセル 等）不出现在合同里。
SEMANTIC_CORE = [
    ("bn_m_root",        ["全ての親"],              None),
    ("bn_m_center",      ["センター"],              "bn_m_root"),
    ("bn_m_groove",      ["グルーブ"],              "bn_m_center"),
    ("bn_m_waist",       ["腰"],                    "bn_m_groove"),

    ("bn_m_spine_low",   ["上半身"],                "bn_m_waist"),
    ("bn_m_spine_up",    ["上半身2"],               "bn_m_spine_low"),
    ("bn_m_neck",        ["首"],                    "bn_m_spine_up"),
    ("bn_m_head",        ["頭"],                    "bn_m_neck"),

    ("bn_m_l_shoulder",  ["左肩"],                  "bn_m_spine_up"),
    ("bn_m_l_arm",       ["左腕"],                  "bn_m_l_shoulder"),
    ("bn_m_l_elbow",     ["左ひじ"],                "bn_m_l_arm"),
    ("bn_m_l_wrist",     ["左手首"],                "bn_m_l_elbow"),
    ("bn_m_r_shoulder",  ["右肩"],                  "bn_m_spine_up"),
    ("bn_m_r_arm",       ["右腕"],                  "bn_m_r_shoulder"),
    ("bn_m_r_elbow",     ["右ひじ"],                "bn_m_r_arm"),
    ("bn_m_r_wrist",     ["右手首"],                "bn_m_r_elbow"),

    ("bn_m_pelvis",      ["下半身"],                "bn_m_waist"),
    ("bn_m_l_hip",       ["左足"],                  "bn_m_pelvis"),
    ("bn_m_l_knee",      ["左ひざ"],                "bn_m_l_hip"),
    ("bn_m_l_ankle",     ["左足首"],                "bn_m_l_knee"),
    ("bn_m_l_toe",       ["左つま先"],              "bn_m_l_ankle"),
    ("bn_m_r_hip",       ["右足"],                  "bn_m_pelvis"),
    ("bn_m_r_knee",      ["右ひざ"],                "bn_m_r_hip"),
    ("bn_m_r_ankle",     ["右足首"],                "bn_m_r_knee"),
    ("bn_m_r_toe",       ["右つま先"],              "bn_m_r_ankle"),
]

# 少前2 全系列缺这两根，合同允许缺失
OPTIONAL = {"bn_m_l_toe", "bn_m_r_toe"}

MMD_TO_SEMANTIC = {}
for sem, jp_list, _parent in SEMANTIC_CORE:
    for jp in jp_list:
        MMD_TO_SEMANTIC[jp] = sem
SEMANTIC_PARENT = {sem: parent for sem, _jp, parent in SEMANTIC_CORE}
SEMANTIC_ORDER = [sem for sem, _jp, _p in SEMANTIC_CORE]

# ---------------------------------------------------------------- 非核心骨转写
# 手指、头发、裙摆等不进语义核心，但仍要导出（有蒙皮权重），
# 所以需要一个确定性的 ASCII 转写方案。

# 手指：MMD 标准命名 左親指0/1/2、左人指1/2/3 ...
FINGER_JP_TO_EN = {
    "親指": "thumb", "人指": "index", "中指": "middle",
    "薬指": "ring", "小指": "pinky",
}
PART_JP_TO_EN = {
    "左": "l", "右": "r",
    "髪": "hair", "スカート": "skirt", "リボン": "ribbon",
    "胸": "breast", "尻": "butt", "袖": "sleeve", "裾": "hem",
    "しっぽ": "tail", "尾": "tail", "耳": "ear", "角": "horn",
    "マント": "cape", "帽子": "hat", "武器": "weapon",
    "肩P": "shoulderP", "肩C": "shoulderC",
    "腕捩": "armTwist", "手捩": "wristTwist",
    "足D": "legD", "ひざD": "kneeD", "足首D": "ankleD",
    "足先EX": "toeEX",
    "目": "eye", "眉": "brow", "口": "mouth", "舌": "tongue",
    "あご": "jaw", "顎": "jaw", "頬": "cheek",
    "腰キャンセル": "waistCancel",
    "上半身": "spine", "下半身": "pelvis",
    "首": "neck", "頭": "head", "腰": "waist",
    "肩": "shoulder", "腕": "arm", "ひじ": "elbow", "手首": "wrist",
    "足": "leg", "ひざ": "knee", "足首": "ankle", "つま先": "toe",
    "センター": "center", "グルーブ": "groove", "全ての親": "root",
    "先": "tip", "ダミー": "dummy",
}


def transliterate(name):
    """把任意 MMD 骨名转成 ASCII。确定性、可逆性不保证，但保证稳定与唯一。"""
    out = name
    # 先替换长词，避免 "上半身2" 里的 "上半身" 被短词抢先
    for jp in sorted(PART_JP_TO_EN, key=len, reverse=True):
        out = out.replace(jp, "_" + PART_JP_TO_EN[jp] + "_")
    for jp, en in FINGER_JP_TO_EN.items():
        out = out.replace(jp, "_" + en + "_")
    # 清理
    ascii_chars = []
    for ch in out:
        if ch.isascii() and (ch.isalnum() or ch in "_."):
            ascii_chars.append(ch)
        elif not ch.isascii():
            # 残留的非 ASCII 字符用码点表示，保证唯一
            ascii_chars.append(f"u{ord(ch):04x}")
        else:
            ascii_chars.append("_")
    out = "".join(ascii_chars)
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_") or "bone"



# {transliterate 出来的名字: 合同里的语义名}。**两套映射并不一致**：
# transliterate 的词表给 上半身 → "spine"、上半身2 → "spine_2"，
# 而合同表给 bn_m_spine_low / bn_m_spine_up —— 导出的 rig 用的是合同名。
# 物理管线（bake_hair_bullet）拿 PREFIX + transliterate(日文骨名) 去查 rig，
# 于是这两根查不到、被 rig.data.bones.get 静默吃掉：
# 洛茜 18 面身体碰撞墙只建成 16 面，缺的正是 `上半身`/`上半身2` 两面躯干墙。
# 后果是裙子 16 根链首（スカート_0_*）的关节 obj1 找不到对象，全部退化到
# "连锚"分支 —— 锚在骨 head 另建、位置与躯干墙不同，而枢轴仍用 PMX 的
# 交界位置，力臂错位，那一整环链首全程乱转（2026-08-22 实测链深 0 中位
# 50.45°，并沿链累积到深度 7~8 的 179.7°）。
# 这里按合同表反查生成，避免再手写第三张表。
TRANSLIT_TO_SEMANTIC = {}
for _sem, _jps, _p in SEMANTIC_CORE:
    for _jp in _jps:
        _t = transliterate(_jp).strip("_")
        if _t and _t != _sem[len("bn_m_"):]:
            TRANSLIT_TO_SEMANTIC[_t] = _sem[len("bn_m_"):]


def resolve_bone_key(key):
    """把 transliterate 出来的骨名键换成合同里的语义名（查不到则原样返回）。

    给物理管线用：spec 的键来自 transliterate，rig 的骨名来自合同表，
    两者对躯干几根骨拼写不同。
    """
    return TRANSLIT_TO_SEMANTIC.get(key, key)

def build_name_map(bone_names):
    """给一批 MMD 骨名生成 名字→ASCII 的映射。核心骨用语义名，其余转写。

    返回 (mapping, collisions)。mapping 保证值唯一。
    """
    mapping = {}
    used = set()
    collisions = []

    # 核心骨优先，拿语义名
    for jp in bone_names:
        sem = MMD_TO_SEMANTIC.get(jp)
        if sem and sem not in used:
            mapping[jp] = sem
            used.add(sem)

    # 其余转写，冲突时加序号
    for jp in bone_names:
        if jp in mapping:
            continue
        base = "bn_m_" + transliterate(jp)
        cand = base
        n = 1
        while cand in used:
            n += 1
            cand = f"{base}_{n}"
            if n == 2:
                collisions.append((jp, base))
        mapping[jp] = cand
        used.add(cand)

    return mapping, collisions


def contract_report(bone_names):
    """检查一批骨名对合同的满足情况。"""
    present, missing = [], []
    for sem, jp_list, _p in SEMANTIC_CORE:
        if any(jp in bone_names for jp in jp_list):
            present.append(sem)
        else:
            missing.append(sem)
    required_missing = [m for m in missing if m not in OPTIONAL]
    return {
        "core_present": present,
        "core_missing": missing,
        "required_missing": required_missing,
        "satisfies_contract": not required_missing,
        "core_ratio": round(len(present) / len(SEMANTIC_CORE), 4),
    }


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    inv = Path(r"D:\有趣计划\CK3_MoeMod\work\pmx_skeleton_inventory")
    out = {}
    for p in sorted(inv.glob("*.json")):
        if p.stem in ("comparison", "anim_portability", "bone_name_maps"):
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        if not d.get("import_ok"):
            continue
        arm = max(d["armatures"], key=lambda a: a["bone_count"])
        names = [b["name"] for b in arm["bones"]]
        mapping, collisions = build_name_map(names)
        rep = contract_report(set(names))
        out[p.stem] = {
            "bone_count": len(names),
            "contract": rep,
            "collisions": collisions,
            "name_map": mapping,
        }
        status = "OK" if rep["satisfies_contract"] else "缺" + ",".join(rep["required_missing"])
        print(f"{p.stem:<24} 骨={len(names):>4} 核心={len(rep['core_present'])}/{len(SEMANTIC_CORE)} "
              f"冲突={len(collisions):>2} {status}")

    dest = inv / "bone_name_maps.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {dest}")
