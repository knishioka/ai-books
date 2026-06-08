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

from ai_books.etax import (
    build_agricultural_etax_export,
    build_real_estate_etax_export,
    etax_export_snapshot,
    render_etax_xtx,
)
from ai_books.reports import (
    agricultural_income_snapshot,
    balance_sheet_snapshot,
    financial_statements_snapshot,
    general_ledger_snapshot,
    journal_book_snapshot,
    profit_and_loss_snapshot,
    real_estate_income_snapshot,
    worksheet_snapshot,
)

from .agricultural import agricultural_income_from_dataset
from .dataset import FISCAL_YEAR, SeedEntry
from .edge_cases import EDGE_DATASETS
from .real_estate import real_estate_income_from_dataset
from .reports import (
    MONTHLY_TREND_ACCOUNTS,
    balance_sheet_from_dataset,
    etax_export_from_dataset,
    financial_statements_from_dataset,
    general_ledger_from_dataset,
    journal_book_from_dataset,
    monthly_trend_from_dataset,
    profit_and_loss_from_dataset,
    trial_balance_from_dataset,
    worksheet_from_dataset,
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


def etax_xtx_snapshot() -> dict[str, Any]:
    """Golden shape for the real e-Tax ``.xtx`` (KOA210) rendered from the synthetic year (#79).

    Freezes the .xtx as a list of lines (so a regression diffs to the exact changed line, not one
    opaque blob) plus the 様式 identity. The .xtx is a deterministic function of the same
    :func:`etax_export_from_dataset` that backs ``etax_export.json``, so this golden pins the
    *rendered form* while the XSD test (``tests/test_etax_xtx.py``) pins its *形式妥当性*.
    """
    xtx = render_etax_xtx(etax_export_from_dataset())
    return {
        "report": "etax_xtx",
        "form_id": "KOA210",
        "version": "11.0",
        "namespace": "http://xml.e-tax.nta.go.jp/XSD/shotoku",
        "xtx_lines": xtx.splitlines(),
    }


_ETAX_NS = "http://xml.e-tax.nta.go.jp/XSD/shotoku"


def _etax_xtx_lines_snapshot(xtx: str, form_id: str, version: str) -> dict[str, Any]:
    """Golden shape for a rendered ``.xtx``: 様式 identity + the file split into lines (#79/#126).

    Freezing the .xtx as a list of lines means a regression diffs to the exact changed line, not one
    opaque blob; the 様式 identity (form_id/version/namespace) makes the file self-describing.
    """
    return {
        "report": "etax_xtx",
        "form_id": form_id,
        "version": version,
        "namespace": _ETAX_NS,
        "xtx_lines": xtx.splitlines(),
    }


def real_estate_etax_xtx_snapshot() -> dict[str, Any]:
    """Golden shape for the KOA220(不動産所得用) ``.xtx`` rendered from the synthetic landlord (#126)."""
    return _etax_xtx_lines_snapshot(
        render_etax_xtx(build_real_estate_etax_export(real_estate_income_from_dataset())),
        "KOA220",
        "8.0",
    )


def agricultural_etax_xtx_snapshot() -> dict[str, Any]:
    """Golden shape for the KOA240(農業所得用) ``.xtx`` rendered from the synthetic farm (#126)."""
    return _etax_xtx_lines_snapshot(
        render_etax_xtx(build_agricultural_etax_export(agricultural_income_from_dataset())),
        "KOA240",
        "8.0",
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
    "journal_book": (
        "journal_book.json",
        lambda: journal_book_snapshot(journal_book_from_dataset()),
    ),
    "general_ledger": (
        "general_ledger.json",
        lambda: general_ledger_snapshot(general_ledger_from_dataset()),
    ),
    "profit_and_loss": (
        "profit_and_loss.json",
        lambda: profit_and_loss_snapshot(profit_and_loss_from_dataset()),
    ),
    "balance_sheet": (
        "balance_sheet.json",
        lambda: balance_sheet_snapshot(balance_sheet_from_dataset()),
    ),
    "worksheet": (
        "worksheet.json",
        lambda: worksheet_snapshot(worksheet_from_dataset()),
    ),
    "financial_statements": (
        "financial_statements.json",
        lambda: financial_statements_snapshot(financial_statements_from_dataset()),
    ),
    "etax_export": (
        "etax_export.json",
        lambda: etax_export_snapshot(etax_export_from_dataset()),
    ),
    "etax_xtx": (
        "etax_xtx.json",
        etax_xtx_snapshot,
    ),
    "real_estate_income": (
        "real_estate_income.json",
        lambda: real_estate_income_snapshot(real_estate_income_from_dataset()),
    ),
    "agricultural_income": (
        "agricultural_income.json",
        lambda: agricultural_income_snapshot(agricultural_income_from_dataset()),
    ),
    "etax_export_koa220": (
        "etax_export_koa220.json",
        lambda: etax_export_snapshot(
            build_real_estate_etax_export(real_estate_income_from_dataset())
        ),
    ),
    "etax_xtx_koa220": (
        "etax_xtx_koa220.json",
        real_estate_etax_xtx_snapshot,
    ),
    "etax_export_koa240": (
        "etax_export_koa240.json",
        lambda: etax_export_snapshot(
            build_agricultural_etax_export(agricultural_income_from_dataset())
        ),
    ),
    "etax_xtx_koa240": (
        "etax_xtx_koa240.json",
        agricultural_etax_xtx_snapshot,
    ),
}


# ── エッジケース golden (Issue #57) ───────────────────────────────────────────────
# The edge years in :mod:`.edge_cases` reuse the *same* offline reducers + snapshotters as the main
# year — only the input dataset differs — so each is registered into GOLDEN_REPORTS programmatically
# rather than by hand. Keys are ``<report>__<name>`` and files live under ``golden/edge/`` to keep them
# apart from the main year's golden. This means the whole update/diff/CLI machinery (and 誤上書き防止)
# covers the edge golden for free, exactly as it does the main reports.

#: report → ``dataset → golden dict``. Each builder reduces a dataset offline and snapshots it with the
#: same serializer the main golden uses, so an edge golden cannot drift in shape from its main twin.
_EDGE_REPORT_BUILDERS: dict[str, Callable[[tuple[SeedEntry, ...]], dict[str, Any]]] = {
    "trial_balance": lambda ds: trial_balance_snapshot(trial_balance_from_dataset(ds)),
    "profit_and_loss": lambda ds: profit_and_loss_snapshot(profit_and_loss_from_dataset(ds)),
    "balance_sheet": lambda ds: balance_sheet_snapshot(balance_sheet_from_dataset(ds)),
    "worksheet": lambda ds: worksheet_snapshot(worksheet_from_dataset(ds)),
    "journal_book": lambda ds: journal_book_snapshot(journal_book_from_dataset(ds)),
    "general_ledger": lambda ds: general_ledger_snapshot(general_ledger_from_dataset(ds)),
}

#: edge dataset name → the reports golden-pinned for it. 集計 (試算表) / PL / BS cover every year; the
#: 月跨ぎ整理 year additionally pins 精算表 (the report its 修正記入 split is about) and the 帳簿 (仕訳帳 /
#: 総勘定元帳). The 空 year only needs the always-empty 集計/PL/BS.
EDGE_GOLDEN_REPORTS: dict[str, tuple[str, ...]] = {
    "empty": ("trial_balance", "profit_and_loss", "balance_sheet"),
    "one_sided": ("trial_balance", "profit_and_loss", "balance_sheet"),
    "fractional": ("trial_balance", "profit_and_loss", "balance_sheet"),
    "cross_month_adjustment": (
        "trial_balance",
        "profit_and_loss",
        "balance_sheet",
        "worksheet",
        "journal_book",
        "general_ledger",
    ),
}


def _edge_generator(
    dataset: tuple[SeedEntry, ...],
    build: Callable[[tuple[SeedEntry, ...]], dict[str, Any]],
) -> Callable[[], dict[str, Any]]:
    """Bind ``dataset`` and ``build`` into a zero-arg golden generator (GOLDEN_REPORTS' contract).

    A named factory (rather than an inline lambda) gives each closure its own captured pair — avoiding
    the late-binding loop trap — and a signature mypy can match to ``Callable[[], dict[str, Any]]``.
    """

    def generate() -> dict[str, Any]:
        return build(dataset)

    return generate


def _register_edge_golden() -> None:
    """Add one ``<report>__<name>`` entry to :data:`GOLDEN_REPORTS` per edge report.

    Files land under ``golden/edge/<report>__<name>.json`` so they stay apart from the main year's.
    """
    for name, reports in EDGE_GOLDEN_REPORTS.items():
        dataset = EDGE_DATASETS[name]
        for report in reports:
            key = f"{report}__{name}"
            filename = f"edge/{report}__{name}.json"
            GOLDEN_REPORTS[key] = (
                filename,
                _edge_generator(dataset, _EDGE_REPORT_BUILDERS[report]),
            )


_register_edge_golden()


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


#: Fields a list of objects can be matched on, in preference order. When every item in
#: both lists is a dict carrying one of these with *unique* values, the diff matches by
#: that natural key (so a message names the 科目コード / 伝票番号 it concerns) rather than by
#: positional index — far more readable, and stable under reordering.
_LIST_KEYS = ("code", "voucher_no")


def diff_snapshots(expected: Any, actual: Any) -> list[str]:
    """Return human-readable, path-tagged differences between two snapshots (empty ⇒ same).

    Walks the two JSON-shaped structures recursively. Lists of objects are matched by a
    natural key when available (``code`` for trial-balance/ledger accounts, ``voucher_no``
    for journal entries) so a diff points at the offending row ("rows[code=7250].balance:
    '360000.00' != '350000.00'") instead of dumping the whole structure; otherwise items
    are compared by index. Report-agnostic, so every report reuses it as-is.
    """
    problems: list[str] = []
    _diff(expected, actual, "", problems)
    return problems


def _diff(expected: Any, actual: Any, path: str, problems: list[str]) -> None:
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            child = f"{path}.{key}" if path else key
            if key not in expected:
                problems.append(f"{child}: unexpected (= {actual[key]!r})")
            elif key not in actual:
                problems.append(f"{child}: missing (expected {expected[key]!r})")
            else:
                _diff(expected[key], actual[key], child, problems)
    elif isinstance(expected, list) and isinstance(actual, list):
        _diff_lists(expected, actual, path, problems)
    elif expected != actual:
        problems.append(f"{path or '(root)'}: {expected!r} != {actual!r}")


def _diff_lists(expected: list[Any], actual: list[Any], path: str, problems: list[str]) -> None:
    key = _list_key(expected, actual)
    if key is None:
        for index in range(max(len(expected), len(actual))):
            child = f"{path}[{index}]"
            if index >= len(expected):
                problems.append(f"{child}: unexpected (= {actual[index]!r})")
            elif index >= len(actual):
                problems.append(f"{child}: missing (expected {expected[index]!r})")
            else:
                _diff(expected[index], actual[index], child, problems)
        return

    expected_by = {item[key]: item for item in expected}
    actual_by = {item[key]: item for item in actual}
    for missing in sorted(expected_by.keys() - actual_by.keys()):
        problems.append(f"{path}[{key}={missing}]: missing from actual")
    for unexpected in sorted(actual_by.keys() - expected_by.keys()):
        problems.append(f"{path}[{key}={unexpected}]: unexpected in actual")
    for shared in sorted(expected_by.keys() & actual_by.keys()):
        _diff(expected_by[shared], actual_by[shared], f"{path}[{key}={shared}]", problems)


def _list_key(expected: list[Any], actual: list[Any]) -> str | None:
    """The natural key to match two lists on, or ``None`` to fall back to index matching."""
    for key in _LIST_KEYS:
        items = expected + actual
        if items and all(isinstance(item, dict) and key in item for item in items):
            expected_values = [item[key] for item in expected]
            actual_values = [item[key] for item in actual]
            if len(set(expected_values)) == len(expected_values) and len(set(actual_values)) == len(
                actual_values
            ):
                return key
    return None


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
        problems = diff_snapshots(current, fresh)
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
