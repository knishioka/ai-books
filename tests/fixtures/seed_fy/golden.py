"""ゴールデンスナップショット基盤 — serialize, compare, and (only on demand) update.

A *golden snapshot* is the expected output of a report over the synthetic year, frozen
as JSON under ``golden/``. The harness regenerates the report from the dataset and
compares it field-by-field; a mismatch prints a readable per-row diff so a regression is
obvious. Golden files are **only** rewritten by the explicit ``--update`` command — never
implicitly during a test run — so an accidental logic change can never silently overwrite
the expected values (誤上書き防止).

Reports are looked up by name in :data:`GOLDEN_REPORTS`, so a later Issue adds its report
(PL / BS / 決算書 …) by registering one more entry and committing one more golden file —
the compare/update/CLI machinery here is report-agnostic and reused as-is.

Generate or refresh golden files (intentional changes only)::

    uv run python -m tests.fixtures.seed_fy            # dry-run: show what would change
    uv run python -m tests.fixtures.seed_fy --update   # write golden/*.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .dataset import FISCAL_YEAR
from .reports import (
    MONTHLY_TREND_ACCOUNTS,
    monthly_trend_from_dataset,
    trial_balance_from_dataset,
)

if TYPE_CHECKING:
    from ai_books.models import MonthlyTrend, TrialBalance

#: Directory holding the committed golden JSON files (one per report).
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

#: Two-decimal quantum matching ``numeric(18, 2)`` so serialized amounts are stable
#: ("300000.00", never "300000" or "3E+5") regardless of how the Decimal was built.
_MONEY = Decimal("0.01")


def _money(value: Decimal) -> str:
    """Serialize a Decimal amount as a fixed 2-dp string (numeric(18, 2) shape)."""
    return str(value.quantize(_MONEY))


def trial_balance_snapshot(trial_balance: TrialBalance) -> dict[str, Any]:
    """Turn a :class:`~tests.fixtures.seed_fy.reports.TrialBalance` into golden JSON shape.

    Amounts are fixed-point strings (浮動小数禁止 — a balance never becomes a float),
    rows stay ordered by code, and the column footings are included so the 借貸平均
    invariant is visible in the file itself.
    """
    return {
        "report": "trial_balance",
        "fiscal_year": FISCAL_YEAR,
        "rows": [
            {
                "code": row.code,
                "name": row.name,
                "debit_total": _money(row.debit_total),
                "credit_total": _money(row.credit_total),
                "balance": _money(row.balance),
            }
            for row in trial_balance.rows
        ],
        "total_debit": _money(trial_balance.total_debit),
        "total_credit": _money(trial_balance.total_credit),
    }


def monthly_trend_snapshot(trends: list[MonthlyTrend]) -> dict[str, Any]:
    """Turn a list of :class:`~ai_books.models.MonthlyTrend` into golden JSON shape.

    Amounts are fixed-point strings (浮動小数禁止), accounts stay in the given order and
    each carries its 12 monthly points in order. ``account_id`` is deliberately omitted —
    it is DB-assigned, so a golden keyed on it could never match the offline reduction.
    """
    return {
        "report": "monthly_trend",
        "fiscal_year": FISCAL_YEAR,
        "accounts": [
            {
                "code": trend.code,
                "name": trend.name,
                "normal_balance": trend.normal_balance.value,
                "opening_balance": _money(trend.opening_balance),
                "closing_balance": _money(trend.closing_balance),
                "points": [
                    {
                        "month": point.month,
                        "debit_total": _money(point.debit_total),
                        "credit_total": _money(point.credit_total),
                        "net_change": _money(point.net_change),
                        "closing_balance": _money(point.closing_balance),
                    }
                    for point in trend.points
                ],
            }
            for trend in trends
        ],
    }


def _monthly_trend_snapshot_from_dataset() -> dict[str, Any]:
    """Generate the monthly-trend golden for the fixed :data:`MONTHLY_TREND_ACCOUNTS`."""
    return monthly_trend_snapshot(
        [monthly_trend_from_dataset(code) for code in MONTHLY_TREND_ACCOUNTS]
    )


#: name → (filename, generator). The generator returns the golden-shaped dict from the
#: in-memory dataset (no DB), so golden files can be produced offline. Downstream report
#: Issues append their own entry here.
GOLDEN_REPORTS: dict[str, tuple[str, Callable[[], dict[str, Any]]]] = {
    "trial_balance": (
        "trial_balance.json",
        lambda: trial_balance_snapshot(trial_balance_from_dataset()),
    ),
    "monthly_trend": (
        "monthly_trend.json",
        _monthly_trend_snapshot_from_dataset,
    ),
}


def golden_path(report: str) -> Path:
    """Path of the golden file for ``report``."""
    filename, _ = GOLDEN_REPORTS[report]
    return GOLDEN_DIR / filename


def load_golden(report: str) -> dict[str, Any]:
    """Load the committed golden snapshot for ``report``.

    Raises ``FileNotFoundError`` (with a hint to run ``--update``) if it is missing.
    """
    path = golden_path(report)
    if not path.exists():
        raise FileNotFoundError(
            f"golden file {path} is missing; generate it with "
            f"`python -m tests.fixtures.seed_fy --update`"
        )
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def diff_snapshots(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Return human-readable differences between two snapshots (empty ⇒ identical).

    Rows are matched by ``code`` so a diff points at the offending account ("7250 地代家賃:
    balance 360000.00 != 350000.00") rather than dumping the whole structure.
    """
    problems: list[str] = []

    for key in ("report", "fiscal_year", "total_debit", "total_credit"):
        if expected.get(key) != actual.get(key):
            problems.append(f"{key}: {expected.get(key)!r} != {actual.get(key)!r}")

    expected_rows = {row["code"]: row for row in expected.get("rows", [])}
    actual_rows = {row["code"]: row for row in actual.get("rows", [])}

    for code in sorted(expected_rows.keys() - actual_rows.keys()):
        problems.append(f"{code} {expected_rows[code].get('name', '')}: missing from actual")
    for code in sorted(actual_rows.keys() - expected_rows.keys()):
        problems.append(f"{code} {actual_rows[code].get('name', '')}: unexpected in actual")

    for code in sorted(expected_rows.keys() & actual_rows.keys()):
        exp, act = expected_rows[code], actual_rows[code]
        for field in ("name", "debit_total", "credit_total", "balance"):
            if exp.get(field) != act.get(field):
                problems.append(
                    f"{code} {exp.get('name', '')}: {field} "
                    f"{exp.get(field)!r} != {act.get(field)!r}"
                )
    return problems


def diff_monthly_trend(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Return human-readable differences between two monthly-trend snapshots.

    Accounts are matched by ``code`` and the per-month points by ``month``, so a diff points
    at the offending cell ("1141 2025-06: closing_balance 1290000.00 != 1300000.00") rather
    than dumping the whole structure. Empty list ⇒ identical.
    """
    problems: list[str] = []

    for key in ("report", "fiscal_year"):
        if expected.get(key) != actual.get(key):
            problems.append(f"{key}: {expected.get(key)!r} != {actual.get(key)!r}")

    expected_accounts = {acc["code"]: acc for acc in expected.get("accounts", [])}
    actual_accounts = {acc["code"]: acc for acc in actual.get("accounts", [])}

    for code in sorted(expected_accounts.keys() - actual_accounts.keys()):
        problems.append(f"{code}: missing from actual")
    for code in sorted(actual_accounts.keys() - expected_accounts.keys()):
        problems.append(f"{code}: unexpected in actual")

    for code in sorted(expected_accounts.keys() & actual_accounts.keys()):
        exp, act = expected_accounts[code], actual_accounts[code]
        for field in ("name", "normal_balance", "opening_balance", "closing_balance"):
            if exp.get(field) != act.get(field):
                problems.append(f"{code}: {field} {exp.get(field)!r} != {act.get(field)!r}")

        exp_points = {p["month"]: p for p in exp.get("points", [])}
        act_points = {p["month"]: p for p in act.get("points", [])}
        for month in sorted(exp_points.keys() - act_points.keys()):
            problems.append(f"{code} {month}: missing from actual")
        for month in sorted(act_points.keys() - exp_points.keys()):
            problems.append(f"{code} {month}: unexpected in actual")
        for month in sorted(exp_points.keys() & act_points.keys()):
            pe, pa = exp_points[month], act_points[month]
            for field in ("debit_total", "credit_total", "net_change", "closing_balance"):
                if pe.get(field) != pa.get(field):
                    problems.append(
                        f"{code} {month}: {field} {pe.get(field)!r} != {pa.get(field)!r}"
                    )
    return problems


#: report → comparison function. Reports not listed fall back to :func:`diff_snapshots`
#: (the trial-balance shape). A report registers its own differ here when its JSON shape
#: differs from the per-account-row trial balance.
GOLDEN_DIFFERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], list[str]]] = {
    "monthly_trend": diff_monthly_trend,
}


def diff_report(report: str, expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Diff two snapshots of ``report`` using its registered differ (default trial-balance)."""
    return GOLDEN_DIFFERS.get(report, diff_snapshots)(expected, actual)


def _serialize(snapshot: dict[str, Any]) -> str:
    """Canonical on-disk JSON: 2-space indent, non-ASCII kept (科目名 readable), trailing NL."""
    return json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"


def write_golden(report: str) -> Path:
    """Regenerate and overwrite the golden file for ``report``; return its path.

    Only ever called from the ``--update`` CLI path, never from a test.
    """
    _, generate = GOLDEN_REPORTS[report]
    path = golden_path(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialize(generate()), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    """CLI: dry-run (default) or, with ``--update``, rewrite golden files.

    Without ``--update`` nothing on disk changes — it only reports which golden files
    are missing or stale, and exits non-zero if any are, so it doubles as a check.
    """
    parser = argparse.ArgumentParser(
        prog="python -m tests.fixtures.seed_fy",
        description="Generate or verify golden snapshots for the synthetic fiscal year.",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="rewrite golden/*.json from the dataset (intentional changes only)",
    )
    parser.add_argument(
        "report",
        nargs="?",
        choices=sorted(GOLDEN_REPORTS),
        help="limit to one report (default: all)",
    )
    args = parser.parse_args(argv)

    reports = [args.report] if args.report else sorted(GOLDEN_REPORTS)
    stale = 0
    for report in reports:
        _, generate = GOLDEN_REPORTS[report]
        fresh = generate()
        if args.update:
            path = write_golden(report)
            print(f"updated {path}")
            continue
        try:
            current = load_golden(report)
        except FileNotFoundError as exc:
            print(f"{report}: {exc}", file=sys.stderr)
            stale += 1
            continue
        problems = diff_report(report, current, fresh)
        if problems:
            stale += 1
            print(f"{report}: golden is stale ({len(problems)} difference(s)):", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
        else:
            print(f"{report}: up to date")

    if stale and not args.update:
        print(
            f"\n{stale} report(s) stale or missing. Re-run with --update to regenerate.",
            file=sys.stderr,
        )
        return 1
    return 0
