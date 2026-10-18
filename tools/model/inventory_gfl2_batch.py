# -*- coding: utf-8 -*-
"""批量盘点 assets/GFL2 下全部 PMX（每个独立 Blender 进程）。slug = gfl2_<pmx 英文名小写>。

    python tools/model/inventory_gfl2_batch.py            # 只盘未盘过的
    python tools/model/inventory_gfl2_batch.py --force
"""
import os, re, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
BLENDER = Path(r"D:\steam\steamapps\common\Blender\blender.exe")
OUT = ROOT / "work" / "pmx_skeleton_inventory"
SCRIPT = ROOT / "tools" / "model" / "inventory_pmx_skeletons.py"

def slug_of(pmx: Path) -> str:
    s = pmx.stem
    s = re.sub(r"^GirlsFrontline\s+", "", s)
    s = re.sub(r"\s*Ver\s*[\d.]+$", "", s); s = re.sub(r"\s+[\d.]+$", "", s)
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    return "gfl2_" + s

def entries():
    for d in sorted((ROOT / "assets" / "GFL2").iterdir()):
        if not d.is_dir(): continue
        for f in sorted(d.rglob("*.pmx")):
            if f.name.lower().startswith("girlsfrontline"):
                yield slug_of(f), f

if __name__ == "__main__":
    force = "--force" in sys.argv
    todo = [(s, f) for s, f in entries() if force or not (OUT / f"{s}.json").is_file()]
    print(f"共 {len(list(entries()))} 个 PMX，待盘 {len(todo)}")
    ok = fail = 0
    for s, f in todo:
        env = dict(os.environ, MOE_PMX_PATH=str(f), MOE_PMX_REPORT=str(OUT / f"{s}.json"), PYTHONUTF8="1")
        r = subprocess.run([str(BLENDER), "-b", "--python", str(SCRIPT)], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        (OUT / f"{s}.log").write_text(r.stdout + r.stderr, encoding="utf-8")
        good = (OUT / f"{s}.json").is_file()
        ok += good; fail += not good
        print(f"  {'ok' if good else '!!'} {s:34} ← {f.name}")
    print(f"[done] ok {ok} fail {fail}")
