"""量脸部明暗分布 —— **面积比不是有效判据，读 docstring 再用**。

## 这个脚本的判据失效过一次，先看这条

我曾拿「暗部面积占比」调 MOE_TOON_THRESHOLD：0.18 时报 71%、判「过大」，
降到 0.08 后**反而涨到 81%**。降阈值本该减少暗部 —— 涨了说明这个量根本
不受阈值控制。

真因：**洛茜的脸贴图自带腮红**（`T_actor_wulfa_face_01_D.dds` 实采
R−B +42，腮红处 +88，额头 +58），脸上的明暗大头来自**贴图内容**而不是
二值化，所以按亮度切出的「暗部面积」主要在数贴图的暖色像素。
芙洛伦用的是另一张贴图 `c_Florence_slg_face_d.dds`，那张才是干净平涂
（(242,188,183)），**两个角色不能共用一套判据**。我把芙洛伦的结论错套
到洛茜身上，才追错了方向。

所以这个脚本只用来**看分布形状**（双峰 = 二值化生效、单峰 = 没生效），
不要用面积比调阈值。调阈值要看「明暗交界线落在脸的什么位置」。


## 为什么必须量而不能靠肉眼

「脸上阴影很重」这句话对应的客观量是**暗部面积占比**，而不是某一点的亮度。
我靠手点坐标采样栽过一次：目测的「额头暗块」量出 83.0，比「额头亮」的
62.8 还高 —— 缩略图上目测的坐标换算到原图后根本没落在预期位置，得出的
「比值 1.10x」是假的，会导出「阈值没问题」的错误结论。

扫整个脸部区域做直方图才靠得住：

  · 二值化生效时亮度分布是**双峰**，两峰之间的谷就是实际分界线
  · 单峰 = 二值化没生效（或阈值在分布之外，全部落进同一档）
  · 双峰但暗峰面积压倒性 = 阈值过高

## 实测记录（面积比，仅供参考，不作判据）

    阈值 0.35 → 暗部 ~85%
    阈值 0.18 → 暗部  71%   （暗档均值 65.7 / 亮档 147.9）
    阈值 0.08 → 暗部  81%   ← **降阈值却涨了**，判据在此失效

## 怎么筛出脸部像素

`R > G+8 且 G > B 且亮度 > 25`。肉色一定满足 R>G>B；头发是灰绿（R≈G）、
背景是地图或深色 UI，都被筛掉。**不要用固定矩形直接统计** —— 框里必然
混进头发和背景，它们的亮度会把直方图的峰位整体带偏。

用法：
  python tools/verify/measure_face_shading.py work/shot/xxx_f0.png
  python tools/verify/measure_face_shading.py work/shot/xxx_f0.png --box 205,195,270,250
"""
import argparse
import io
import sys
from pathlib import Path

# stdout 转 utf-8。**用 reconfigure 不用 TextIOWrapper** —— 后者每次都新建一个
# 包装对象，多个模块各包一层时外层被 GC 会关掉共享的 buffer，于是后面任何
# print 都抛 `ValueError: I/O operation on closed file`（动画其实已经写完了，
# 只是收尾的 print 炸掉，看起来像整条失败）。reconfigure 原地改编码，天然幂等。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

# 缩略图坐标系（768 宽）下的脸部框，与 burst_capture 的输出配套。
# 换角色或换视角要重新给 --box。
DEFAULT_BOX = (205, 195, 270, 250)
# 两档分界线（渲染结果的亮度值）。取两峰之间的谷，不是阈值本身。
SPLIT = 110


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--box", default=None,
                    help="脸部框 x0,y0,x1,y1（缩略图 768 宽坐标系）")
    ap.add_argument("--split", type=float, default=SPLIT,
                    help=f"亮/暗分界亮度，默认 {SPLIT}")
    a = ap.parse_args()

    p = Path(a.image)
    if not p.exists():
        print(f"找不到图：{p}", file=sys.stderr)
        return 1
    im = Image.open(p).convert("RGB")
    arr = np.asarray(im).astype(np.float32)

    box = (tuple(int(v) for v in a.box.split(","))
           if a.box else DEFAULT_BOX)
    s = im.width / 768.0
    x0, y0, x1, y1 = (int(box[0] * s), int(box[1] * s),
                      int(box[2] * s), int(box[3] * s))
    reg = arr[y0:y1, x0:x1]
    if reg.size == 0:
        print(f"框超出图像范围：{box}", file=sys.stderr)
        return 1

    lum = (0.2126 * reg[..., 0] + 0.7152 * reg[..., 1]
           + 0.0722 * reg[..., 2])
    r, g, b = reg[..., 0], reg[..., 1], reg[..., 2]
    # 见 docstring：只留肉色像素，筛掉头发（R≈G）与背景
    skin = (r > g + 8) & (g > b) & (lum > 25)
    n = int(skin.sum())
    if n < 200:
        print(f"**筛出的脸部像素只有 {n} 个** —— 框没对准脸，或角色不在画面里。"
              f"换 --box 重试，别拿这个数下结论", file=sys.stderr)
        return 1
    sl = lum[skin]

    print(f"图 {p.name}   脸部框 {box}   筛出皮肤像素 {n}/{skin.size}")
    print(f"亮度 min {sl.min():.0f}  P10 {np.percentile(sl, 10):.0f}  "
          f"中位 {np.median(sl):.0f}  P90 {np.percentile(sl, 90):.0f}  "
          f"max {sl.max():.0f}")

    h, edges = np.histogram(sl, bins=12, range=(0, 240))
    print("\n亮度直方图（双峰 = 二值化生效；单峰 = 没生效）")
    for i, c in enumerate(h):
        bar = "#" * int(56 * c / max(h.max(), 1))
        print(f"  {edges[i]:5.0f}-{edges[i+1]:5.0f} {bar:56s} {c}")

    lo = int((sl < a.split).sum())
    hi = n - lo
    pct = 100.0 * lo / n
    print(f"\n分界 {a.split:.0f}：暗档 {lo} ({pct:.1f}%)   "
          f"亮档 {hi} ({100-pct:.1f}%)")
    if lo:
        print(f"暗档均值 {sl[sl < a.split].mean():.1f}", end="   ")
    if hi:
        print(f"亮档均值 {sl[sl >= a.split].mean():.1f}")

    # **不给「该调高/调低阈值」的结论** —— 见 docstring：贴图自带腮红的
    # 角色（洛茜）这个占比不受阈值控制，照它调会往错的方向走。
    # 我最初在这里写了 `pct > 45 → 调低阈值`，据此把阈值从 0.18 降到 0.08，
    # 结果占比反而 71%→81%。判据本身不成立，不该由脚本给出这种结论。
    print(f"\n暗部占比 {pct:.0f}% —— **这个数不能用来调阈值**。")
    print(f"  贴图自带腮红/花纹时它主要在数贴图内容，不反映二值化"
          f"（洛茜实测阈值 0.18→0.08 时占比反而 71%→81%）。")
    print(f"  调阈值请看「明暗交界线落在脸的什么位置」，并分角色分别判断。")
    # 单峰检测：二值化没生效时只有一个峰
    top2 = np.sort(h)[-2:]
    if h.max() and top2[0] < h.max() * 0.15:
        print("**直方图接近单峰** —— 二值化可能没生效（宏没加上、或阈值落在"
              "分布之外），别只调阈值，先确认 MOE_TOON 真的编译进去了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
