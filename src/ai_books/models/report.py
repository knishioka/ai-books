"""Report output models — 仕訳帳 (journal book) and 総勘定元帳 (general ledger).

These are the *保存義務帳簿* of 青色申告 (Issue #19): the chronological book of every
journal entry, and the per-account ledger with a running balance. Unlike the read-side
query DTOs in :mod:`.query` (which mirror what a single read tool returns and carry the
database ids the tool resolved), these are **report shapes**: code-oriented (科目コード +
科目名 inline, no DB ids), so the same object is produced identically from the in-memory
synthetic dataset (golden generation, no DB) and from Postgres, and the Vercel viewer
(#25) can render JSON/CSV/HTML straight from them.

Amounts stay :class:`~decimal.Decimal` end to end (浮動小数禁止). Each line/row carries its
``voucher_no`` (伝票番号) so a figure is traceable back to its 伝票 — the 電子帳簿保存 検索要件
(日付 / 科目 / 金額) is satisfied by the fields here, and a 取消 (voided) entry stays visible
as history rather than disappearing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import Field

from .base import DomainModel
from .enums import EntrySide, EntryStatus, NormalSide


class JournalBookLine(DomainModel):
    """One debit or credit line of a 仕訳帳 entry, with its 勘定科目 named inline."""

    account_code: str  # 勘定科目コード
    account_name: str  # 勘定科目名
    side: EntrySide  # 借方 / 貸方
    amount: Decimal  # 金額
    line_description: str | None = None  # 明細摘要


class JournalBookEntry(DomainModel):
    """One 伝票 in the 仕訳帳: a dated, numbered entry with all its lines.

    ``status`` and ``void_reason`` are carried so a 取消 entry remains in the book as
    auditable history (電子帳簿保存 訂正・削除履歴) rather than vanishing.
    """

    entry_date: date  # 取引日
    voucher_no: str | None = None  # 伝票番号
    description: str | None = None  # 摘要
    status: EntryStatus  # draft / posted / voided
    void_reason: str | None = None  # 取消理由 (status=voided のときのみ)
    lines: list[JournalBookLine] = Field(default_factory=list)


class JournalBook(DomainModel):
    """仕訳帳: every 伝票 in 取引日 → 伝票番号 order over an optional window.

    ``total_debit`` / ``total_credit`` are the column footings over the listed entries;
    they are equal when the listed books balance (借貸平均). ``status`` records the filter
    applied (``None`` = all but 取消; an explicit value = exactly that status, so a caller
    can pull the 取消 entries alone to audit them).
    """

    start_date: date | None = None
    end_date: date | None = None
    status: EntryStatus | None = None
    entries: list[JournalBookEntry] = Field(default_factory=list)
    total_debit: Decimal  # 借方合計
    total_credit: Decimal  # 貸方合計

    @property
    def is_balanced(self) -> bool:
        """True when the debit and credit column footings are equal."""
        return self.total_debit == self.total_credit


class GeneralLedgerRow(DomainModel):
    """A single 総勘定元帳 line: one journal line touching the ledger's account.

    ``counter_accounts`` are the 勘定科目コード of the *other* accounts in the same 伝票
    (相手科目); more than one means a renderer shows 諸口. ``running_balance`` is the
    account balance, in its 正常残高 direction, after this line is applied.
    """

    entry_date: date  # 取引日
    voucher_no: str | None = None  # 伝票番号
    description: str | None = None  # 伝票摘要
    line_description: str | None = None  # 明細摘要
    counter_accounts: list[str] = Field(default_factory=list)  # 相手科目コード
    side: EntrySide  # 借方 / 貸方
    amount: Decimal  # 金額
    running_balance: Decimal  # この明細適用後の残高 (正常残高方向)


class GeneralLedgerAccount(DomainModel):
    """One account's 総勘定元帳: its detail rows plus the carried 繰越 / 期末残高.

    ``opening_balance`` is the balance carried in from before ``start_date`` (繰越);
    ``closing_balance`` is the running balance after the last row.
    """

    code: str  # 勘定科目コード
    name: str  # 勘定科目名
    normal_balance: NormalSide  # 正常残高
    opening_balance: Decimal  # 繰越残高 (start_date 直前まで)
    closing_balance: Decimal  # 期末残高 (最終行適用後)
    rows: list[GeneralLedgerRow] = Field(default_factory=list)


class GeneralLedger(DomainModel):
    """総勘定元帳: every active account (科目コード順), each with its ledger detail.

    An account appears when it has any line up to ``end_date`` (so an account carried
    forward as a 繰越-only balance still shows). ``status`` records the filter applied,
    mirroring :class:`JournalBook`.
    """

    start_date: date | None = None
    end_date: date | None = None
    status: EntryStatus | None = None
    accounts: list[GeneralLedgerAccount] = Field(default_factory=list)
