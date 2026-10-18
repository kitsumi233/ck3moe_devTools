"""Read-only source body-root gate; see moe_retarget_root for the contract."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "lib"))
from moe_retarget_root import (Clip, SourceContract, angles, check_character,
                               conjugate, main, multiply, normalize, parents)


if __name__ == "__main__":
    raise SystemExit(main())
