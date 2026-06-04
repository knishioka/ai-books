"""Read-side query result models (DTOs) for the MCP read tools.

These mirror nothing in the database directly; they are the *shapes the read
tools return*. Like every other domain model they keep amounts as
:class:`~decimal.Decimal` end to end (浮動小数禁止) so a balance or ledger total
never silently turns into a float on the way out to an MCP client.

- :class:`JournalEntryPage` — one page of ``list_journal_entries`` plus the total
  match count, so a client can page without a second "count" round-trip.
- :class:`AccountBalance` — a single account's balance at a 基準日, already signed
  into its normal-balance direction.
- :class:`AccountLedger` / :class:`LedgerRow` — 総勘定元帳 raw data: the per-account
  time-series of lines with a running balance (the input #19 renders from).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import Field

from .base import DomainModel
from .enums import EntrySide, NormalSide
from .journal import JournalEntry


class JournalEntryPage(DomainModel):
    """One page of journal entries plus the total number that matched the filter.

    ``total`` counts every entry matching the query *ignoring* ``limit``/``offset``,
    so a caller can show "showing 50 of 1,234" and decide whether to ask for more.
    """

    entries: list[JournalEntry] = Field(default_factory=list)
    total: int  # total matching entries, ignoring limit/offset
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        """True when entries beyond this page remain (``offset + len < total``)."""
        return self.offset + len(self.entries) < self.total


class AccountBalance(DomainModel):
    """An account's balance as of a 基準日, signed into its normal direction.

    ``balance`` is positive when the account carries its *normal* balance: for a
    debit-normal account (資産/費用) that is ``debit_total - credit_total``; for a
    credit-normal account (負債/純資産/収益) it is ``credit_total - debit_total``. A
    negative value therefore means the account sits on the opposite side of its
    normal balance.
    """

    account_id: int
    code: str  # 勘定科目コード
    name: str  # 勘定科目名
    normal_balance: NormalSide  # 正常残高
    as_of: date | None = None  # 基準日 (None = 全期間)
    debit_total: Decimal  # 借方合計
    credit_total: Decimal  # 貸方合計
    balance: Decimal  # 正常残高方向に符号付けした残高


class LedgerRow(DomainModel):
    """A single 総勘定元帳 line: one journal line touching the ledger's account.

    ``counter_accounts`` are the 勘定科目コード of the *other* accounts in the same
    entry (相手科目). When more than one is present, a renderer shows 諸口.
    ``running_balance`` is the account balance, in its normal direction, after this
    line is applied.
    """

    entry_id: int
    line_no: int
    entry_date: date  # 取引日
    description: str | None = None  # 伝票摘要
    line_description: str | None = None  # 明細摘要
    counter_accounts: list[str] = Field(default_factory=list)  # 相手科目コード
    side: EntrySide  # 借方 / 貸方
    amount: Decimal  # 金額
    running_balance: Decimal  # この明細適用後の残高 (正常残高方向)


class AccountLedger(DomainModel):
    """総勘定元帳 for one account over an optional date window.

    ``opening_balance`` is the balance carried in from *before* ``start_date`` (繰越);
    ``closing_balance`` is the running balance after the last row. With no
    ``start_date`` the opening balance is zero and the rows cover all history.
    """

    account_id: int
    code: str  # 勘定科目コード
    name: str  # 勘定科目名
    normal_balance: NormalSide  # 正常残高
    start_date: date | None = None
    end_date: date | None = None
    opening_balance: Decimal  # 繰越残高 (start_date 直前まで)
    closing_balance: Decimal  # 期末残高 (最終行適用後)
    rows: list[LedgerRow] = Field(default_factory=list)
