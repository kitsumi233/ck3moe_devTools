"""PMX 的「回転付与」骨表 → 我们的骨名（retarget 的 --inherit-rot 用）。

MMD 的付与骨（足調整 / 肩調整 / 腕-限 / 飘带根 …）自己没有动作，运行时由引擎按
「付与源骨的局部旋转 × 付与率」驱动。导进来之后这层约束没了：它们既不在原版映射里，
也不在模板里有通道，于是一直停在 rest，蒙在上面的皮肤（大腿根、肩头）被钉在父骨上
（忌炎坐姿大腿根 p99 偏差 6.5，白芷 24.6，阿布手臂 30）。

只读 PMX 的骨骼段（不依赖 Blender / mmd_tools），按 report 的 bone_name_map 换成我们的骨名。
返回 [(我们的付与骨, 我们的付与源骨, 付与率)]，只保留：
  · 回転付与（flag 0x100），不含移動付与 / ローカル付与（0x80，鸣潮 44 人里没有）
  · 付与率 > 0（-1 的 肩C / 腰キャンセル 是抵消骨，retarget 明确不驱动它们）
  · 两根骨都在 bone_name_map 里
"""
import json
import struct
from pathlib import Path


def _bones(path):
    b = Path(path).read_bytes()
    if b[:4] != b"PMX ":
        raise ValueError(f"不是 PMX: {path}")
    o = 8
    n = b[o]; o += 1
    g = list(b[o:o + n]); o += n
    enc = "utf-16-le" if g[0] == 0 else "utf-8"
    addv, vis, tis, mis, bis = g[1], g[2], g[3], g[4], g[5]

    def txt():
        nonlocal o
        ln = struct.unpack_from("<i", b, o)[0]; o += 4
        s = b[o:o + ln].decode(enc, "replace"); o += ln
        return s

    def idx(sz):
        nonlocal o
        v = int.from_bytes(b[o:o + sz], "little", signed=sz != 1); o += sz
        return v

    for _ in range(4):
        txt()
    nv = struct.unpack_from("<i", b, o)[0]; o += 4
    for _ in range(nv):
        o += 32 + 16 * addv
        wt = b[o]; o += 1
        o += {0: bis, 1: 2 * bis + 4, 2: 4 * bis + 16, 3: 2 * bis + 4 + 36, 4: 4 * bis + 16}[wt]
        o += 4
    nf = struct.unpack_from("<i", b, o)[0]; o += 4 + nf * vis
    nt = struct.unpack_from("<i", b, o)[0]; o += 4
    for _ in range(nt):
        txt()
    nm = struct.unpack_from("<i", b, o)[0]; o += 4
    for _ in range(nm):
        txt(); txt()
        o += 16 + 12 + 4 + 12 + 1 + 16 + 4 + tis + tis + 1
        shared = b[o]; o += 1
        o += 1 if shared else tis
        txt()
        o += 4
    nb = struct.unpack_from("<i", b, o)[0]; o += 4
    out = []
    for _ in range(nb):
        name = txt(); txt()
        o += 12
        idx(bis)
        o += 4
        fl = struct.unpack_from("<H", b, o)[0]; o += 2
        if fl & 0x1:
            idx(bis)
        else:
            o += 12
        app = None
        if fl & 0x300:
            src = idx(bis); rate = struct.unpack_from("<f", b, o)[0]; o += 4
            app = (src, rate, fl)
        if fl & 0x400:
            o += 12
        if fl & 0x800:
            o += 24
        if fl & 0x2000:
            o += 4
        if fl & 0x20:
            idx(bis); o += 8
            nl = struct.unpack_from("<i", b, o)[0]; o += 4
            for _ in range(nl):
                idx(bis)
                lim = b[o]; o += 1
                if lim:
                    o += 24
        out.append((name, app))
    return out


def inherit_rows(report_path):
    rep = json.loads(Path(report_path).read_text(encoding="utf-8"))
    bm = rep["bone_name_map"]
    bones = _bones(rep["source_pmx"])
    rows = []
    for name, app in bones:
        if not app:
            continue
        src, rate, fl = app
        if not (fl & 0x100) or (fl & 0x80) or rate <= 0 or not (0 <= src < len(bones)):
            continue
        a, t = bm.get(name), bm.get(bones[src][0])
        if a and t and a != t:
            rows.append((a, t, round(float(rate), 6)))
    return rows


if __name__ == "__main__":
    import sys
    for r in inherit_rows(sys.argv[1]):
        print(*r)
