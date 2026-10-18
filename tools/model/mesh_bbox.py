"""量 PDX .mesh 的顶点包围盒与骨骼列表，用于和原版比尺度。

为什么需要它：`.anim` 和 `.mesh` 里的骨骼矩阵是烘过的，用 tx 反推世界坐标
不可靠（试过 -R^T·t，原版自己的脊椎数据都算不出单调递增）。
顶点位置 `p` 是无歧义的模型空间坐标，直接拿来比尺度最稳。

用法:
    python tools/model/mesh_bbox.py <mesh 路径> [<mesh 路径> ...]
"""
import sys

import sys as _sys
from pathlib import Path as _P
_sys.path[:0] = [str(_P(__file__).resolve().parents[2] / "tools" / _d) for _d in ("lib", "anim")]   # 子目录化后底座不在脚本同目录
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parents[2] / "tools" / "lib")]
from pdx_parse import parse, walk  # noqa: E402


def analyse(path):
    tree = parse(open(path, "rb").read())
    lo = [1e18] * 3
    hi = [-1e18] * 3
    nverts = 0
    bones = set()
    submeshes = 0

    for node_path, name, kind, val in walk(tree):
        if name == "p" and kind == "float":
            submeshes += 1
            nverts += len(val) // 3
            for i in range(0, len(val), 3):
                for a in range(3):
                    v = val[i + a]
                    lo[a] = min(lo[a], v)
                    hi[a] = max(hi[a], v)
        elif "/skeleton/" in node_path:
            # **骨名是路径段本身**，不是某个叫 name 的属性。
            # 这里原先按 `name == "name" and "/skeleton/bone" in node_path`
            # 取，而真实路径形如 `/object/moe_torso_mesh/skeleton/bn_m_pelvis`
            # —— 没有 `bone` 这一段，也没有 name 属性，于是**一直报「骨骼 0」**
            # 且不报错。数字对不上却没人看出来，因为 bbox 那部分是对的。
            bones.add(node_path.split("/skeleton/", 1)[1].split("[")[0])

    print(f"=== {path}")
    fx = sum(1 for b in bones if "_fx_" in b)
    print(f"  submesh {submeshes}  顶点 {nverts}  骨骼 {len(bones)}"
          f"（其中 fx 表情骨 {fx}）")
    if nverts:
        # PDX 是 Y-up 左手系，Blender 是 Z-up；导出器做了轴转换，
        # 所以这里的 Y 才是人体高度方向。
        print("  bbox  X [%.2f, %.2f]  Y [%.2f, %.2f]  Z [%.2f, %.2f]"
              % (lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]))
        print("  尺寸  %.2f x %.2f x %.2f" % tuple(hi[a] - lo[a] for a in range(3)))
    return bones


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    for p in args:
        bones = analyse(p)
        if "--bones" in sys.argv:
            for b in sorted(bones):
                print("   ", b)
