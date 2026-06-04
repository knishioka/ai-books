"""DB-backed tests for the journal write service and MCP write tools (Issue #13).

Skips when ``AI_BOOKS_DB_URL`` is unset (so ``./scripts/verify.sh`` stays green
without a live Postgres); runs in CI. Covers the acceptance criteria that need a real
round-trip:

- an imbalanced entry is rejected with a machine-readable error;
- a missing / inactive 勘定科目 reference is rejected;
- 取消 keeps the original row and leaves an audit-log trail; voided entries drop out
  of balances and the 総勘定元帳;
- ``Decimal`` precision (端数) survives the round-trip;
- concurrent creates never collide on 伝票番号;
- the lifecycle rules (draft-only edit, draft→posted, period validation) hold;
- the MCP tool wrappers surface failures as a ``ToolError`` JSON payload.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from fastmcp.exceptions import ToolError
from psycopg.rows import dict_row

from ai_books import db, server
from ai_books.db.repository import AccountRepository, JournalRepository, LedgerRepository
from ai_books.errors import (
    DomainValidationError,
    EntryStateError,
    InactiveAccountError,
    RecordNotFoundError,
)
from ai_books.models import (
    Account,
    AccountType,
    EntrySide,
    EntryStatus,
    JournalEntryInput,
    JournalLineInput,
    NormalSide,
)
from ai_books.services import JournalService

pytestmark = pytest.mark.skipif(
    not os.environ.get(db.DB_URL_ENV),
    reason=f"{db.DB_URL_ENV} not set; skipping DB-backed journal-write tests",
)

_TEST_SCHEMA = "ai_books_layer_test"


# --- fixtures / builders ------------------------------------------------------


def _cash() -> Account:
    return Account(
        code="1110", name="現金", account_type=AccountType.ASSET, normal_balance=NormalSide.DEBIT
    )


def _sales() -> Account:
    return Account(
        code="4000",
        name="売上高",
        account_type=AccountType.REVENUE,
        normal_balance=NormalSide.CREDIT,
    )


def _line(code: str, side: EntrySide, amount: str) -> JournalLineInput:
    return JournalLineInput(account_code=code, side=side, amount=Decimal(amount))


def _input(
    amount: str = "1000.00",
    *,
    entry_date: date = date(2026, 4, 1),
    status: EntryStatus = EntryStatus.DRAFT,
    description: str | None = None,
    debit_code: str = "1110",
    credit_code: str = "4000",
) -> JournalEntryInput:
    """A balanced cash←sales entry input for the seeded chart."""
    return JournalEntryInput(
        entry_date=entry_date,
        status=status,
        description=description,
        lines=[
            _line(debit_code, EntrySide.DEBIT, amount),
            _line(credit_code, EntrySide.CREDIT, amount),
        ],
    )


class _Seed:
    def __init__(self, cash: int, sales: int) -> None:
        self.cash = cash
        self.sales = sales


@pytest.fixture
def seed(migrated_conn: psycopg.Connection[Any]) -> _Seed:
    """Insert the two accounts the write tests post against; return their ids.

    ``migrated_conn`` is autocommit, so these rows are visible to the fresh
    connections the tool wrappers and the concurrency test open.
    """
    accounts = AccountRepository(migrated_conn)
    cash = accounts.insert(_cash())
    sales = accounts.insert(_sales())
    assert cash.id is not None
    assert sales.id is not None
    return _Seed(cash.id, sales.id)


# --- create -------------------------------------------------------------------


def test_create_persists_assigns_voucher_and_audits(
    migrated_conn: psycopg.Connection[Any], seed: _Seed
) -> None:
    service = JournalService(migrated_conn)
    stored = service.create_entry(_input("1234.56", description="売上"), actor="tester")

    assert stored.id is not None
    assert stored.status is EntryStatus.DRAFT
    assert stored.voucher_no is not None
    assert stored.voucher_no.startswith("V")
    assert len(stored.lines) == 2
    assert stored.is_balanced

    # Decimal survives the round-trip with no float drift.
    for line in stored.lines:
        assert isinstance(line.amount, Decimal)
        assert line.amount == Decimal("1234.56")

    row = migrated_conn.execute(
        "SELECT actor, action, table_name, record_id, after FROM audit_logs WHERE action = 'insert'"
    ).fetchone()
    assert row is not None
    assert row["actor"] == "tester"
    assert row["table_name"] == "journal_entries"
    assert row["record_id"] == str(stored.id)
    assert row["after"]["voucher_no"] == stored.voucher_no


def test_create_respects_explicit_voucher_no(
    migrated_conn: psycopg.Connection[Any], seed: _Seed
) -> None:
    data = _input("100.00")
    data = data.model_copy(update={"voucher_no": "MANUAL-1"})
    stored = JournalService(migrated_conn).create_entry(data)
    assert stored.voucher_no == "MANUAL-1"


def test_create_rejects_imbalance_machine_readably(
    migrated_conn: psycopg.Connection[Any], seed: _Seed
) -> None:
    imbalanced = JournalEntryInput(
        entry_date=date(2026, 4, 1),
        lines=[
            _line("1110", EntrySide.DEBIT, "1000.00"),
            _line("4000", EntrySide.CREDIT, "999.00"),
        ],
    )
    with pytest.raises(DomainValidationError) as excinfo:
        JournalService(migrated_conn).create_entry(imbalanced)

    payload = excinfo.value.to_dict()
    assert payload["error"] == "validation_error"
    assert any("imbalance" in err["message"] for err in payload["details"])

    # The failed create left nothing behind (transaction rolled back).
    count = migrated_conn.execute("SELECT count(*) AS n FROM journal_entries").fetchone()
    assert count is not None
    assert count["n"] == 0


def test_create_rejects_unknown_account(
    migrated_conn: psycopg.Connection[Any], seed: _Seed
) -> None:
    bad = JournalEntryInput(
        entry_date=date(2026, 4, 1),
        lines=[
            _line("9999", EntrySide.DEBIT, "100.00"),
            _line("4000", EntrySide.CREDIT, "100.00"),
        ],
    )
    with pytest.raises(RecordNotFoundError):
        JournalService(migrated_conn).create_entry(bad)


def test_create_rejects_inactive_account(
    migrated_conn: psycopg.Connection[Any], seed: _Seed
) -> None:
    accounts = AccountRepository(migrated_conn)
    retired = accounts.insert(
        Account(
            code="5999",
            name="廃止科目",
            account_type=AccountType.EXPENSE,
            normal_balance=NormalSide.DEBIT,
            is_active=False,
        )
    )
    assert retired.id is not None

    bad = JournalEntryInput(
        entry_date=date(2026, 4, 1),
        lines=[
            _line("5999", EntrySide.DEBIT, "100.00"),
            _line("4000", EntrySide.CREDIT, "100.00"),
        ],
    )
    with pytest.raises(InactiveAccountError):
        JournalService(migrated_conn).create_entry(bad)


def test_create_preserves_fractional_decimal(
    migrated_conn: psycopg.Connection[Any], seed: _Seed
) -> None:
    stored = JournalService(migrated_conn).create_entry(_input("0.01"))
    assert stored.lines[0].amount == Decimal("0.01")
    refetched = JournalRepository(migrated_conn).get_entry(stored.id)  # type: ignore[arg-type]
    assert refetched is not None
    assert refetched.lines[0].amount == Decimal("0.01")


# --- post ---------------------------------------------------------------------


def test_post_confirms_draft(migrated_conn: psycopg.Connection[Any], seed: _Seed) -> None:
    service = JournalService(migrated_conn)
    draft = service.create_entry(_input("100.00"))
    assert draft.id is not None

    posted = service.post_entry(draft.id, actor="poster")
    assert posted.status is EntryStatus.POSTED

    row = migrated_conn.execute(
        "SELECT before, after FROM audit_logs WHERE action = 'post'"
    ).fetchone()
    assert row is not None
    assert row["before"]["status"] == "draft"
    assert row["after"]["status"] == "posted"


def test_post_rejects_non_draft(migrated_conn: psycopg.Connection[Any], seed: _Seed) -> None:
    service = JournalService(migrated_conn)
    draft = service.create_entry(_input("100.00"))
    assert draft.id is not None
    service.post_entry(draft.id)

    with pytest.raises(EntryStateError):
        service.post_entry(draft.id)  # already posted


def test_post_rejects_empty_entry(migrated_conn: psycopg.Connection[Any], seed: _Seed) -> None:
    service = JournalService(migrated_conn)
    header_only = service.create_entry(JournalEntryInput(entry_date=date(2026, 4, 1)))
    assert header_only.id is not None
    with pytest.raises(DomainValidationError):
        service.post_entry(header_only.id)


def test_post_missing_entry_raises(migrated_conn: psycopg.Connection[Any], seed: _Seed) -> None:
    with pytest.raises(RecordNotFoundError):
        JournalService(migrated_conn).post_entry(999_999)


# --- update -------------------------------------------------------------------


def test_update_replaces_draft_and_audits(
    migrated_conn: psycopg.Connection[Any], seed: _Seed
) -> None:
    service = JournalService(migrated_conn)
    draft = service.create_entry(_input("100.00", description="旧"))
    assert draft.id is not None

    updated = service.update_entry(draft.id, _input("250.00", description="新"), actor="editor")
    assert updated.description == "新"
    assert updated.lines[0].amount == Decimal("250.00")
    # The auto-assigned voucher number is preserved across the edit.
    assert updated.voucher_no == draft.voucher_no

    row = migrated_conn.execute(
        "SELECT before, after FROM audit_logs WHERE action = 'update'"
    ).fetchone()
    assert row is not None
    assert row["before"]["description"] == "旧"
    assert row["after"]["description"] == "新"


def test_update_rejects_posted_entry(migrated_conn: psycopg.Connection[Any], seed: _Seed) -> None:
    service = JournalService(migrated_conn)
    draft = service.create_entry(_input("100.00"))
    assert draft.id is not None
    service.post_entry(draft.id)

    with pytest.raises(EntryStateError):
        service.update_entry(draft.id, _input("200.00"))


# --- void (取消) ---------------------------------------------------------------


def test_void_keeps_row_and_leaves_audit_trail(
    migrated_conn: psycopg.Connection[Any], seed: _Seed
) -> None:
    service = JournalService(migrated_conn)
    draft = service.create_entry(_input("500.00"))
    assert draft.id is not None
    service.post_entry(draft.id)

    voided = service.void_entry(draft.id, reason="二重計上のため取消", actor="auditor")
    assert voided.status is EntryStatus.VOIDED
    assert voided.void_reason == "二重計上のため取消"
    assert voided.voided_at is not None

    # The original row still exists — voiding never deletes (帳簿の連続性維持).
    still_there = JournalRepository(migrated_conn).get_entry(draft.id)
    assert still_there is not None
    assert still_there.status is EntryStatus.VOIDED

    row = migrated_conn.execute(
        "SELECT before, after FROM audit_logs WHERE action = 'void'"
    ).fetchone()
    assert row is not None
    assert row["before"]["status"] == "posted"
    assert row["after"]["status"] == "voided"
    assert row["after"]["void_reason"] == "二重計上のため取消"


def test_void_already_voided_rejected(migrated_conn: psycopg.Connection[Any], seed: _Seed) -> None:
    service = JournalService(migrated_conn)
    draft = service.create_entry(_input("100.00"))
    assert draft.id is not None
    service.void_entry(draft.id, reason="取消")
    with pytest.raises(EntryStateError):
        service.void_entry(draft.id, reason="再取消")


def test_void_requires_reason(migrated_conn: psycopg.Connection[Any], seed: _Seed) -> None:
    service = JournalService(migrated_conn)
    draft = service.create_entry(_input("100.00"))
    assert draft.id is not None
    with pytest.raises(DomainValidationError):
        service.void_entry(draft.id, reason="   ")


def test_voided_entry_excluded_from_balance_and_ledger(
    migrated_conn: psycopg.Connection[Any], seed: _Seed
) -> None:
    service = JournalService(migrated_conn)
    keep = service.create_entry(_input("1000.00", status=EntryStatus.POSTED))
    drop = service.create_entry(_input("400.00", status=EntryStatus.POSTED))
    assert keep.id is not None
    assert drop.id is not None
    service.void_entry(drop.id, reason="取消")

    ledger = LedgerRepository(migrated_conn)
    bal = ledger.account_balance(seed.cash)
    # Only the surviving 1000 entry counts; the voided 400 drops out.
    assert bal.debit_total == Decimal("1000.00")
    assert bal.balance == Decimal("1000.00")

    led = ledger.account_ledger(seed.cash)
    assert len(led.rows) == 1
    assert led.closing_balance == Decimal("1000.00")

    # list_journal_entries default view also hides the voided entry...
    page = JournalRepository(migrated_conn).list_entries()
    assert page.total == 1
    # ...but it is still queryable for audit by asking for it explicitly.
    voided_page = JournalRepository(migrated_conn).list_entries(status=EntryStatus.VOIDED)
    assert voided_page.total == 1


# --- period validation --------------------------------------------------------


def test_entry_date_must_be_within_fiscal_year(
    migrated_conn: psycopg.Connection[Any], seed: _Seed
) -> None:
    migrated_conn.execute(
        "INSERT INTO fiscal_years (name, start_date, end_date) VALUES (%s, %s, %s)",
        ("FY2026", date(2026, 1, 1), date(2026, 12, 31)),
    )
    service = JournalService(migrated_conn)

    # In-range date is accepted.
    ok = service.create_entry(_input("100.00", entry_date=date(2026, 6, 1)))
    assert ok.id is not None

    # Out-of-range date is rejected as 期間外.
    with pytest.raises(DomainValidationError) as excinfo:
        service.create_entry(_input("100.00", entry_date=date(2027, 1, 1)))
    assert excinfo.value.to_dict()["details"][0]["field"] == "entry_date"


# --- concurrency --------------------------------------------------------------


def test_concurrent_creates_get_unique_voucher_numbers(
    migrated_conn: psycopg.Connection[Any], seed: _Seed
) -> None:
    n = 8
    voucher_nos: list[str] = []
    errors: list[Exception] = []
    collect_lock = threading.Lock()
    start = threading.Barrier(n)

    def worker() -> None:
        conn = psycopg.connect(db.get_db_url(), autocommit=True, row_factory=dict_row)
        try:
            conn.execute(f"SET search_path TO {_TEST_SCHEMA}, public")
            start.wait()  # maximise contention on the sequence
            stored = JournalService(conn).create_entry(_input("100.00"))
            with collect_lock:
                assert stored.voucher_no is not None
                voucher_nos.append(stored.voucher_no)
        except Exception as exc:
            with collect_lock:
                errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(voucher_nos) == n
    assert len(set(voucher_nos)) == n  # no duplicates under concurrency


# --- MCP tool wiring ----------------------------------------------------------


@pytest.fixture
def patched_connect(
    monkeypatch: pytest.MonkeyPatch, migrated_conn: psycopg.Connection[Any]
) -> None:
    """Point ``db.connect`` (used by the tools) at the throwaway test schema."""

    def _connect(db_url: str | None = None) -> psycopg.Connection[Any]:
        conn = psycopg.connect(db.get_db_url(), row_factory=dict_row)
        conn.execute(f"SET search_path TO {_TEST_SCHEMA}, public")
        return conn

    monkeypatch.setattr(db, "connect", _connect)


def test_tool_create_then_void_roundtrip(patched_connect: None, seed: _Seed) -> None:
    created = server.create_journal_entry(_input("777.00", description="ツール作成"))
    assert created.id is not None
    assert created.description == "ツール作成"

    voided = server.void_journal_entry(created.id, reason="取消テスト")
    assert voided.status is EntryStatus.VOIDED


def test_tool_imbalance_raises_tool_error_with_payload(patched_connect: None, seed: _Seed) -> None:
    imbalanced = JournalEntryInput(
        entry_date=date(2026, 4, 1),
        lines=[
            _line("1110", EntrySide.DEBIT, "100.00"),
            _line("4000", EntrySide.CREDIT, "90.00"),
        ],
    )
    with pytest.raises(ToolError) as excinfo:
        server.create_journal_entry(imbalanced)
    payload = json.loads(str(excinfo.value))
    assert payload["error"] == "validation_error"


def test_tool_unknown_account_raises_tool_error(patched_connect: None, seed: _Seed) -> None:
    bad = JournalEntryInput(
        entry_date=date(2026, 4, 1),
        lines=[
            _line("0000", EntrySide.DEBIT, "100.00"),
            _line("4000", EntrySide.CREDIT, "100.00"),
        ],
    )
    with pytest.raises(ToolError) as excinfo:
        server.create_journal_entry(bad)
    payload = json.loads(str(excinfo.value))
    assert payload["error"] == "not_found"
