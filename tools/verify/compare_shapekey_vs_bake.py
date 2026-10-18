"""对某条网格边，比较「骨骼烘焙重建」与「真实 shapekey 直接驱动」的分离量。

## 为什么需要这个工具

改反向骨/双侧合并烘焙算法后，`verify_expression_bake.py` 的撕裂判据从
0.3~0.9 涨到 0.6~1.1，第一反应是「变坏了」。但那个判断基准错了——旧烘焙
重建率只有 24.7%~84.9%，本来就把真实形变打了折扣，新版更忠实地逼近源
模型，撕裂读数必然涨。真正该比的不是「新旧烘焙互相比」，而是「骨骼烘焙
是否 <= 真实 shapekey 驱动的分离量」：前者是重建精度问题，后者才是过冲
（反向骨方向算错、两根骨打架）的信号。

2026-08-18 用这个方法手工核对过三个 worst edge，结论是骨骼烘焙全部
**低于**真实值，说明当前实现是保守的，没有过冲。详见
tools/verify/verify_expression_bake.py 里的 baseline 注释。

## 用法

  blender.exe -b work/blenderProj/florence_poc.blend --python \
      tools/verify/compare_shapekey_vs_bake.py -- v1235 v1241 angry

  或不带参数跑内置的三个已知 worst edge。

命令行参数用 `--` 隔开（Blender 约定），格式: <va> <vb> <expr_name>。
expr_name 取 tools/lib/moe_expression_contract.py 的 EXPRESSIONS 键。
"""
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import moe_expression_contract as C  # noqa: E402


# 已知的三个 worst edge（来自 verify_expression_bake.py 首次跑出的结果）,
# 无参数时用它们做回归检查。
KNOWN_WORST = [
    (1235, 1241, "angry"),
    (1234, 1256, "happy"),
    (1254, 1594, "shock"),   # sadness 与 shock 共用这条边，取幅度更大的 shock 配方
]


def expr_peak_targets(scheme_bindings, expr_name):
    """把 EXPRESSIONS 配方的峰值强度，转成 shapekey 名 -> value。"""
    curve = C.EXPRESSIONS[expr_name]
    peak = {}
    for _t, vals in curve:
        for slot, s in vals.items():
            peak[slot] = max(peak.get(slot, 0.0), s)
    out = {}
    for slot, s in peak.items():
        keyname = scheme_bindings.get(slot)
        if keyname:
            out[keyname] = max(out.get(keyname, 0.0), s)
    return out


def measure(obj, a, b, targets):
    sk = obj.data.shape_keys
    basis = sk.key_blocks["Basis"].data
    l0 = (basis[a].co - basis[b].co).length
    for kb in sk.key_blocks:
        kb.value = targets.get(kb.name, 0.0)
    deps = bpy.context.evaluated_depsgraph_get()
    deps.update()
    eo = obj.evaluated_get(deps)
    me = eo.to_mesh()
    pos = [v.co.copy() for v in me.vertices]
    eo.to_mesh_clear()
    for kb in sk.key_blocks:
        kb.value = 0.0
    if a >= len(pos) or b >= len(pos):
        return l0, None, None
    l1 = (pos[a] - pos[b]).length
    return l0, l1, abs(l1 - l0)


def main():
    argv = sys.argv
    if "--" in argv:
        args = argv[argv.index("--") + 1:]
    else:
        args = []

    obj = bpy.data.objects["moe_head"]
    keys = [k.name for k in obj.data.shape_keys.key_blocks]
    scheme, _ = C.detect_naming_scheme(keys)
    bindings, _ = C.resolve_bindings(keys, scheme)

    if len(args) >= 3:
        cases = [(int(args[0]), int(args[1]), args[2])]
    else:
        cases = KNOWN_WORST

    print(f"命名体系: {scheme}")
    for a, b, expr_name in cases:
        targets = expr_peak_targets(bindings, expr_name)
        l0, l1, sep = measure(obj, a, b, targets)
        if l1 is None:
            print(f"v{a}-v{b} ({expr_name}): 顶点不在 moe_head 上，跳过")
            continue
        print(f"v{a}-v{b} ({expr_name})  原长={l0:.4f}  "
              f"真实shapekey现长={l1:.4f}  分离={sep:.4f}")
        print(f"  用到的形态键: {targets}")


main()
