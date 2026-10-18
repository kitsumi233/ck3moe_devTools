"""Convert one model pack to the carrier architecture (same steps gfl2 went through, in order).

  1. split_portrait_carriers   carriers + face/outfit accessories
  2. inline_anim_sets          skeletal_animation_set imports -> inline animation lines
  3. merge_portrait_anims      head+torso anims merged per torso state (<ck>/anims_merged/)
  4. apply_merged_anims        torso states -> merged ids
  5. reskin heads              real head mesh gets the torso carrier bone table (pose copied by index)
  6. build_cam_rig             head carrier = 5-bone rig, rig anims, props on rig bones
  7. prune_portrait_anims      unreferenced anims -> trash/<date>_<pack>_old_anims (same volume)
  8. fix_eye_highlight         Eyes+ layer back on eyeblend

Original asset is kept as work/bisect_20261001/<pack>.asset.before_split (merge/apply read it).

usage: python tools/build/carrier_pipeline.py <pack dir>
"""
import glob
import os
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / 'lib')]
import build_portrait_carriers as C

BK = Path('work/bisect_20261001')


def run(*args):
    print('>>', ' '.join(map(str, args)), flush=True)
    subprocess.run([sys.executable, *map(str, args)], check=True)


def reskin_head(args):
    head, carrier = args
    C.reskin(head, carrier, head)
    return head


def main():
    pack = Path(sys.argv[1].rstrip('/'))
    name = pack.name
    moe = pack / 'gfx/models/portraits/moe'
    asset = next(moe.glob('moe_portrait_*.asset'))
    orig = BK / f'{name}.asset.before_split'
    if not orig.exists():
        shutil.copy2(asset, orig)
        for sub in ('common/portrait_types', 'gfx/portraits/accessories'):
            d = BK / f'{name}_before_split' / sub
            d.mkdir(parents=True, exist_ok=True)
            for f in (pack / sub).glob('*.txt'):
                shutil.copy2(f, d / f.name)
    run(HERE / 'split_portrait_carriers.py', pack)
    run(HERE / 'inline_anim_sets.py', asset)
    run(HERE / 'merge_portrait_anims.py', pack, orig)
    run(HERE / 'apply_merged_anims.py', pack, orig)
    models = moe / 'models'
    jobs = []
    for h in sorted(models.glob('moe_*head.mesh')):
        c = models / (h.name[:-len('head.mesh')] + 'torso_carrier.mesh')
        assert c.exists(), c
        jobs.append((str(h), str(c)))
    with ProcessPoolExecutor(16) as ex:
        print('reskinned heads', len(list(ex.map(reskin_head, jobs))), flush=True)
    run(HERE / 'build_cam_rig.py', pack)
    for p in models.glob('*_head_carrier.mesh'):      # head is the shared rig now
        p.unlink()
    run(HERE / 'prune_portrait_anims.py', pack, f'trash/20261002_{name}_old_anims')
    run(HERE / 'fix_eye_highlight.py', pack)
    print('PIPELINE DONE', name, flush=True)


if __name__ == '__main__':
    main()
