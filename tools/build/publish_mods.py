"""发布：把仓库 mod/ 里的 7 个基础包拷到常规文档 mod 目录，供启动器上传创意工坊。

只维护仓库 mod/ 一份（2026-10-01 用户定），这里是唯一的拷贝出口。
拷贝按 runtime_animation_selection 省掉未被引用的动画；目标整目录替换，
外层 .mod 写绝对路径（启动器认这个）。只动 7 个 moe_* 包，不碰目录里别的 mod。

先跑过 finalize_mods.py（自检 + 写 descriptor/缩略图）再跑本脚本。

用法: python tools/build/publish_mods.py [--dest <dir>] [--jobs 12] [--only moe_core ...]
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / 'lib'), str(HERE.parent / 'anim')]
import finalize_mods as FM

DEFAULT_DEST = Path(r"C:\Users\Administrator\Documents\Paradox Interactive\Crusader Kings III\mod")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dest', type=Path, default=DEFAULT_DEST)
    ap.add_argument('--jobs', type=int, default=12)
    ap.add_argument('--only', nargs='+')
    a = ap.parse_args()
    mids = a.only or FM.ORDER
    bad = [m for m in mids if m not in FM.ORDER]
    if bad:
        sys.exit(f'不是基础包: {bad}')
    ok, problems = FM.checks()
    if not ok:
        sys.exit('自检不通过，拒绝发布:\n  ' + '\n  '.join(problems))
    selection = FM.runtime_animation_selection()
    print(f'省去未引用动画 {len(selection["omitted"])} 个，{selection["omitted_bytes"] / 1e6:.1f} MB')
    a.dest.mkdir(parents=True, exist_ok=True)
    for mid in mids:
        n = FM.copy_pack(mid, a.dest, a.jobs, animation_selection=selection)
        thumb = (a.dest / mid / 'thumbnail.png').is_file()
        print(f'  {mid:26} {n:6} 个文件' + ('' if thumb else '  !! 无 thumbnail.png'), flush=True)
    print(f'已拷到 {a.dest}')


if __name__ == '__main__':
    main()
