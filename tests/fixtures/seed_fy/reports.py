"""Reports over the synthetic year — pure reducers vs. the production engines.

The golden harness compares two independent computations of the *same* report:

* a pure-Python reducer over :data:`~tests.fixtures.seed_fy.dataset.FY_ENTRIES` (no database) —
  ``*_from_dataset``. These generate the committed golden files.
* the **production** engine (:class:`ai_books.db.repository.JournalRepository` /
  :class:`~ai_books.db.repository.LedgerRepository`) run over the same data after it has been
  loaded into Postgres — ``*_from_db``. These are what the pytest harness checks against golden.

The two paths compute independently (a Python reduction vs. SQL) but share the one signing rule
in :func:`ai_books.ledger.balance_from_totals`, so a mismatch surfaces a storage/aggregation bug
(Decimal lost, a line dropped, a sign flipped) rather than a tautology. Routing every ``*_from_db``
through the production engine means the golden tests *are* the acceptance checks for the report
Issues (#18 集計 / #19 帳簿) — there is no second SQL implementation to drift (二重実装を避ける).

A trial balance is the foundation every later report (#20 PL / #21 BS / #22 精算表 / #23 決算書)
builds on; the 仕訳帳 / 総勘定元帳 are the 青色申告で備える帳簿 themselves.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from ai_books import aggregation, ledger
from ai_books.db.repository import (
    AccountRepository,
    FiscalYearRepository,
    JournalRepository,
    LedgerRepository,
)
from ai_books.etax import build_etax_export
from ai_books.models import (
    YEAR_END_ADJUSTMENT_SOURCE,
    BalanceSheet,
    EntrySide,
    EntryStatus,
    EtaxExport,
    FinancialStatements,
    GeneralLedger,
    GeneralLedgerAccount,
    GeneralLedgerRow,
    JournalBook,
    JournalBookEntry,
    JournalBookLine,
    MonthlyTrend,
    NormalSide,
    ProfitAndLoss,
    StatementCategory,
    TrialBalance,
    TrialBalanceRow,
    Worksheet,
)

from .dataset import (
    FISCAL_YEAR,
    FY_END,
    FY_ENTRIES,
    FY_START,
    SeedEntry,
    account_name,
    account_type,
    normal_side,
    statement_category,
)

if TYPE_CHECKING:
    import psycopg


# ── 合計残高試算表 (trial balance, Issue #18) ─────────────────────────────────────


def _assemble(
    debit_by_code: dict[str, Decimal],
    credit_by_code: dict[str, Decimal],
    name_of: dict[str, str],
    normal_of: dict[str, NormalSide],
) -> TrialBalance:
    """Build a :class:`~ai_books.models.TrialBalance` from per-code sums, ordered by code."""
    rows: list[TrialBalanceRow] = []
    total_debit = Decimal(0)
    total_credit = Decimal(0)
    for code in sorted(debit_by_code.keys() | credit_by_code.keys()):
        debit = debit_by_code.get(code, Decimal(0))
        credit = credit_by_code.get(code, Decimal(0))
        balance = ledger.balance_from_totals(debit, credit, normal_of[code])
        rows.append(
            TrialBalanceRow(
                code=code,
                name=name_of[code],
                normal_balance=normal_of[code],
                debit_total=debit,
                credit_total=credit,
                balance=balance,
            )
        )
        total_debit += debit
        total_credit += credit
    return TrialBalance(rows=rows, total_debit=total_debit, total_credit=total_credit)


def trial_balance_from_dataset(entries: tuple[SeedEntry, ...] = FY_ENTRIES) -> TrialBalance:
    """Reduce the in-memory dataset into a trial balance — no database required."""
    debit_by_code: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    credit_by_code: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for entry in entries:
        for line in entry.lines:
            bucket = debit_by_code if line.side is EntrySide.DEBIT else credit_by_code
            bucket[line.account_code] += line.amount

    codes = debit_by_code.keys() | credit_by_code.keys()
    name_of = {code: account_name(code) for code in codes}
    normal_of = {code: normal_side(code) for code in codes}
    return _assemble(dict(debit_by_code), dict(credit_by_code), name_of, normal_of)


def trial_balance_from_db(
    conn: psycopg.Connection[Any], *, status: EntryStatus | None = EntryStatus.POSTED
) -> TrialBalance:
    """Aggregate ``journal_lines`` into a trial balance via the production engine.

    Delegates straight to :meth:`ai_books.db.repository.LedgerRepository.trial_balance` —
    the very code Issue #18 ships — so there is no second SQL implementation to drift
    (#18 calls for 二重実装を避ける). Independent of :func:`trial_balance_from_dataset`
    (SQL ``GROUP BY`` vs. in-memory reduction), so a divergence surfaces a storage or
    aggregation bug, and the golden test doubles as #18's acceptance check. ``status``
    filters which entries are summed (default: 記帳確定 only).
    """
    return LedgerRepository(conn).trial_balance(status=status)


# ── 月次推移 (monthly trend, Issue #18) ───────────────────────────────────────────
#: The accounts the monthly-trend golden snapshot fixes. Chosen to exercise both normal
#: sides and movement spread across the year: 普通預金 (most active asset), 売掛金 (掛売上
#: then 回収), 売上高 (credit-normal revenue), 地代家賃 (expense + 期末家事按分).
MONTHLY_TREND_ACCOUNTS: tuple[str, ...] = ("1141", "1160", "4110", "7250")


def monthly_trend_from_dataset(
    code: str, entries: tuple[SeedEntry, ...] = FY_ENTRIES
) -> MonthlyTrend:
    """Reduce the in-memory dataset into one account's 月次推移 — no database required.

    Sums the account's lines per calendar month and carries the balance forward through
    the production tiling/​signing helpers (:func:`ai_books.aggregation.month_windows` /
    :func:`~ai_books.aggregation.build_monthly_trend_points`), so the offline golden source
    and the DB path agree on *structure* while summing independently. The opening balance
    is whatever the account carried in from before 期首 (zero for every account in this
    dataset, since the 期首残高 仕訳 itself falls on 期首).
    """
    normal = normal_side(code)
    open_debit = Decimal(0)
    open_credit = Decimal(0)
    debit_by_month: dict[date, Decimal] = defaultdict(lambda: Decimal(0))
    credit_by_month: dict[date, Decimal] = defaultdict(lambda: Decimal(0))
    for entry in entries:
        for line in entry.lines:
            if line.account_code != code:
                continue
            if entry.entry_date < FY_START:
                if line.side is EntrySide.DEBIT:
                    open_debit += line.amount
                else:
                    open_credit += line.amount
                continue
            if entry.entry_date > FY_END:
                continue
            month_start = date(entry.entry_date.year, entry.entry_date.month, 1)
            month_bucket = debit_by_month if line.side is EntrySide.DEBIT else credit_by_month
            month_bucket[month_start] += line.amount

    amounts_by_month = {
        month: aggregation.MonthAmounts(
            debit_total=debit_by_month.get(month, Decimal(0)),
            credit_total=credit_by_month.get(month, Decimal(0)),
        )
        for month in debit_by_month.keys() | credit_by_month.keys()
    }
    opening_balance = ledger.balance_from_totals(open_debit, open_credit, normal)
    windows = aggregation.month_windows(FY_START, FY_END)
    points, closing_balance = aggregation.build_monthly_trend_points(
        windows, amounts_by_month, normal, opening_balance
    )
    return MonthlyTrend(
        account_id=0,  # offline reduction has no DB id; the snapshot omits it
        code=code,
        name=account_name(code),
        normal_balance=normal,
        fiscal_year=FISCAL_YEAR,
        start_date=FY_START,
        end_date=FY_END,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        points=points,
    )


def monthly_trend_from_db(
    conn: psycopg.Connection[Any], code: str, *, status: EntryStatus | None = EntryStatus.POSTED
) -> MonthlyTrend:
    """Compute one account's 月次推移 from the DB via the production engine.

    Resolves the account and the :data:`~tests.fixtures.seed_fy.dataset.FISCAL_YEAR` row
    (seeded by :func:`~tests.fixtures.seed_fy.loader.load_fiscal_year`) and delegates to
    :meth:`ai_books.db.repository.LedgerRepository.monthly_trend` — the code Issue #18 ships.
    """
    account = AccountRepository(conn).get_by_code(code)
    assert account is not None, f"account {code} not seeded"
    assert account.id is not None
    year = FiscalYearRepository(conn).get_by_name(FISCAL_YEAR)
    assert year is not None, f"fiscal year {FISCAL_YEAR} not seeded"
    return LedgerRepository(conn).monthly_trend(
        account.id,
        fiscal_year=year.name,
        start=year.start_date,
        end=year.end_date,
        status=status,
    )


# ── 損益計算書 (profit & loss, Issue #20) ─────────────────────────────────────────
# Same dual-path cross-check as the trial balance: a pure reduction of the in-memory dataset
# (used to generate the golden, no DB) and a DB-backed read through the production engine
# (checked against golden by the pytest harness). Both share the one signing rule and the one
# 表示区分 grouping (ai_books.aggregation.assemble_profit_and_loss), so a divergence surfaces a
# storage/aggregation bug rather than a tautology. The dataset is FY2025 exactly, so the offline
# reduction (all entries) and the DB read (bounded by the FY2025 期首/期末) cover the same data.


def profit_and_loss_from_dataset(entries: tuple[SeedEntry, ...] = FY_ENTRIES) -> ProfitAndLoss:
    """Reduce the in-memory dataset into a 損益計算書 — no database required.

    Sums each account's 借方 / 貸方 then hands the per-account footings (with 区分 / 表示区分
    borrowed from the canonical chart) to the production
    :func:`ai_books.aggregation.assemble_profit_and_loss`, so the offline golden source and the
    DB path agree on *structure* while summing independently.
    """
    debit_by_code: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    credit_by_code: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for entry in entries:
        for line in entry.lines:
            bucket = debit_by_code if line.side is EntrySide.DEBIT else credit_by_code
            bucket[line.account_code] += line.amount

    codes = sorted(debit_by_code.keys() | credit_by_code.keys())
    accounts = [
        aggregation.PlAccountTotals(
            code=code,
            name=account_name(code),
            account_type=account_type(code),
            statement_category=statement_category(code),
            normal_balance=normal_side(code),
            debit_total=debit_by_code.get(code, Decimal(0)),
            credit_total=credit_by_code.get(code, Decimal(0)),
        )
        for code in codes
    ]
    return aggregation.assemble_profit_and_loss(
        accounts, fiscal_year=FISCAL_YEAR, start_date=FY_START, end_date=FY_END
    )


def profit_and_loss_from_db(
    conn: psycopg.Connection[Any], *, status: EntryStatus | None = EntryStatus.POSTED
) -> ProfitAndLoss:
    """Compute the 損益計算書 from the DB via the production engine.

    Resolves the :data:`~tests.fixtures.seed_fy.dataset.FISCAL_YEAR` row (seeded by
    :func:`~tests.fixtures.seed_fy.loader.load_fiscal_year`) and delegates to
    :meth:`ai_books.db.repository.LedgerRepository.profit_and_loss` — the code Issue #20 ships.
    """
    year = FiscalYearRepository(conn).get_by_name(FISCAL_YEAR)
    assert year is not None, f"fiscal year {FISCAL_YEAR} not seeded"
    return LedgerRepository(conn).profit_and_loss(
        fiscal_year=year.name, start=year.start_date, end=year.end_date, status=status
    )


# ── 仕訳帳 / 総勘定元帳 (Issue #19) ─────────────────────────────────────────────
# Same dual-path cross-check as the trial balance: a pure reduction of the in-memory
# dataset (used to generate the golden, no DB) and a DB-backed read through the
# production repositories (checked against golden by the pytest harness). The dataset
# is POSTED on load, so both default to 記帳確定 over the full fiscal year.


def _counter_codes(entry: SeedEntry, code: str) -> list[str]:
    """The 相手科目コード of ``entry`` for the ledger account ``code`` (dedup, order kept).

    Mirrors :meth:`ai_books.db.repository.LedgerRepository._counter_accounts`: the other
    accounts in the same 伝票, in line order, with duplicates collapsed.
    """
    counters: list[str] = []
    for line in entry.lines:
        if line.account_code != code and line.account_code not in counters:
            counters.append(line.account_code)
    return counters


def journal_book_from_dataset(entries: tuple[SeedEntry, ...] = FY_ENTRIES) -> JournalBook:
    """Reduce the in-memory dataset into a 仕訳帳 — no database required.

    Entries are already in 取引日 → 伝票番号 order; each is POSTED (as the loader stores it).
    """
    book_entries: list[JournalBookEntry] = []
    total_debit = Decimal(0)
    total_credit = Decimal(0)
    for entry in entries:
        lines: list[JournalBookLine] = []
        for line in entry.lines:
            lines.append(
                JournalBookLine(
                    account_code=line.account_code,
                    account_name=account_name(line.account_code),
                    side=line.side,
                    amount=line.amount,
                )
            )
            if line.side is EntrySide.DEBIT:
                total_debit += line.amount
            else:
                total_credit += line.amount
        book_entries.append(
            JournalBookEntry(
                entry_date=entry.entry_date,
                voucher_no=entry.voucher_no,
                description=entry.description,
                status=EntryStatus.POSTED,
                lines=lines,
            )
        )
    return JournalBook(
        start_date=FY_START,
        end_date=FY_END,
        status=EntryStatus.POSTED,
        entries=book_entries,
        total_debit=total_debit,
        total_credit=total_credit,
    )


def general_ledger_from_dataset(entries: tuple[SeedEntry, ...] = FY_ENTRIES) -> GeneralLedger:
    """Reduce the in-memory dataset into a 総勘定元帳 — no database required.

    For each account (科目コード順) the lines that touch it are collected in chronological
    order and run through the shared :func:`ai_books.ledger.build_ledger_rows`, so the
    running balance matches the production read path exactly. Opening balances are zero
    (the year opens with the 期首残高 伝票, dated on ``FY_START``).
    """
    codes = sorted({line.account_code for entry in entries for line in entry.lines})
    accounts: list[GeneralLedgerAccount] = []
    for code in codes:
        normal = normal_side(code)
        raw_lines: list[ledger.RawLedgerLine] = []
        for index, entry in enumerate(entries):
            counters = _counter_codes(entry, code)
            for line_no, line in enumerate(entry.lines, start=1):
                if line.account_code != code:
                    continue
                raw_lines.append(
                    ledger.RawLedgerLine(
                        entry_id=index,
                        line_no=line_no,
                        entry_date=entry.entry_date,
                        voucher_no=entry.voucher_no,
                        description=entry.description,
                        line_description=None,
                        counter_accounts=counters,
                        side=line.side,
                        amount=line.amount,
                    )
                )
        rows, closing = ledger.build_ledger_rows(raw_lines, normal, Decimal(0))
        accounts.append(
            GeneralLedgerAccount(
                code=code,
                name=account_name(code),
                normal_balance=normal,
                opening_balance=Decimal(0),
                closing_balance=closing,
                rows=[
                    GeneralLedgerRow(
                        entry_date=row.entry_date,
                        voucher_no=row.voucher_no,
                        description=row.description,
                        line_description=row.line_description,
                        counter_accounts=row.counter_accounts,
                        side=row.side,
                        amount=row.amount,
                        running_balance=row.running_balance,
                    )
                    for row in rows
                ],
            )
        )
    return GeneralLedger(
        start_date=FY_START, end_date=FY_END, status=EntryStatus.POSTED, accounts=accounts
    )


def journal_book_from_db(
    conn: psycopg.Connection[Any], *, status: EntryStatus | None = EntryStatus.POSTED
) -> JournalBook:
    """Read the 仕訳帳 from Postgres through the production :class:`JournalRepository`."""
    return JournalRepository(conn).journal_book(start_date=FY_START, end_date=FY_END, status=status)


def general_ledger_from_db(
    conn: psycopg.Connection[Any], *, status: EntryStatus | None = EntryStatus.POSTED
) -> GeneralLedger:
    """Read the 総勘定元帳 from Postgres through the production :class:`LedgerRepository`."""
    return LedgerRepository(conn).general_ledger(start=FY_START, end=FY_END, status=status)


# ── 精算表 (worksheet, Issue #22) ─────────────────────────────────────────────


def worksheet_from_dataset(entries: tuple[SeedEntry, ...] = FY_ENTRIES) -> Worksheet:
    """Reduce the in-memory dataset into the 精算表 — no database required.

    Splits each account's footings by ``entry.source`` (operating vs. 期末整理仕訳, mirroring
    the DB path's source filter) and runs them through the shared
    :func:`ai_books.aggregation.assemble_worksheet`, so the offline golden source and the DB
    path net/route the columns the same way while summing independently.
    """
    unadjusted_debit: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    unadjusted_credit: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    adjustment_debit: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    adjustment_credit: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for entry in entries:
        if entry.entry_date < FY_START or entry.entry_date > FY_END:
            continue
        is_adjustment = entry.source == YEAR_END_ADJUSTMENT_SOURCE
        for line in entry.lines:
            if line.side is EntrySide.DEBIT:
                bucket = adjustment_debit if is_adjustment else unadjusted_debit
            else:
                bucket = adjustment_credit if is_adjustment else unadjusted_credit
            bucket[line.account_code] += line.amount

    codes = (
        unadjusted_debit.keys()
        | unadjusted_credit.keys()
        | adjustment_debit.keys()
        | adjustment_credit.keys()
    )
    accounts = [
        aggregation.WorksheetAccount(
            code=code,
            name=account_name(code),
            account_type=account_type(code),
            unadjusted_debit=unadjusted_debit.get(code, Decimal(0)),
            unadjusted_credit=unadjusted_credit.get(code, Decimal(0)),
            adjustment_debit=adjustment_debit.get(code, Decimal(0)),
            adjustment_credit=adjustment_credit.get(code, Decimal(0)),
        )
        for code in sorted(codes)
    ]
    return aggregation.assemble_worksheet(
        accounts, fiscal_year=FISCAL_YEAR, start_date=FY_START, end_date=FY_END
    )


def worksheet_from_db(
    conn: psycopg.Connection[Any], *, status: EntryStatus | None = EntryStatus.POSTED
) -> Worksheet:
    """Read the 精算表 from Postgres through the production :class:`LedgerRepository`.

    Resolves the :data:`~tests.fixtures.seed_fy.dataset.FISCAL_YEAR` row (seeded by
    :func:`~tests.fixtures.seed_fy.loader.load_fiscal_year`) and delegates to
    :meth:`ai_books.db.repository.LedgerRepository.worksheet` — the code Issue #22 ships —
    so there is no second arithmetic path to drift from :func:`worksheet_from_dataset`.
    """
    year = FiscalYearRepository(conn).get_by_name(FISCAL_YEAR)
    assert year is not None, f"fiscal year {FISCAL_YEAR} not seeded"
    return LedgerRepository(conn).worksheet(
        fiscal_year=year.name, start=year.start_date, end=year.end_date, status=status
    )


# ── 貸借対照表 (balance sheet, Issue #21) ─────────────────────────────────────────
# Same dual-path cross-check: a pure reduction of the in-memory dataset (the offline golden
# source, no DB) and a DB-backed read through the production engine. Both feed the same
# in-memory trial balance into :func:`ai_books.aggregation.assemble_balance_sheet`, so the
# grouping / 当期純利益 / 貸借一致 arithmetic is shared and a divergence pins a storage bug.


def balance_sheet_from_dataset(entries: tuple[SeedEntry, ...] = FY_ENTRIES) -> BalanceSheet:
    """Reduce the in-memory dataset into a 貸借対照表 — no database required.

    Builds the trial balance offline, tags every row with its 表示区分 from the canonical
    chart, and rolls it up through the production :func:`ai_books.aggregation.assemble_balance_sheet`.
    The synthetic year is a loss year, so 当期純利益 is negative — exercising the sign handling.
    ``status`` is fixed to ``posted`` to match the DB path (the loader stores every entry
    POSTED), so the offline golden and :func:`balance_sheet_from_db` agree on that field.
    """
    trial_balance = trial_balance_from_dataset(entries)
    balances = [
        aggregation.ClassifiedBalance(
            code=row.code,
            name=row.name,
            statement_category=statement_category(row.code),
            balance=row.balance,
        )
        for row in trial_balance.rows
    ]
    return aggregation.assemble_balance_sheet(balances, status=EntryStatus.POSTED)


def balance_sheet_from_db(
    conn: psycopg.Connection[Any], *, status: EntryStatus | None = EntryStatus.POSTED
) -> BalanceSheet:
    """Read the 貸借対照表 from Postgres through the production :class:`LedgerRepository`."""
    return LedgerRepository(conn).balance_sheet(status=status)


# ── 青色申告決算書 (financial statements, Issue #23) ─────────────────────────────
# Same dual-path cross-check: a pure reduction of the in-memory dataset (the offline golden
# source, no DB) and a DB-backed read through the production engine. Both feed the assembled
# PL/BS plus the journal-derived 内訳 (月別売上・仕入 / 減価償却 / 製造原価) into
# :func:`ai_books.aggregation.assemble_financial_statements`, so the 内訳 ↔ PL/BS 突合 is shared
# and a divergence pins a storage/aggregation bug.


def _balance_sheet_as_of_end(entries: tuple[SeedEntry, ...]) -> BalanceSheet:
    """The 貸借対照表 as of 期末 (期末時点) — matches the DB path's ``balance_sheet(as_of=end)``."""
    trial_balance = trial_balance_from_dataset(entries)
    balances = [
        aggregation.ClassifiedBalance(
            code=row.code,
            name=row.name,
            statement_category=statement_category(row.code),
            balance=row.balance,
        )
        for row in trial_balance.rows
    ]
    return aggregation.assemble_balance_sheet(balances, as_of=FY_END, status=EntryStatus.POSTED)


def _monthly_amounts_from_dataset(
    entries: tuple[SeedEntry, ...], codes: set[str]
) -> dict[date, aggregation.MonthAmounts]:
    """Sum 借方 / 貸方 per 月初 for the given account ``codes`` over the fiscal year."""
    debit_by_month: dict[date, Decimal] = defaultdict(lambda: Decimal(0))
    credit_by_month: dict[date, Decimal] = defaultdict(lambda: Decimal(0))
    for entry in entries:
        if entry.entry_date < FY_START or entry.entry_date > FY_END:
            continue
        month_start = date(entry.entry_date.year, entry.entry_date.month, 1)
        for line in entry.lines:
            if line.account_code not in codes:
                continue
            bucket = debit_by_month if line.side is EntrySide.DEBIT else credit_by_month
            bucket[month_start] += line.amount
    return {
        month: aggregation.MonthAmounts(
            debit_total=debit_by_month.get(month, Decimal(0)),
            credit_total=credit_by_month.get(month, Decimal(0)),
        )
        for month in debit_by_month.keys() | credit_by_month.keys()
    }


def _fixed_asset_totals_from_dataset(
    entries: tuple[SeedEntry, ...],
) -> list[aggregation.FixedAssetTotals]:
    """Each 固定資産勘定's 取得価額 / 当期償却 / 期末簿価 from the dataset (直接法), 科目コード順."""
    codes = sorted(
        {
            line.account_code
            for entry in entries
            for line in entry.lines
            if statement_category(line.account_code) is StatementCategory.FIXED_ASSETS
        }
    )
    debit_by_code: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    credit_by_code: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    period_credit_by_code: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for entry in entries:
        if entry.entry_date > FY_END:
            continue
        for line in entry.lines:
            if line.account_code not in codes:
                continue
            if line.side is EntrySide.DEBIT:
                debit_by_code[line.account_code] += line.amount
            else:
                credit_by_code[line.account_code] += line.amount
                if entry.entry_date >= FY_START:
                    period_credit_by_code[line.account_code] += line.amount
    return [
        aggregation.FixedAssetTotals(
            code=code,
            name=account_name(code),
            acquisition_cost=debit_by_code.get(code, Decimal(0)),
            depreciation_expense=period_credit_by_code.get(code, Decimal(0)),
            closing_book_value=ledger.balance_from_totals(
                debit_by_code.get(code, Decimal(0)),
                credit_by_code.get(code, Decimal(0)),
                normal_side(code),
            ),
        )
        for code in codes
    ]


def financial_statements_from_dataset(
    entries: tuple[SeedEntry, ...] = FY_ENTRIES,
) -> FinancialStatements:
    """Reduce the in-memory dataset into a 青色申告決算書 — no database required.

    Assembles the PL/BS offline (sharing the production engines) and the 内訳 from the same
    dataset reduction (月別売上・仕入, 減価償却, 製造原価), then hands them to the production
    :func:`ai_books.aggregation.assemble_financial_statements`, so the offline golden source and
    the DB path agree on *structure* while summing independently.
    """
    profit_and_loss = profit_and_loss_from_dataset(entries)
    balance_sheet = _balance_sheet_as_of_end(entries)
    sales_codes = {
        line.account_code
        for entry in entries
        for line in entry.lines
        if statement_category(line.account_code) is StatementCategory.SALES
    }
    purchase_codes = {
        line.account_code
        for entry in entries
        for line in entry.lines
        if aggregation.is_purchase_account_name(account_name(line.account_code))
    }
    return aggregation.assemble_financial_statements(
        fiscal_year=FISCAL_YEAR,
        start_date=FY_START,
        end_date=FY_END,
        profit_and_loss=profit_and_loss,
        balance_sheet=balance_sheet,
        sales_by_month=_monthly_amounts_from_dataset(entries, sales_codes),
        purchases_by_month=_monthly_amounts_from_dataset(entries, purchase_codes),
        fixed_assets=_fixed_asset_totals_from_dataset(entries),
    )


def financial_statements_from_db(
    conn: psycopg.Connection[Any], *, status: EntryStatus | None = EntryStatus.POSTED
) -> FinancialStatements:
    """Compute the 青色申告決算書 from the DB via the production engine.

    Resolves the :data:`~tests.fixtures.seed_fy.dataset.FISCAL_YEAR` row and delegates to
    :meth:`ai_books.db.repository.LedgerRepository.financial_statements` — the code Issue #23
    ships — so there is no second arithmetic path to drift from
    :func:`financial_statements_from_dataset`.
    """
    year = FiscalYearRepository(conn).get_by_name(FISCAL_YEAR)
    assert year is not None, f"fiscal year {FISCAL_YEAR} not seeded"
    return LedgerRepository(conn).financial_statements(
        fiscal_year=year.name, start=year.start_date, end=year.end_date, status=status
    )


# ── e-Tax 取込データ (Issue #24) ────────────────────────────────────────────────
# Same dual-path cross-check: the 決算書 reduced offline (the golden source, no DB) and read from
# the DB through the production engine are each mapped to e-Tax records by the one
# :func:`ai_books.etax.build_etax_export`. The e-Tax layer has no independent arithmetic — it
# re-expresses the 決算書 — so the cross-check is that the *same export* falls out of both 決算書
# paths, pinning that the storage round-trip does not perturb the e-Tax 取込データ.


def etax_export_from_dataset(entries: tuple[SeedEntry, ...] = FY_ENTRIES) -> EtaxExport:
    """Map the offline-reduced 青色申告決算書 to e-Tax 取込データ — no database required.

    Builds the 決算書 offline (sharing the production engines) and runs it through the production
    :func:`ai_books.etax.build_etax_export`, so this generates the committed golden while staying a
    pure function of the dataset.
    """
    return build_etax_export(financial_statements_from_dataset(entries))


def etax_export_from_db(
    conn: psycopg.Connection[Any], *, status: EntryStatus | None = EntryStatus.POSTED
) -> EtaxExport:
    """Map the DB-read 青色申告決算書 to e-Tax 取込データ via the production engine.

    Reads the 決算書 from Postgres (:func:`financial_statements_from_db`) and runs the same
    :func:`ai_books.etax.build_etax_export`, so there is no second mapping path to drift from
    :func:`etax_export_from_dataset` — a divergence pins a storage/aggregation bug upstream.
    """
    return build_etax_export(financial_statements_from_db(conn, status=status))
