"""Split diffuse alpha by shader use before the texture contract check.

Some source images serve both as skin palette masks and as face/attachment
opacity maps. Their single source alpha cannot satisfy both shader contracts.
Keep the original image for color-mask slots, and generate a binary-alpha
variant for opacity slots. Opacity-only textures with no filename rule also
get a named variant so the normal conversion and verifier contracts agree.

The generated suffix is part of the shared alpha contract; rerunning this
script is safe because the asset references are changed only once.
"""
from __future__ import annotations

import os
import re
import shutil
from concurrent.futures import ProcessPoolExecutor
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "lib"))
import moe_material_contract as MC  # noqa: E402

MESHSETTINGS_RE = re.compile(r"meshsettings\s*=\s*\{.*?\}", re.S)
SHADER_RE = re.compile(r'\bshader\s*=\s*"([^"]+)"')
DIFFUSE_RE = re.compile(r'\btexture_diffuse\s*=\s*"([^"]+)"')
OPACITY_SUFFIX = "__moe_opacity"
MIN_FREE_BYTES = 25 * 1024**3


def middle_alpha_pct(path: Path) -> float:
    with Image.open(path) as image:
        alpha = np.asarray(image.convert("RGBA"), dtype=np.uint8)[..., 3]
    return 100.0 * float(((alpha > 0) & (alpha < 255)).mean())


def write_binary_variant(source: Path, target: Path, cutout: int) -> int:
    with Image.open(source) as image:
        rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    alpha = rgba[..., 3]
    changed = int(np.count_nonzero((alpha > 0) & (alpha < 255)))
    rgba[..., 3] = np.where(alpha < cutout, 0, 255).astype(np.uint8)
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(target, format="PNG")
    return changed


def _texture_key(ref: str) -> str:
    return Path(ref.replace("\\", "/")).stem.casefold()


def _variant_ref(ref: str) -> str:
    normalized = ref.replace("\\", "/")
    prefix, _, basename = normalized.rpartition("/")
    source = Path(basename)
    name = f"{source.stem}{OPACITY_SUFFIX}.dds"
    return f"{prefix}/{name}" if prefix else name


def _source_ref(ref: str) -> str:
    normalized = ref.replace("\\", "/")
    prefix, _, basename = normalized.rpartition("/")
    source = Path(basename)
    if not source.stem.endswith(OPACITY_SUFFIX):
        return ref
    name = source.stem[:-len(OPACITY_SUFFIX)] + ".dds"
    return f"{prefix}/{name}" if prefix else name


def restore_unneeded_variants(roots: list[Path]) -> int:
    """Undo stale generated variants whose source alpha is already binary."""
    restored = 0
    candidates_to_remove: dict[Path, set[str]] = {root: set() for root in roots}
    for root in roots:
        assets = sorted(root.rglob("*.asset"))
        images = {
            p.stem.casefold(): p for p in root.rglob("*.png") if p.is_file()
        }
        for p in root.rglob("*.dds"):
            if p.is_file():
                images.setdefault(p.stem.casefold(), p)
        for asset in assets:
            text = asset.read_text(encoding="utf-8-sig", errors="replace")

            def restore_block(match: re.Match[str]) -> str:
                nonlocal restored
                block = match.group(0)
                shader = SHADER_RE.search(block)
                diffuse = DIFFUSE_RE.search(block)
                if not shader or shader.group(1) not in MC.ALPHA_IS_OPACITY or not diffuse:
                    return block
                ref = diffuse.group(1)
                basename = Path(ref.replace("\\", "/")).name
                stem = Path(basename).stem
                if not stem.endswith(OPACITY_SUFFIX):
                    return block
                source_name = stem[:-len(OPACITY_SUFFIX)]
                source = images.get(source_name.casefold())
                if source is None:
                    return block
                mid_pct = middle_alpha_pct(source)
                original_name = source_name + ".dds"
                still_needed = (mid_pct > 0 and
                                (MC.wants_binary_alpha(original_name)
                                 or mid_pct > MC.ALPHA_MID_TOLERANCE_PCT))
                if still_needed:
                    return block
                candidates_to_remove[root].add(stem.casefold())
                restored += 1
                new_ref = _source_ref(ref)
                return DIFFUSE_RE.sub(
                    lambda m: m.group(0).replace(m.group(1), new_ref), block, count=1
                )

            updated = MESHSETTINGS_RE.sub(restore_block, text)
            if updated != text:
                asset.write_text(updated, encoding="utf-8-sig")

    for root, stems in candidates_to_remove.items():
        referenced: set[str] = set()
        for asset in root.rglob("*.asset"):
            text = asset.read_text(encoding="utf-8-sig", errors="replace")
            for match in DIFFUSE_RE.finditer(text):
                referenced.add(Path(match.group(1).replace("\\", "/")).stem.casefold())
        for stem in stems - referenced:
            for suffix in (".png", ".dds"):
                for generated in root.rglob(stem + suffix):
                    generated.unlink()
    return restored


def prepare(roots: list[Path]) -> int:
    roots = [p.resolve() for p in roots if p.is_dir()]
    if not roots:
        raise RuntimeError("No model texture roots were provided")

    restored = restore_unneeded_variants(roots)
    if restored:
        print(f"[alpha split] restored {restored} stale opacity references and removed "
              "unneeded generated copies")

    per_root: dict[Path, list[Path]] = {}
    per_root_usage: dict[Path, dict[str, set[str]]] = {}
    global_shaders: dict[str, set[str]] = {}
    ref_names: dict[str, str] = {}
    pngs_by_root: dict[Path, dict[str, Path]] = {}
    dds_by_root: dict[Path, dict[str, Path]] = {}
    for root in roots:
        assets = sorted(root.rglob("*.asset"))
        per_root[root] = assets
        per_root_usage[root] = {}
        pngs_by_root[root] = {
            p.stem.casefold(): p for p in root.rglob("*.png") if p.is_file()
        }
        dds_by_root[root] = {
            p.stem.casefold(): p for p in root.rglob("*.dds") if p.is_file()
        }
        for asset in assets:
            text = asset.read_text(encoding="utf-8-sig", errors="replace")
            for block in MESHSETTINGS_RE.findall(text):
                shader_match = SHADER_RE.search(block)
                diffuse_match = DIFFUSE_RE.search(block)
                if not shader_match or not diffuse_match:
                    continue
                shader = shader_match.group(1)
                key = _texture_key(diffuse_match.group(1))
                global_shaders.setdefault(key, set()).add(shader)
                per_root_usage[root].setdefault(key, set()).add(shader)
                ref_names.setdefault(key, Path(diffuse_match.group(1).replace("\\", "/")).name)

    variants: dict[Path, dict[str, str]] = {root: {} for root in roots}
    preserve_alpha: set[str] = set()
    rows: list[tuple[str, Path, float, int, bool]] = []

    # 读图量中间 alpha 是这里的重活，先按进程并行把要用的都算好（2026-10-01）。
    _mid_src = {}
    for key, shaders in global_shaders.items():
        if shaders & MC.ALPHA_IS_OPACITY:
            _c = ([index[key] for index in pngs_by_root.values() if key in index]
                  or [index[key] for index in dds_by_root.values() if key in index])
            if _c:
                _mid_src[key] = _c[0]
    with ProcessPoolExecutor(max(1, int(os.environ.get("MOE_BUILD_JOBS", os.cpu_count() or 4)))) as _pool:
        _mid = dict(zip(_mid_src, _pool.map(middle_alpha_pct, _mid_src.values(), chunksize=8)))

    for key, shaders in sorted(global_shaders.items()):
        opacity = shaders & MC.ALPHA_IS_OPACITY
        if not opacity:
            continue
        has_other_use = bool(shaders - MC.ALPHA_IS_OPACITY)
        original_name = ref_names[key]
        candidates = [index[key] for index in pngs_by_root.values() if key in index]
        if not candidates:
            candidates = [index[key] for index in dds_by_root.values() if key in index]
        if not candidates:
            # Match the alpha verifier's scope: an asset reference whose image
            # is not present in these texture roots is handled by the missing
            # texture asset check, not by this alpha transformation.
            continue
        source = candidates[0]
        mid_pct = _mid[key]
        needs_variant = has_other_use or not MC.wants_binary_alpha(original_name)
        if not needs_variant:
            continue
        wants_binary = MC.wants_binary_alpha(original_name)
        if has_other_use:
            if mid_pct <= 0 or (not wants_binary
                                and mid_pct <= MC.ALPHA_MID_TOLERANCE_PCT):
                continue
        elif wants_binary or mid_pct <= MC.ALPHA_MID_TOLERANCE_PCT:
            continue

        asset_roots = [root for root in roots
                       if (key in per_root_usage[root]
                           and per_root_usage[root][key] & MC.ALPHA_IS_OPACITY)]
        if not asset_roots:
            continue

        source_roots = [root for root in roots
                        if key in pngs_by_root[root] or key in dds_by_root[root]]
        if not source_roots:
            continue
        source_root = source_roots[0]
        local_source = (pngs_by_root[source_root].get(key)
                        or dds_by_root[source_root].get(key))
        if local_source is None:
            continue

        cutout = MC.binary_alpha_cutout(original_name)
        variant = local_source.with_name(local_source.stem + OPACITY_SUFFIX + ".png")
        free = shutil.disk_usage(variant.parent).free
        if free - local_source.stat().st_size < MIN_FREE_BYTES:
            raise RuntimeError(
                f"Disk reserve guard: need 25 GiB free before writing {variant.name}; "
                f"currently {free / 1024**3:.2f} GiB"
            )
        changed = write_binary_variant(local_source, variant, cutout)
        for root in asset_roots:
            variants[root][key] = variant.stem + ".dds"
        rows.append((original_name, variant, mid_pct, changed, has_other_use))
        if has_other_use:
            preserve_alpha.add(key)

    rewritten = 0
    for root, assets in per_root.items():
        root_variants = variants[root]
        if not root_variants:
            continue
        for asset in assets:
            text = asset.read_text(encoding="utf-8-sig", errors="replace")

            def replace_opacity_block(match: re.Match[str]) -> str:
                nonlocal rewritten
                block = match.group(0)
                shader = SHADER_RE.search(block)
                diffuse = DIFFUSE_RE.search(block)
                if not shader or shader.group(1) not in MC.ALPHA_IS_OPACITY or not diffuse:
                    return block
                replacement = root_variants.get(_texture_key(diffuse.group(1)))
                if not replacement:
                    return block
                ref = diffuse.group(1).replace("\\", "/")
                new_ref = _variant_ref(ref)
                rewritten += 1
                return DIFFUSE_RE.sub(
                    lambda m: m.group(0).replace(m.group(1), new_ref), block, count=1
                )

            updated = MESHSETTINGS_RE.sub(replace_opacity_block, text)
            if updated != text:
                asset.write_text(updated, encoding="utf-8-sig")

    # 2026-09-28：源图 alpha 已是二值时本次不生成变体，但上次构建残留的
    # `__moe_opacity.dds` 还在（引用已被 .asset 重写抹掉，restore_unneeded_variants
    # 只看引用所以抓不到它）。finalize 见到变体文件就要求 opacity 槽引用它，
    # 于是 13 个槽位报「未引用同一源图」。本次没生成、也没被引用的变体一律清掉。
    produced = {v.stem.casefold() for _n, v, _m, _c, _s in rows}
    removed = 0
    for root in roots:
        referenced: set[str] = set()
        for asset in root.rglob("*.asset"):
            text = asset.read_text(encoding="utf-8-sig", errors="replace")
            referenced |= {Path(m.group(1).replace("\\", "/")).stem.casefold()
                           for m in DIFFUSE_RE.finditer(text)}
        for stale in list(root.rglob(f"*{OPACITY_SUFFIX}.*")):
            stem = stale.stem.casefold()
            if stale.suffix.lower() in (".png", ".dds") and stem not in produced and stem not in referenced:
                stale.unlink()
                removed += 1
    if removed:
        print(f"[alpha split] removed {removed} stale unreferenced opacity variants")

    for name, variant, mid_pct, changed, shared in rows:
        relation = "shared opacity/color-mask" if shared else "opacity-only"
        print(f"[alpha split] {name} -> {variant.name} ({relation}, "
              f"mid={mid_pct:.2f}%, cutout={MC.binary_alpha_cutout(name)}, "
              f"binarized={changed:,} px)")
    print(f"[alpha split] variants={len(rows)}, asset references rewritten={rewritten}, "
          f"original alpha preserved for {len(preserve_alpha)} shared textures")
    return 0


def main() -> int:
    roots = [Path(value) for value in sys.argv[1:]]
    if not roots:
        import moe_paths as MP
        sys.path.insert(0, str(ROOT / "tools" / "lib"))
        import moe_out as O
        roots = [MP.base_of(game) for game in O.GAMES]
    return prepare(roots)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[alpha split] ERROR: {exc}", file=sys.stderr)
        raise
