# -*- coding: utf-8 -*-
"""改 PDX .mesh 里的对象名（`/object/<名字>` 那一层）。

为什么需要：同一个 mod 里两套模型的 .mesh 如果对象名相同，
`pdxmesh` 块的 `name = ` 就无法区分，引擎按名字查找会取到错的那份。
洛茜与芙洛伦都从同一条管线出来，对象名都是 moe_head_mesh / moe_torso_mesh。

PDX 二进制里对象名是 **NUL 结尾的字符串**（后面跟若干 `[` 表示层级深度），
不是长度前缀 —— 2026-08-21 先按「1 字节长度 + 内容」写过一版，替换 0 处。
所以按「名字 + NUL」匹配，长度自适应，不需要改任何计数字节。

用法:
    python tools/model/rename_mesh_object.py <mesh 文件> <旧名> <新名>
"""
import sys
from pathlib import Path


def rename(path: Path, old: str, new: str) -> int:
    """返回替换次数。原地写回。"""
    ob, nb = old.encode("utf-8"), new.encode("utf-8")
    data = path.read_bytes()
    # 匹配「名字 + NUL」，避免误伤名字作为别处子串（如 moe_head_mesh_foo）
    pat = ob + bytes([0])
    rep = nb + bytes([0])
    n = data.count(pat)
    if n:
        path.write_bytes(data.replace(pat, rep))
    return n


def main():
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    p = Path(sys.argv[1])
    if not p.is_file():
        raise SystemExit(f"文件不存在: {p}")
    n = rename(p, sys.argv[2], sys.argv[3])
    print(f"{p.name}: 替换 {n} 处  {sys.argv[2]} -> {sys.argv[3]}")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
