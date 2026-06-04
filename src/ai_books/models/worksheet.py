"""精算表 (worksheet) result models (DTOs) for Issue #22.

A 精算表 (8桁ワークシート) lays the 決算過程 out as one table so the flow from the
試算表 to the 損益計算書 / 貸借対照表 is auditable end to end (AI も人間も決算の流れを追える):
each account carries four column-pairs —

* **残高試算表** (``trial_debit`` / ``trial_credit``) — the *unadjusted* balance, before
  期末整理, placed on the side its net falls on;
* **修正記入** (``adjustment_debit`` / ``adjustment_credit``) — the 期末整理仕訳 footings
  (減価償却 / 棚卸 / 経過勘定 / 家事按分 等), shown gross so each adjustment is visible;
* **損益計算書欄** (``pl_debit`` / ``pl_credit``) — the *adjusted* balance of a 収益 / 費用
  account; and
* **貸借対照表欄** (``bs_debit`` / ``bs_credit``) — the *adjusted* balance of a 資産 / 負債 /
  純資産 account.

Like every other domain model, amounts are :class:`~decimal.Decimal` end to end (浮動小数
禁止). The worksheet's self-check (精算表の自己検算) is that 当期純利益 computed from the P/L
columns equals the figure implied by the B/S columns — :attr:`Worksheet.is_consistent`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import Field

from .base import DomainModel
from .enums import AccountType


class WorksheetRow(DomainModel):
    """One account's row across the 精算表 — the four column-pairs for that 勘定科目.

    Each pair holds an amount on at most one side (the other is zero): the 残高試算表 and
    P/L・B/S columns carry the account's *net* balance placed on whichever side it falls,
    while 修正記入 carries the gross 借方 / 貸方 footings of that account's 期末整理仕訳 (an
    account can be adjusted on both sides). ``account_type`` is what routes the adjusted
    balance to the P/L (収益 / 費用) or the B/S (資産 / 負債 / 純資産) columns.
    """

    code: str  # 勘定科目コード
    name: str  # 勘定科目名
    account_type: AccountType  # 科目区分 (P/L か B/S かを決める)
    trial_debit: Decimal  # 残高試算表 借方
    trial_credit: Decimal  # 残高試算表 貸方
    adjustment_debit: Decimal  # 修正記入 借方 (期末整理仕訳)
    adjustment_credit: Decimal  # 修正記入 貸方 (期末整理仕訳)
    pl_debit: Decimal  # 損益計算書欄 借方 (費用)
    pl_credit: Decimal  # 損益計算書欄 貸方 (収益)
    bs_debit: Decimal  # 貸借対照表欄 借方 (資産)
    bs_credit: Decimal  # 貸借対照表欄 貸方 (負債・純資産)


class Worksheet(DomainModel):
    """精算表 — every touched account's four column-pairs plus the column footings.

    ``rows`` stay ordered by 勘定科目コード. The ``*_total`` fields are the plain sums of
    each column; ``net_income`` (当期純利益) is the balancing figure: the P/L columns foot
    once it is added to the 借方 side (収益 - 費用), the B/S columns once it is added to the
    貸方 side (純資産の増加). Because the underlying books balance (借貸平均), the two
    derivations agree — that equality is the worksheet's 自己検算 (:attr:`is_consistent`).
    """

    fiscal_year: str  # 会計年度名 (例: 'FY2025')
    start_date: date  # 期首 (inclusive)
    end_date: date  # 期末 (inclusive)
    rows: list[WorksheetRow] = Field(default_factory=list)
    trial_debit_total: Decimal  # 残高試算表 借方合計
    trial_credit_total: Decimal  # 残高試算表 貸方合計
    adjustment_debit_total: Decimal  # 修正記入 借方合計
    adjustment_credit_total: Decimal  # 修正記入 貸方合計
    pl_debit_total: Decimal  # 損益計算書欄 借方合計 (費用計, 当期純利益を含まない)
    pl_credit_total: Decimal  # 損益計算書欄 貸方合計 (収益計)
    bs_debit_total: Decimal  # 貸借対照表欄 借方合計 (資産計)
    bs_credit_total: Decimal  # 貸借対照表欄 貸方合計 (負債・純資産計, 当期純利益を含まない)
    net_income: Decimal  # 当期純利益 (損益計算書欄から: 収益計 - 費用計)

    @property
    def pl_net_income(self) -> Decimal:
        """当期純利益 as the P/L columns imply it (収益計 - 費用計)."""
        return self.pl_credit_total - self.pl_debit_total

    @property
    def bs_net_income(self) -> Decimal:
        """当期純利益 as the B/S columns imply it (資産計 - (負債 + 純資産)計)."""
        return self.bs_debit_total - self.bs_credit_total

    @property
    def is_trial_balanced(self) -> bool:
        """True when the 残高試算表 column footings are equal (借貸平均)."""
        return self.trial_debit_total == self.trial_credit_total

    @property
    def is_adjustments_balanced(self) -> bool:
        """True when the 修正記入 column footings are equal (整理仕訳も借貸が揃う)."""
        return self.adjustment_debit_total == self.adjustment_credit_total

    @property
    def is_consistent(self) -> bool:
        """True when every column foots and 当期純利益 agrees across the P/L and B/S欄.

        The full 自己検算: the 残高試算表 and 修正記入 columns each balance, and the 当期純利益
        derived from the 損益計算書欄 equals the one implied by the 貸借対照表欄 (and the
        stored :attr:`net_income`). Holds exactly when the books behind it balance.
        """
        return (
            self.is_trial_balanced
            and self.is_adjustments_balanced
            and self.pl_net_income == self.bs_net_income == self.net_income
        )
