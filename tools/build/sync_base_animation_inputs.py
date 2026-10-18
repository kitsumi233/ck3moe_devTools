"""Refresh work/base_animation_inputs from canonical bake output only."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools' / 'lib'))
import moe_chars as X
from moe_base_inputs import sync_base_animation_inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--characters', default='all', help='all or comma-separated character keys')
    parser.add_argument('--jobs', type=int, default=8)
    parser.add_argument('--sources',type=Path,help='Explicit unactivated immutable-release source manifest')
    args = parser.parse_args()
    keys = list(X.BY_CK) if args.characters == 'all' else list(dict.fromkeys(args.characters.split(',')))
    unknown = set(keys) - set(X.BY_CK)
    if unknown:
        parser.error(f'Unknown characters: {sorted(unknown)}')
    for character in keys:
        info = sync_base_animation_inputs(character, jobs=args.jobs,sources=args.sources)
        print(f'{character}: {info["count"]} verified base inputs, {info["copied"]} copied, {info["total_bytes"]:,} bytes', flush=True)


if __name__ == '__main__':
    main()
