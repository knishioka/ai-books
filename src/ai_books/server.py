"""FastMCP server entry point.

Registers the read-only query surface:

- chart-of-accounts tools (``list_accounts`` / ``get_account`` / ``search_accounts``)
  over the seeded master data (Issue #12);
- journal / balance / ledger tools (``list_journal_entries`` / ``get_journal_entry``
  / ``get_account_balance`` / ``get_account_ledger``) — the shared read API that
  aggregation (#18) and the Vercel viewer (#16) reuse (Issue #15);
- aggregation tools (``trial_balance`` / ``monthly_trend``) — the 合計残高試算表 and
  月次推移 the later reports (PL/BS/精算表/決算書) derive from, built on the #15 read
  layer (Issue #18);
- 決算書 tools (``profit_and_loss``) — the 損益計算書, the 収益/費用 staged into the
  青色申告決算書 layout (売上総利益 → 営業利益 → 経常利益 → 当期純利益) (Issue #20).

Plus the write surface (#13): ``create_journal_entry`` / ``update_journal_entry`` /
``post_journal_entry`` / ``void_journal_entry``, which delegate to
:class:`~ai_books.services.JournalService` so balance, Decimal precision, account-FK,
and lifecycle validation happen server-side (invariant #2) and every write appends an
audit-log trail (invariant #5). And the ``hello`` smoke test.

The account tools keep their logic in plain ``_…`` helpers that take an open
connection (unit-testable against the throwaway-schema fixture without going
through FastMCP dispatch); the journal/balance/ledger tools open a short-lived
connection and delegate to the repository layer. Either way amounts stay
``Decimal`` (serialised as a string, never a float). The write tools translate the
service's typed failures into a :class:`ToolError` whose message is the JSON
``to_dict`` payload, so a calling agent gets a machine-readable reason.
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any

import psycopg
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from ai_books import db
from ai_books.db.repository import (
    AccountRepository,
    FiscalYearRepository,
    JournalRepository,
    LedgerRepository,
)
from ai_books.errors import AiBooksError, RecordNotFoundError
from ai_books.etax import (
    LATEST_ETAX_VERSION,
    build_etax_export,
    parse_etax_format,
    render_etax,
)
from ai_books.models import (
    Account,
    AccountBalance,
    AccountLedger,
    AccountType,
    BalanceSheet,
    EntryStatus,
    GeneralLedger,
    ImportSummary,
    JournalBook,
    JournalEntry,
    JournalEntryInput,
    JournalEntryPage,
    MonthlyTrend,
    ProfitAndLoss,
    StatementCategory,
    TrialBalance,
    Worksheet,
)
from ai_books.services import CsvImportService, JournalService

mcp: FastMCP = FastMCP(
    name="ai-books",
    instructions=(
        "AI-first accounting MCP server. Provides double-entry bookkeeping primitives "
        "(chart of accounts, journal entries, trial balance, financial statements). "
        "Read tools cover the chart of accounts (list_accounts / get_account / "
        "search_accounts) and the journals, balances, and general ledger (総勘定元帳) — "
        "list_journal_entries / get_journal_entry / get_account_balance / "
        "get_account_ledger. Aggregation tools (trial_balance / monthly_trend / worksheet) "
        "return the 合計残高試算表, 月次推移, and 精算表; profit_and_loss returns the 損益計算書 "
        "(P/L) staged into the 青色申告決算書 layout and balance_sheet returns the 貸借対照表 "
        "(B/S). export_etax renders the 決算書 as e-Tax 取込データ (xtx/CSV/XML) for electronic filing. "
        "Amounts are exact decimals returned as strings."
    ),
)


def _greet(name: str) -> str:
    """Pure greeting helper. Kept separate from the MCP tool wrapper so unit tests
    can exercise the logic without going through FastMCP dispatch."""
    return f"Hello, {name}! ai-books server is alive."


@mcp.tool
def hello(name: str = "world") -> str:
    """Return a greeting. M0 smoke-test tool only."""
    return _greet(name)


# --- chart of accounts (Issue #12) --------------------------------------------


def _list_accounts(
    conn: psycopg.Connection[Any],
    *,
    account_type: AccountType | None = None,
    statement_category: StatementCategory | None = None,
    is_active: bool | None = None,
) -> list[Account]:
    return AccountRepository(conn).find(
        account_type=account_type,
        statement_category=statement_category,
        is_active=is_active,
    )


def _get_account(conn: psycopg.Connection[Any], code: str) -> Account:
    account = AccountRepository(conn).get_by_code(code)
    if account is None:
        raise RecordNotFoundError("account", code)
    return account


def _search_accounts(
    conn: psycopg.Connection[Any], query: str, *, include_inactive: bool = False
) -> list[Account]:
    return AccountRepository(conn).search(query, include_inactive=include_inactive)


@mcp.tool
def list_accounts(
    account_type: AccountType | None = None,
    statement_category: StatementCategory | None = None,
    is_active: bool | None = None,
) -> list[Account]:
    """List chart-of-accounts entries, optionally filtered by 区分 / 表示区分 / 有効.

    All filters are optional and combined with AND. Returns typed ``Account`` rows
    ordered by 勘定科目コード.
    """
    with db.connect() as conn:
        return _list_accounts(
            conn,
            account_type=account_type,
            statement_category=statement_category,
            is_active=is_active,
        )


@mcp.tool
def get_account(code: str) -> Account:
    """Fetch one account by its 勘定科目コード. Errors if no such account exists."""
    with db.connect() as conn:
        return _get_account(conn, code)


@mcp.tool
def search_accounts(query: str, include_inactive: bool = False) -> list[Account]:
    """Search accounts by 勘定科目コード or 科目名 substring (case-insensitive).

    Active accounts only unless ``include_inactive`` is true. Ordered by code.
    """
    with db.connect() as conn:
        return _search_accounts(conn, query, include_inactive=include_inactive)


# --- journals / balances / ledger (Issue #15) ---------------------------------


def _parse_date(value: str | None, field: str) -> date | None:
    """Parse an optional ISO ``YYYY-MM-DD`` string, raising a clear error on bad input."""
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD); got {value!r}") from exc


def _parse_status(value: str | None) -> EntryStatus | None:
    """Parse an optional entry status, raising a clear error listing valid values."""
    if value is None:
        return None
    try:
        return EntryStatus(value)
    except ValueError as exc:
        allowed = ", ".join(s.value for s in EntryStatus)
        raise ValueError(f"status must be one of: {allowed}; got {value!r}") from exc


def _resolve_account_id(conn: psycopg.Connection[Any], code: str) -> int:
    """Resolve a 勘定科目コード to its id, raising :class:`RecordNotFoundError` if unknown."""
    account = AccountRepository(conn).get_by_code(code)
    if account is None or account.id is None:
        raise RecordNotFoundError("account", code)
    return account.id


@mcp.tool
def list_journal_entries(
    start_date: str | None = None,
    end_date: str | None = None,
    account_code: str | None = None,
    status: str | None = None,
    text: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> JournalEntryPage:
    """List journal entries (仕訳), newest first, with paging and a total match count.

    All filters are optional and combine: ``start_date``/``end_date`` (ISO, inclusive)
    bound the 取引日; ``account_code`` keeps only entries touching that account;
    ``status`` is ``draft`` or ``posted``; ``text`` is a case-insensitive substring
    matched against the entry or any line 摘要. ``limit`` is capped server-side.
    """
    with db.connect() as conn:
        account_id = _resolve_account_id(conn, account_code) if account_code else None
        return JournalRepository(conn).list_entries(
            start_date=_parse_date(start_date, "start_date"),
            end_date=_parse_date(end_date, "end_date"),
            account_id=account_id,
            status=_parse_status(status),
            text=text,
            limit=limit,
            offset=offset,
        )


@mcp.tool
def get_journal_entry(entry_id: int) -> JournalEntry:
    """Fetch a single journal entry with its lines attached (errors if absent)."""
    with db.connect() as conn:
        entry = JournalRepository(conn).get_entry(entry_id)
    if entry is None:
        raise RecordNotFoundError("journal_entry", entry_id)
    return entry


@mcp.tool
def get_account_balance(
    account_code: str,
    as_of: str | None = None,
    status: str | None = None,
) -> AccountBalance:
    """Return an account's balance as of ``as_of`` (ISO date, inclusive; default all time).

    ``balance`` is signed into the account's normal direction, so it is positive when
    the account carries its normal balance. Pass ``status='posted'`` to count only the
    confirmed books (記帳確定); the default includes drafts.
    """
    with db.connect() as conn:
        account_id = _resolve_account_id(conn, account_code)
        return LedgerRepository(conn).account_balance(
            account_id,
            as_of=_parse_date(as_of, "as_of"),
            status=_parse_status(status),
        )


@mcp.tool
def get_account_ledger(
    account_code: str,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> AccountLedger:
    """Return the 総勘定元帳 for an account over ``[start_date, end_date]`` (ISO, inclusive).

    Rows are chronological with a running balance; ``opening_balance`` is the 繰越 from
    before ``start_date`` and each row lists its 相手科目 (counter accounts). Pass
    ``status='posted'`` to restrict to the confirmed books.
    """
    with db.connect() as conn:
        account_id = _resolve_account_id(conn, account_code)
        return LedgerRepository(conn).account_ledger(
            account_id,
            start=_parse_date(start_date, "start_date"),
            end=_parse_date(end_date, "end_date"),
            status=_parse_status(status),
        )


# --- aggregation: trial balance / monthly trend (Issue #18) -------------------


@mcp.tool
def trial_balance(
    as_of: str | None = None,
    start_date: str | None = None,
    status: str | None = None,
) -> TrialBalance:
    """Return the 合計残高試算表: every touched account's 借方計 / 貸方計 / 残高 plus footings.

    ``start_date`` and ``as_of`` are ISO dates bounding 取引日 inclusively; omit both for
    the cumulative all-time trial balance, or pass them for a 期間試算表. Each ``balance``
    is signed into the account's 正常残高 direction, and ``total_debit`` / ``total_credit``
    are equal exactly when the books balance (借貸平均). Pass ``status='posted'`` for the
    confirmed books (記帳確定); the default includes drafts but never 取消 entries.
    """
    with db.connect() as conn:
        return LedgerRepository(conn).trial_balance(
            as_of=_parse_date(as_of, "as_of"),
            start=_parse_date(start_date, "start_date"),
            status=_parse_status(status),
        )


@mcp.tool
def monthly_trend(
    account_code: str,
    fiscal_year: str,
    status: str | None = None,
) -> MonthlyTrend:
    """Return one account's 月次推移 across ``fiscal_year`` (会計年度名, 例: ``FY2025``).

    Resolves the fiscal year's 期首 / 期末 and tiles it into accounting months: each point
    carries that month's 借方計 / 貸方計, the 当月増減 (normal-signed), and the carried-forward
    月末残高. ``opening_balance`` is the 期首残高 and ``closing_balance`` the 期末残高, with
    期首残高 + Σ期中増減 = 期末残高 by construction. Errors if the account or fiscal year is
    unknown. Pass ``status='posted'`` for the confirmed books; the default excludes 取消.
    """
    with db.connect() as conn:
        account_id = _resolve_account_id(conn, account_code)
        year = FiscalYearRepository(conn).get_by_name(fiscal_year)
        if year is None:
            raise RecordNotFoundError("fiscal_year", fiscal_year)
        return LedgerRepository(conn).monthly_trend(
            account_id,
            fiscal_year=year.name,
            start=year.start_date,
            end=year.end_date,
            status=_parse_status(status),
        )


@mcp.tool
def worksheet(fiscal_year: str, status: str | None = None) -> Worksheet:
    """Return the 精算表 (8桁ワークシート) for ``fiscal_year`` (会計年度名, 例: ``FY2025``).

    Lays the 決算過程 out as one table so the flow from the 試算表 to the 損益計算書 / 貸借
    対照表 is auditable: each account carries 残高試算表 (期末整理前の残高), 修正記入 (期末
    整理仕訳 — ``source='year_end_adjustment'`` の仕訳), 損益計算書欄, and 貸借対照表欄. The
    self-check is 当期純利益 が PL 欄と BS 欄で一致 — exact when the books balance. Errors if the
    fiscal year is unknown. Pass ``status='posted'`` for the 記帳確定 books; the default
    excludes 取消.
    """
    with db.connect() as conn:
        year = FiscalYearRepository(conn).get_by_name(fiscal_year)
        if year is None:
            raise RecordNotFoundError("fiscal_year", fiscal_year)
        return LedgerRepository(conn).worksheet(
            fiscal_year=year.name,
            start=year.start_date,
            end=year.end_date,
            status=_parse_status(status),
        )


# --- 決算書: 損益計算書 (Issue #20) -------------------------------------------


@mcp.tool
def profit_and_loss(fiscal_year: str, status: str | None = None) -> ProfitAndLoss:
    """Return the 損益計算書 (P/L) for ``fiscal_year`` (会計年度名, 例: ``FY2025``).

    Resolves the fiscal year's 期首 / 期末, sums the 収益/費用 over that window, and groups
    them into the 段階表示: 売上高 → 売上原価 (製造原価を含む) → 売上総利益 → 販管費 →
    営業利益 → 営業外損益 → 経常利益 → 当期純利益. Each section carries its 科目別 lines and a
    subtotal; amounts are exact decimals returned as strings. Any 収益/費用 account whose
    表示区分 is unset is surfaced under ``unclassified`` rather than dropped. Errors if the
    fiscal year is unknown. Pass ``status='posted'`` for the 記帳確定 books; the default
    excludes 取消.
    """
    with db.connect() as conn:
        year = FiscalYearRepository(conn).get_by_name(fiscal_year)
        if year is None:
            raise RecordNotFoundError("fiscal_year", fiscal_year)
        return LedgerRepository(conn).profit_and_loss(
            fiscal_year=year.name,
            start=year.start_date,
            end=year.end_date,
            status=_parse_status(status),
        )


# --- 決算書: 貸借対照表 (Issue #21) --------------------------------------------


@mcp.tool
def balance_sheet(
    as_of: str | None = None,
    status: str | None = None,
) -> BalanceSheet:
    """Return the 貸借対照表 (B/S) as of ``as_of`` (ISO date, inclusive; default 全期間).

    Every touched B/S account's 期末残高 is rolled up into its 表示区分 — 流動資産 / 固定資産,
    流動負債 / 固定負債, and 純資産 (元入金・事業主借) — each side carrying per-区分 subtotals.
    ``net_income`` is the 当期純利益 (青色申告特別控除前所得), the signed sum of every P/L account,
    folded into 純資産合計 so 資産合計 = 負債合計 + 純資産合計 holds (貸借一致). Pass
    ``status='posted'`` for the 記帳確定 books; the default includes drafts but never 取消 entries.
    """
    with db.connect() as conn:
        return LedgerRepository(conn).balance_sheet(
            as_of=_parse_date(as_of, "as_of"),
            status=_parse_status(status),
        )


# --- ledger reports: 仕訳帳 / 総勘定元帳 (Issue #19) ----------------------------


@mcp.tool
def journal_book(
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> JournalBook:
    """Return the 仕訳帳 (journal book) over ``[start_date, end_date]`` (ISO, inclusive).

    Every 伝票 in 取引日 → 伝票番号 order, each line naming its 勘定科目 inline, with the
    借方/貸方 column footings. This is a 青色申告 保存義務帳簿. ``status`` defaults to all but
    取消 (voided); pass ``'posted'`` for the 記帳確定 books, or ``'voided'`` to pull the 取消
    entries alone for an audit (each carries its 取消理由).
    """
    with db.connect() as conn:
        return JournalRepository(conn).journal_book(
            start_date=_parse_date(start_date, "start_date"),
            end_date=_parse_date(end_date, "end_date"),
            status=_parse_status(status),
        )


@mcp.tool
def general_ledger(
    account_code: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> GeneralLedger:
    """Return the 総勘定元帳 (general ledger) over ``[start_date, end_date]`` (ISO, inclusive).

    Each account is shown 科目別 with its 繰越 (opening) and 期末残高 (closing) and a running
    balance per line; every row carries its 伝票番号 and 相手科目 for traceability. Omit
    ``account_code`` for the whole book (every active account, 科目コード順) or pass one to
    get a single account. ``status`` defaults to all but 取消; pass ``'posted'`` for the
    記帳確定 books.
    """
    with db.connect() as conn:
        account_id = _resolve_account_id(conn, account_code) if account_code else None
        return LedgerRepository(conn).general_ledger(
            account_id=account_id,
            start=_parse_date(start_date, "start_date"),
            end=_parse_date(end_date, "end_date"),
            status=_parse_status(status),
        )


# --- e-Tax 取込データ出力 (Issue #24) -----------------------------------------


@mcp.tool
def export_etax(
    fiscal_year: str,
    format: str = "csv",
    format_version: str = LATEST_ETAX_VERSION,
) -> str:
    """Export the 青色申告決算書 of ``fiscal_year`` as e-Tax 取込データ (electronic-filing data).

    Builds the 決算書 (損益計算書 / 月別売上・仕入 / 減価償却 / 製造原価 / 貸借対照表) for the fiscal
    year, maps it onto the e-Tax 様式 ``format_version`` (年度ごとの様式はデータ駆動で外出し), and
    renders it. ``format`` is ``'xtx'`` (実 e-Tax 交換ファイル — 官式 KOA210 青色申告決算書(一般用)
    XML, 実申告に渡すのはこれ), or ``'csv'`` / ``'xml'`` (補助の人間確認用フラット出力). Amounts are
    emitted in 整数円; the mapping is schema-validated (必須項目・桁数・勘定科目コード・月) and a fault
    returns a ``ToolError`` whose JSON payload lists every problem. ``'xtx'`` requires the real 様式
    (``format_version='2025'`` → KOA210). Errors if the fiscal year or version is unknown.

    注意: 生成物は事業者の確定数値を含むため、ファイルに保存する場合もリポジトリにはコミットしない
    こと (運用は README 参照)。
    """
    output_format = parse_etax_format(format)
    with db.connect() as conn:
        year = FiscalYearRepository(conn).get_by_name(fiscal_year)
        if year is None:
            raise RecordNotFoundError("fiscal_year", fiscal_year)
        statements = LedgerRepository(conn).financial_statements(
            fiscal_year=year.name,
            start=year.start_date,
            end=year.end_date,
            status=EntryStatus.POSTED,
        )
    try:
        export = build_etax_export(statements, version=format_version)
    except AiBooksError as exc:
        raise _tool_error(exc) from exc
    return render_etax(export, output_format)


# --- journal writes (Issue #13) -----------------------------------------------


def _tool_error(exc: AiBooksError) -> ToolError:
    """Wrap a domain failure as a :class:`ToolError` carrying its machine-readable payload."""
    return ToolError(json.dumps(exc.to_dict(), ensure_ascii=False))


@mcp.tool
def create_journal_entry(entry: JournalEntryInput, actor: str = "ai-agent") -> JournalEntry:
    """Create a journal entry (仕訳) after full server-side validation.

    The entry must balance (借方合計 = 貸方合計), every line must reference an existing
    *active* 勘定科目 by code, amounts must fit ``numeric(18, 2)``, and the 取引日 must
    fall within a defined fiscal year when any exist. A 伝票番号 is auto-assigned from
    the sequence unless one is supplied. Returns the stored entry; on a validation,
    account, or period failure raises a ``ToolError`` whose message is a JSON payload.
    """
    with db.connect() as conn:
        try:
            return JournalService(conn).create_entry(entry, actor=actor)
        except AiBooksError as exc:
            raise _tool_error(exc) from exc


@mcp.tool
def update_journal_entry(
    entry_id: int, entry: JournalEntryInput, actor: str = "ai-agent"
) -> JournalEntry:
    """Replace a *draft* entry's header and lines (posted entries are immutable).

    Only a ``draft`` can be edited — a posted entry must be corrected by a reversing
    entry or 取消 (``void_journal_entry``). The same balance / account / period rules
    as create apply. Returns the updated entry; raises ``ToolError`` if it is not a
    draft or fails validation.
    """
    with db.connect() as conn:
        try:
            return JournalService(conn).update_entry(entry_id, entry, actor=actor)
        except AiBooksError as exc:
            raise _tool_error(exc) from exc


@mcp.tool
def post_journal_entry(entry_id: int, actor: str = "ai-agent") -> JournalEntry:
    """Confirm a draft entry into the books (draft → posted, 記帳確定).

    Only a balanced draft with lines can be posted. Returns the posted entry; raises
    ``ToolError`` if the entry is not a draft or has no lines.
    """
    with db.connect() as conn:
        try:
            return JournalService(conn).post_entry(entry_id, actor=actor)
        except AiBooksError as exc:
            raise _tool_error(exc) from exc


@mcp.tool
def void_journal_entry(entry_id: int, reason: str, actor: str = "ai-agent") -> JournalEntry:
    """Cancel an entry (取消) without deleting it, keeping 帳簿の連続性.

    A ``draft`` or ``posted`` entry can be voided; an already-voided one cannot. The
    row is kept and flipped to ``voided`` with the ``reason`` recorded, and the
    before/after is written to the audit log (電子帳簿保存 訂正・削除履歴). Voided entries
    no longer count toward balances or the 総勘定元帳. Returns the voided entry; raises
    ``ToolError`` if it is already voided or the reason is empty.
    """
    with db.connect() as conn:
        try:
            return JournalService(conn).void_entry(entry_id, reason=reason, actor=actor)
        except AiBooksError as exc:
            raise _tool_error(exc) from exc


# --- CSV import (Issue #14) ---------------------------------------------------


@mcp.tool
def import_transactions_csv(
    csv_text: str,
    account_code: str,
    csv_format: str = "auto",
    actor: str = "ai-agent",
) -> ImportSummary:
    """Import a bank/CC statement CSV into *draft* 仕訳 and return a run summary.

    ``csv_text`` is the statement's CSV content; ``account_code`` is the 勘定科目コード of
    the account the statement belongs to (e.g. 普通預金 for a bank export, 未払金 for a
    credit-card export). ``csv_format`` is ``auto`` (header-detected) or a named preset
    (``generic_bank`` / ``generic_card``). Each row becomes a balanced two-line draft:
    the account on one side and the 相手科目 inferred from the 摘要 (or a suspense 科目 —
    仮払金/仮受金 — when nothing matches) on the other. Re-importing the same file never
    duplicates (each row is fingerprinted into ``import_hash``). Entries are always
    ``draft`` — confirm them with ``post_journal_entry``. Returns counts of
    取込/重複/未割当 with the created entry ids; raises a ``ToolError`` (JSON payload) on a
    parse, format, account, or period failure.
    """
    with db.connect() as conn:
        try:
            return CsvImportService(conn).import_csv(
                csv_text,
                account_code=account_code,
                csv_format=csv_format,
                actor=actor,
            )
        except AiBooksError as exc:
            raise _tool_error(exc) from exc


# --- transport selection (Issue #106) -----------------------------------------
#
# stdio is the default and unchanged (FastMCP default transport): a local Claude
# Desktop / CLI client launches the process and talks over stdio. Setting
# ``AI_BOOKS_MCP_TRANSPORT=http`` opts in to FastMCP's Streamable HTTP transport so
# the same tools are reachable over the network — a deliberate operator choice, never
# enabled implicitly (ADR 0008). Host/port come from the environment; the default
# host is loopback (``127.0.0.1``) so an http launch never opens a public listener by
# accident. Authentication is intentionally NOT wired here yet: the remote endpoint is
# unauthenticated until the auth issue lands, at which point ``auth=`` is passed to the
# ``FastMCP(...)`` constructor above — this entry point stays the same (ADR 0008).

TRANSPORT_ENV = "AI_BOOKS_MCP_TRANSPORT"
HOST_ENV = "AI_BOOKS_MCP_HOST"
PORT_ENV = "AI_BOOKS_MCP_PORT"

_DEFAULT_TRANSPORT = "stdio"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8000
_VALID_TRANSPORTS = frozenset({"stdio", "http"})


def _resolve_transport() -> str:
    """Resolve the transport from ``AI_BOOKS_MCP_TRANSPORT`` (default ``stdio``)."""
    raw = os.environ.get(TRANSPORT_ENV)
    if not raw or not raw.strip():
        return _DEFAULT_TRANSPORT
    transport = raw.strip().lower()
    if transport not in _VALID_TRANSPORTS:
        allowed = ", ".join(sorted(_VALID_TRANSPORTS))
        raise RuntimeError(f"{TRANSPORT_ENV} must be one of: {allowed}; got {raw!r}")
    return transport


def _resolve_host() -> str:
    """Resolve the HTTP bind host from ``AI_BOOKS_MCP_HOST`` (default ``127.0.0.1``)."""
    raw = os.environ.get(HOST_ENV)
    if not raw or not raw.strip():
        return _DEFAULT_HOST
    return raw.strip()


def _resolve_port() -> int:
    """Resolve the HTTP bind port from ``AI_BOOKS_MCP_PORT`` (default ``8000``)."""
    raw = os.environ.get(PORT_ENV)
    if not raw or not raw.strip():
        return _DEFAULT_PORT
    try:
        port = int(raw.strip())
    except ValueError as exc:
        raise RuntimeError(f"{PORT_ENV} must be an integer; got {raw!r}") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError(f"{PORT_ENV} must be in 1..65535; got {port}")
    return port


def main() -> None:
    """Run the MCP server.

    Default transport is stdio (FastMCP default): unchanged local behaviour. Set
    ``AI_BOOKS_MCP_TRANSPORT=http`` to expose the tools over Streamable HTTP, bound to
    ``AI_BOOKS_MCP_HOST`` / ``AI_BOOKS_MCP_PORT`` (defaults ``127.0.0.1:8000``).
    """
    transport = _resolve_transport()
    if transport == "stdio":
        mcp.run()
        return
    mcp.run(transport="http", host=_resolve_host(), port=_resolve_port())


if __name__ == "__main__":
    main()
