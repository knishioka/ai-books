"""仕訳 — journal entry (伝票ヘッダ) and journal line (明細) domain models.

The double-entry core. Amounts are :class:`~decimal.Decimal` end to end (浮動小数
禁止) and constrained to ``numeric(18, 2)`` — the same precision the ``journal_lines``
table stores — so a value that would lose precision on the way into Postgres is
rejected at the model layer instead of being silently rounded.

Debit/credit *balance* is the central invariant (invariant #2). The DB enforces
shape and positivity; balancing across an entry's lines is enforced here, where the
whole entry is visible. Balance is checked only once an entry actually carries
lines, so a header-only ``draft`` can still be represented.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import Field, field_validator, model_validator

from .base import DomainModel
from .enums import EntrySide, EntryStatus

#: ``numeric(18, 2)``: at most 2 fractional digits and 18 significant digits total.
_AMOUNT_SCALE = 2
_AMOUNT_PRECISION = 18


class JournalLine(DomainModel):
    """A single debit or credit line of a journal entry (明細)."""

    id: int | None = None
    entry_id: int | None = None
    line_no: int = 1  # 伝票内の明細順
    account_id: int
    side: EntrySide  # 借方 / 貸方
    amount: Decimal  # 金額 (numeric(18, 2), 浮動小数禁止)
    tax_category: str | None = None  # 税区分
    sub_account: str | None = None  # 補助科目
    line_description: str | None = None  # 明細摘要
    created_at: datetime | None = None

    @field_validator("amount")
    @classmethod
    def _check_amount(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("amount must be positive; use side to record direction")
        # ``numeric(18, 2)``: reject anything Postgres would have to round, so
        # precision loss surfaces as a validation error rather than silent data drift.
        exponent = value.as_tuple().exponent
        if not isinstance(exponent, int) or -exponent > _AMOUNT_SCALE:
            raise ValueError(f"amount supports at most {_AMOUNT_SCALE} decimal places")
        if len(value.as_tuple().digits) > _AMOUNT_PRECISION:
            raise ValueError(f"amount exceeds {_AMOUNT_PRECISION} significant digits")
        return value


class JournalEntry(DomainModel):
    """A journal entry header together with its lines (伝票)."""

    id: int | None = None
    entry_date: date  # 取引日
    recorded_date: date | None = None  # 起票日
    description: str | None = None  # 摘要
    voucher_no: str | None = None  # 伝票番号
    source: str = "manual"  # 起票元
    status: EntryStatus = EntryStatus.DRAFT
    created_at: datetime | None = None
    updated_at: datetime | None = None
    lines: list[JournalLine] = Field(default_factory=list)

    @property
    def total_debit(self) -> Decimal:
        """Sum of all debit-side line amounts (借方合計)."""
        return sum((ln.amount for ln in self.lines if ln.side is EntrySide.DEBIT), Decimal(0))

    @property
    def total_credit(self) -> Decimal:
        """Sum of all credit-side line amounts (貸方合計)."""
        return sum((ln.amount for ln in self.lines if ln.side is EntrySide.CREDIT), Decimal(0))

    @property
    def is_balanced(self) -> bool:
        """True when debits equal credits (an empty entry is trivially balanced)."""
        return self.total_debit == self.total_credit

    @model_validator(mode="after")
    def _check_balanced(self) -> JournalEntry:
        if not self.lines:
            return self
        has_debit = any(ln.side is EntrySide.DEBIT for ln in self.lines)
        has_credit = any(ln.side is EntrySide.CREDIT for ln in self.lines)
        if not (has_debit and has_credit):
            raise ValueError("a journal entry with lines needs both a debit and a credit")
        if self.total_debit != self.total_credit:
            raise ValueError(
                f"debit/credit imbalance: 借方 {self.total_debit} != 貸方 {self.total_credit}"
            )
        return self
