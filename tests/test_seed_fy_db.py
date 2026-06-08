"""DB-backed tests: load the synthetic year and check it against golden over a real DB.

Skips when ``AI_BOOKS_DB_URL`` is unset (so ``./scripts/verify.sh`` stays green without a
live Postgres); runs in CI on the throwaway-schema ``migrated_conn`` fixture. Covers the
acceptance criteria that need a round-trip: idempotent load, the DB books balancing, the
SQL-derived trial balance matching the committed golden, and the existing read API (#15)
returning the same balances downstream report Issues will reuse.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from ai_books import db
from ai_books.db.repository import AccountRepository, JournalRepository, LedgerRepository
from ai_books.etax import etax_export_snapshot
from ai_books.models import EntryStatus
from ai_books.reports import (
    balance_sheet_snapshot,
    financial_statements_snapshot,
    general_ledger_snapshot,
    journal_book_snapshot,
    profit_and_loss_snapshot,
    real_estate_income_snapshot,
    worksheet_snapshot,
)
from tests.fixtures.seed_fy import (
    FY_ENTRIES,
    RE_ENTRIES,
    balance_sheet_from_db,
    diff_snapshots,
    etax_export_from_db,
    financial_statements_from_db,
    general_ledger_from_db,
    journal_book_from_db,
    load_fiscal_year,
    load_golden,
    profit_and_loss_from_db,
    real_estate_income_from_db,
    trial_balance_from_db,
    trial_balance_snapshot,
    worksheet_from_db,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get(db.DB_URL_ENV),
    reason=f"{db.DB_URL_ENV} not set; skipping DB-backed seed_fy tests",
)


def _entry_count(conn: psycopg.Connection[Any]) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM journal_entries")
        row = cur.fetchone()
        assert row is not None
        return int(row["n"])


def test_load_inserts_full_year(migrated_conn: psycopg.Connection[Any]) -> None:
    result = load_fiscal_year(migrated_conn)
    assert result.inserted == len(FY_ENTRIES)
    assert result.total == len(FY_ENTRIES)
    assert _entry_count(migrated_conn) == len(FY_ENTRIES)


def test_load_is_idempotent(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC: seed 投入が冪等 — re-loading inserts nothing and does not duplicate.
    load_fiscal_year(migrated_conn)
    after_first = _entry_count(migrated_conn)

    second = load_fiscal_year(migrated_conn)
    assert second.inserted == 0
    assert second.skipped == len(FY_ENTRIES)
    assert _entry_count(migrated_conn) == after_first == len(FY_ENTRIES)


def test_load_is_atomic_on_midbatch_failure(
    migrated_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The batch loads under one transaction: a failure partway through must roll back
    # every already-inserted entry, never leaving the books partially seeded.
    real_insert = JournalRepository.insert_entry
    calls = {"n": 0}

    def flaky_insert(self: JournalRepository, entry: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("simulated mid-batch failure")
        return real_insert(self, entry)

    monkeypatch.setattr(JournalRepository, "insert_entry", flaky_insert)
    with pytest.raises(RuntimeError, match="mid-batch failure"):
        load_fiscal_year(migrated_conn)

    assert _entry_count(migrated_conn) == 0  # the two earlier inserts were rolled back


def test_db_books_balance_overall(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC: 借貸が全体でバランスする, verified against the real stored rows.
    load_fiscal_year(migrated_conn)
    trial_balance = trial_balance_from_db(migrated_conn)
    assert trial_balance.is_balanced
    assert trial_balance.total_debit == trial_balance.total_credit


def test_db_trial_balance_matches_golden(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC: golden 比較ハーネスが pytest から動く — DB round-trip equals the frozen snapshot.
    load_fiscal_year(migrated_conn)
    actual = trial_balance_snapshot(trial_balance_from_db(migrated_conn))
    expected = load_golden("trial_balance")
    problems = diff_snapshots(expected, actual)
    assert problems == [], "DB trial balance diverged from golden:\n  - " + "\n  - ".join(problems)


def test_golden_only_counts_posted_entries(migrated_conn: psycopg.Connection[Any]) -> None:
    # The harness reads 記帳確定 only; a stray draft must not perturb the snapshot.
    load_fiscal_year(migrated_conn)
    migrated_conn.execute(
        "UPDATE journal_entries SET status = 'draft' WHERE voucher_no = %s",
        ("FY2025-004",),
    )
    posted = trial_balance_from_db(migrated_conn, status=EntryStatus.POSTED)
    expected = load_golden("trial_balance")
    # 売上 (現金) FY2025-004 is now a draft, so 売上高 and 現金 drop out of the posted books.
    problems = diff_snapshots(expected, trial_balance_snapshot(posted))
    assert any("4110" in problem for problem in problems)


def test_read_api_balances_match_golden(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC: 後続レポート Issue がこの seed/golden を再利用できる — the existing #15 read API
    # (get_account_balance) returns exactly the golden balances, so reports built on it agree.
    load_fiscal_year(migrated_conn)
    golden_rows = {row["code"]: row for row in load_golden("trial_balance")["rows"]}
    accounts = AccountRepository(migrated_conn)
    ledger = LedgerRepository(migrated_conn)

    for code in ("4110", "1160", "7250", "5130", "2120"):
        account = accounts.get_by_code(code)
        assert account is not None
        assert account.id is not None
        balance = ledger.account_balance(account.id, status=EntryStatus.POSTED)
        assert balance.balance == Decimal(golden_rows[code]["balance"])
        assert balance.debit_total == Decimal(golden_rows[code]["debit_total"])
        assert balance.credit_total == Decimal(golden_rows[code]["credit_total"])


# --- 決算書: 損益計算書 (Issue #20) --------------------------------------------


def test_db_profit_and_loss_matches_golden(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC (#20): the DB-read 損益計算書 (段階利益・表示区分集約) equals the frozen golden, so the
    # SQL aggregation and the offline reduction agree end to end (出力が #17 の golden と一致).
    load_fiscal_year(migrated_conn)
    actual = profit_and_loss_snapshot(profit_and_loss_from_db(migrated_conn))
    expected = load_golden("profit_and_loss")
    problems = diff_snapshots(expected, actual)
    assert problems == [], "DB profit & loss diverged from golden:\n  - " + "\n  - ".join(problems)


def test_db_profit_and_loss_net_income_reconciles(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC (#20): 各段階利益が試算表と整合, verified against the real stored rows — the P/L
    # 当期純利益 equals the trial balance's Σ収益 - Σ費用 computed via the read engine.
    load_fiscal_year(migrated_conn)
    pl = profit_and_loss_from_db(migrated_conn)
    assert pl.is_consistent
    assert pl.net_income == Decimal("-580500")
    assert pl.cost_of_goods_sold.subtotal == Decimal("1490000")  # 製造原価を含む 売上原価
    assert pl.unclassified == []


# --- 帳簿レポート: 仕訳帳 / 総勘定元帳 (Issue #19) ------------------------------


def _entry_id(conn: psycopg.Connection[Any], voucher_no: str) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM journal_entries WHERE voucher_no = %s", (voucher_no,))
        row = cur.fetchone()
        assert row is not None
        return int(row["id"])


def test_db_journal_book_matches_golden(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC (#19): the DB-read 仕訳帳 equals the frozen golden (storage round-trip is faithful).
    load_fiscal_year(migrated_conn)
    actual = journal_book_snapshot(journal_book_from_db(migrated_conn))
    expected = load_golden("journal_book")
    problems = diff_snapshots(expected, actual)
    assert problems == [], "DB journal book diverged from golden:\n  - " + "\n  - ".join(problems)


def test_db_general_ledger_matches_golden(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC (#19): the DB-read 総勘定元帳 (running balances, 相手科目) equals the frozen golden.
    load_fiscal_year(migrated_conn)
    actual = general_ledger_snapshot(general_ledger_from_db(migrated_conn))
    expected = load_golden("general_ledger")
    problems = diff_snapshots(expected, actual)
    assert problems == [], "DB general ledger diverged from golden:\n  - " + "\n  - ".join(problems)


# --- 精算表 (Issue #22) --------------------------------------------------------


def test_db_worksheet_matches_golden(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC (#22): the DB-read 精算表 (source-split into 残高試算表 / 修正記入, routed to PL/BS)
    # equals the frozen golden — the SQL path agrees with the offline reduction.
    load_fiscal_year(migrated_conn)
    actual = worksheet_snapshot(worksheet_from_db(migrated_conn))
    expected = load_golden("worksheet")
    problems = diff_snapshots(expected, actual)
    assert problems == [], "DB worksheet diverged from golden:\n  - " + "\n  - ".join(problems)


def test_db_worksheet_is_self_balancing(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC (#22): 当期純利益 が PL 欄と BS 欄で一致, verified over the real stored rows.
    load_fiscal_year(migrated_conn)
    ws = worksheet_from_db(migrated_conn)
    assert ws.is_consistent
    assert ws.pl_net_income == ws.bs_net_income == ws.net_income


# --- 決算書: 貸借対照表 (Issue #21) --------------------------------------------


def test_db_balance_sheet_matches_golden(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC (#21): the SQL-derived 貸借対照表 (試算表 → 表示区分 集約) equals the frozen golden,
    # so the production engine and the offline reduction agree (Decimal/符号/集約 round-trip).
    load_fiscal_year(migrated_conn)
    actual = balance_sheet_snapshot(balance_sheet_from_db(migrated_conn))
    expected = load_golden("balance_sheet")
    problems = diff_snapshots(expected, actual)
    assert problems == [], "DB balance sheet diverged from golden:\n  - " + "\n  - ".join(problems)


def test_db_balance_sheet_balances_and_net_income_matches_pl(
    migrated_conn: psycopg.Connection[Any],
) -> None:
    # AC (#21): 貸借一致 over real stored rows, and 当期純利益 equals 収益残高 - 費用残高 of the
    # SQL 試算表 (what the 損益計算書 #20 will report for the same period/status).
    load_fiscal_year(migrated_conn)
    repo = LedgerRepository(migrated_conn)
    balance_sheet = repo.balance_sheet(status=EntryStatus.POSTED)
    assert balance_sheet.is_balanced
    assert balance_sheet.total_assets == Decimal("3319500")

    tb_rows = {row.code: row for row in repo.trial_balance(status=EntryStatus.POSTED).rows}
    revenue = sum(
        (r.balance for r in tb_rows.values() if r.code.startswith("4") or r.code == "8110"),
        Decimal(0),
    )
    expense = sum(
        (r.balance for r in tb_rows.values() if r.code[0] in {"5", "6", "7"} or r.code == "8210"),
        Decimal(0),
    )
    assert balance_sheet.net_income == revenue - expense == Decimal("-580500")


def test_journal_book_traces_voided_entries(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC (#19): 取消/訂正仕訳が履歴として追える — a 取消 伝票 leaves the active books but stays
    # auditable via status='voided', carrying its 取消理由.
    load_fiscal_year(migrated_conn)
    repo = JournalRepository(migrated_conn)
    repo.mark_voided(_entry_id(migrated_conn, "FY2025-004"), "重複計上のため取消")

    active = repo.journal_book(status=EntryStatus.POSTED)
    assert all(e.voucher_no != "FY2025-004" for e in active.entries)
    # Default (no status) also excludes 取消 so cancelled entries never silently count.
    assert all(e.voucher_no != "FY2025-004" for e in repo.journal_book().entries)

    voided = [e for e in repo.journal_book(status=EntryStatus.VOIDED).entries]
    assert [e.voucher_no for e in voided] == ["FY2025-004"]
    assert voided[0].status is EntryStatus.VOIDED
    assert voided[0].void_reason == "重複計上のため取消"


def test_general_ledger_drops_voided_from_balances(migrated_conn: psycopg.Connection[Any]) -> None:
    # A 取消 伝票 must no longer move the 総勘定元帳 running balance.
    load_fiscal_year(migrated_conn)
    repo = LedgerRepository(migrated_conn)

    def cash_closing() -> Decimal:
        book = repo.general_ledger(status=EntryStatus.POSTED)
        return next(a for a in book.accounts if a.code == "1110").closing_balance

    before = cash_closing()
    # FY2025-004 is the 現金 sale (+220,000); voiding it drops that from 現金.
    JournalRepository(migrated_conn).mark_voided(
        _entry_id(migrated_conn, "FY2025-004"), "取消テスト"
    )
    assert cash_closing() == before - Decimal("220000")


# --- 青色申告決算書 (Issue #23) ------------------------------------------------


def test_db_financial_statements_matches_golden(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC (#23): the DB-read 決算書 (PL/BS + 月別売上・仕入 / 減価償却 / 製造原価 の内訳) equals the
    # frozen golden, so the SQL aggregation and the offline reduction agree end to end.
    load_fiscal_year(migrated_conn)
    actual = financial_statements_snapshot(financial_statements_from_db(migrated_conn))
    expected = load_golden("financial_statements")
    problems = diff_snapshots(expected, actual)
    assert problems == [], "DB financial statements diverged from golden:\n  - " + "\n  - ".join(
        problems
    )


def test_db_financial_statements_reconciles(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC (#23): 各内訳合計が PL/BS と一致 + 製造原価が売上原価と整合 + 減価償却が固定資産データと
    # 整合, verified over the real stored rows.
    load_fiscal_year(migrated_conn)
    fs = financial_statements_from_db(migrated_conn)
    assert fs.is_consistent
    # 月別売上 → 売上高, 製造原価 → 売上原価, 減価償却 → PL 減価償却費 & BS 固定資産簿価.
    assert fs.monthly.sales_total == fs.profit_and_loss.sales.subtotal == Decimal("1650000")
    assert fs.manufacturing_cost.cost_of_goods_manufactured == Decimal("940000")
    assert (
        fs.profit_and_loss.cost_of_goods_sold.subtotal
        == fs.merchandise_cost_of_sales + fs.manufacturing_cost.cost_of_goods_manufactured
    )
    assert fs.depreciation.total_depreciation == fs.depreciation.expense_total == Decimal("300000")
    assert fs.balance_sheet.is_balanced


# --- e-Tax 取込データ (Issue #24) ----------------------------------------------


def test_db_etax_export_matches_golden(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC (#24): the e-Tax 取込データ mapped from the DB-read 決算書 equals the frozen golden, so the
    # storage round-trip does not perturb the export (出力が #17 の golden と一致).
    load_fiscal_year(migrated_conn)
    actual = etax_export_snapshot(etax_export_from_db(migrated_conn))
    expected = load_golden("etax_export")
    problems = diff_snapshots(expected, actual)
    assert problems == [], "DB e-Tax export diverged from golden:\n  - " + "\n  - ".join(problems)


def test_db_etax_export_passes_schema_validation(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC (#24): 出力がフォーマット仕様のスキーマ検証を通る — build over the real stored rows raises
    # no EtaxValidationError, and the export is non-empty and 整数円.
    load_fiscal_year(migrated_conn)
    export = etax_export_from_db(migrated_conn)  # raises EtaxValidationError on a schema fault
    assert export.records
    amounts = [r.value for r in export.records if r.kind.value == "amount"]
    assert amounts
    assert all("." not in value for value in amounts)


def test_db_etax_xtx_matches_golden(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC (#79): seed_fy → 実様式の .xtx — the DB-read 決算書 renders to the same KOA210 .xtx as the
    # offline golden, so the storage round-trip does not perturb the real e-Tax 交換ファイル.
    from ai_books.etax import render_etax_xtx

    load_fiscal_year(migrated_conn)
    actual = {
        "report": "etax_xtx",
        "form_id": "KOA210",
        "version": "11.0",
        "namespace": "http://xml.e-tax.nta.go.jp/XSD/shotoku",
        "xtx_lines": render_etax_xtx(etax_export_from_db(migrated_conn)).splitlines(),
    }
    problems = diff_snapshots(load_golden("etax_xtx"), actual)
    assert problems == [], "DB e-Tax .xtx diverged from golden:\n  - " + "\n  - ".join(problems)


def test_db_etax_xtx_passes_official_xsd(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC (#79): 生成 .xtx が 国税庁 .xsd のスキーマ検証を pass — over the real stored rows.
    # Gated on the fetched .xsd (国税庁 著作物のため非同梱); CI's etax-xsd job runs the fetch.
    from ai_books.etax import render_etax_xtx
    from tests.etax_xsd import skip_reason, validate_xtx, xsd_available

    if not xsd_available():
        pytest.skip(skip_reason())
    load_fiscal_year(migrated_conn)
    errors = validate_xtx(render_etax_xtx(etax_export_from_db(migrated_conn)))
    assert errors == [], f"DB .xtx failed official XSD: {errors}"


# --- 不動産所得 収入側 内訳 (KOA220 data-supply, Issue #124) --------------------


def test_db_real_estate_income_matches_golden(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC (#124): the DB-read 不動産所得 収入側 内訳 equals the frozen golden, so the SQL aggregation
    # (受取家賃 / 地代家賃 / 借入金 残高) and the offline reduction agree — the 二経路一致.
    load_fiscal_year(migrated_conn, RE_ENTRIES)
    actual = real_estate_income_snapshot(real_estate_income_from_db(migrated_conn))
    expected = load_golden("real_estate_income")
    problems = diff_snapshots(expected, actual)
    assert problems == [], "DB real estate income diverged from golden:\n  - " + "\n  - ".join(
        problems
    )


def test_db_real_estate_income_reconciles(migrated_conn: psycopg.Connection[Any]) -> None:
    # AC (#124): 各内訳の計が内訳行と一致 + 本年中の収入金額が受取家賃残高と整合, over real stored rows.
    load_fiscal_year(migrated_conn, RE_ENTRIES)
    re = real_estate_income_from_db(migrated_conn)
    assert re.is_consistent
    assert re.gross_income == Decimal("2260000")
    assert re.rent_paid_total == Decimal("240000")
    assert re.loan_year_end_balance_total == Decimal("7500000")
    assert re.loan_interest_total == Decimal("80000")
