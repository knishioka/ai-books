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
from typing import Any

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel

from ai_books.errors import RepositoryError
from ai_books.models import Account, JournalEntry, JournalLine

#: A bound SQL statement's positional parameters.
Params = Sequence[Any]
_NO_PARAMS: Params = ()


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
                account.statement_category,
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
