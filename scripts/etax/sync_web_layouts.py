#!/usr/bin/env python3
"""Sync committed e-Tax layout JSONs into the Vercel web root.

The Python package layout files under ``src/ai_books/etax`` are the source of
truth. The Vercel project root is ``web/``, so the viewer also needs committed
copies under ``web/lib/etax/layouts`` for static JSON imports. This script makes
those copies generated artifacts instead of hand-maintained data.
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / "src" / "ai_books" / "etax"
WEB_DIR = ROOT / "web" / "lib" / "etax" / "layouts"
PATTERN = "*_layout.json"


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def sync_layouts(check: bool) -> int:
    sources = sorted(SOURCE_DIR.glob(PATTERN))
    if not sources:
        raise SystemExit(f"no layout JSONs found in {_relative(SOURCE_DIR)}")

    if not check:
        WEB_DIR.mkdir(parents=True, exist_ok=True)
    expected_names = {source.name for source in sources}
    stale = sorted(path for path in WEB_DIR.glob(PATTERN) if path.name not in expected_names)
    drifted: list[tuple[Path, Path]] = []

    for source in sources:
        target = WEB_DIR / source.name
        if not target.exists() or not filecmp.cmp(source, target, shallow=False):
            drifted.append((source, target))
            if not check:
                shutil.copyfile(source, target)

    if not check:
        for target in stale:
            target.unlink()
        changed = len(drifted) + len(stale)
        print(f"synced {len(sources)} layout JSON(s) ({changed} changed)")
        return 0

    if not drifted and not stale:
        print(f"web e-Tax layouts are in sync ({len(sources)} file(s))")
        return 0

    for source, target in drifted:
        print(f"out of sync: {_relative(target)} should match {_relative(source)}")
    for target in stale:
        print(f"stale web layout: {_relative(target)}")
    print("run: uv run python scripts/etax/sync_web_layouts.py")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="only verify sync state; do not update web copies",
    )
    args = parser.parse_args()
    raise SystemExit(sync_layouts(check=args.check))


if __name__ == "__main__":
    main()
