# 完整调查所有手持道具的三层链（pack → item → gene:template），
# 归 tools/（2026-09-05 从 cache/ 挪来）：build_moe_props.py 缺 cache/props_chain.json 时会自动调它。
# 为后续生成器输出清单。输出 JSON 供其它脚本消费。
import re
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
G = Path("E:/SteamLibrary/steamapps/common/Crusader Kings III/game")
PROP_GENES = {"props_left", "props_right", "animated_props"}


def balanced(s, i):
    assert s[i] == "{", s[i:i + 20]
    d, j = 0, i
    while j < len(s):
        if s[j] == "{":
            d += 1
        elif s[j] == "}":
            d -= 1
            if d == 0:
                return s[i + 1:j]
        j += 1
    return None


# 1) animations_props item → [(gene, template)]
txt = (G / "gfx/portraits/portrait_modifiers/00_animation_props.txt"
       ).read_text(encoding="utf-8-sig", errors="replace")
items = {}               # item 名 -> [(gene, template)]
for m in re.finditer(r'^\t([a-z0-9_]+)\s*=\s*\{', txt, re.M):
    iname = m.group(1)
    body = balanced(txt, m.end() - 1)
    if body is None:
        continue
    refs = []
    for am in re.finditer(
            r'gene\s*=\s*([a-z0-9_]+)[\s\S]{0,120}?template\s*=\s*([a-z0-9_]+)',
            body):
        refs.append((am.group(1), am.group(2)))
    if any(g in PROP_GENES for g, _ in refs):
        items[iname] = refs

# 2) .modifierpack 里所有 pack 的定义
pack_txt = (G / "gfx/portraits/portrait_animations/animations.modifierpack"
            ).read_text(encoding="utf-8-sig", errors="replace")
packs = {}               # pack 名 -> [item 名]
for m in re.finditer(r'^([a-z0-9_]+)\s*=\s*\{([\s\S]+?)^\}', pack_txt, re.M):
    pn = m.group(1)
    body = m.group(2)
    its = re.findall(r'animations_props\s*=\s*([a-z0-9_]+)', body)
    # 过滤：只保留引用了手持道具 item 的 pack
    if any(i in items for i in its):
        packs[pn] = its

# 3) animations.txt 的引用：所有 portrait_modifier_pack / animations_props
anim_txt = (G / "gfx/portraits/portrait_animations/animations.txt").read_text(
    encoding="utf-8-sig", errors="replace")
cur = None
anim_refs = []           # (动画块, 类型, 名)
for ln in anim_txt.split("\n"):
    m = re.match(r'^([a-z0-9_]+)\s*=\s*\{', ln)
    if m:
        cur = m.group(1)
    m2 = re.search(r'(animations_props|portrait_modifier_pack)\s*=\s*([a-z0-9_]+)',
                   ln)
    if m2 and cur:
        name = m2.group(2)
        # 只保留引用了 pack 或 item 的
        if name in packs or name in items:
            anim_refs.append((cur, m2.group(1), name))

# 输出 JSON
out = {
    "items": {i: [{"gene": g, "template": t} for g, t in rs]
              for i, rs in items.items()},
    "packs": packs,
    "anim_refs": [{"anim": a, "type": t, "name": n}
                  for a, t, n in anim_refs],
}
json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
