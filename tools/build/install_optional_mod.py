#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install one optional Moe mod into the isolated CK3 userdir.

The normal invocation installs the package but deliberately leaves
``dlc_load.json`` unchanged.  ``--enable`` is an explicit opt-in.  Before a
change to ``dlc_load.json`` this tool stores the exact original bytes and a
post-change hash, so ``--restore`` can put the user's previous load order back
without guessing at formatting or at fields added by another mod.

This is intentionally separate from ``finalize_mods.py``.  It installs an
optional wardrobe, travel-pawn, or cultural-units package after the five base
packages have already been installed, and it does not rebuild or re-materialise
any asset.  All source and destination paths are checked to remain inside their
configured roots; the package is copied through a staging directory and every
regular file is verified by size and SHA-256 before it replaces the target.

Examples (run from the repository root)::

    python tools/build/install_optional_mod.py
    python tools/build/install_optional_mod.py --package moe_travel_pawns --enable
    python tools/build/install_optional_mod.py --package moe_travel_pawns --restore

The default target is ``work/ck3_florence_userdir``.  ``--userdir`` and
``--record`` exist for an isolated test copy and for retaining more than one
reversible install record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_USERDIR = ROOT / "work" / "ck3_florence_userdir"
DEFAULT_PACKAGE = "moe_outfit_collection"
OPTIONAL_PACKAGES = (
    "moe_outfit_collection",
    "moe_cultural_units",
    # Kept as an explicit legacy option for rollback of old profiles. New
    # profiles use moe_cultural_units, which now contains travel pawns.
    "moe_travel_pawns",
)


def _absolute(path: Path) -> Path:
    return path.expanduser().resolve()


def _within(path: Path, root: Path) -> bool:
    """Return true only when *path* is a descendant of *root*."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _reject_link(path: Path) -> None:
    # Do not follow a symlink/junction while hashing or deploying a package.
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ValueError(f"links are not accepted in an optional package: {path}")


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _file_rows(root: Path) -> dict[str, dict[str, int | str]]:
    """Return a complete regular-file manifest below *root*."""
    _reject_link(root)
    if not root.is_dir():
        raise FileNotFoundError(f"package directory does not exist: {root}")
    rows: dict[str, dict[str, int | str]] = {}
    for path in sorted(root.rglob("*")):
        _reject_link(path)
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"unsupported package entry: {path}")
        relative = path.relative_to(root).as_posix()
        rows[relative] = {"bytes": path.stat().st_size, "sha256": _sha256(path)}
    if not rows:
        raise ValueError(f"optional package is empty: {root}")
    return rows


def _package_paths(package: str, userdir: Path) -> tuple[Path, Path, Path, Path]:
    source_root = ROOT / "mod" / package
    source_outer = ROOT / "mod" / f"{package}.mod"
    destination_root = userdir / "mod"
    destination_package = destination_root / package
    destination_outer = destination_root / f"{package}.mod"

    # Resolve existing roots, but do not resolve a missing final package path
    # through an attacker-controlled link.  The parent must be the exact mod
    # directory in the configured userdir.
    workspace = ROOT.resolve()
    # Check the unresolved destination first.  Resolving it before this check
    # would hide a mod-root symlink that points outside the isolated userdir.
    if destination_root.exists():
        _reject_link(destination_root)
    userdir = userdir.resolve()
    destination_root = destination_root.resolve()
    for path, root in (
        (source_root, workspace / "mod"),
        (source_outer, workspace / "mod"),
        (destination_root, userdir),
        (destination_package, destination_root),
        (destination_outer, destination_root),
    ):
        if not _within(path, root):
            raise ValueError(f"path escapes its configured root: {path}")
    if source_root.resolve().parent != (workspace / "mod").resolve():
        raise ValueError("source package is not a direct child of the project mod root")
    if destination_root != (userdir / "mod").resolve():
        raise ValueError("userdir mod root resolved unexpectedly")
    return source_root, source_outer, destination_package, destination_outer


def _copy_verified(source_root: Path, source_outer: Path,
                   destination_root: Path, destination_outer: Path,
                   jobs: int) -> dict[str, object]:
    """Stage, hash-check, and replace the package directory."""
    _reject_link(source_outer)
    if not source_outer.is_file():
        raise FileNotFoundError(f"optional package descriptor does not exist: {source_outer}")
    source_files = _file_rows(source_root)
    outer_row = {"bytes": source_outer.stat().st_size, "sha256": _sha256(source_outer)}
    destination_root.mkdir(parents=True, exist_ok=True)
    _reject_link(destination_root)
    destination_package = destination_root / source_root.name
    if not _within(destination_package, destination_root):
        raise ValueError(f"package destination escaped userdir mod root: {destination_package}")

    # A sibling staging directory stays within the userdir mod root.  The
    # final directory replacement is deliberately described as replacement,
    # not as a multi-file atomic transaction: the directory and outer
    # descriptor are moved in two separate operations.
    staging = Path(tempfile.mkdtemp(prefix=f".{source_root.name}.install-",
                                     dir=str(destination_root)))
    try:
        staged_package = staging / source_root.name
        staged_package.mkdir()
        pairs: list[tuple[Path, Path]] = []
        for relative in source_files:
            source = source_root / Path(relative)
            target = staged_package / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            pairs.append((source, target))

        def copy_one(pair: tuple[Path, Path]) -> None:
            source, target = pair
            shutil.copy2(source, target)

        with ThreadPoolExecutor(max_workers=jobs) as pool:
            list(pool.map(copy_one, pairs))
        staged_outer = staging / source_outer.name
        shutil.copy2(source_outer, staged_outer)

        staged_files = _file_rows(staged_package)
        if staged_files != source_files:
            raise ValueError("staged optional package SHA/size manifest differs from source")
        if {"bytes": staged_outer.stat().st_size,
            "sha256": _sha256(staged_outer)} != outer_row:
            raise ValueError("staged optional descriptor SHA/size differs from source")

        # Re-read the source after copying.  A concurrently changing source is
        # refused instead of silently deploying a mixed revision.
        if _file_rows(source_root) != source_files or {
            "bytes": source_outer.stat().st_size,
            "sha256": _sha256(source_outer),
        } != outer_row:
            raise RuntimeError("optional package changed while it was copied")

        if destination_outer.exists():
            _reject_link(destination_outer)
            if not destination_outer.is_file():
                raise ValueError(f"destination descriptor is not a file: {destination_outer}")
        if destination_package.exists():
            _reject_link(destination_package)
            if not destination_package.is_dir():
                raise ValueError(f"destination package is not a directory: {destination_package}")
        if destination_package.exists():
            shutil.rmtree(destination_package)
        if destination_outer.exists():
            destination_outer.unlink()
        os.replace(staged_package, destination_package)
        os.replace(staged_outer, destination_outer)

        installed_files = _file_rows(destination_package)
        if installed_files != source_files:
            raise ValueError("installed optional package SHA/size manifest differs from source")
        if {"bytes": destination_outer.stat().st_size,
            "sha256": _sha256(destination_outer)} != outer_row:
            raise ValueError("installed optional descriptor SHA/size differs from source")
        return {"directory": source_files, "descriptor": outer_row}
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _read_dlc(path: Path) -> tuple[bytes, dict[str, object]]:
    _reject_link(path)
    if not path.is_file():
        raise FileNotFoundError(f"missing isolated userdir dlc_load.json: {path}")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid dlc_load.json: {path}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("enabled_mods"), list):
        raise ValueError("dlc_load.json must contain an enabled_mods array")
    if not all(isinstance(item, str) for item in value["enabled_mods"]):
        raise ValueError("dlc_load.json enabled_mods must contain strings")
    return raw, value


def _atomic_write(path: Path, data: bytes) -> None:
    _reject_link(path) if path.exists() else None
    parent = path.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    if not _within(path, parent):
        raise ValueError(f"refusing to write outside parent: {path}")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _record_paths(record: Path) -> tuple[Path, Path]:
    requested = record.expanduser()
    if requested.exists():
        _reject_link(requested)
    record = _absolute(requested)
    root = ROOT.resolve()
    if not _within(record, root / "work"):
        raise ValueError("install record must remain under the project's work directory")
    backup = record.with_suffix(record.suffix + ".dlc_load.before")
    if not _within(backup, root / "work"):
        raise ValueError("dlc backup must remain under the project's work directory")
    return record, backup


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _preflight_load_change(dlc_path: Path, record: Path) -> None:
    """Validate mutable inputs before replacing an already-installed package."""
    _read_dlc(dlc_path)
    record, _ = _record_paths(record)
    if record.exists():
        raise FileExistsError(
            f"install record already exists; choose --record or remove it deliberately: {record}"
        )


def _change_load_order(dlc_path: Path, package: str, enable: bool,
                       record: Path, package_manifest: dict[str, object],
                       userdir: Path) -> None:
    before_raw, before = _read_dlc(dlc_path)
    enabled = list(before["enabled_mods"])
    entry = f"mod/{package}.mod"
    if enable and entry not in enabled:
        enabled.append(entry)
    if not enable and entry in enabled:
        enabled = [item for item in enabled if item != entry]
    after = dict(before)
    after["enabled_mods"] = enabled
    after_raw = (json.dumps(after, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    record, backup = _record_paths(record)
    record.parent.mkdir(parents=True, exist_ok=True)
    if record.exists():
        raise FileExistsError(
            f"install record already exists; choose --record or remove it deliberately: {record}"
        )
    _atomic_write(backup, before_raw)
    payload = {
        "schema": 1,
        "status": "verified_optional_mod_load_order_change",
        "package": package,
        "userdir": str(userdir),
        "dlc_load": str(dlc_path),
        "before_backup": str(backup),
        "before_sha256": _sha_bytes(before_raw),
        "after_sha256": _sha_bytes(after_raw),
        "before_enabled_mods": before["enabled_mods"],
        "after_enabled_mods": enabled,
        "package_manifest": package_manifest,
    }
    record_raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        _atomic_write(dlc_path, after_raw)
        _atomic_write(record, record_raw)
    except Exception:
        # If writing the record fails after dlc_load changed, put the exact
        # original bytes back immediately.  The backup remains for forensics.
        try:
            _atomic_write(dlc_path, before_raw)
        except Exception as rollback_error:
            raise RuntimeError(
                f"dlc_load changed but rollback failed: {rollback_error}"
            ) from rollback_error
        if record.exists():
            record.unlink()
        raise


def _restore(record: Path) -> None:
    record, _ = _record_paths(record)
    if not record.is_file():
        raise FileNotFoundError(f"install record does not exist: {record}")
    payload = json.loads(record.read_text(encoding="utf-8"))
    if payload.get("status") != "verified_optional_mod_load_order_change":
        raise ValueError("unsupported optional install record")
    dlc = _absolute(Path(payload["dlc_load"]))
    backup = _absolute(Path(payload["before_backup"]))
    recorded_userdir = _absolute(Path(payload["userdir"]))
    expected_backup = record.with_suffix(record.suffix + ".dlc_load.before")
    if backup != expected_backup:
        raise ValueError("record backup path does not match the record location")
    if (dlc.parent != recorded_userdir
            or dlc.name != "dlc_load.json"
            or not _within(dlc, recorded_userdir)):
        raise ValueError("record dlc path is outside its recorded userdir")
    raw = backup.read_bytes()
    if _sha_bytes(raw) != payload.get("before_sha256"):
        raise ValueError("dlc backup SHA does not match the install record")
    current, _ = _read_dlc(dlc)
    if _sha_bytes(current) != payload.get("after_sha256"):
        raise ValueError("current dlc_load differs from the recorded post-install file; refusing restore")
    _atomic_write(dlc, raw)
    print(f"restored {dlc} from {backup}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--package", default=DEFAULT_PACKAGE,
                    choices=OPTIONAL_PACKAGES, help="optional package to install")
    ap.add_argument("--userdir", type=Path, default=DEFAULT_USERDIR)
    ap.add_argument("--record", type=Path, default=None,
                    help="reversible package-specific load-order record (under work/)")
    ap.add_argument("--enable", action="store_true",
                    help="explicitly add the package to dlc_load.json")
    ap.add_argument("--disable", action="store_true",
                    help="explicitly remove the package from dlc_load.json")
    ap.add_argument("--restore", action="store_true",
                    help="restore the exact dlc_load.json recorded by a previous --enable/--disable")
    ap.add_argument("--jobs", type=int, default=12)
    args = ap.parse_args(argv)
    if args.jobs < 1:
        ap.error("--jobs must be positive")
    if args.enable and args.disable:
        ap.error("--enable and --disable are mutually exclusive")
    record = args.record or (ROOT / "work" / "optional_mod_install" / f"{args.package}.json")
    if args.restore:
        if args.enable or args.disable:
            ap.error("--restore cannot be combined with --enable/--disable")
        _restore(record)
        return 0

    requested_userdir = args.userdir.expanduser()
    if requested_userdir.exists():
        _reject_link(requested_userdir)
    userdir = _absolute(requested_userdir)
    if not userdir.is_dir():
        raise FileNotFoundError(f"isolated userdir does not exist: {userdir}")
    source_root, source_outer, destination_package, destination_outer = _package_paths(
        args.package, userdir
    )
    dlc_path = userdir / "dlc_load.json"
    if args.enable or args.disable:
        # Do this before staging/removing the existing package.  A stale
        # record or a broken dlc file must never turn a harmless re-run into a
        # package replacement followed by an error.
        _preflight_load_change(dlc_path, record)
    package_manifest = _copy_verified(
        source_root, source_outer, userdir / "mod", destination_outer, args.jobs
    )
    if args.enable or args.disable:
        _change_load_order(
            dlc_path, args.package, args.enable, record, package_manifest, userdir
        )
        action = "enabled" if args.enable else "disabled"
        print(f"installed and {action} {args.package}; reversible record: {args.record}")
    else:
        print(f"installed {args.package}; dlc_load.json left unchanged (not enabled)")
    print(f"verified files: {len(package_manifest['directory']) + 1}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileExistsError, FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
