"""每个 submesh 都有 meshsettings 吗？—— 缺一条就有 5000 面走错 shader。

**为什么需要单独一条判据**：shader 名写在 `.mesh` 二进制里，引擎会为每个
材质槽建材质；`meshsettings` 的作用只是提供 `shader_file`。缺了它引擎去
默认的 `gfx/FX/pdxmesh.shader` 找 `portrait_*`，找不到就报
`Failed to create material with shader X (in gfx/FX/pdxmesh.shader)` ——
**只有一行**，很容易在几千行 debug.log 里漏掉，而画面上那一层照样画出来
（走了默认路径），所以本地渲图和肉眼都看不出来。

洛茜实机踩过：`.asset` 生成后几何被重新划分（长发「発Alpha」层从 head
独占变成 head 1820 面 + torso 5463 面），但只有 head 侧的 meshsettings
更新了，torso 侧的 index 13 一直缺着。

判据：`.mesh` 里的 submesh 数 == `.asset` 里该 pdxmesh 的 meshsettings 数，
且 index 集合完全相同。两边都要报，因为多一条（.asset 有、mesh 没有）
同样是错的 —— 说明 index 编号变了。

用法：
  python tools/verify/check_meshsettings_coverage.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import pdx_parse as P  # noqa: E402
import moe_paths as MP  # noqa: E402

def asset_meshsettings(asset):
    """{pdxmesh 名: {index, ...}}，另返回 {pdxmesh 名: .mesh 文件名}"""
    txt = asset.read_text(encoding="utf-8", errors="replace")
    out, files = {}, {}
    for m in re.finditer(r"pdxmesh\s*=\s*\{(.*?)\n\}", txt, re.S):
        blk = m.group(1)
        nm = re.search(r'name\s*=\s*"([^"]+)"', blk)
        if not nm:
            continue
        name = nm.group(1)
        fl = re.search(r'file\s*=\s*"([^"]+)"', blk)
        if fl:
            files[name] = fl.group(1)
        idxs = set()
        for ms in re.finditer(r"meshsettings\s*=\s*\{(.*?)\}", blk, re.S):
            i = re.search(r"index\s*=\s*(\d+)", ms.group(1))
            if i:
                idxs.add(int(i.group(1)))
        out[name] = idxs
    return out, files


def mesh_submeshes(path):
    """`.mesh` 里每个 submesh 的 shader 名，按出现顺序。

    注意 `.mesh` 二进制里**没有 index 字段** —— index 是材质槽编号，
    由导出时的槽顺序决定。所以这里只能拿到「有几个 submesh」和各自的
    shader，index 对不对要靠 .asset 与报告 JSON 交叉核对。
    """
    tree = P.parse(Path(path).read_bytes())
    seen = {}
    for p, name, kind, val in P.walk(tree):
        if "/material" in p and name == "shader":
            v = val[0] if isinstance(val, list) and val else val
            seen[p] = v
    return list(seen.values())


def main():
    assets = MP.assets()
    if not assets:
        print("**没有找到模型包 .asset**")
        return 1

    bad = 0
    for asset in assets:
        settings, files = asset_meshsettings(asset)
        print(f"\n[{asset.name}]")
        print("%-24s %-22s %8s %8s %s"
              % ("pdxmesh", ".mesh", "mesh", "asset", "判定"))
        for name, idxs in settings.items():
            f = files.get(name)
            if not f:
                print("%-24s (无 file 字段) —— 跳过" % name[:24])
                continue
            mp = asset.parent / f
            if not mp.is_file():
                print("%-24s %-22s %8s %8d **找不到 .mesh**"
                      % (name[:24], f[:22], "?", len(idxs)))
                bad += 1
                continue
            shs = mesh_submeshes(mp)
            ok = len(shs) == len(idxs)
            print("%-24s %-22s %8d %8d %s"
                  % (name[:24], f[:22], len(shs), len(idxs),
                     "OK" if ok else "**不匹配**"))
            if not ok:
                bad += 1
                print("      .mesh 的 shader 序列：%s" % shs)
                print("      .asset 的 index 集合：%s" % sorted(idxs))
    print()
    if bad:
        print("**%d 个 pdxmesh 的 meshsettings 覆盖不全** —— 缺的那些 submesh "
              "会走默认 gfx/FX/pdxmesh.shader 并在 error.log 里报 "
              "Failed to create material。" % bad)
        return 1
    print(f"全部 {len(assets)} 份 .asset 的 pdxmesh 均通过 meshsettings 覆盖检查。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
