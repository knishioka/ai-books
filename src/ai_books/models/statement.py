"""決算書 (financial-statement) models — 損益計算書 (P/L), and later 貸借対照表 (B/S, #21).

These are the 青色申告決算書 shapes derived from the 集計エンジン (#18): the trial balance's
per-account balances re-grouped into the 決算書 display sections (表示区分) with the staged
subtotals a reader expects (売上総利益 → 営業利益 → 経常利益 → 当期純利益). Like the report
shapes in :mod:`.report` they are code-oriented (科目コード + 科目名 inline, no DB ids) and keep
amounts as :class:`~decimal.Decimal` end to end (浮動小数禁止), so the same object is produced
identically offline (golden generation, no DB) and from Postgres, and the Vercel viewer (#25)
can render straight from them.

Each amount is signed into the account's 正常残高 direction — the one trial-balance convention
(:func:`ai_books.ledger.balance_from_totals`): a 収益 / 費用 line is positive when it carries its
normal balance, so a contra such as 期末商品棚卸高 (貸方計上) shows *negative* inside 売上原価 and
correctly reduces it. Summing a section's line amounts therefore yields its subtotal directly, and
the staged profits follow by plain subtraction — see :attr:`ProfitAndLoss.is_consistent`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import Field

from .base import DomainModel
from .enums import StatementCategory


class ProfitAndLossLine(DomainModel):
    """One account's contribution to the 損益計算書 (損益計算書の一行).

    ``amount`` is the account's balance signed into its 正常残高 direction (収益・費用 とも
    正常残高なら正), so it sums straight into the section subtotal. ``category`` is the
    表示区分 the account rolls up under, or ``None`` for an 未分類 account surfaced in
    :attr:`ProfitAndLoss.unclassified`.
    """

    code: str  # 勘定科目コード
    name: str  # 勘定科目名
    category: StatementCategory | None = None  # 表示区分 (未分類なら None)
    amount: Decimal  # 正常残高方向の金額


class ProfitAndLossSection(DomainModel):
    """One 段階表示 block of the P/L (e.g. 売上高 / 売上原価 / 販管費).

    ``key`` is the machine-stable section name (``sales`` / ``cost_of_goods_sold`` / …) and
    ``label`` its 日本語表示名. ``subtotal`` is exactly the sum of ``lines`` amounts (科目コード順).
    """

    key: str  # machine key (sales / cost_of_goods_sold / …)
    label: str  # 日本語表示名
    lines: list[ProfitAndLossLine] = Field(default_factory=list)
    subtotal: Decimal  # Σ lines.amount


class ProfitAndLoss(DomainModel):
    """損益計算書 (P/L) — the収益/費用 of a fiscal year staged into the 決算書 layout.

    ``cost_of_goods_sold`` folds the 製造原価 区分 (材料費 / 労務費 / 製造経費) in alongside
    the 売上原価 区分 (期首棚卸 + 仕入 - 期末棚卸): this is the *PL本体*, with the full
    製造原価報告書 breakout deferred to #23. The staged profits are derived purely by
    subtraction so they reconcile with the trial balance end to end:

    * ``gross_profit`` (売上総利益)   = 売上高 - 売上原価
    * ``operating_income`` (営業利益) = 売上総利益 - 販管費
    * ``ordinary_income`` (経常利益)  = 営業利益 + 営業外収益 - 営業外費用
    * ``net_income`` (当期純利益)     = 経常利益 (特別損益なし)

    ``unclassified`` holds any 収益/費用 account whose 表示区分 is unset (or not a P/L 区分):
    such accounts are *not* folded into any subtotal, so a gap is surfaced rather than silently
    swallowed (表示区分への科目集約が網羅的 — 未分類科目を検出).
    """

    fiscal_year: str  # 会計年度名 (例: 'FY2025')
    start_date: date  # 期首
    end_date: date  # 期末
    sales: ProfitAndLossSection  # 売上(収入)金額
    cost_of_goods_sold: ProfitAndLossSection  # 売上原価 (製造原価を含む)
    gross_profit: Decimal  # 売上総利益
    selling_admin_expenses: ProfitAndLossSection  # 販売費及び一般管理費 (経費)
    operating_income: Decimal  # 営業利益
    non_operating_income: ProfitAndLossSection  # 営業外収益
    non_operating_expenses: ProfitAndLossSection  # 営業外費用
    ordinary_income: Decimal  # 経常利益
    net_income: Decimal  # 当期純利益
    unclassified: list[ProfitAndLossLine] = Field(default_factory=list)  # 未分類科目

    @property
    def is_consistent(self) -> bool:
        """True when every 段階利益 follows from the section subtotals (各段階利益が整合)."""
        return (
            self.gross_profit == self.sales.subtotal - self.cost_of_goods_sold.subtotal
            and self.operating_income == self.gross_profit - self.selling_admin_expenses.subtotal
            and self.ordinary_income
            == self.operating_income
            + self.non_operating_income.subtotal
            - self.non_operating_expenses.subtotal
            and self.net_income == self.ordinary_income
        )
