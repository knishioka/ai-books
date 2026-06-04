"""CSV 取込フィクスチャ + ゴールデンスナップショット基盤 (Issue #14).

Fixed bank/CC CSV inputs and the harness that pins the *pure*
:func:`~ai_books.services.csv_import.plan_import` output over them against frozen golden
snapshots. DB-free, so it verifies parsing / column mapping / 相手科目推定 / import_hash
everywhere — including ``./scripts/verify.sh`` without a live Postgres.

Update golden files with ``python -m tests.fixtures.csv --update`` (explicit flag only).
"""

from __future__ import annotations

from .golden import (
    CSV_FIXTURES,
    CSV_FIXTURES_BY_NAME,
    CsvFixture,
    diff_snapshots,
    golden_path,
    load_golden,
    plan_snapshot,
    write_golden,
)

__all__ = [
    "CSV_FIXTURES",
    "CSV_FIXTURES_BY_NAME",
    "CsvFixture",
    "diff_snapshots",
    "golden_path",
    "load_golden",
    "plan_snapshot",
    "write_golden",
]
