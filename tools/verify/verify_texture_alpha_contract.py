"""按 shader 槽位审计每张 diffuse 的 alpha，抓「该二值化却没二值化」的漏网。

为什么要有这个自检
------------------
2026-09-11 用户「不止那三个人有问题」。此前两轮我都是**顺着一张贴图查一张**：
先发现终末地共用表情图集糊脸，修了；用户再指出别人也有问题，才查出 gfl2 侧
等价的那张叫 `extra.png`、名字不含任何关键词、从来没被二值化。

根因是判据挂在**贴图文件名**上（关键词表），而语义在**shader 槽位**上。
文件名是各家美术随手起的（`extra` / `T_actor_common_female_emotion_atlas_01_D`
是同一种东西），关键词表永远追不齐。所以必须反过来：从 `.asset` 读每张 diffuse
挂在哪个 shader，按 shader 的 alpha 语义判定它该不该二值化，再和合同的判定对账。

alpha 语义按槽位分三类（依据 `portrait.shader` 的各 PS_*）
-----------------------------------------------------------
- **透明度**：`attachment` / `attachment_alpha_to_coverage` / `hair` / `hair_alpha`
  / `face`。中间值 → alpha-to-coverage 点阵抖动：要么透出背景（帽檐透地图），
  要么糊上颜色（脸上的假阴影）。**必须二值化。**
- **palette 染色强度**（`ColorMaskStrength = Diffuse.a`）：`skin` / `eye`。
  中间值是正常的染色遮罩，二值化会把染色开关切成 0/1 两档。**不能动。**
- 不接受染色的特例由 `wants_no_palette_alpha()` 单独处理，不在本自检范围。

用法
----
    python tools/verify/verify_texture_alpha_contract.py

退出码 0 = 无漏网；2 = 有漏网（**构建应当失败**，别让它静默通过）。
"""
import os
import re
from concurrent.futures import ProcessPoolExecutor
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / "lib")]
import moe_material_contract as MC   # noqa: E402
import moe_out as O                  # noqa: E402  产物根（MOE_OUT_ROOT 沙盒时不是 ROOT/mod）

# A 通道是透明度的槽位 —— 中间值会被 alpha-to-coverage 渲成点阵，必须二值化
ALPHA_IS_OPACITY = MC.ALPHA_IS_OPACITY
# A 通道是 palette 染色强度（ColorMaskStrength）—— 二值化会破坏染色，不能动
ALPHA_IS_COLORMASK = MC.ALPHA_IS_COLORMASK

# 中间 alpha 占比低于这个数就不算问题（压缩噪声、单像素抗锯齿边）
MID_TOLERANCE_PCT = MC.ALPHA_MID_TOLERANCE_PCT


def build_index():
    """从全部 .asset 建「diffuse 基名 → {shader 名}」。

    只读 `texture_diffuse`：`texture_specular` / `texture_normal` 的 A 是
    粗糙度/高光强，不是透明度，与本自检无关（合同的 `wants_binary_alpha`
    也已按 `_properties` / `_normal` 后缀排除它们）。
    """
    idx = defaultdict(set)
    for a in sorted(O.OUT.rglob("*.asset")):
        txt = a.read_text(encoding="utf-8-sig", errors="replace")
        for blk in re.findall(r"meshsettings\s*=\s*\{(.*?)\}", txt, re.S):
            m = re.search(r'texture_diffuse\s*=\s*"([^"]+)"', blk)
            if not m:
                continue
            s = re.search(r'shader\s*=\s*"([^"]+)"', blk)
            idx[m.group(1).rsplit("/", 1)[-1]].add(s.group(1) if s else "?")
    return idx


def find_files():
    """贴图基名 → 磁盘路径。**PNG 与 DDS 都要收**。

    转换脚本转完就 unlink 源 PNG，所以同一张图在构建前后分别以 .png / .dds
    存在。只收一种会让自检在某个时刻「找不到文件」而静默跳过 —— 那正是
    这次漏网能瞒过我两轮的原因之一。
    """
    # 2026-09-28：两者并存时**必须优先 PNG**。PNG 是本次待转换的源，旧 DDS 是上次
    # 构建的残留；BC 压缩还会在二值 alpha 边缘插值出中间值（whiten_face 源 PNG 0%，
    # 旧 DDS 6.73%）。按 rglob 顺序 .dds 先到，自检读到残留 DDS 报了 6 张假漏网。
    files = {}
    paths = [p for p in O.OUT.rglob("*")
             if p.suffix.lower() in (".dds", ".png")]
    for p in sorted(paths, key=lambda q: q.suffix.lower() != ".png"):
        files.setdefault(p.name, p)
        # 允许用 .dds 名字查到还没转的 .png（反之亦然）
        files.setdefault(p.stem + ".dds", p)
        files.setdefault(p.stem + ".png", p)
    return files


def mid_alpha_pct(path):
    a = np.asarray(Image.open(path).convert("RGBA"))[..., 3]
    return 100.0 * float(((a > 0) & (a < 255)).mean())


def main():
    idx = build_index()
    files = find_files()

    # 读图量中间 alpha 是唯一的重活，先按进程并行算完（2026-10-01：几千张串行几分钟）
    _need = {tex: files[tex] for tex in idx
             if tex in files and idx[tex] & ALPHA_IS_OPACITY}
    with ProcessPoolExecutor(max(1, int(os.environ.get("MOE_BUILD_JOBS", os.cpu_count() or 4)))) as _pool:
        _mid = dict(zip(_need, _pool.map(mid_alpha_pct, _need.values(), chunksize=8)))

    bad, unknown, checked = [], [], 0
    for tex in sorted(idx):
        shaders = idx[tex]
        p = files.get(tex)
        if p is None:
            continue           # 引用了但文件不在（另有 asset 引用自检管这件事）
        opacity = shaders & ALPHA_IS_OPACITY
        colormask = shaders & ALPHA_IS_COLORMASK
        other = shaders - ALPHA_IS_OPACITY - ALPHA_IS_COLORMASK
        if other:
            unknown.append((tex, sorted(other)))
        if not opacity:
            continue           # 只挂在染色语义的槽上 → 不该二值化，跳过
        checked += 1
        mid = _mid[tex]
        if mid <= MID_TOLERANCE_PCT:
            continue
        if MC.wants_binary_alpha(tex):
            continue           # 合同已认领，转换时会处理
        bad.append((tex, mid, sorted(opacity), sorted(colormask)))

    print(f"[alpha] 透明度语义的 diffuse {checked} 张，"
          f"中间 alpha 容差 {MID_TOLERANCE_PCT}%")

    if unknown:
        print(f"[alpha] **{len(unknown)} 张挂在未登记的 shader 槽上** —— "
              f"alpha 语义未知，本自检无法判定，请把槽位补进"
              f" ALPHA_IS_OPACITY 或 ALPHA_IS_COLORMASK：")
        for tex, shs in unknown[:10]:
            print(f"          {tex:<50} {','.join(shs)}")

    if not bad:
        print("[alpha] 无漏网 ✓")
        return 0

    print(f"[alpha] **{len(bad)} 张该二值化却没被合同认领** —— "
          f"中间 alpha 会被 alpha-to-coverage 渲成点阵"
          f"（透出背景，或糊上颜色）：")
    for tex, mid, op, cm in bad:
        note = f"  （同时挂染色槽 {','.join(cm)}，改关键词前先确认）" if cm else ""
        print(f"          {tex:<50} 中间 {mid:6.2f}%  {','.join(op)}{note}")
    print("[alpha] 修法：把这些贴图的**部件名**补进"
          " moe_material_contract.BINARIZE_ALPHA_KEYWORDS"
          "（贴花图集类走 BINARIZE_ALPHA_MIDPOINT_KEYWORDS，阈值 128）。"
          "别给单张贴图开特例 —— 关键词是 pipeline 判据，新角色要自动走对。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
