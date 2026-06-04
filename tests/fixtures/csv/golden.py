"""CSV 取込ゴールデンスナップショット基盤 (Issue #14).

Mirrors ``tests.fixtures.seed_fy.golden`` for the CSV import path: it freezes the
*pure* :func:`~ai_books.services.csv_import.plan_import` output over fixed CSV fixtures
as JSON under ``golden/``, then regenerates and compares field-by-field so an
unintended change to parsing, column mapping, 相手科目推定, or the ``import_hash`` shows
up as a readable diff. Golden files are rewritten **only** through the explicit
``--update`` path (誤上書き防止), never implicitly during a test run.

The snapshot is DB-free — ``plan_import`` does no I/O — so this runs everywhere,
including ``./scripts/verify.sh`` without a live Postgres.

Generate or refresh golden files (intentional changes only)::

    uv run python -m tests.fixtures.csv            # dry-run: show what would change
    uv run python -m tests.fixtures.csv --update   # write golden/*.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, NamedTuple

from ai_books.services.csv_import import plan_import

#: Directory of committed CSV fixtures and their golden snapshots.
FIXTURES_DIR = Path(__file__).resolve().parent
GOLDEN_DIR = FIXTURES_DIR / "golden"


class CsvFixture(NamedTuple):
    """One fixed CSV input and the import parameters to plan it under."""

    name: str  # snapshot key / golden filename stem
    csv_file: str  # CSV fixture filename under FIXTURES_DIR
    account_code: str  # 取込先口座の 勘定科目コード
    csv_format: str  # "auto" or a named preset


#: The fixed CSV inputs the golden harness pins. Add a fixture by registering it here
#: and committing one more golden file.
CSV_FIXTURES: tuple[CsvFixture, ...] = (
    CsvFixture(name="bank", csv_file="bank_sample.csv", account_code="1141", csv_format="auto"),
    CsvFixture(name="card", csv_file="card_sample.csv", account_code="2130", csv_format="auto"),
)

CSV_FIXTURES_BY_NAME: dict[str, CsvFixture] = {fx.name: fx for fx in CSV_FIXTURES}


def plan_snapshot(fixture: CsvFixture) -> dict[str, Any]:
    """Turn a fixture's planned import into golden JSON shape (deterministic, no DB).

    Amounts are fixed strings (浮動小数禁止) and the deterministic ``import_hash`` is
    included so a change to the 二重取込検知 fingerprint is caught by the golden too.
    """
    csv_text = (FIXTURES_DIR / fixture.csv_file).read_text(encoding="utf-8")
    plan = plan_import(csv_text, fixture.account_code, fixture.csv_format)
    return {
        "fixture": fixture.name,
        "account_code": fixture.account_code,
        "format": fixture.csv_format,
        "entries": [
            {
                "import_hash": planned.import_hash,
                "to_suspense": planned.to_suspense,
                "entry_date": planned.entry.entry_date.isoformat(),
                "description": planned.entry.description,
                "source": planned.entry.source,
                "lines": [
                    {
                        "account_code": line.account_code,
                        "side": line.side.value,
                        "amount": str(line.amount),
                    }
                    for line in planned.entry.lines
                ],
            }
            for planned in plan
        ],
    }


def golden_path(name: str) -> Path:
    """Path of the golden file for fixture ``name``."""
    return GOLDEN_DIR / f"{name}.json"


def load_golden(name: str) -> dict[str, Any]:
    """Load the committed golden snapshot for fixture ``name`` (hint to ``--update`` if absent)."""
    path = golden_path(name)
    if not path.exists():
        raise FileNotFoundError(
            f"golden file {path} is missing; generate it with "
            f"`python -m tests.fixtures.csv --update`"
        )
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def diff_snapshots(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Return human-readable differences between two plan snapshots (empty ⇒ identical).

    Entries are matched positionally (a statement's row order is its identity), so a diff
    points at the offending row index and field rather than dumping the whole structure.
    """
    problems: list[str] = []
    for key in ("fixture", "account_code", "format"):
        if expected.get(key) != actual.get(key):
            problems.append(f"{key}: {expected.get(key)!r} != {actual.get(key)!r}")

    exp_entries = expected.get("entries", [])
    act_entries = actual.get("entries", [])
    if len(exp_entries) != len(act_entries):
        problems.append(f"entry count: {len(exp_entries)} != {len(act_entries)}")

    for index in range(min(len(exp_entries), len(act_entries))):
        exp, act = exp_entries[index], act_entries[index]
        for field_name in ("import_hash", "to_suspense", "entry_date", "description", "source"):
            if exp.get(field_name) != act.get(field_name):
                problems.append(
                    f"entry[{index}].{field_name}: {exp.get(field_name)!r} != {act.get(field_name)!r}"
                )
        if exp.get("lines") != act.get("lines"):
            problems.append(f"entry[{index}].lines: {exp.get('lines')!r} != {act.get('lines')!r}")
    return problems


def _serialize(snapshot: dict[str, Any]) -> str:
    """Canonical on-disk JSON: 2-space indent, non-ASCII kept (摘要 readable), trailing NL."""
    return json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"


def write_golden(name: str) -> Path:
    """Regenerate and overwrite the golden file for fixture ``name``; return its path.

    Only ever called from the ``--update`` CLI path, never from a test.
    """
    path = golden_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialize(plan_snapshot(CSV_FIXTURES_BY_NAME[name])), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    """CLI: dry-run (default) or, with ``--update``, rewrite golden files."""
    parser = argparse.ArgumentParser(
        prog="python -m tests.fixtures.csv",
        description="Generate or verify golden snapshots for CSV import fixtures.",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="rewrite golden/*.json from the fixtures (intentional changes only)",
    )
    parser.add_argument(
        "fixture",
        nargs="?",
        choices=sorted(CSV_FIXTURES_BY_NAME),
        help="limit to one fixture (default: all)",
    )
    args = parser.parse_args(argv)

    names = [args.fixture] if args.fixture else sorted(CSV_FIXTURES_BY_NAME)
    stale = 0
    for name in names:
        fresh = plan_snapshot(CSV_FIXTURES_BY_NAME[name])
        if args.update:
            path = write_golden(name)
            print(f"updated {path}")
            continue
        try:
            current = load_golden(name)
        except FileNotFoundError as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            stale += 1
            continue
        problems = diff_snapshots(current, fresh)
        if problems:
            stale += 1
            print(f"{name}: golden is stale ({len(problems)} difference(s)):", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
        else:
            print(f"{name}: up to date")

    if stale and not args.update:
        print(
            f"\n{stale} fixture(s) stale or missing. Re-run with --update to regenerate.",
            file=sys.stderr,
        )
        return 1
    return 0
