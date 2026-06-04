"""Tests for the bank/CC CSV import path (Issue #14).

Two halves, mirroring the seed-fixture split:

- **Pure** (no DB, always run): format auto-detection, column mapping, the bank/card
  sign conventions, 摘要ベースの相手科目推定 + suspense fallback, deterministic
  ``import_hash``, amount/date parsing, machine-readable parse errors, and that every
  planned entry balances. Plus the committed golden snapshots over fixed CSV fixtures
  (固定 CSV フィクスチャでゴールデン検証) and the 誤上書き防止 guard.
- **DB-backed** (skipped without ``AI_BOOKS_DB_URL``): the full round-trip — drafts are
  created through the production validation path, re-importing the same file creates no
  duplicates, undetermined counter-accounts land in 仮払金/仮受金, and the MCP tool
  surfaces failures as a ``ToolError`` JSON payload.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from fastmcp.exceptions import ToolError

from ai_books import db, server
from ai_books.errors import CsvImportError
from ai_books.models import EntrySide, ImportSummary
from ai_books.seed.accounts import seed_accounts
from ai_books.services import CsvImportService, plan_import
from ai_books.services.csv_import import (
    SUSPENSE_CREDIT_CODE,
    SUSPENSE_DEBIT_CODE,
    _import_hash,
)
from tests.fixtures.csv import (
    CSV_FIXTURES,
    CSV_FIXTURES_BY_NAME,
    diff_snapshots,
    load_golden,
    plan_snapshot,
)
from tests.fixtures.csv import golden as csv_golden

# --- sample CSV strings (self-contained for the pure tests) -------------------

BANK_CSV = (
    "日付,摘要,出金,入金,残高\n"
    "2026/04/05,東京電力 電気料金,8500,,191500\n"
    "2026/04/15,振込 ABC商事 売上代金,,150000,338500\n"
    "2026/04/20,謎の支払,5000,,333500\n"
)

CARD_CSV = (
    "利用日,利用店名,利用金額\n"
    "2026/05/02,AMAZON.CO.JP,4980\n"
    "2026/05/08,スターバックス,680\n"
    "2026/05/20,返金 AMAZON,-1500\n"
)


# --- format detection ---------------------------------------------------------


def test_auto_detects_bank_format() -> None:
    plan = plan_import(BANK_CSV, "1141")
    assert len(plan) == 3
    assert all(p.entry.source == "csv:generic_bank" for p in plan)


def test_auto_detects_card_format() -> None:
    plan = plan_import(CARD_CSV, "2130")
    assert len(plan) == 3
    assert all(p.entry.source == "csv:generic_card" for p in plan)


def test_explicit_format_mismatch_is_rejected() -> None:
    with pytest.raises(CsvImportError, match="does not match"):
        plan_import(BANK_CSV, "1141", csv_format="generic_card")


def test_unknown_format_name_is_rejected() -> None:
    with pytest.raises(CsvImportError, match="unknown csv_format"):
        plan_import(BANK_CSV, "1141", csv_format="nonesuch")


def test_missing_header_is_rejected() -> None:
    with pytest.raises(CsvImportError, match="ヘッダ"):
        plan_import("", "1141")


# --- bank mapping -------------------------------------------------------------


def test_bank_deposit_debits_the_account() -> None:
    # A 入金 increases the (asset) bank account → debit it; counter is credited.
    plan = plan_import(BANK_CSV, "1141")
    deposit = plan[1]  # 振込 ABC商事 売上代金 150000
    debit = next(line for line in deposit.entry.lines if line.side is EntrySide.DEBIT)
    credit = next(line for line in deposit.entry.lines if line.side is EntrySide.CREDIT)
    assert debit.account_code == "1141"
    assert debit.amount == Decimal("150000")
    assert credit.account_code == "4110"  # 売上高 inferred from 売上/振込
    assert deposit.to_suspense is False


def test_bank_withdrawal_credits_the_account() -> None:
    # A 出金 decreases the (asset) bank account → credit it; counter is debited.
    plan = plan_import(BANK_CSV, "1141")
    expense = plan[0]  # 東京電力 8500
    credit = next(line for line in expense.entry.lines if line.side is EntrySide.CREDIT)
    debit = next(line for line in expense.entry.lines if line.side is EntrySide.DEBIT)
    assert credit.account_code == "1141"
    assert debit.account_code == "7130"  # 水道光熱費 inferred from 電気/電力
    assert expense.to_suspense is False


def test_bank_unmatched_outflow_falls_to_debit_suspense() -> None:
    plan = plan_import(BANK_CSV, "1141")
    unknown = plan[2]  # 謎の支払 — no keyword matches
    assert unknown.to_suspense is True
    debit = next(line for line in unknown.entry.lines if line.side is EntrySide.DEBIT)
    assert debit.account_code == SUSPENSE_DEBIT_CODE  # 仮払金


# --- card mapping -------------------------------------------------------------


def test_card_charge_credits_the_payable() -> None:
    # A card 利用 (charge) increases 未払金 → credit it; the expense is debited.
    plan = plan_import(CARD_CSV, "2130")
    charge = plan[0]  # AMAZON 4980
    credit = next(line for line in charge.entry.lines if line.side is EntrySide.CREDIT)
    debit = next(line for line in charge.entry.lines if line.side is EntrySide.DEBIT)
    assert credit.account_code == "2130"
    assert debit.account_code == "7200"  # 消耗品費 inferred from AMAZON
    assert charge.to_suspense is False


def test_card_refund_reverses_the_charge() -> None:
    plan = plan_import(CARD_CSV, "2130")
    refund = plan[2]  # 返金 AMAZON -1500
    debit = next(line for line in refund.entry.lines if line.side is EntrySide.DEBIT)
    credit = next(line for line in refund.entry.lines if line.side is EntrySide.CREDIT)
    assert debit.account_code == "2130"  # payable reduced
    assert credit.account_code == SUSPENSE_CREDIT_CODE  # 仮受金 (no inflow rule matched)
    assert refund.to_suspense is True
    assert debit.amount == Decimal("1500")  # abs of the negative


def test_card_unmatched_charge_falls_to_debit_suspense() -> None:
    plan = plan_import(CARD_CSV, "2130")
    unknown = plan[1]  # スターバックス — no keyword
    assert unknown.to_suspense is True
    debit = next(line for line in unknown.entry.lines if line.side is EntrySide.DEBIT)
    assert debit.account_code == SUSPENSE_DEBIT_CODE  # 仮払金


# --- balance invariant --------------------------------------------------------


@pytest.mark.parametrize(("csv_text", "code"), [(BANK_CSV, "1141"), (CARD_CSV, "2130")])
def test_every_planned_entry_balances(csv_text: str, code: str) -> None:
    for planned in plan_import(csv_text, code):
        debit = sum(
            (line.amount for line in planned.entry.lines if line.side is EntrySide.DEBIT),
            Decimal(0),
        )
        credit = sum(
            (line.amount for line in planned.entry.lines if line.side is EntrySide.CREDIT),
            Decimal(0),
        )
        assert debit == credit
        assert len(planned.entry.lines) == 2


# --- import_hash --------------------------------------------------------------


def test_import_hash_is_deterministic_and_unique_per_row() -> None:
    first = plan_import(BANK_CSV, "1141")
    second = plan_import(BANK_CSV, "1141")
    assert [p.import_hash for p in first] == [p.import_hash for p in second]
    assert len({p.import_hash for p in first}) == len(first)  # no collisions


def test_import_hash_varies_with_account_code() -> None:
    a = plan_import(BANK_CSV, "1141")
    b = plan_import(BANK_CSV, "1142")
    assert a[0].import_hash != b[0].import_hash


def test_identical_rows_get_distinct_hashes() -> None:
    # Two genuinely identical transactions in one file must not collapse into one.
    dup_csv = (
        "日付,摘要,出金,入金,残高\n2026/04/05,コーヒー,500,,1000\n2026/04/05,コーヒー,500,,500\n"
    )
    plan = plan_import(dup_csv, "1141")
    assert len(plan) == 2
    assert plan[0].import_hash != plan[1].import_hash  # row index + 残高 disambiguate


def test_import_hash_helper_is_stable() -> None:
    from ai_books.services.csv_import import PRESETS_BY_NAME

    fmt = PRESETS_BY_NAME["generic_bank"]
    from datetime import date

    h = _import_hash(
        "1141", fmt, 0, date(2026, 4, 5), EntrySide.CREDIT, Decimal("8500"), "x", "100"
    )
    assert isinstance(h, str)
    assert len(h) == 64  # sha256 hex


# --- amount / date parsing ----------------------------------------------------


def test_amount_strips_separators_and_symbols() -> None:
    csv_text = '日付,摘要,出金,入金,残高\n2026/04/05,X,"¥1,234",,0\n'
    plan = plan_import(csv_text, "1141")
    assert plan[0].entry.lines[0].amount == Decimal("1234")


@pytest.mark.parametrize("raw", ["2026/04/05", "2026-04-05", "2026.04.05", "2026年4月5日"])
def test_date_formats_are_accepted(raw: str) -> None:
    from datetime import date

    csv_text = f"日付,摘要,出金,入金,残高\n{raw},X,100,,0\n"
    plan = plan_import(csv_text, "1141")
    assert plan[0].entry.entry_date == date(2026, 4, 5)


def test_bad_date_is_rejected_with_row_number() -> None:
    csv_text = "日付,摘要,出金,入金,残高\nnot-a-date,X,100,,0\n"
    with pytest.raises(CsvImportError) as excinfo:
        plan_import(csv_text, "1141")
    assert excinfo.value.row == 1
    assert excinfo.value.to_dict()["error"] == "csv_import_error"


def test_bad_amount_is_rejected() -> None:
    csv_text = "利用日,利用店名,利用金額\n2026/05/02,X,abc\n"
    with pytest.raises(CsvImportError, match="金額"):
        plan_import(csv_text, "2130")


def test_row_with_no_amount_is_rejected() -> None:
    csv_text = "日付,摘要,出金,入金,残高\n2026/04/05,X,,,500\n"
    with pytest.raises(CsvImportError, match="いずれにも金額がありません"):
        plan_import(csv_text, "1141")


def test_row_with_both_amounts_is_rejected() -> None:
    csv_text = "日付,摘要,出金,入金,残高\n2026/04/05,X,100,200,500\n"
    with pytest.raises(CsvImportError, match="両方に金額"):
        plan_import(csv_text, "1141")


def test_blank_trailing_line_is_skipped() -> None:
    csv_text = "日付,摘要,出金,入金,残高\n2026/04/05,X,100,,0\n,,,,\n"
    plan = plan_import(csv_text, "1141")
    assert len(plan) == 1


def test_bom_prefixed_header_still_maps() -> None:
    csv_text = "﻿日付,摘要,出金,入金,残高\n2026/04/05,X,100,,0\n"
    plan = plan_import(csv_text, "1141")
    assert len(plan) == 1
    assert plan[0].entry.entry_date.isoformat() == "2026-04-05"


# --- ImportSummary model ------------------------------------------------------


def test_import_summary_totals_must_reconcile() -> None:
    with pytest.raises(ValueError, match="must equal total_rows"):
        ImportSummary(total_rows=5, imported=2, duplicates=1, unassigned=0, entry_ids=[1, 2])


def test_import_summary_entry_ids_match_imported() -> None:
    with pytest.raises(ValueError, match="entry_ids"):
        ImportSummary(total_rows=2, imported=2, duplicates=0, unassigned=0, entry_ids=[1])


def test_import_summary_unassigned_bounded_by_imported() -> None:
    with pytest.raises(ValueError, match="unassigned"):
        ImportSummary(total_rows=1, imported=1, duplicates=0, unassigned=2, entry_ids=[1])


# --- golden snapshots ---------------------------------------------------------


@pytest.mark.parametrize("fixture", CSV_FIXTURES, ids=lambda fx: fx.name)
def test_committed_golden_is_up_to_date(fixture: Any) -> None:
    # AC: 固定 CSV フィクスチャでゴールデン検証 — the committed snapshot matches the plan.
    fresh = plan_snapshot(fixture)
    committed = load_golden(fixture.name)
    problems = diff_snapshots(committed, fresh)
    assert problems == [], (
        f"golden/{fixture.name}.json is stale; regenerate with "
        "`python -m tests.fixtures.csv --update`:\n  - " + "\n  - ".join(problems)
    )


def test_golden_updates_only_via_explicit_flag(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AC: 誤上書き防止 — without --update nothing is written; with it, the files appear.
    monkeypatch.setattr(csv_golden, "GOLDEN_DIR", tmp_path)
    assert csv_golden.main([]) == 1  # missing goldens → stale, writes nothing
    assert not list(tmp_path.glob("*.json"))

    assert csv_golden.main(["--update"]) == 0
    assert {p.stem for p in tmp_path.glob("*.json")} == set(CSV_FIXTURES_BY_NAME)
    assert csv_golden.main([]) == 0


# --- DB-backed round-trip -----------------------------------------------------

_DB = pytest.mark.skipif(
    not os.environ.get(db.DB_URL_ENV),
    reason=f"{db.DB_URL_ENV} not set; skipping DB-backed CSV-import tests",
)
_TEST_SCHEMA = "ai_books_layer_test"


@_DB
def test_import_creates_balanced_drafts_and_summary(
    migrated_conn: psycopg.Connection[Any],
) -> None:
    seed_accounts(migrated_conn)
    summary = CsvImportService(migrated_conn).import_csv(
        BANK_CSV, account_code="1141", actor="importer"
    )
    assert isinstance(summary, ImportSummary)
    assert summary.total_rows == 3
    assert summary.imported == 3
    assert summary.duplicates == 0
    assert summary.unassigned == 1  # 謎の支払 → 仮払金
    assert len(summary.entry_ids) == 3

    from ai_books.db.repository import JournalRepository
    from ai_books.models import EntryStatus

    repo = JournalRepository(migrated_conn)
    for entry_id in summary.entry_ids:
        stored = repo.get_entry(entry_id)
        assert stored is not None
        assert stored.status is EntryStatus.DRAFT  # 取込は必ず draft 起票
        assert stored.is_balanced
        assert stored.source == "csv:generic_bank"
        assert stored.import_hash is not None


@_DB
def test_reimport_same_file_creates_no_duplicates(
    migrated_conn: psycopg.Connection[Any],
) -> None:
    # AC: 同一ファイル再取込で重複が作られない.
    seed_accounts(migrated_conn)
    service = CsvImportService(migrated_conn)
    first = service.import_csv(BANK_CSV, account_code="1141")
    assert first.imported == 3

    second = service.import_csv(BANK_CSV, account_code="1141")
    assert second.imported == 0
    assert second.duplicates == 3
    assert second.entry_ids == []

    count = migrated_conn.execute("SELECT count(*) AS n FROM journal_entries").fetchone()
    assert count is not None
    assert count["n"] == 3  # still only the first import's entries


@_DB
def test_unassigned_lands_in_suspense_accounts(
    migrated_conn: psycopg.Connection[Any],
) -> None:
    # AC: 相手科目未確定の明細は suspense 科目に退避され、後で振替できる.
    seed_accounts(migrated_conn)
    CsvImportService(migrated_conn).import_csv(CARD_CSV, account_code="2130")

    from ai_books.db.repository import AccountRepository, LedgerRepository

    accounts = AccountRepository(migrated_conn)
    ledger = LedgerRepository(migrated_conn)
    karibarai = accounts.get_by_code(SUSPENSE_DEBIT_CODE)
    kariuke = accounts.get_by_code(SUSPENSE_CREDIT_CODE)
    assert karibarai is not None
    assert karibarai.id is not None
    assert kariuke is not None
    assert kariuke.id is not None

    # スターバックス 680 → 仮払金(借), 返金 1500 → 仮受金(貸).
    assert ledger.account_balance(karibarai.id).balance == Decimal("680")
    assert ledger.account_balance(kariuke.id).balance == Decimal("1500")


@_DB
def test_import_audits_each_entry(migrated_conn: psycopg.Connection[Any]) -> None:
    seed_accounts(migrated_conn)
    CsvImportService(migrated_conn).import_csv(BANK_CSV, account_code="1141", actor="importer")
    row = migrated_conn.execute(
        "SELECT count(*) AS n FROM audit_logs WHERE tool_name = 'import_transactions_csv'"
    ).fetchone()
    assert row is not None
    assert row["n"] == 3


@_DB
def test_unknown_account_rolls_back_whole_import(
    migrated_conn: psycopg.Connection[Any],
) -> None:
    # The chart is NOT seeded, so the very first entry's account resolution fails;
    # the batch transaction rolls every row back (all-or-nothing).
    from ai_books.errors import RecordNotFoundError

    with pytest.raises(RecordNotFoundError):
        CsvImportService(migrated_conn).import_csv(BANK_CSV, account_code="1141")
    count = migrated_conn.execute("SELECT count(*) AS n FROM journal_entries").fetchone()
    assert count is not None
    assert count["n"] == 0


# --- MCP tool wiring ----------------------------------------------------------


@pytest.fixture
def patched_connect(
    monkeypatch: pytest.MonkeyPatch, migrated_conn: psycopg.Connection[Any]
) -> None:
    """Point ``db.connect`` (used by the tool) at the throwaway test schema."""
    from psycopg.rows import dict_row

    seed_accounts(migrated_conn)

    def _connect(db_url: str | None = None) -> psycopg.Connection[Any]:
        conn = psycopg.connect(db.get_db_url(), row_factory=dict_row)
        conn.execute(f"SET search_path TO {_TEST_SCHEMA}, public")
        return conn

    monkeypatch.setattr(db, "connect", _connect)


@_DB
def test_tool_import_returns_summary(patched_connect: None) -> None:
    summary = server.import_transactions_csv(BANK_CSV, account_code="1141")
    assert isinstance(summary, ImportSummary)
    assert summary.imported == 3
    assert summary.unassigned == 1


@_DB
def test_tool_parse_error_raises_tool_error_with_payload(patched_connect: None) -> None:
    import json

    bad_csv = "日付,摘要,出金,入金,残高\nnot-a-date,X,100,,0\n"
    with pytest.raises(ToolError) as excinfo:
        server.import_transactions_csv(bad_csv, account_code="1141")
    payload = json.loads(str(excinfo.value))
    assert payload["error"] == "csv_import_error"
    assert payload["row"] == "1"
