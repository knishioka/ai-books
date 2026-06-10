"""DB-backed tests for the e-Tax filing preflight (Issue #159).

Exercise :func:`ai_books.etax.preflight.filing_preflight` over a real Postgres: the clean
synthetic year is 申告可 (``ok``), each injected defect (draft 残存 / 期間外日付 / マッピング欠落)
is reported as an error 全件, and a 記帳漏れの可能性 (空の月) surfaces as a warning without
flipping ``status`` to ``error``.

Skips when ``AI_BOOKS_DB_URL`` is unset (so ``./scripts/verify.sh`` stays green without a live
Postgres); runs in CI on the throwaway-schema ``migrated_conn`` fixture.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from ai_books import db
from ai_books.db.repository import AccountRepository, JournalRepository
from ai_books.errors import EtaxValidationError, RecordNotFoundError
from ai_books.etax.preflight import (
    VOIDED_WARNING_THRESHOLD,
    PreflightCheck,
    PreflightReport,
    filing_preflight,
)
from ai_books.models import EntrySide, EntryStatus, JournalEntry, JournalLine
from tests.fixtures.seed_fy import FY_ENTRIES, load_fiscal_year

pytestmark = pytest.mark.skipif(
    not os.environ.get(db.DB_URL_ENV),
    reason=f"{db.DB_URL_ENV} not set; skipping DB-backed preflight tests",
)

_FY = "FY2025"


def _insert_entry(
    conn: psycopg.Connection[Any],
    *,
    voucher_no: str,
    entry_date: date,
    status: EntryStatus,
    debit_code: str = "1110",  # 現金
    credit_code: str = "1141",  # 普通預金
    amount: int = 10_000,
) -> JournalEntry:
    """Insert one balanced entry via the production write path, with the given status/date.

    A 現金 ↔ 普通預金 振替 keeps it valid and (since it touches no PL/sales account) inert for the
    決算書, so the only thing under test is how preflight classifies the entry's status/date.
    """
    accounts = AccountRepository(conn).find()
    code_to_id = {a.code: a.id for a in accounts if a.id is not None}
    entry = JournalEntry(
        entry_date=entry_date,
        description="preflight test entry",
        voucher_no=voucher_no,
        status=status,
        lines=[
            JournalLine(
                line_no=1,
                account_id=code_to_id[debit_code],
                side=EntrySide.DEBIT,
                amount=Decimal(amount),
            ),
            JournalLine(
                line_no=2,
                account_id=code_to_id[credit_code],
                side=EntrySide.CREDIT,
                amount=Decimal(amount),
            ),
        ],
    )
    return JournalRepository(conn).insert_entry(entry)


def _insert_fiscal_year(
    conn: psycopg.Connection[Any], *, name: str, start: date, end: date
) -> None:
    """Register another fiscal year so its entries are not orphaned (会計期間外 判定の対象外)."""
    conn.execute(
        "INSERT INTO fiscal_years (name, start_date, end_date) VALUES (%s, %s, %s)"
        " ON CONFLICT (name) DO NOTHING",
        (name, start, end),
    )


# --- clean year -----------------------------------------------------------------


def test_clean_year_is_ok(migrated_conn: psycopg.Connection[Any]) -> None:
    load_fiscal_year(migrated_conn)
    report = filing_preflight(migrated_conn, fiscal_year=_FY)
    assert isinstance(report, PreflightReport)
    assert report.fiscal_year == _FY
    assert report.start_date == date(2025, 1, 1)
    assert report.end_date == date(2025, 12, 31)
    assert report.ok is True
    assert report.status == "ok"
    assert report.errors == []
    # The synthetic year touches every month and voids nothing → no warnings either.
    assert report.warnings == []


# --- error: draft (未転記) 残存 --------------------------------------------------


def test_draft_entry_blocks_filing(migrated_conn: psycopg.Connection[Any]) -> None:
    load_fiscal_year(migrated_conn)
    _insert_entry(
        migrated_conn,
        voucher_no="DRAFT-01",
        entry_date=date(2025, 6, 15),
        status=EntryStatus.DRAFT,
    )
    report = filing_preflight(migrated_conn, fiscal_year=_FY)
    assert report.ok is False
    assert report.status == "error"
    drafts = [e for e in report.errors if e.check == PreflightCheck.DRAFT_ENTRY]
    assert len(drafts) == 1
    assert drafts[0].voucher_no == "DRAFT-01"
    assert drafts[0].entry_date == date(2025, 6, 15)


def test_all_drafts_reported_not_just_first(migrated_conn: psycopg.Connection[Any]) -> None:
    load_fiscal_year(migrated_conn)
    for i in range(3):
        _insert_entry(
            migrated_conn,
            voucher_no=f"DRAFT-{i}",
            entry_date=date(2025, 3, 1),
            status=EntryStatus.DRAFT,
        )
    report = filing_preflight(migrated_conn, fiscal_year=_FY)
    drafts = {e.voucher_no for e in report.errors if e.check == PreflightCheck.DRAFT_ENTRY}
    assert drafts == {"DRAFT-0", "DRAFT-1", "DRAFT-2"}


# --- error: 会計期間外の posted 仕訳 --------------------------------------------


def test_out_of_period_posted_blocks_filing(migrated_conn: psycopg.Connection[Any]) -> None:
    load_fiscal_year(migrated_conn)
    _insert_entry(
        migrated_conn,
        voucher_no="AFTER-END",
        entry_date=date(2026, 1, 15),  # 期末 (2025-12-31) より後
        status=EntryStatus.POSTED,
    )
    _insert_entry(
        migrated_conn,
        voucher_no="BEFORE-START",
        entry_date=date(2024, 12, 20),  # 期首 (2025-01-01) より前
        status=EntryStatus.POSTED,
    )
    report = filing_preflight(migrated_conn, fiscal_year=_FY)
    assert report.status == "error"
    out = {e.voucher_no for e in report.errors if e.check == PreflightCheck.OUT_OF_PERIOD}
    assert out == {"AFTER-END", "BEFORE-START"}


def test_other_fiscal_year_entries_are_not_flagged(
    migrated_conn: psycopg.Connection[Any],
) -> None:
    # Regression for PR #170 review: an entry dated in a *registered* other fiscal year is a
    # legitimate entry of that year, not a FY2025 申告ブロッカー. Only true orphans (belonging to
    # no fiscal year) are reported — so multi-year databases produce no false positives.
    load_fiscal_year(migrated_conn)
    _insert_fiscal_year(
        migrated_conn, name="FY2024", start=date(2024, 1, 1), end=date(2024, 12, 31)
    )
    _insert_entry(
        migrated_conn,
        voucher_no="FY2024-POSTED",  # 2024 の正当な仕訳 (FY2024 に属する) → 報告しない
        entry_date=date(2024, 6, 15),
        status=EntryStatus.POSTED,
    )
    _insert_entry(
        migrated_conn,
        voucher_no="ORPHAN",  # どの会計年度にも属さない孤児 → 報告する
        entry_date=date(2099, 1, 1),
        status=EntryStatus.POSTED,
    )
    report = filing_preflight(migrated_conn, fiscal_year=_FY)
    out = {e.voucher_no for e in report.errors if e.check == PreflightCheck.OUT_OF_PERIOD}
    assert out == {"ORPHAN"}
    assert "FY2024-POSTED" not in out


# --- error: 決算書 → KOA210 マッピング欠落 (全件収集) --------------------------


def test_etax_mapping_problems_all_reported(
    migrated_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 実データから端数/桁あふれを作るのは困難なので、export 検証 (#24) が投げる
    # EtaxValidationError を差し替え、preflight が problems を 1 件残らず error 化することを固定する。
    load_fiscal_year(migrated_conn)
    problems = [
        {"item_code": "AMF00100", "message": "必須項目 売上(収入)金額 が欠落しています"},
        {"item_code": "AMG00260", "row": "3", "message": "金額が桁数を超えています"},
    ]

    def _raise(*_args: Any, **_kwargs: Any) -> None:
        raise EtaxValidationError(problems)

    monkeypatch.setattr("ai_books.etax.preflight.build_etax_export", _raise)
    report = filing_preflight(migrated_conn, fiscal_year=_FY)
    assert report.status == "error"
    mapping = [e for e in report.errors if e.check == PreflightCheck.ETAX_MAPPING]
    assert len(mapping) == 2
    assert {e.item_code for e in mapping} == {"AMF00100", "AMG00260"}
    overflow = next(e for e in mapping if e.item_code == "AMG00260")
    assert overflow.row == "3"
    assert overflow.message == "金額が桁数を超えています"


def test_all_defect_categories_collected_together(
    migrated_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # draft 残存 / 期間外日付 / マッピング欠落 を同時に注入 → どれも握り潰さず全件報告される。
    load_fiscal_year(migrated_conn)
    _insert_entry(
        migrated_conn, voucher_no="D1", entry_date=date(2025, 2, 2), status=EntryStatus.DRAFT
    )
    _insert_entry(
        migrated_conn, voucher_no="OOP1", entry_date=date(2026, 2, 2), status=EntryStatus.POSTED
    )

    def _raise(*_args: Any, **_kwargs: Any) -> None:
        raise EtaxValidationError([{"item_code": "AMF00100", "message": "欠落"}])

    monkeypatch.setattr("ai_books.etax.preflight.build_etax_export", _raise)
    report = filing_preflight(migrated_conn, fiscal_year=_FY)
    checks = {e.check for e in report.errors}
    assert checks == {
        PreflightCheck.DRAFT_ENTRY,
        PreflightCheck.OUT_OF_PERIOD,
        PreflightCheck.ETAX_MAPPING,
    }


# --- warning: 空の月 (status は ok のまま) --------------------------------------


def test_empty_month_warns_but_stays_ok(migrated_conn: psycopg.Connection[Any]) -> None:
    # 7月の唯一の仕訳 (通信費) を除いて投入 → 7月は posted 0 件。warning は出るが status は ok。
    entries = tuple(e for e in FY_ENTRIES if e.entry_date.month != 7)
    load_fiscal_year(migrated_conn, entries)
    report = filing_preflight(migrated_conn, fiscal_year=_FY)
    assert report.ok is True
    assert report.status == "ok"
    assert report.errors == []
    empty = [w for w in report.warnings if w.check == PreflightCheck.EMPTY_MONTH]
    assert [w.month for w in empty] == ["2025-07"]


# --- warning: void 済仕訳の多発 -------------------------------------------------


def test_many_voided_entries_warn(migrated_conn: psycopg.Connection[Any]) -> None:
    load_fiscal_year(migrated_conn)
    for i in range(VOIDED_WARNING_THRESHOLD):
        _insert_entry(
            migrated_conn,
            voucher_no=f"VOID-{i}",
            entry_date=date(2025, 4, 1),
            status=EntryStatus.VOIDED,
        )
    report = filing_preflight(migrated_conn, fiscal_year=_FY)
    # Voided entries do not affect the posted 決算書, so status stays ok; only a 参考 warning.
    assert report.status == "ok"
    voided = [w for w in report.warnings if w.check == PreflightCheck.VOIDED_ENTRIES]
    assert len(voided) == 1
    assert str(VOIDED_WARNING_THRESHOLD) in voided[0].message


# --- unknown fiscal year --------------------------------------------------------


def test_unknown_fiscal_year_raises(migrated_conn: psycopg.Connection[Any]) -> None:
    load_fiscal_year(migrated_conn)
    with pytest.raises(RecordNotFoundError):
        filing_preflight(migrated_conn, fiscal_year="FY1999")
