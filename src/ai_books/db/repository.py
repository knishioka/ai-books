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

from ai_books import ledger
from ai_books.errors import RecordNotFoundError, RepositoryError
from ai_books.models import (
    Account,
    AccountBalance,
    AccountLedger,
    AccountType,
    EntrySide,
    EntryStatus,
    JournalEntry,
    JournalEntryPage,
    JournalLine,
    StatementCategory,
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
                    (entry_date, recorded_date, description, voucher_no, source, status)
                VALUES (%s, COALESCE(%s, CURRENT_DATE), %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    entry.entry_date,
                    entry.recorded_date,
                    entry.description,
                    entry.voucher_no,
                    entry.source,
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
    _ENTRY_FILTER = """
        FROM journal_entries je
        WHERE (%(start)s::date IS NULL OR je.entry_date >= %(start)s::date)
          AND (%(end)s::date IS NULL OR je.entry_date <= %(end)s::date)
          AND (%(status)s::entry_status IS NULL OR je.status = %(status)s::entry_status)
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
                SELECT je.id AS entry_id, jl.line_no, je.entry_date,
                       je.description, jl.line_description, jl.side, jl.amount
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.entry_id
                WHERE jl.account_id = %(account_id)s
                  AND (%(start)s::date IS NULL OR je.entry_date >= %(start)s::date)
                  AND (%(end)s::date IS NULL OR je.entry_date <= %(end)s::date)
                  AND (%(status)s::entry_status IS NULL OR je.status = %(status)s::entry_status)
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
                  AND (%(status)s::entry_status IS NULL OR je.status = %(status)s::entry_status)
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
