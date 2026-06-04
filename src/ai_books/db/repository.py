"""Typed repository base + the minimal concrete repositories.

The repository layer is the *only* place raw SQL meets the domain models. It is
deliberately thin: raw SQL over ``psycopg`` (no ORM — invariant #4), with a small
generic base that turns ``dict`` rows into validated Pydantic models so callers
get typed objects, not tuples.

Scope here is the **type contract freeze point**, not full CRUD. We ship just
enough to prove the round-trip works end to end (``insert`` → ``select`` returning
a typed model, with ``Decimal`` preserved). The full create/read/update surface
lands in #13, built on this base.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel

from ai_books import aggregation, ledger
from ai_books.errors import RecordNotFoundError, RepositoryError
from ai_books.models import (
    YEAR_END_ADJUSTMENT_SOURCE,
    Account,
    AccountBalance,
    AccountLedger,
    AccountType,
    BalanceSheet,
    EntrySide,
    EntryStatus,
    FinancialStatements,
    FiscalYear,
    GeneralLedger,
    GeneralLedgerAccount,
    GeneralLedgerRow,
    JournalBook,
    JournalBookEntry,
    JournalBookLine,
    JournalEntry,
    JournalEntryPage,
    JournalLine,
    MonthlyTrend,
    NormalSide,
    ProfitAndLoss,
    StatementCategory,
    TrialBalance,
    Worksheet,
)

#: A bound SQL statement's positional parameters.
Params = Sequence[Any]
_NO_PARAMS: Params = ()

#: Hard ceiling on a single ``list_journal_entries`` page, so an unbounded request
#: cannot ask the database for the whole table in one round-trip.
MAX_PAGE_LIMIT = 500
#: Page size used when a caller does not specify one.
DEFAULT_PAGE_LIMIT = 50


def _category_value(account: Account) -> str | None:
    """The text to store for ``statement_category`` (the enum's value, or ``None``)."""
    return None if account.statement_category is None else account.statement_category.value


class BaseRepository[ModelT: BaseModel]:
    """Maps ``dict`` rows from raw SQL onto a Pydantic model ``model``.

    Subclasses set the class attribute ``model`` and add their own query methods,
    reusing :meth:`fetch_one` / :meth:`fetch_all` / :meth:`execute`. The connection
    is supplied by the caller (who owns its transaction boundary — see
    :func:`ai_books.db.transaction`).
    """

    #: The model subclasses validate rows into. Set on each concrete subclass.
    model: type[ModelT]

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn

    def _to_model(self, row: dict[str, Any]) -> ModelT:
        # validate() is the single conversion point so DB rows and hand-built
        # objects share one validation path.
        return self.model.model_validate(row)

    def fetch_one(self, query: str, params: Params = _NO_PARAMS) -> ModelT | None:
        """Return the first row as a validated model, or ``None`` if there is none."""
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            row = cur.fetchone()
        return None if row is None else self._to_model(row)

    def fetch_all(self, query: str, params: Params = _NO_PARAMS) -> list[ModelT]:
        """Return every row as a validated model."""
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        return [self._to_model(row) for row in rows]

    def execute(self, query: str, params: Params = _NO_PARAMS) -> int:
        """Run a statement for its effect; return the affected-row count."""
        with self._conn.cursor() as cur:
            cur.execute(query, params)
            return cur.rowcount


class AccountRepository(BaseRepository[Account]):
    """Read/insert access to the ``accounts`` table (勘定科目)."""

    model = Account

    def insert(self, account: Account) -> Account:
        """Insert ``account`` and return it as stored (DB-assigned id / timestamps)."""
        stored = self.fetch_one(
            """
            INSERT INTO accounts
                (code, name, account_type, statement_category, normal_balance,
                 parent_id, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                account.code,
                account.name,
                account.account_type.value,
                _category_value(account),
                account.normal_balance.value,
                account.parent_id,
                account.is_active,
            ),
        )
        if stored is None:  # pragma: no cover - RETURNING always yields a row
            raise RepositoryError("accounts INSERT ... RETURNING produced no row")
        return stored

    def get(self, account_id: int) -> Account | None:
        """Fetch one account by id (``None`` if absent)."""
        return self.fetch_one("SELECT * FROM accounts WHERE id = %s", (account_id,))

    def get_by_code(self, code: str) -> Account | None:
        """Fetch one account by its 勘定科目コード (``None`` if absent)."""
        return self.fetch_one("SELECT * FROM accounts WHERE code = %s", (code,))

    def find(
        self,
        *,
        account_type: AccountType | None = None,
        statement_category: StatementCategory | None = None,
        is_active: bool | None = None,
    ) -> list[Account]:
        """List accounts, optionally filtered by 区分 / 表示区分 / 有効フラグ.

        Every filter is optional and ``AND``-combined; omitting all returns the whole
        chart. Results are ordered by 勘定科目コード so output is stable.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if account_type is not None:
            clauses.append("account_type = %s")
            params.append(account_type.value)
        if statement_category is not None:
            clauses.append("statement_category = %s")
            params.append(statement_category.value)
        if is_active is not None:
            clauses.append("is_active = %s")
            params.append(is_active)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.fetch_all(f"SELECT * FROM accounts {where} ORDER BY code", params)

    def search(self, query: str, *, include_inactive: bool = False) -> list[Account]:
        """Find accounts whose code or 科目名 contains ``query`` (case-insensitive).

        Active accounts only unless ``include_inactive`` is set. Ordered by code.
        """
        pattern = f"%{query}%"
        active = "" if include_inactive else "AND is_active"
        return self.fetch_all(
            f"SELECT * FROM accounts WHERE (code ILIKE %s OR name ILIKE %s) {active} ORDER BY code",
            (pattern, pattern),
        )


class JournalRepository(BaseRepository[JournalEntry]):
    """Read/insert access to ``journal_entries`` + ``journal_lines`` (仕訳)."""

    model = JournalEntry

    def insert_entry(self, entry: JournalEntry) -> JournalEntry:
        """Insert a balanced entry and its lines atomically; return it as stored.

        The header and every line go in under one (possibly nested) transaction, so
        a failure on any line rolls back the whole entry. Line ordering is assigned
        positionally to keep ``(entry_id, line_no)`` unique.
        """
        with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO journal_entries
                    (entry_date, recorded_date, description, voucher_no, source,
                     import_hash, status)
                VALUES (%s, COALESCE(%s, CURRENT_DATE), %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    entry.entry_date,
                    entry.recorded_date,
                    entry.description,
                    entry.voucher_no,
                    entry.source,
                    entry.import_hash,
                    entry.status.value,
                ),
            )
            header = cur.fetchone()
            if header is None:  # pragma: no cover - RETURNING always yields a row
                raise RepositoryError("journal_entries INSERT ... RETURNING produced no row")
            entry_id = int(header["id"])

            for line_no, line in enumerate(entry.lines, start=1):
                cur.execute(
                    """
                    INSERT INTO journal_lines
                        (entry_id, line_no, account_id, side, amount,
                         tax_category, sub_account, line_description)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        entry_id,
                        line_no,
                        line.account_id,
                        line.side.value,
                        line.amount,
                        line.tax_category,
                        line.sub_account,
                        line.line_description,
                    ),
                )

        stored = self.get_entry(entry_id)
        if stored is None:  # pragma: no cover - just inserted
            raise RepositoryError(f"journal entry {entry_id} vanished after insert")
        return stored

    #: Width of the zero-padded numeric part of an auto-assigned 伝票番号 (``V0000001``).
    _VOUCHER_NO_WIDTH = 7

    def next_voucher_no(self) -> str:
        """Return the next auto-assigned 伝票番号 from the shared sequence.

        ``nextval`` is atomic, so two concurrent creates always receive distinct
        numbers (the partial UNIQUE index on ``voucher_no`` is the storage backstop).
        The sequence is monotonic but may gap on rollback — the intended basis for
        downstream 連番付与 / 欠番検知, not a gapless guarantee.
        """
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT nextval('journal_voucher_no_seq') AS seq")
            row = cur.fetchone()
        if row is None:  # pragma: no cover - nextval always yields a row
            raise RepositoryError("journal_voucher_no_seq nextval produced no row")
        return f"V{int(row['seq']):0{self._VOUCHER_NO_WIDTH}d}"

    def existing_import_hashes(self, hashes: list[str]) -> set[str]:
        """Return which of ``hashes`` are already stored (CSV 取込の二重取込検知, #14).

        The CSV import service calls this before inserting so an already-imported row
        is skipped rather than duplicated; the partial UNIQUE index on ``import_hash``
        is the storage-layer backstop behind this check.
        """
        if not hashes:
            return set()
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT import_hash FROM journal_entries WHERE import_hash = ANY(%s)",
                (hashes,),
            )
            return {row["import_hash"] for row in cur.fetchall()}

    def replace_entry(self, entry_id: int, entry: JournalEntry) -> JournalEntry:
        """Overwrite a draft entry's header and lines atomically; return it as stored.

        Lines are replaced wholesale (delete + re-insert with fresh ``line_no``), so the
        caller hands in the *complete* desired state. The lifecycle guard (draft-only)
        lives in the service; this is the storage mechanic.
        """
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE journal_entries
                   SET entry_date    = %s,
                       recorded_date = COALESCE(%s, recorded_date),
                       description   = %s,
                       voucher_no    = %s,
                       source        = %s,
                       status        = %s,
                       updated_at    = now()
                 WHERE id = %s
                """,
                (
                    entry.entry_date,
                    entry.recorded_date,
                    entry.description,
                    entry.voucher_no,
                    entry.source,
                    entry.status.value,
                    entry_id,
                ),
            )
            if cur.rowcount == 0:  # pragma: no cover - caller checks existence first
                raise RecordNotFoundError("journal_entry", entry_id)
            cur.execute("DELETE FROM journal_lines WHERE entry_id = %s", (entry_id,))
            for line_no, line in enumerate(entry.lines, start=1):
                cur.execute(
                    """
                    INSERT INTO journal_lines
                        (entry_id, line_no, account_id, side, amount,
                         tax_category, sub_account, line_description)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        entry_id,
                        line_no,
                        line.account_id,
                        line.side.value,
                        line.amount,
                        line.tax_category,
                        line.sub_account,
                        line.line_description,
                    ),
                )
        stored = self.get_entry(entry_id)
        if stored is None:  # pragma: no cover - just updated
            raise RepositoryError(f"journal entry {entry_id} vanished after update")
        return stored

    def mark_posted(self, entry_id: int) -> JournalEntry:
        """Transition an entry to ``posted`` (記帳確定); return it as stored."""
        return self._set_status(entry_id, EntryStatus.POSTED)

    def mark_voided(self, entry_id: int, reason: str) -> JournalEntry:
        """Transition an entry to ``voided`` (取消), recording the 理由 and 取消時刻.

        The row is kept — voiding never deletes — so the books stay continuous and the
        audit trail (written by the caller) has something to point at.
        """
        return self._set_status(entry_id, EntryStatus.VOIDED, void_reason=reason)

    def _set_status(
        self, entry_id: int, status: EntryStatus, *, void_reason: str | None = None
    ) -> JournalEntry:
        """Set an entry's status (and, for 取消, the void bookkeeping) atomically."""
        voided_at_sql = "now()" if status is EntryStatus.VOIDED else "voided_at"
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE journal_entries
                   SET status      = %s,
                       void_reason = %s,
                       voided_at   = {voided_at_sql},
                       updated_at  = now()
                 WHERE id = %s
                """,
                (status.value, void_reason, entry_id),
            )
            if cur.rowcount == 0:  # pragma: no cover - caller checks existence first
                raise RecordNotFoundError("journal_entry", entry_id)
        stored = self.get_entry(entry_id)
        if stored is None:  # pragma: no cover - just updated
            raise RepositoryError(f"journal entry {entry_id} vanished after status change")
        return stored

    def get_entry(self, entry_id: int) -> JournalEntry | None:
        """Fetch one entry with its lines attached (``None`` if absent)."""
        header = self.fetch_one("SELECT * FROM journal_entries WHERE id = %s", (entry_id,))
        if header is None:
            return None
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM journal_lines WHERE entry_id = %s ORDER BY line_no",
                (entry_id,),
            )
            line_rows = cur.fetchall()
        lines = [JournalLine.model_validate(row) for row in line_rows]
        # Rebuild from trusted storage; copy preserves the already-valid header.
        return header.model_copy(update={"lines": lines})

    # The filter is identical for the count and the page, so it is built once and
    # shared. Every comparison is guarded by a ``%(x)s IS NULL`` so a NULL bind
    # neutralises that clause — one query body serves any combination of filters.
    # The casts give psycopg an explicit type for the NULL binds (Postgres cannot
    # otherwise infer the type of a bare NULL parameter).
    #
    # The status clause does double duty: an explicit status matches exactly (so a
    # caller *can* ask for ``voided`` to audit 取消 entries), while the default
    # (no status) excludes ``voided`` so cancelled entries never silently count
    # toward the active books. The balance / ledger reads below apply the same rule.
    _ENTRY_FILTER = """
        FROM journal_entries je
        WHERE (%(start)s::date IS NULL OR je.entry_date >= %(start)s::date)
          AND (%(end)s::date IS NULL OR je.entry_date <= %(end)s::date)
          AND (CASE WHEN %(status)s::entry_status IS NULL
                    THEN je.status <> 'voided'::entry_status
                    ELSE je.status = %(status)s::entry_status END)
          AND (%(account_id)s::bigint IS NULL OR EXISTS (
                SELECT 1 FROM journal_lines jl
                WHERE jl.entry_id = je.id AND jl.account_id = %(account_id)s::bigint))
          AND (%(text)s::text IS NULL
               OR je.description ILIKE %(text_like)s
               OR EXISTS (SELECT 1 FROM journal_lines jl
                          WHERE jl.entry_id = je.id AND jl.line_description ILIKE %(text_like)s))
    """

    def list_entries(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        account_id: int | None = None,
        status: EntryStatus | None = None,
        text: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> JournalEntryPage:
        """Return one page of entries (newest first) matching the filters, with the total.

        Filters compose (each is skipped when ``None``): a ``[start_date, end_date]``
        window on 取引日, an ``account_id`` the entry must touch, an entry ``status``,
        and a ``text`` substring matched (case-insensitively) against the entry 摘要
        or any line 摘要. ``total`` is the full match count ignoring paging.

        ``limit`` is clamped to ``[1, MAX_PAGE_LIMIT]`` and ``offset`` to ``>= 0`` so
        a single call can never request an unbounded result set.
        """
        limit = max(1, min(limit, MAX_PAGE_LIMIT))
        offset = max(0, offset)
        params: dict[str, Any] = {
            "start": start_date,
            "end": end_date,
            "status": status.value if status is not None else None,
            "account_id": account_id,
            "text": text,
            "text_like": f"%{text}%" if text is not None else None,
        }

        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT count(*) AS n {self._ENTRY_FILTER}", params)
            count_row = cur.fetchone()
            total = 0 if count_row is None else int(count_row["n"])

            cur.execute(
                f"SELECT je.* {self._ENTRY_FILTER}"
                " ORDER BY je.entry_date DESC, je.id DESC"
                " LIMIT %(limit)s OFFSET %(offset)s",
                {**params, "limit": limit, "offset": offset},
            )
            header_rows = cur.fetchall()

        headers = [JournalEntry.model_validate(row) for row in header_rows]
        entries = self._attach_lines(headers)
        return JournalEntryPage(entries=entries, total=total, limit=limit, offset=offset)

    def _attach_lines(self, headers: list[JournalEntry]) -> list[JournalEntry]:
        """Bulk-load every header's lines in one query and reattach them in order."""
        ids = [h.id for h in headers if h.id is not None]
        if not ids:
            return headers
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM journal_lines WHERE entry_id = ANY(%s) ORDER BY entry_id, line_no",
                (ids,),
            )
            line_rows = cur.fetchall()
        by_entry: dict[int, list[JournalLine]] = {}
        for row in line_rows:
            by_entry.setdefault(int(row["entry_id"]), []).append(JournalLine.model_validate(row))
        return [
            h.model_copy(update={"lines": by_entry.get(h.id, []) if h.id is not None else []})
            for h in headers
        ]

    # The journal book (仕訳帳) is the chronological 保存義務帳簿: every 伝票 in
    # 取引日 → 伝票番号 order. Unlike list_entries it is *not* paged (a 帳簿 is read
    # whole, bounded only by the period) and is ordered oldest-first. The status
    # rule matches _ENTRY_FILTER: default excludes 取消 (voided), an explicit status
    # selects exactly that status — so passing ``voided`` pulls the 取消 entries on
    # their own for an audit, and they otherwise never perturb the listed totals.
    def journal_book(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        status: EntryStatus | None = None,
    ) -> JournalBook:
        """Return the 仕訳帳 over ``[start_date, end_date]`` (ISO, inclusive), oldest first.

        Each entry carries its 勘定科目 named inline and (for a 取消) its 取消理由, so the
        book is a self-contained, traceable record. ``total_debit`` / ``total_credit`` foot
        the listed lines.
        """
        params = {
            "start": start_date,
            "end": end_date,
            "status": status.value if status is not None else None,
        }
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT je.id, je.entry_date, je.voucher_no, je.description,
                       je.status, je.void_reason
                FROM journal_entries je
                WHERE (%(start)s::date IS NULL OR je.entry_date >= %(start)s::date)
                  AND (%(end)s::date IS NULL OR je.entry_date <= %(end)s::date)
                  AND (CASE WHEN %(status)s::entry_status IS NULL
                            THEN je.status <> 'voided'::entry_status
                            ELSE je.status = %(status)s::entry_status END)
                ORDER BY je.entry_date, je.voucher_no NULLS LAST, je.id
                """,
                params,
            )
            header_rows = cur.fetchall()

            entry_ids = [int(row["id"]) for row in header_rows]
            lines_by_entry: dict[int, list[JournalBookLine]] = {}
            if entry_ids:
                cur.execute(
                    """
                    SELECT jl.entry_id, a.code, a.name, jl.side, jl.amount,
                           jl.line_description
                    FROM journal_lines jl
                    JOIN accounts a ON a.id = jl.account_id
                    WHERE jl.entry_id = ANY(%s)
                    ORDER BY jl.entry_id, jl.line_no
                    """,
                    (entry_ids,),
                )
                for row in cur.fetchall():
                    lines_by_entry.setdefault(int(row["entry_id"]), []).append(
                        JournalBookLine(
                            account_code=row["code"],
                            account_name=row["name"],
                            side=EntrySide(row["side"]),
                            amount=row["amount"],
                            line_description=row["line_description"],
                        )
                    )

        entries: list[JournalBookEntry] = []
        total_debit = Decimal(0)
        total_credit = Decimal(0)
        for row in header_rows:
            lines = lines_by_entry.get(int(row["id"]), [])
            for line in lines:
                if line.side is EntrySide.DEBIT:
                    total_debit += line.amount
                else:
                    total_credit += line.amount
            entries.append(
                JournalBookEntry(
                    entry_date=row["entry_date"],
                    voucher_no=row["voucher_no"],
                    description=row["description"],
                    status=EntryStatus(row["status"]),
                    void_reason=row["void_reason"],
                    lines=lines,
                )
            )
        return JournalBook(
            start_date=start_date,
            end_date=end_date,
            status=status,
            entries=entries,
            total_debit=total_debit,
            total_credit=total_credit,
        )


class LedgerRepository:
    """Read-only balance / 総勘定元帳 access derived from ``journal_lines``.

    Unlike the model-backed repositories above, a balance or a ledger is an
    *aggregate* over many rows, not one table row, so this class talks to the
    cursor directly and delegates the accounting arithmetic to
    :mod:`ai_books.ledger` (kept pure for unit testing). Every method resolves the
    account first so a caller gets a clear :class:`RecordNotFoundError` rather than
    an empty, misleading zero balance for an id that does not exist.

    ``status`` filters all reads: pass :attr:`EntryStatus.POSTED` for the confirmed
    books (記帳確定 only), or leave it ``None`` to include drafts as well.
    """

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn

    def account_balance(
        self,
        account_id: int,
        *,
        as_of: date | None = None,
        status: EntryStatus | None = None,
    ) -> AccountBalance:
        """Return ``account_id``'s balance as of ``as_of`` (inclusive; ``None`` = all time)."""
        account = self._account_or_raise(account_id)
        debit_total, credit_total = self._sum_sides(
            account_id, as_of=as_of, before=None, status=status
        )
        balance = ledger.balance_from_totals(debit_total, credit_total, account.normal_balance)
        return AccountBalance(
            account_id=account_id,
            code=account.code,
            name=account.name,
            normal_balance=account.normal_balance,
            as_of=as_of,
            debit_total=debit_total,
            credit_total=credit_total,
            balance=balance,
        )

    def account_ledger(
        self,
        account_id: int,
        *,
        start: date | None = None,
        end: date | None = None,
        status: EntryStatus | None = None,
    ) -> AccountLedger:
        """Return the 総勘定元帳 for ``account_id`` over ``[start, end]`` with a running balance.

        The opening balance is everything *before* ``start`` (繰越); each in-window line
        moves the running balance in the account's normal direction. ``counter_accounts``
        on each row are the other accounts in the same entry (相手科目).
        """
        account = self._account_or_raise(account_id)
        # With no start there is no 繰越 — opening is zero and the rows cover all
        # history. Only when a start is given do we carry in the prior balance
        # (otherwise an unbounded ``before`` would sum the whole history twice).
        if start is None:
            opening_balance = Decimal("0")
        else:
            open_debit, open_credit = self._sum_sides(
                account_id, as_of=None, before=start, status=status
            )
            opening_balance = ledger.balance_from_totals(
                open_debit, open_credit, account.normal_balance
            )

        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT je.id AS entry_id, jl.line_no, je.entry_date, je.voucher_no,
                       je.description, jl.line_description, jl.side, jl.amount
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.entry_id
                WHERE jl.account_id = %(account_id)s
                  AND (%(start)s::date IS NULL OR je.entry_date >= %(start)s::date)
                  AND (%(end)s::date IS NULL OR je.entry_date <= %(end)s::date)
                  AND (CASE WHEN %(status)s::entry_status IS NULL
                            THEN je.status <> 'voided'::entry_status
                            ELSE je.status = %(status)s::entry_status END)
                ORDER BY je.entry_date, je.id, jl.line_no
                """,
                {
                    "account_id": account_id,
                    "start": start,
                    "end": end,
                    "status": status.value if status is not None else None,
                },
            )
            line_rows = cur.fetchall()

        entry_ids = sorted({int(row["entry_id"]) for row in line_rows})
        counter_map = self._counter_accounts(entry_ids, account_id)
        raw_lines = [
            ledger.RawLedgerLine(
                entry_id=int(row["entry_id"]),
                line_no=int(row["line_no"]),
                entry_date=row["entry_date"],
                voucher_no=row["voucher_no"],
                description=row["description"],
                line_description=row["line_description"],
                counter_accounts=counter_map.get(int(row["entry_id"]), []),
                side=EntrySide(row["side"]),
                amount=row["amount"],
            )
            for row in line_rows
        ]
        rows, closing_balance = ledger.build_ledger_rows(
            raw_lines, account.normal_balance, opening_balance
        )
        return AccountLedger(
            account_id=account_id,
            code=account.code,
            name=account.name,
            normal_balance=account.normal_balance,
            start_date=start,
            end_date=end,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            rows=rows,
        )

    def trial_balance(
        self,
        *,
        as_of: date | None = None,
        start: date | None = None,
        status: EntryStatus | None = None,
    ) -> TrialBalance:
        """Aggregate every account's footings into a 合計残高試算表 with one GROUP BY.

        One SQL pass sums 借方 / 貸方 per account over the window — ``start`` (inclusive)
        and ``as_of`` (inclusive) each bound 取引日 and are skipped when ``None``, so the
        default (both ``None``) is the cumulative all-time trial balance. Only accounts
        that were actually touched appear. Signing and the column footings are delegated
        to :func:`ai_books.aggregation.assemble_trial_balance` (the one signing rule the
        per-account :meth:`account_balance` also uses), so 借方合計 = 貸方合計 holds exactly
        when the underlying books balance. ``status`` follows the same rule as every other
        read: an explicit value matches exactly, the default excludes 取消 (voided).
        """
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT a.code, a.name, a.normal_balance,
                       COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'debit'), 0)  AS debit_total,
                       COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'credit'), 0) AS credit_total
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.entry_id
                JOIN accounts a ON a.id = jl.account_id
                WHERE (%(start)s::date IS NULL OR je.entry_date >= %(start)s::date)
                  AND (%(as_of)s::date IS NULL OR je.entry_date <= %(as_of)s::date)
                  AND (CASE WHEN %(status)s::entry_status IS NULL
                            THEN je.status <> 'voided'::entry_status
                            ELSE je.status = %(status)s::entry_status END)
                GROUP BY a.code, a.name, a.normal_balance
                ORDER BY a.code
                """,
                {
                    "start": start,
                    "as_of": as_of,
                    "status": status.value if status is not None else None,
                },
            )
            rows = cur.fetchall()

        totals = [
            aggregation.AccountTotals(
                code=row["code"],
                name=row["name"],
                normal_balance=NormalSide(row["normal_balance"]),
                debit_total=Decimal(row["debit_total"]),
                credit_total=Decimal(row["credit_total"]),
            )
            for row in rows
        ]
        return aggregation.assemble_trial_balance(totals, as_of=as_of, start_date=start)

    def worksheet(
        self,
        *,
        fiscal_year: str,
        start: date,
        end: date,
        status: EntryStatus | None = None,
        adjustment_source: str = YEAR_END_ADJUSTMENT_SOURCE,
    ) -> Worksheet:
        """Build the 精算表 over ``[start, end]`` from each account's split footings.

        One GROUP BY sums every touched account's 借方 / 貸方 footings, split by whether the
        entry's ``source`` marks it a 期末整理仕訳 (``adjustment_source``) or an operating
        entry — so the same SQL pass feeds both the 残高試算表 and the 修正記入 columns. The
        netting / routing into the 損益計算書欄 and 貸借対照表欄 is delegated to
        :func:`ai_books.aggregation.assemble_worksheet` (no second arithmetic path), so the
        worksheet's 自己検算 (当期純利益 が PL 欄と BS 欄で一致) holds exactly when the books
        balance. ``status`` follows the same rule as every other read (default excludes 取消).
        """
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT a.code, a.name, a.account_type,
                       COALESCE(SUM(jl.amount) FILTER (
                           WHERE jl.side = 'debit' AND je.source <> %(adjustment_source)s), 0)
                           AS unadjusted_debit,
                       COALESCE(SUM(jl.amount) FILTER (
                           WHERE jl.side = 'credit' AND je.source <> %(adjustment_source)s), 0)
                           AS unadjusted_credit,
                       COALESCE(SUM(jl.amount) FILTER (
                           WHERE jl.side = 'debit' AND je.source = %(adjustment_source)s), 0)
                           AS adjustment_debit,
                       COALESCE(SUM(jl.amount) FILTER (
                           WHERE jl.side = 'credit' AND je.source = %(adjustment_source)s), 0)
                           AS adjustment_credit
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.entry_id
                JOIN accounts a ON a.id = jl.account_id
                WHERE je.entry_date >= %(start)s::date
                  AND je.entry_date <= %(end)s::date
                  AND (CASE WHEN %(status)s::entry_status IS NULL
                            THEN je.status <> 'voided'::entry_status
                            ELSE je.status = %(status)s::entry_status END)
                GROUP BY a.code, a.name, a.account_type
                ORDER BY a.code
                """,
                {
                    "start": start,
                    "end": end,
                    "adjustment_source": adjustment_source,
                    "status": status.value if status is not None else None,
                },
            )
            rows = cur.fetchall()

        accounts = [
            aggregation.WorksheetAccount(
                code=row["code"],
                name=row["name"],
                account_type=AccountType(row["account_type"]),
                unadjusted_debit=Decimal(row["unadjusted_debit"]),
                unadjusted_credit=Decimal(row["unadjusted_credit"]),
                adjustment_debit=Decimal(row["adjustment_debit"]),
                adjustment_credit=Decimal(row["adjustment_credit"]),
            )
            for row in rows
        ]
        return aggregation.assemble_worksheet(
            accounts, fiscal_year=fiscal_year, start_date=start, end_date=end
        )

    def balance_sheet(
        self,
        *,
        as_of: date | None = None,
        status: EntryStatus | None = None,
    ) -> BalanceSheet:
        """Roll the 合計残高試算表 up into a 貸借対照表 as of ``as_of`` (inclusive; ``None`` = 全期間).

        Reuses :meth:`trial_balance` for every touched account's signed balance (no second
        aggregation to drift), then tags each row with its 表示区分 from the chart and delegates
        the grouping / 当期純利益 / 貸借一致 arithmetic to
        :func:`ai_books.aggregation.assemble_balance_sheet`. ``status`` follows the same rule as
        every other read: an explicit value matches exactly, the default excludes 取消 (voided).
        """
        trial_balance = self.trial_balance(as_of=as_of, status=status)
        category_by_code = {
            account.code: account.statement_category
            for account in AccountRepository(self._conn).find()
        }
        balances: list[aggregation.ClassifiedBalance] = []
        for row in trial_balance.rows:
            category = category_by_code.get(row.code)
            if category is None:
                # A touched account with no 表示区分 cannot be placed, which would silently
                # break 貸借一致 — fail loudly instead (a chart-of-accounts data error).
                raise RepositoryError(
                    f"account {row.code} ({row.name}) has no statement_category; "
                    "cannot assemble balance sheet"
                )
            balances.append(
                aggregation.ClassifiedBalance(
                    code=row.code,
                    name=row.name,
                    statement_category=category,
                    balance=row.balance,
                )
            )
        return aggregation.assemble_balance_sheet(balances, as_of=as_of, status=status)

    def financial_statements(
        self,
        *,
        fiscal_year: str,
        start: date,
        end: date,
        status: EntryStatus | None = None,
    ) -> FinancialStatements:
        """Compose the 青色申告決算書 over the fiscal year ``[start, end]`` (#23).

        Reuses the production engines for the 損益計算書 (:meth:`profit_and_loss`) and the
        貸借対照表 (:meth:`balance_sheet` as of 期末) — no second aggregation to drift — then
        reads the form's 内訳 from the same journal/勘定科目 data: the per-month 売上(収入)・仕入
        footings and each 固定資産's 当期減少額. The grouping / 突合 arithmetic is delegated to
        :func:`ai_books.aggregation.assemble_financial_statements`, so every breakdown reconciles
        with the PL/BS (:attr:`~ai_books.models.FinancialStatements.is_consistent`). ``status``
        follows the same rule as every other read (default excludes 取消).
        """
        profit_and_loss = self.profit_and_loss(
            fiscal_year=fiscal_year, start=start, end=end, status=status
        )
        balance_sheet = self.balance_sheet(as_of=end, status=status)
        sales_by_month = self._monthly_amounts(
            "a.statement_category = 'sales'", start=start, end=end, status=status
        )
        purchases_by_month = self._monthly_amounts(
            "a.name LIKE '%%' || %(purchase_suffix)s",
            start=start,
            end=end,
            status=status,
            extra_params={"purchase_suffix": aggregation.PURCHASE_ACCOUNT_NAME_SUFFIX},
        )
        fixed_assets = self._fixed_asset_totals(start=start, end=end, status=status)
        return aggregation.assemble_financial_statements(
            fiscal_year=fiscal_year,
            start_date=start,
            end_date=end,
            profit_and_loss=profit_and_loss,
            balance_sheet=balance_sheet,
            sales_by_month=sales_by_month,
            purchases_by_month=purchases_by_month,
            fixed_assets=fixed_assets,
        )

    def _monthly_amounts(
        self,
        account_selector: str,
        *,
        start: date,
        end: date,
        status: EntryStatus | None,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[date, aggregation.MonthAmounts]:
        """Sum 借方 / 貸方 per calendar month for the accounts matching ``account_selector``.

        ``account_selector`` is an ``accounts`` predicate (e.g. ``a.statement_category = 'sales'``)
        spliced into the WHERE clause; the date window and 取消 rule mirror every other read. The
        :class:`~ai_books.aggregation.MonthAmounts` are keyed by 月初 (the ``date_trunc`` bucket),
        ready for :func:`ai_books.aggregation.assemble_financial_statements` to sign and tile.
        """
        params: dict[str, Any] = {
            "start": start,
            "end": end,
            "status": status.value if status is not None else None,
            **(extra_params or {}),
        }
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT date_trunc('month', je.entry_date)::date AS month_start,
                       COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'debit'), 0)  AS debit_total,
                       COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'credit'), 0) AS credit_total
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.entry_id
                JOIN accounts a ON a.id = jl.account_id
                WHERE {account_selector}
                  AND je.entry_date >= %(start)s::date
                  AND je.entry_date <= %(end)s::date
                  AND (CASE WHEN %(status)s::entry_status IS NULL
                            THEN je.status <> 'voided'::entry_status
                            ELSE je.status = %(status)s::entry_status END)
                GROUP BY month_start
                """,
                params,
            )
            rows = cur.fetchall()
        return {
            row["month_start"]: aggregation.MonthAmounts(
                debit_total=Decimal(row["debit_total"]),
                credit_total=Decimal(row["credit_total"]),
            )
            for row in rows
        }

    def _fixed_asset_totals(
        self, *, start: date, end: date, status: EntryStatus | None
    ) -> list[aggregation.FixedAssetTotals]:
        """Each 固定資産勘定's 取得価額 / 当期償却 / 期末簿価, for the 減価償却費の計算 (直接法).

        ``acquisition_cost`` and ``closing_book_value`` are cumulative up to 期末 (so the 簿価
        equals the 貸借対照表 figure even with prior-year activity), while ``depreciation_expense``
        is only the 当期の貸方 (this fiscal year's 減少額 — the 償却費). Ordered by 勘定科目コード.
        """
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT a.code, a.name, a.normal_balance,
                       COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'debit'), 0)
                           AS debit_total,
                       COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'credit'), 0)
                           AS credit_total,
                       COALESCE(SUM(jl.amount) FILTER (
                           WHERE jl.side = 'credit' AND je.entry_date >= %(start)s::date), 0)
                           AS period_credit
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.entry_id
                JOIN accounts a ON a.id = jl.account_id
                WHERE a.statement_category = 'fixed_assets'
                  AND je.entry_date <= %(end)s::date
                  AND (CASE WHEN %(status)s::entry_status IS NULL
                            THEN je.status <> 'voided'::entry_status
                            ELSE je.status = %(status)s::entry_status END)
                GROUP BY a.code, a.name, a.normal_balance
                ORDER BY a.code
                """,
                {
                    "start": start,
                    "end": end,
                    "status": status.value if status is not None else None,
                },
            )
            rows = cur.fetchall()
        return [
            aggregation.FixedAssetTotals(
                code=row["code"],
                name=row["name"],
                acquisition_cost=Decimal(row["debit_total"]),
                depreciation_expense=Decimal(row["period_credit"]),
                closing_book_value=ledger.balance_from_totals(
                    Decimal(row["debit_total"]),
                    Decimal(row["credit_total"]),
                    NormalSide(row["normal_balance"]),
                ),
            )
            for row in rows
        ]

    def monthly_trend(
        self,
        account_id: int,
        *,
        fiscal_year: str,
        start: date,
        end: date,
        status: EntryStatus | None = None,
    ) -> MonthlyTrend:
        """Return ``account_id``'s month-by-month movement across ``[start, end]`` (期首/期末).

        The opening balance is everything posted *before* ``start`` (期首残高); the fiscal
        year is tiled into accounting months (:func:`ai_books.aggregation.month_windows`)
        and each month's 借方 / 貸方 sums roll the running balance forward, so a quiet month
        still appears with the balance unchanged (月次推移が会計期間で正しく区切られる). By
        construction 期首残高 + Σ期中増減 = 期末残高 (:attr:`MonthlyTrend.is_consistent`).
        ``status`` follows the same rule as every other read (default excludes 取消).
        """
        account = self._account_or_raise(account_id)
        open_debit, open_credit = self._sum_sides(
            account_id, as_of=None, before=start, status=status
        )
        opening_balance = ledger.balance_from_totals(
            open_debit, open_credit, account.normal_balance
        )

        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT date_trunc('month', je.entry_date)::date AS month_start,
                       COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'debit'), 0)  AS debit_total,
                       COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'credit'), 0) AS credit_total
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.entry_id
                WHERE jl.account_id = %(account_id)s
                  AND je.entry_date >= %(start)s::date
                  AND je.entry_date <= %(end)s::date
                  AND (CASE WHEN %(status)s::entry_status IS NULL
                            THEN je.status <> 'voided'::entry_status
                            ELSE je.status = %(status)s::entry_status END)
                GROUP BY month_start
                """,
                {
                    "account_id": account_id,
                    "start": start,
                    "end": end,
                    "status": status.value if status is not None else None,
                },
            )
            month_rows = cur.fetchall()

        amounts_by_month = {
            row["month_start"]: aggregation.MonthAmounts(
                debit_total=Decimal(row["debit_total"]),
                credit_total=Decimal(row["credit_total"]),
            )
            for row in month_rows
        }
        windows = aggregation.month_windows(start, end)
        points, closing_balance = aggregation.build_monthly_trend_points(
            windows, amounts_by_month, account.normal_balance, opening_balance
        )
        return MonthlyTrend(
            account_id=account_id,
            code=account.code,
            name=account.name,
            normal_balance=account.normal_balance,
            fiscal_year=fiscal_year,
            start_date=start,
            end_date=end,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            points=points,
        )

    def profit_and_loss(
        self,
        *,
        fiscal_year: str,
        start: date,
        end: date,
        status: EntryStatus | None = None,
    ) -> ProfitAndLoss:
        """Aggregate 収益/費用 footings over ``[start, end]`` into the staged 損益計算書 (#20).

        One SQL pass sums 借方 / 貸方 per 収益/費用 account within the fiscal year (the same
        GROUP BY shape as :meth:`trial_balance`, narrowed to P/L account types and carrying
        each account's 表示区分), then :func:`ai_books.aggregation.assemble_profit_and_loss`
        groups them into the 段階表示 and derives 売上総利益 → 営業利益 → 経常利益 → 当期純利益.
        Because both go through the one signing rule, the 段階利益 reconcile with the trial
        balance. ``status`` follows the same rule as every other read (default excludes 取消).
        """
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT a.code, a.name, a.account_type, a.statement_category, a.normal_balance,
                       COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'debit'), 0)  AS debit_total,
                       COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'credit'), 0) AS credit_total
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.entry_id
                JOIN accounts a ON a.id = jl.account_id
                WHERE a.account_type IN ('revenue', 'expense')
                  AND je.entry_date >= %(start)s::date
                  AND je.entry_date <= %(end)s::date
                  AND (CASE WHEN %(status)s::entry_status IS NULL
                            THEN je.status <> 'voided'::entry_status
                            ELSE je.status = %(status)s::entry_status END)
                GROUP BY a.code, a.name, a.account_type, a.statement_category, a.normal_balance
                ORDER BY a.code
                """,
                {
                    "start": start,
                    "end": end,
                    "status": status.value if status is not None else None,
                },
            )
            rows = cur.fetchall()

        accounts = [
            aggregation.PlAccountTotals(
                code=row["code"],
                name=row["name"],
                account_type=AccountType(row["account_type"]),
                statement_category=(
                    StatementCategory(row["statement_category"])
                    if row["statement_category"] is not None
                    else None
                ),
                normal_balance=NormalSide(row["normal_balance"]),
                debit_total=Decimal(row["debit_total"]),
                credit_total=Decimal(row["credit_total"]),
            )
            for row in rows
        ]
        return aggregation.assemble_profit_and_loss(
            accounts, fiscal_year=fiscal_year, start_date=start, end_date=end
        )

    def general_ledger(
        self,
        *,
        account_id: int | None = None,
        start: date | None = None,
        end: date | None = None,
        status: EntryStatus | None = None,
    ) -> GeneralLedger:
        """Return the 総勘定元帳 over ``[start, end]``, the whole book or one account.

        With ``account_id`` omitted, every "active" account is included (科目コード順) — an
        account is active when it has any line dated on or before ``end`` under the status
        filter, so an account touched only before ``start`` still appears with its 繰越
        balance (and no in-window rows). With ``account_id`` given, only that account is
        returned (raising :class:`RecordNotFoundError` if it does not exist). Each account's
        detail is computed by :meth:`account_ledger`, so the per-row running balance and
        相手科目 are identical to the single-account read tool — this is just the whole book
        at once.
        """
        if account_id is not None:
            account_ids = [account_id]
        else:
            account_ids = self._active_account_ids(end=end, status=status)
        accounts = [
            self._to_general_ledger_account(
                self.account_ledger(account_id, start=start, end=end, status=status)
            )
            for account_id in account_ids
        ]
        return GeneralLedger(start_date=start, end_date=end, status=status, accounts=accounts)

    @staticmethod
    def _to_general_ledger_account(ledger_view: AccountLedger) -> GeneralLedgerAccount:
        """Reshape a single-account :class:`AccountLedger` into the report's account block."""
        return GeneralLedgerAccount(
            code=ledger_view.code,
            name=ledger_view.name,
            normal_balance=ledger_view.normal_balance,
            opening_balance=ledger_view.opening_balance,
            closing_balance=ledger_view.closing_balance,
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
                for row in ledger_view.rows
            ],
        )

    def _active_account_ids(self, *, end: date | None, status: EntryStatus | None) -> list[int]:
        """Account ids (科目コード順) with any line dated on/before ``end`` under ``status``."""
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT DISTINCT jl.account_id, a.code
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.entry_id
                JOIN accounts a ON a.id = jl.account_id
                WHERE (%(end)s::date IS NULL OR je.entry_date <= %(end)s::date)
                  AND (CASE WHEN %(status)s::entry_status IS NULL
                            THEN je.status <> 'voided'::entry_status
                            ELSE je.status = %(status)s::entry_status END)
                ORDER BY a.code
                """,
                {"end": end, "status": status.value if status is not None else None},
            )
            return [int(row["account_id"]) for row in cur.fetchall()]

    def _account_or_raise(self, account_id: int) -> Account:
        account = AccountRepository(self._conn).get(account_id)
        if account is None:
            raise RecordNotFoundError("account", account_id)
        return account

    def _sum_sides(
        self,
        account_id: int,
        *,
        as_of: date | None,
        before: date | None,
        status: EntryStatus | None,
    ) -> tuple[Decimal, Decimal]:
        """Sum (debit_total, credit_total) for an account under the given date/status bounds.

        ``as_of`` bounds entry_date inclusively (``<=``); ``before`` bounds it
        exclusively (``<``, used for 繰越). Each bound is skipped when ``None``.
        """
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                  COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'debit'), 0) AS debit_total,
                  COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'credit'), 0) AS credit_total
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.entry_id
                WHERE jl.account_id = %(account_id)s
                  AND (%(as_of)s::date IS NULL OR je.entry_date <= %(as_of)s::date)
                  AND (%(before)s::date IS NULL OR je.entry_date < %(before)s::date)
                  AND (CASE WHEN %(status)s::entry_status IS NULL
                            THEN je.status <> 'voided'::entry_status
                            ELSE je.status = %(status)s::entry_status END)
                """,
                {
                    "account_id": account_id,
                    "as_of": as_of,
                    "before": before,
                    "status": status.value if status is not None else None,
                },
            )
            row = cur.fetchone()
        # COALESCE(..., 0) guarantees a row with non-NULL totals even for no matches.
        assert row is not None
        return Decimal(row["debit_total"]), Decimal(row["credit_total"])

    def _counter_accounts(self, entry_ids: list[int], account_id: int) -> dict[int, list[str]]:
        """Map each entry id to the 勘定科目コード of its *other* accounts (相手科目)."""
        if not entry_ids:
            return {}
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT jl.entry_id, a.code
                FROM journal_lines jl
                JOIN accounts a ON a.id = jl.account_id
                WHERE jl.entry_id = ANY(%s) AND jl.account_id <> %s
                ORDER BY jl.entry_id, jl.line_no
                """,
                (entry_ids, account_id),
            )
            rows = cur.fetchall()
        result: dict[int, list[str]] = {}
        for row in rows:
            codes = result.setdefault(int(row["entry_id"]), [])
            if row["code"] not in codes:  # collapse duplicate counter accounts, keep order
                codes.append(row["code"])
        return result


class FiscalYearRepository(BaseRepository[FiscalYear]):
    """Read access to the ``fiscal_years`` table (会計年度).

    Aggregation resolves a fiscal year by its name (例: ``FY2025``) to obtain the 期首 /
    期末 boundaries that bound a 月次推移; the period rows themselves are derived from that
    range at query time (the schema does not hard-link entries to a period).
    """

    model = FiscalYear

    def get_by_name(self, name: str) -> FiscalYear | None:
        """Fetch one fiscal year by its name (``None`` if absent)."""
        return self.fetch_one("SELECT * FROM fiscal_years WHERE name = %s", (name,))
