"""Aggregation result models (DTOs) for the 集計エンジン (Issue #18).

These are the *shapes the aggregation tools return* — the numeric foundation every
later report (PL #20 / BS #21 / 精算表 #22 / 決算書 #23) derives from. Like every
other domain model they keep amounts as :class:`~decimal.Decimal` end to end
(浮動小数禁止), and each balance is already signed into its account's 正常残高 direction
(so a contra account such as 期末商品棚卸高 shows a negative balance), matching
:class:`~ai_books.models.query.AccountBalance`.

- :class:`TrialBalance` / :class:`TrialBalanceRow` — 合計残高試算表: per-account 借方計 /
  貸方計 / 残高 plus the two column footings. :attr:`TrialBalance.is_balanced` is the
  借貸平均 invariant (借方合計 = 貸方合計) the books must always satisfy.
- :class:`MonthlyTrend` / :class:`MonthlyTrendPoint` — 月次推移: one account's movement
  per accounting month within a fiscal year, with a carried-forward running balance.
  :attr:`MonthlyTrend.is_consistent` is the 期首残高 + Σ期中増減 = 期末残高 integrity check.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import Field

from .base import DomainModel
from .enums import NormalSide


class TrialBalanceRow(DomainModel):
    """One account's footing in the trial balance (試算表の一行).

    ``debit_total`` / ``credit_total`` are the summed 借方 / 貸方 amounts that touched
    the account within the report window; ``balance`` is signed into the account's
    正常残高 direction (debit-normal → ``debit_total - credit_total``; credit-normal →
    ``credit_total - debit_total``), so a negative value means the account sits on the
    opposite side of its normal balance.
    """

    code: str  # 勘定科目コード
    name: str  # 勘定科目名
    normal_balance: NormalSide  # 正常残高
    debit_total: Decimal  # 借方合計
    credit_total: Decimal  # 貸方合計
    balance: Decimal  # 正常残高方向に符号付けした残高


class TrialBalance(DomainModel):
    """合計残高試算表 — per-account rows plus the two column footings.

    ``as_of`` (inclusive) bounds the period end and ``start_date`` (inclusive) the
    start; with both ``None`` the report covers all history (the cumulative trial
    balance). ``total_debit`` / ``total_credit`` are the sums of the per-account
    columns; they are equal iff the books balance overall (借貸平均).
    """

    as_of: date | None = None  # 基準日 (期末, inclusive; None = 全期間)
    start_date: date | None = None  # 期間開始 (inclusive; None = 期首から)
    rows: list[TrialBalanceRow] = Field(default_factory=list)
    total_debit: Decimal  # 借方合計
    total_credit: Decimal  # 貸方合計

    @property
    def is_balanced(self) -> bool:
        """True when the debit and credit column footings are equal (借貸平均)."""
        return self.total_debit == self.total_credit


class MonthlyTrendPoint(DomainModel):
    """One accounting month of an account's movement, with the carried-forward balance.

    ``debit_total`` / ``credit_total`` are the 借方 / 貸方 amounts posted *within* this
    month; ``net_change`` is that month's movement signed into the account's 正常残高
    direction; ``closing_balance`` is the running balance (also normal-signed) after
    this month, i.e. the opening balance plus every ``net_change`` up to and including
    this point.
    """

    month: str  # 'YYYY-MM'
    start_date: date  # 月の開始 (会計期間内にクランプ)
    end_date: date  # 月の終了 (会計期間内にクランプ)
    debit_total: Decimal  # 当月借方計
    credit_total: Decimal  # 当月貸方計
    net_change: Decimal  # 当月増減 (正常残高方向)
    closing_balance: Decimal  # 月末残高 (累計, 正常残高方向)


class MonthlyTrend(DomainModel):
    """月次推移 — one account's per-month movement across a fiscal year.

    ``opening_balance`` is the balance carried in from *before* ``start_date`` (期首残高);
    ``closing_balance`` is the running balance after the last month (期末残高). The
    months tile the fiscal year so a quiet month still appears (zero movement, balance
    unchanged), which is what makes the series 会計期間で正しく区切られた.
    """

    account_id: int
    code: str  # 勘定科目コード
    name: str  # 勘定科目名
    normal_balance: NormalSide  # 正常残高
    fiscal_year: str  # 会計年度名 (例: 'FY2025')
    start_date: date  # 期首
    end_date: date  # 期末
    opening_balance: Decimal  # 期首残高 (start_date 直前まで)
    closing_balance: Decimal  # 期末残高 (最終月適用後)
    points: list[MonthlyTrendPoint] = Field(default_factory=list)

    @property
    def net_change(self) -> Decimal:
        """The fiscal year's total movement (Σ of every month's ``net_change``)."""
        return sum((point.net_change for point in self.points), Decimal(0))

    @property
    def is_consistent(self) -> bool:
        """True when 期首残高 + Σ期中増減 = 期末残高 (the trend reconciles end to end)."""
        return self.opening_balance + self.net_change == self.closing_balance
