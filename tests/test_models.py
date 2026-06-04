"""Unit tests for the Pydantic domain models — no database required.

Covers the accounting invariants the models re-enforce at the validation boundary:
normal-balance consistency, debit/credit balance, ``Decimal`` precision, date
ordering, and round-tripping through ``model_dump`` / ``model_validate``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from ai_books.models import (
    Account,
    AccountType,
    AuditLog,
    EntrySide,
    EntryStatus,
    FiscalYear,
    JournalEntry,
    JournalEntryInput,
    JournalLine,
    JournalLineInput,
    NormalSide,
    Period,
    normal_side_for,
)

# --- enums / helper -----------------------------------------------------------


@pytest.mark.parametrize(
    ("account_type", "expected"),
    [
        (AccountType.ASSET, NormalSide.DEBIT),
        (AccountType.EXPENSE, NormalSide.DEBIT),
        (AccountType.LIABILITY, NormalSide.CREDIT),
        (AccountType.EQUITY, NormalSide.CREDIT),
        (AccountType.REVENUE, NormalSide.CREDIT),
    ],
)
def test_normal_side_for(account_type: AccountType, expected: NormalSide) -> None:
    assert normal_side_for(account_type) is expected


def test_enum_values_match_postgres() -> None:
    assert AccountType.ASSET.value == "asset"
    assert NormalSide.CREDIT.value == "credit"
    assert EntrySide.DEBIT.value == "debit"
    assert EntryStatus.POSTED.value == "posted"
    assert EntryStatus.VOIDED.value == "voided"


# --- Account ------------------------------------------------------------------


def test_account_valid() -> None:
    account = Account(
        code="1110",
        name="現金",
        account_type=AccountType.ASSET,
        normal_balance=NormalSide.DEBIT,
    )
    assert account.is_active is True
    assert account.id is None


def test_account_normal_balance_must_match_type() -> None:
    with pytest.raises(ValidationError, match="normal_balance"):
        Account(
            code="1110",
            name="現金",
            account_type=AccountType.ASSET,
            normal_balance=NormalSide.CREDIT,
        )


def test_account_cannot_be_its_own_parent() -> None:
    with pytest.raises(ValidationError, match="own parent"):
        Account(
            id=5,
            code="1110",
            name="現金",
            account_type=AccountType.ASSET,
            normal_balance=NormalSide.DEBIT,
            parent_id=5,
        )


def test_account_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        Account(
            code="1110",
            name="現金",
            account_type=AccountType.ASSET,
            normal_balance=NormalSide.DEBIT,
            unexpected="x",  # type: ignore[call-arg]
        )


def test_account_strips_whitespace() -> None:
    account = Account(
        code="  1110  ",
        name="現金",
        account_type=AccountType.ASSET,
        normal_balance=NormalSide.DEBIT,
    )
    assert account.code == "1110"


# --- JournalLine --------------------------------------------------------------


def _line(side: EntrySide, amount: str) -> JournalLine:
    return JournalLine(account_id=1, side=side, amount=Decimal(amount))


def test_journal_line_amount_must_be_positive() -> None:
    with pytest.raises(ValidationError, match="positive"):
        _line(EntrySide.DEBIT, "0")
    with pytest.raises(ValidationError, match="positive"):
        _line(EntrySide.DEBIT, "-100")


def test_journal_line_rejects_excess_decimal_places() -> None:
    with pytest.raises(ValidationError, match="decimal places"):
        _line(EntrySide.DEBIT, "100.123")


def test_journal_line_accepts_two_decimal_places() -> None:
    line = _line(EntrySide.DEBIT, "1234567.89")
    assert line.amount == Decimal("1234567.89")


def test_journal_line_rejects_excess_precision() -> None:
    # 19 significant digits exceeds numeric(18, 2).
    with pytest.raises(ValidationError, match="significant digits"):
        _line(EntrySide.DEBIT, "12345678901234567.89")


# --- JournalEntry -------------------------------------------------------------


def test_journal_entry_balanced() -> None:
    entry = JournalEntry(
        entry_date=date(2026, 4, 1),
        lines=[_line(EntrySide.DEBIT, "1000.00"), _line(EntrySide.CREDIT, "1000.00")],
    )
    assert entry.is_balanced
    assert entry.total_debit == Decimal("1000.00")
    assert entry.total_credit == Decimal("1000.00")
    assert entry.status is EntryStatus.DRAFT


def test_journal_entry_imbalance_rejected() -> None:
    with pytest.raises(ValidationError, match="imbalance"):
        JournalEntry(
            entry_date=date(2026, 4, 1),
            lines=[_line(EntrySide.DEBIT, "1000.00"), _line(EntrySide.CREDIT, "999.00")],
        )


def test_journal_entry_requires_both_sides() -> None:
    with pytest.raises(ValidationError, match="debit and a credit"):
        JournalEntry(
            entry_date=date(2026, 4, 1),
            lines=[_line(EntrySide.DEBIT, "1000.00"), _line(EntrySide.DEBIT, "1000.00")],
        )


def test_journal_entry_header_only_is_allowed() -> None:
    entry = JournalEntry(entry_date=date(2026, 4, 1))
    assert entry.lines == []
    assert entry.is_balanced  # trivially balanced


def test_journal_entry_multi_line_balance() -> None:
    entry = JournalEntry(
        entry_date=date(2026, 4, 1),
        lines=[
            _line(EntrySide.DEBIT, "600.00"),
            _line(EntrySide.DEBIT, "400.00"),
            _line(EntrySide.CREDIT, "1000.00"),
        ],
    )
    assert entry.total_debit == Decimal("1000.00")
    assert entry.is_balanced


def test_journal_entry_void_fields_default_none() -> None:
    entry = JournalEntry(entry_date=date(2026, 4, 1))
    assert entry.void_reason is None
    assert entry.voided_at is None
    assert entry.status is EntryStatus.DRAFT


# --- write-tool input DTOs ----------------------------------------------------


def test_journal_line_input_reuses_amount_rules() -> None:
    line = JournalLineInput(account_code="1110", side=EntrySide.DEBIT, amount=Decimal("1234.56"))
    assert line.account_code == "1110"
    with pytest.raises(ValidationError, match="positive"):
        JournalLineInput(account_code="1110", side=EntrySide.DEBIT, amount=Decimal("0"))
    with pytest.raises(ValidationError, match="decimal places"):
        JournalLineInput(account_code="1110", side=EntrySide.DEBIT, amount=Decimal("1.234"))


def test_journal_entry_input_defaults_to_draft() -> None:
    entry = JournalEntryInput(entry_date=date(2026, 4, 1))
    assert entry.status is EntryStatus.DRAFT
    assert entry.lines == []
    assert entry.voucher_no is None


def test_journal_entry_input_rejects_voided_status() -> None:
    # 取消 is reachable only through void_journal_entry, never fabricated on input.
    with pytest.raises(ValidationError, match="void_journal_entry"):
        JournalEntryInput(entry_date=date(2026, 4, 1), status=EntryStatus.VOIDED)


def test_journal_entry_input_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        JournalEntryInput(entry_date=date(2026, 4, 1), bogus="x")  # type: ignore[call-arg]


# --- FiscalYear / Period ------------------------------------------------------


def test_fiscal_year_requires_end_after_start() -> None:
    FiscalYear(name="FY2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
    with pytest.raises(ValidationError, match="after start_date"):
        FiscalYear(name="FY2026", start_date=date(2026, 1, 1), end_date=date(2026, 1, 1))


def test_period_allows_single_day() -> None:
    period = Period(
        fiscal_year_id=1,
        name="2026-04-01",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 1),
    )
    assert period.start_date == period.end_date
    with pytest.raises(ValidationError, match="before start_date"):
        Period(
            fiscal_year_id=1,
            name="bad",
            start_date=date(2026, 4, 2),
            end_date=date(2026, 4, 1),
        )


# --- round-trip ---------------------------------------------------------------


def test_account_round_trip() -> None:
    account = Account(
        id=1,
        code="4000",
        name="売上高",
        account_type=AccountType.REVENUE,
        normal_balance=NormalSide.CREDIT,
    )
    restored = Account.model_validate(account.model_dump())
    assert restored == account


def test_journal_entry_round_trip_preserves_decimal() -> None:
    entry = JournalEntry(
        entry_date=date(2026, 4, 1),
        lines=[_line(EntrySide.DEBIT, "1234567.89"), _line(EntrySide.CREDIT, "1234567.89")],
    )
    restored = JournalEntry.model_validate(entry.model_dump())
    assert restored.lines[0].amount == Decimal("1234567.89")
    assert isinstance(restored.lines[0].amount, Decimal)


def test_audit_log_model() -> None:
    log = AuditLog(actor="agent", action="insert", table_name="accounts", record_id="1")
    assert log.before is None
    assert log.after is None
    assert log.tool_name is None
