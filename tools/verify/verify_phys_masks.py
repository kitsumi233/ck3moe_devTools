"""Verify collision exclusions survive the per-animation arm-group remapping."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools/lib'), str(ROOT / 'tools/physics')]
import moe_bullet_bake as bake
import moe_chars as chars


def main():
    checked = 0
    for ck in ('dai', 'che'):
        rig = bake.Rig(bake.char_config(ck))
        config = chars.BY_CK[ck]
        for shield in (None, {0}, set(range(len(rig.model.rigids)))):
            scene, _ = rig._scene_text(shield=shield, nocol=config['phys_nocollide'], erp_skip=())
            records = [line.split() for line in scene.splitlines() if line.startswith('rigid ')]
            for first_index, first in enumerate(rig.model.rigids):
                for second_index, second in enumerate(rig.model.rigids):
                    forbidden = any((first.collision_group_number, second.collision_group_number) in
                                    ((group_a, group_b), (group_b, group_a))
                                    for group_a, group_b in config['phys_nocollide'])
                    forbidden |= first.mode != 0 and any(
                        first.name.lower().startswith(tuple(prefix.lower() for prefix in prefixes))
                        and second.collision_group_number in blocked_groups
                        for prefixes, blocked_groups in config.get('phys_chain_nocollide', ()))
                    if not forbidden:
                        continue
                    first_group, first_mask = map(int, records[first_index][-2:])
                    second_group, second_mask = map(int, records[second_index][-2:])
                    collides = ((1 << first_group) & second_mask) and ((1 << second_group) & first_mask)
                    assert not collides, (ck, first.name, second.name, shield is not None)
                    checked += 1
    print(f'OK: {checked} forbidden pairs stay excluded before/after arm remapping')


if __name__ == '__main__':
    main()
