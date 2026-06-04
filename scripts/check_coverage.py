#!/usr/bin/env python3
"""Enforce separate line / branch coverage thresholds from a coverage.json (#58).

`pytest --cov-fail-under` only gates a single *blended* statement+branch percentage, so it
cannot assert "line >= 80 AND branch >= 70" (the AGENTS.md targets) independently. This reads
the JSON report coverage.py emits (`coverage json` / `pytest --cov-report=json`) and checks each
metric on its own, failing the build (exit 1) if either falls short.

Gating is DB-aware by construction: scripts/verify.sh only runs this checker when a live DB is
configured (AI_BOOKS_DB_URL set), because a DB-less run skips the DB-backed tests and so under-
reports coverage. A DB-less verify.sh still *measures* coverage; it just does not gate on it.

Usage:
    python scripts/check_coverage.py coverage.json [--line 80] [--branch 70]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="path to coverage.json")
    parser.add_argument("--line", type=float, default=80.0, help="minimum line coverage %%")
    parser.add_argument("--branch", type=float, default=70.0, help="minimum branch coverage %%")
    args = parser.parse_args()

    if not args.report.exists():
        print(f"check_coverage: report not found: {args.report}", file=sys.stderr)
        return 2

    totals = json.loads(args.report.read_text())["totals"]

    num_statements = totals["num_statements"]
    line_pct = 100.0 * totals["covered_lines"] / num_statements if num_statements else 100.0

    num_branches = totals["num_branches"]
    # A package with no branchable code trivially satisfies the branch gate.
    branch_pct = 100.0 * totals["covered_branches"] / num_branches if num_branches else 100.0

    line_ok = line_pct >= args.line
    branch_ok = branch_pct >= args.branch

    def mark(ok: bool) -> str:
        return "✅" if ok else "❌"

    print("coverage gate (#58):")
    print(f"  {mark(line_ok)} line   {line_pct:6.2f}%  (>= {args.line:g}%)")
    print(f"  {mark(branch_ok)} branch {branch_pct:6.2f}%  (>= {args.branch:g}%)")

    if line_ok and branch_ok:
        return 0
    print("check_coverage: coverage below threshold", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
