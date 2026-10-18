"""列出 .anim 里每根骨的采样通道（t/q/s），用于判定「谁驱动谁」。

为什么需要:
  head 与 torso 是两个独立 entity，各播自己的 animation。如果表情动画
  也带脊椎/髋的 q 通道，头会按自己那份姿势走，与躯干打架 —— 但从
  .asset 或文件名完全看不出来，必须逐骨看通道。

用法:
  python tools/anim/anim_channels.py <anim...>            # 汇总
  python tools/anim/anim_channels.py <anim> --list        # 逐骨
"""
import io
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[2] / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import pdx_parse as P  # noqa: E402

# stdout 转 utf-8。**用 reconfigure 不用 TextIOWrapper** —— 后者每次都新建一个
# 包装对象，多个模块各包一层时外层被 GC 会关掉共享的 buffer，于是后面任何
# print 都抛 `ValueError: I/O operation on closed file`（动画其实已经写完了，
# 只是收尾的 print 炸掉，看起来像整条失败）。reconfigure 原地改编码，天然幂等。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def channels(path):
    """→ (fps, frame_count, {骨名: 通道串}) 。通道串是 'q'/'t'/'st' 之类。"""
    tree = P.parse(Path(path).read_bytes())
    fps = nframes = None
    per_bone = {}
    cur = None
    for p, name, _kind, val in P.walk(tree):
        if p == "/info":
            if name == "fps":
                fps = val[0] if isinstance(val, list) else val
            elif name == "sa":
                nframes = val[0] if isinstance(val, list) else val
        # 骨节点形如 /info/<骨名> 或 /info/<骨名>[i]
        if p.startswith("/info/") and p.count("/") == 2:
            bone = p.split("/info/", 1)[1]
            if "[" in bone:
                bone = bone.split("[")[0]
            cur = bone
            if name == "sa":
                # sa 是通道标记，形如 ['q'] / ['tq'] / []（空 = 只有静态 rest）
                s = val[0] if (isinstance(val, list) and val) else ""
                per_bone[bone] = s if isinstance(s, str) else str(s)
    return fps, nframes, per_bone


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    want_list = "--list" in sys.argv
    print(f"{'文件':<26}{'fps':>4}{'帧':>6}{'骨':>5}{'带q':>5}{'带t':>5}{'静态':>6}")
    print("-" * 60)
    for a in args:
        fps, nf, pb = channels(a)
        nq = sum(1 for v in pb.values() if "q" in v)
        nt = sum(1 for v in pb.values() if "t" in v)
        static = sum(1 for v in pb.values() if not v)
        print(f"{Path(a).name:<26}{fps or 0:>4}{nf or 0:>6}"
              f"{len(pb):>5}{nq:>5}{nt:>5}{static:>6}")
        if want_list:
            for b, v in sorted(pb.items(), key=lambda kv: (not kv[1], kv[0])):
                print(f"    {b:<34}{v or '(静态)'}")


if __name__ == "__main__":
    main()
