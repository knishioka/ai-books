"""Load the synthetic fiscal year into Postgres, idempotently.

Seeds the chart of accounts (reusing :func:`ai_books.seed.accounts.seed_accounts`), then
inserts each :class:`~tests.fixtures.seed_fy.dataset.SeedEntry` as a real ``journal_entries``
+ ``journal_lines`` row via the production :class:`~ai_books.db.repository.JournalRepository`
— so the golden harness exercises the *same* write path #13 will, not a test-only shortcut.

Idempotency rides on the unique ``journal_entries.voucher_no`` index: an entry whose voucher
is already present is skipped, so re-loading inserts nothing and never duplicates. Codes are
resolved to DB ids once, up front; building each :class:`~ai_books.models.JournalEntry` also
re-runs the model's debit/credit balance validation, a second guard on the dataset.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from ai_books.db.repository import AccountRepository, JournalRepository
from ai_books.models import EntryStatus, JournalEntry, JournalLine
from ai_books.seed.accounts import seed_accounts

from .dataset import FY_ENTRIES, SeedEntry, validate_dataset

if TYPE_CHECKING:
    import psycopg


class LoadResult(NamedTuple):
    """Outcome of a load run: entries newly inserted vs. already present (skipped)."""

    inserted: int
    skipped: int
    total: int


def _existing_vouchers(conn: psycopg.Connection[Any], vouchers: list[str]) -> set[str]:
    """Return which of ``vouchers`` already exist in ``journal_entries``."""
    if not vouchers:
        return set()
    # The caller's connection may use any row factory; dict_row keeps this robust
    # (the throwaway-schema test fixture connects with dict_row).
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT voucher_no FROM journal_entries WHERE voucher_no = ANY(%s)",
            (vouchers,),
        )
        return {row["voucher_no"] for row in cur.fetchall()}


def _to_journal_entry(entry: SeedEntry, code_to_id: dict[str, int]) -> JournalEntry:
    """Build a validated, POSTED :class:`JournalEntry` from a dataset entry."""
    lines = [
        JournalLine(
            line_no=line_no,
            account_id=code_to_id[line.account_code],
            side=line.side,
            amount=line.amount,
        )
        for line_no, line in enumerate(entry.lines, start=1)
    ]
    return JournalEntry(
        entry_date=entry.entry_date,
        description=entry.description,
        voucher_no=entry.voucher_no,
        source="seed",
        status=EntryStatus.POSTED,
        lines=lines,
    )


def load_fiscal_year(
    conn: psycopg.Connection[Any], entries: tuple[SeedEntry, ...] = FY_ENTRIES
) -> LoadResult:
    """Seed accounts + the synthetic year into ``conn``; return insert/skip counts.

    Validates the dataset first (so a bad dataset never touches the DB), seeds the chart
    idempotently, then inserts only the entries whose ``voucher_no`` is not already stored.
    The whole batch goes in under one transaction, so a failure midway rolls every new
    entry back rather than leaving the books partially seeded (all-or-nothing). Safe to
    call repeatedly: a second call inserts nothing.
    """
    validate_dataset(entries)
    seed_accounts(conn)

    accounts = AccountRepository(conn).find()
    code_to_id = {a.code: a.id for a in accounts if a.id is not None}

    already = _existing_vouchers(conn, [e.voucher_no for e in entries])
    to_insert = [entry for entry in entries if entry.voucher_no not in already]
    repo = JournalRepository(conn)
    # One transaction around the batch: each insert_entry's own transaction() nests as a
    # savepoint, so the load commits as a unit (or not at all). On an autocommit connection
    # this opens an explicit block; inside db.transaction() it nests cleanly. Skip the block
    # entirely when there is nothing new, so an idempotent re-call adds no transaction overhead.
    if to_insert:
        with conn.transaction():
            for entry in to_insert:
                repo.insert_entry(_to_journal_entry(entry, code_to_id))

    return LoadResult(inserted=len(to_insert), skipped=len(already), total=len(entries))
