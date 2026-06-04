"""合成シードデータ + ゴールデンスナップショット基盤 (Issue #17).

A fictional one-fiscal-year 個人事業/青色申告 dataset and the harness that verifies
reports over it against frozen golden snapshots. Built so every downstream report Issue
(#18 集計 / #19 帳簿 / #20 PL / #21 BS / #23 決算書 / #24 e-Tax) reuses the same seed and
the same compare/update machinery — adding only its own report + golden file.

Public surface:

* :data:`FY_ENTRIES` / :func:`validate_dataset` — the synthetic year and its self-check.
* :func:`load_fiscal_year` — idempotent load into Postgres.
* :func:`trial_balance_from_dataset` / :func:`trial_balance_from_db` — the first report,
  computed offline (for golden generation) and from the DB (for the test).
* :func:`load_golden` / :func:`diff_snapshots` / :func:`trial_balance_snapshot` — the
  report-agnostic golden harness. Update golden files with
  ``python -m tests.fixtures.seed_fy --update`` (explicit flag only).

See ``README.md`` for the scenario and the hand-traceable expected balances.
"""

from __future__ import annotations

from ai_books.models import TrialBalance, TrialBalanceRow

from .dataset import (
    FISCAL_YEAR,
    FY_END,
    FY_ENTRIES,
    FY_START,
    SeedEntry,
    SeedLine,
    referenced_codes,
    validate_dataset,
)
from .golden import (
    GOLDEN_DIFFERS,
    GOLDEN_REPORTS,
    diff_monthly_trend,
    diff_report,
    diff_snapshots,
    golden_path,
    load_golden,
    monthly_trend_snapshot,
    trial_balance_snapshot,
    write_golden,
)
from .loader import LoadResult, load_fiscal_year
from .reports import (
    MONTHLY_TREND_ACCOUNTS,
    monthly_trend_from_dataset,
    monthly_trend_from_db,
    trial_balance_from_dataset,
    trial_balance_from_db,
)

__all__ = [
    "FISCAL_YEAR",
    "FY_END",
    "FY_ENTRIES",
    "FY_START",
    "GOLDEN_DIFFERS",
    "GOLDEN_REPORTS",
    "MONTHLY_TREND_ACCOUNTS",
    "LoadResult",
    "SeedEntry",
    "SeedLine",
    "TrialBalance",
    "TrialBalanceRow",
    "diff_monthly_trend",
    "diff_report",
    "diff_snapshots",
    "golden_path",
    "load_fiscal_year",
    "load_golden",
    "monthly_trend_from_dataset",
    "monthly_trend_from_db",
    "monthly_trend_snapshot",
    "referenced_codes",
    "trial_balance_from_dataset",
    "trial_balance_from_db",
    "trial_balance_snapshot",
    "validate_dataset",
    "write_golden",
]
