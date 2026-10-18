# -*- coding: utf-8 -*-
"""仓库自审：从**文档声明的入口**出发算 tools/ 的可达闭包，其余列为候选。

为什么要有这个脚本：`grep -l <名字>` 会把**注释里提到的名字**也算成引用，
于是「一次性探针」和「管线核心」在计数上无法区分。这里只认两种真引用：

    import moe_paths / from moe_paths import ...     → 模块依赖
    subprocess([... "tools/x.py" ...]) 或字符串里出现 "tools/x.py"  → 调用

入口 = README.md 与 docs/*.md 里出现在可执行命令行中的 tools/*。
判据：**不可达 ≠ 该删**，只说明「没有任何文档路径会用到它」，
所以输出叫「候选」，由人决定。

    python tools/audit_repo.py                # 打印分类
    python tools/audit_repo.py --json out.json
"""
import argparse
import ast
import io
import json
import pathlib
from pathlib import Path
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
DOCS = [ROOT / "README.md"] + sorted((ROOT / "docs").glob("*.md"))

# **必须允许子目录**（tools/verify/final_verify.py）。只匹配 `tools/x.py`
# 会把 verify/ 下三个脚本判成孤立 —— README 里其实有它们的命令行。
_S = r"((?:[A-Za-z_0-9]+[/\\])*[A-Za-z_0-9]+\.(?:py|sh|ps1))"
SCRIPT_RE = re.compile(r"tools[/\\]" + _S)
CMD_RE = re.compile(
    r"(?:python|blender|bash|powershell|sh)\b[^`|\n]*?tools[/\\]" + _S)


def all_scripts():
    out = {}
    for p in sorted(TOOLS.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        if p.suffix in (".py", ".sh", ".ps1"):
            out[p.relative_to(TOOLS).as_posix()] = p
    return out


def deps_of(path, names):
    """返回该脚本真正引用的其它 tools 脚本（模块 import + 路径字符串）。"""
    txt = io.open(path, encoding="utf-8", errors="replace").read()
    got = set()
    # 1) 路径字符串 "tools/x.py"
    for m in SCRIPT_RE.finditer(txt):
        k = m.group(1).replace("\\", "/")
        if k in names:
            got.add(k)
    # 2) import 本目录模块（.py 才有）
    if path.suffix == ".py":
        stems = {Path(n).stem: n for n in names if n.endswith(".py")}   # 子目录化后按文件名 stem 匹配 import
        try:
            tree = ast.parse(txt)
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                for mod in mods:
                    top = mod.split(".")[0]
                    if top in stems:
                        got.add(stems[top])
        else:
            # 语法解析不了（例如 Blender 专用语法糖）时退回正则
            for stem, n in stems.items():
                if re.search(rf"(?:^|\n)\s*(?:import|from)\s+{stem}\b", txt):
                    got.add(n)
    got.discard(path.name)
    return got


def doc_entries(names):
    """文档里出现在**可执行命令行**中的脚本 = 入口。"""
    ent, mentioned = set(), set()
    for d in DOCS:
        if not d.is_file():
            continue
        txt = io.open(d, encoding="utf-8", errors="replace").read()
        for m in CMD_RE.finditer(txt):
            k = m.group(1).replace("\\", "/")
            if k in names:
                ent.add(k)
        for m in SCRIPT_RE.finditer(txt):
            k = m.group(1).replace("\\", "/")
            if k in names:
                mentioned.add(k)
    return ent, mentioned


def dead_doc_refs():
    """文档里写成**可执行路径**却不存在的脚本。

    只查带目录的引用（`tools/x.py`、`work/evidence/y.py`），**不查裸文件名**
    —— 裸名大量出现在历史叙述里（「当年用 _chk_taillen.py 量的」），那是结论
    的一部分，不是失效链接。分清这两者是这个检查有没有用的关键：早先我用裸名
    统计，得到 52 处「死引用」，其中 40 处是误报（连 `.shader` 都被正则切成
    `.sh` 算进去了）。
    """
    out = []
    for d in DOCS:
        if not d.is_file():
            continue
        txt = io.open(d, encoding="utf-8", errors="replace").read()
        for m in re.finditer(
                r"\b((?:tools|work)[/\\][A-Za-z_0-9/\\]*"
                r"[A-Za-z_0-9]+\.(?:py|sh|ps1))(?![A-Za-z0-9])", txt):
            rel = m.group(1).replace("\\", "/")
            if not (ROOT / rel).is_file():
                out.append((d.name, rel))
    return out


def unresolvable_imports(names):
    """脚本 import 了别的 tools 子目录的模块，却没把那个子目录放进 sys.path。

    判据只看**声明**：脚本自身所在目录天然可见；其余目录要出现在含
    `sys.path` 的语句里（`sys.path[:0] = [... for _d in ("lib", "anim")]`、
    `sys.path.append(... / "build")` 都算）。函数内的懒导入一样要查 ——
    2026-09-06 重组后 lib/moe_anim_catalog 三处懒导入 build_moe_props，静态
    `python -c import` 冒烟全过，跑到艾尔黛拉重定向才 345 条全炸。
    Blender 内跑的脚本一样适用（它们也自己写 sys.path）。
    """
    stem_dir = {}
    for n in names:
        if n.endswith(".py"):
            stem_dir.setdefault(Path(n).stem, set()).add(Path(n).parent.as_posix())
    out = []
    for n, p in names.items():
        if not n.endswith(".py"):
            continue
        txt = io.open(p, encoding="utf-8", errors="replace").read()
        visible = {Path(n).parent.as_posix()}
        for line in txt.splitlines():
            if "sys.path" in line:
                visible.update(re.findall(r"""["']([A-Za-z_0-9]+)["']""", line))
        try:
            tree = ast.parse(txt)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for mod in mods:
                top = mod.split(".")[0]
                if top in stem_dir and top != Path(n).stem and not (stem_dir[top] & visible):
                    out.append((n, top, sorted(stem_dir[top])[0], node.lineno))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()

    names = all_scripts()
    graph = {n: deps_of(p, names) for n, p in names.items()}
    entries, mentioned = doc_entries(names)

    reach, stack = set(entries), list(entries)
    while stack:
        cur = stack.pop()
        for d in graph.get(cur, ()):
            if d not in reach:
                reach.add(d)
                stack.append(d)

    # 被其它脚本引用但自身不可达：多半是「工具的工具」，仍有用
    used_by = {n: set() for n in names}
    for n, ds in graph.items():
        for d in ds:
            used_by[d].add(n)

    cand = sorted(set(names) - reach)
    print(f"脚本 {len(names)}  文档入口 {len(entries)}  可达 {len(reach)}  "
          f"不可达 {len(cand)}")
    print("\n== 入口（文档命令行里出现）")
    for n in sorted(entries):
        print(f"  {n}")
    print(f"\n== 不可达但被别的脚本引用（工具的工具，{sum(1 for n in cand if used_by[n])} 个）")
    for n in cand:
        if used_by[n]:
            print(f"  {n:46} ← {', '.join(sorted(used_by[n])[:3])}")
    print(f"\n== 完全孤立（无入口、无引用；只在文档里被提及的标 doc）")
    for n in cand:
        if not used_by[n]:
            print(f"  {n:46} {'doc' if n in mentioned else ''}")

    dead = dead_doc_refs()
    print(f"\n== 文档里的失效路径（{len(dead)} 处）")
    if not dead:
        print("  无 —— 所有写成路径的脚本引用都指向真实文件")
    for doc, rel in dead:
        print(f"  {doc:28} {rel}")

    bad = unresolvable_imports(names)
    print(f"\n== import 了没放进 sys.path 的子目录（{len(bad)} 处）")
    if not bad:
        print("  无 —— 每个跨目录 import 的目录都在该脚本的 sys.path 声明里")
    for n, mod, d, ln in bad:
        print(f"  {n:46}:{ln:<5} import {mod}  ← 住在 {d}/")

    if a.json:
        io.open(a.json, "w", encoding="utf-8").write(json.dumps({
            "entries": sorted(entries), "reachable": sorted(reach),
            "candidates": cand, "mentioned_only": sorted(mentioned - entries),
            "used_by": {k: sorted(v) for k, v in used_by.items() if v},
            "unresolvable_imports": [list(x) for x in bad],
        }, ensure_ascii=False, indent=1))
        print(f"\n已写 {a.json}")


if __name__ == "__main__":
    main()
