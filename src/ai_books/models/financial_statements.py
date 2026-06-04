"""青色申告決算書 (blue-return financial statements) models — Issue #23.

The 青色申告決算書 is the headline 提出書類 of 青色申告 (65万円控除の中核): a four-面 form that
re-presents a fiscal year's books in the layout the 税務署 expects. This module is the
composed shape that gathers the pieces other Issues already produce — the 損益計算書
(:class:`~ai_books.models.ProfitAndLoss`, #20, 1面) and the 貸借対照表
(:class:`~ai_books.models.BalanceSheet`, #21, 4面) — and adds the 内訳 (breakdowns) the form
carries alongside them:

* **月別売上(収入)金額及び仕入金額** (2面) — :class:`MonthlySalesPurchases`,
* **減価償却費の計算** (3面) — :class:`DepreciationSchedule`,
* **製造原価の計算** (4面) — :class:`ManufacturingCost`.

Like every other report shape these are code-oriented (科目コード + 科目名 inline, no DB ids)
and keep amounts as :class:`~decimal.Decimal` end to end (浮動小数禁止), so the same object is
produced identically offline (golden generation, no DB) and from Postgres, and the Vercel
viewer (#25) renders straight from them.

The whole point of the form is that the 内訳 *reconcile* with the PL/BS totals, so the model
carries that as a single self-check — :attr:`FinancialStatements.is_consistent` — wiring each
breakdown back to the figure it must agree with (製造原価 → 売上原価, 月別売上 → 売上高,
減価償却 → PL 減価償却費 と BS 固定資産簿価). Each breakdown is derived purely from the journal /
勘定科目 data already in the books (no 固定資産マスタ / 従業員マスタ): 減価償却 reads each
固定資産勘定の当期減少額 (直接法では当期償却費), 製造原価 regroups the 製造原価科目 (6xxx) that
the PL本体 folds into 売上原価.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import Field

from .base import DomainModel
from .enums import StatementCategory
from .report import BalanceSheet
from .statement import ProfitAndLoss

#: The 製造原価 表示区分 (材料費 / 労務費 / 製造経費). A 売上原価 line tagged with one of these
#: is the *製造* portion of 売上原価; everything else is the 商品(merchandise)売上原価.
MANUFACTURING_CATEGORIES: frozenset[StatementCategory] = frozenset(
    {
        StatementCategory.MANUFACTURING_MATERIALS,
        StatementCategory.MANUFACTURING_LABOR,
        StatementCategory.MANUFACTURING_OVERHEAD,
    }
)


# ── 製造原価の計算 (4面) ──────────────────────────────────────────────────────────


class ManufacturingCostLine(DomainModel):
    """One 製造原価科目's contribution to a 製造原価 区分 (材料費 / 労務費 / 製造経費).

    ``amount`` is the account balance in its 正常残高 direction (製造原価科目 は費用なので、正常
    残高なら正), so it sums straight into the section subtotal.
    """

    code: str  # 勘定科目コード
    name: str  # 勘定科目名
    amount: Decimal  # 正常残高方向の金額


class ManufacturingCostSection(DomainModel):
    """One 製造原価 区分 block (材料費 / 労務費 / 製造経費).

    ``key`` is the machine-stable section name (``materials`` / ``labor`` / ``overhead``) and
    ``label`` its 日本語表示名; ``subtotal`` is exactly the sum of ``lines`` amounts (科目コード順).
    """

    key: str  # machine key (materials / labor / overhead)
    label: str  # 日本語表示名
    lines: list[ManufacturingCostLine] = Field(default_factory=list)
    subtotal: Decimal  # Σ lines.amount


class ManufacturingCost(DomainModel):
    """製造原価の計算 — 材料費 + 労務費 + 製造経費 staged into 当期製品製造原価.

    ``total_manufacturing_cost`` (当期製造費用) is the sum of the three 区分 subtotals;
    ``cost_of_goods_manufactured`` (当期製品製造原価) is that plus 期首仕掛品棚卸高 minus
    期末仕掛品棚卸高. The chart seeds no 仕掛品 (WIP) account, so the two are equal here — the
    仕掛品 carry is left for a future Issue rather than faked. This figure is the *製造* portion
    of the PL本体's 売上原価; :attr:`FinancialStatements.is_consistent` checks 売上原価 splits
    exactly into it plus the 商品売上原価.
    """

    materials: ManufacturingCostSection  # 材料費
    labor: ManufacturingCostSection  # 労務費
    overhead: ManufacturingCostSection  # 製造経費
    total_manufacturing_cost: Decimal  # 当期製造費用 (材料費 + 労務費 + 製造経費)
    cost_of_goods_manufactured: Decimal  # 当期製品製造原価 (= 当期製造費用; 仕掛品なし)

    @property
    def is_consistent(self) -> bool:
        """True when 当期製造費用 foots the 区分 subtotals and 当期製品製造原価 follows."""
        return (
            self.total_manufacturing_cost
            == self.materials.subtotal + self.labor.subtotal + self.overhead.subtotal
            and self.cost_of_goods_manufactured == self.total_manufacturing_cost
        )


# ── 月別売上(収入)金額及び仕入金額 (2面) ─────────────────────────────────────────


class MonthlySalesPurchasesRow(DomainModel):
    """One calendar month's 売上(収入)金額 and 仕入金額.

    ``sales`` is the net 売上高 posted that month (収益 正常残高方向, 正); ``purchases`` the net
    仕入 (仕入高 + 原材料仕入高; 費用 正常残高方向, 正). A quiet month carries zeros so the series
    tiles the whole fiscal year (12 行) like the form.
    """

    month: str  # 'YYYY-MM'
    sales: Decimal  # 売上(収入)金額
    purchases: Decimal  # 仕入金額


class MonthlySalesPurchases(DomainModel):
    """月別売上(収入)金額及び仕入金額 — every month's 売上 / 仕入 with the column footings.

    ``rows`` tile the fiscal year in chronological order. ``sales_total`` /
    ``purchases_total`` are the column footings; ``sales_total`` must equal the PL's 売上高
    (:attr:`FinancialStatements.is_consistent`), and both foot their column (:attr:`is_consistent`).
    """

    rows: list[MonthlySalesPurchasesRow] = Field(default_factory=list)
    sales_total: Decimal  # 売上(収入)金額 合計
    purchases_total: Decimal  # 仕入金額 合計

    @property
    def is_consistent(self) -> bool:
        """True when each column footing equals the sum of its rows."""
        return self.sales_total == sum(
            (row.sales for row in self.rows), Decimal(0)
        ) and self.purchases_total == sum((row.purchases for row in self.rows), Decimal(0))


# ── 減価償却費の計算 (3面) ────────────────────────────────────────────────────────


class DepreciationLine(DomainModel):
    """One 固定資産's row of the 減価償却費の計算.

    Derived from the 固定資産勘定 itself (直接法): ``acquisition_cost`` (取得価額) is the account's
    累計借方 (期首簿価 + 当期取得) up to 期末, ``depreciation_expense`` (本年分の償却費) is the
    当期の貸方 (当期減少額 — 直接法では償却費), and ``closing_book_value`` (期末未償却残高) the
    期末簿価 in 正常残高方向. The 期末簿価 equals this account's line on the 貸借対照表.
    """

    code: str  # 勘定科目コード (固定資産)
    name: str  # 勘定科目名
    acquisition_cost: Decimal  # 取得価額 (累計借方, ≤ 期末)
    depreciation_expense: Decimal  # 本年分の償却費 (当期の貸方 = 当期減少額)
    closing_book_value: Decimal  # 期末未償却残高 (期末簿価, 正常残高方向)


class DepreciationSchedule(DomainModel):
    """減価償却費の計算 — every depreciated 固定資産 this year, footed against PL.

    ``lines`` are the 固定資産勘定 that recorded 当期償却 (当期の貸方), 科目コード順.
    ``total_depreciation`` foots their 本年分の償却費; ``expense_total`` is the PL's 減価償却費
    (経費 + 製造経費) the schedule must agree with. :attr:`is_consistent` checks both — that the
    asset-side 償却費 foots its rows *and* equals the expense-side figure (固定資産データと整合).
    """

    lines: list[DepreciationLine] = Field(default_factory=list)
    total_depreciation: Decimal  # 本年分の償却費 合計 (固定資産の当期減少額)
    expense_total: Decimal  # PL 減価償却費 (経費 + 製造経費); 突合先

    @property
    def is_consistent(self) -> bool:
        """True when the asset-side 償却費 foots its rows and equals the PL 減価償却費."""
        return (
            self.total_depreciation
            == sum((line.depreciation_expense for line in self.lines), Decimal(0))
            and self.total_depreciation == self.expense_total
        )


# ── 青色申告決算書 (the composed 4-面 form) ──────────────────────────────────────


class FinancialStatements(DomainModel):
    """青色申告決算書 (aoiro kessansho) — the PL/BS plus their 内訳, reconciled.

    Gathers the four 面 of the form: the 損益計算書 (1面) and 貸借対照表 (4面) verbatim, the
    月別売上(収入)金額及び仕入金額 (2面), the 減価償却費の計算 (3面), and the 製造原価の計算 (4面).
    Every breakdown is derived from the same books the PL/BS are, so :attr:`is_consistent` can
    tie each one back to its PL/BS figure — that mutual reconciliation is the whole value of the
    form, and the one thing the golden harness freezes.
    """

    fiscal_year: str  # 会計年度名 (例: 'FY2025')
    start_date: date  # 期首
    end_date: date  # 期末
    profit_and_loss: ProfitAndLoss  # 1面: 損益計算書
    monthly: MonthlySalesPurchases  # 2面: 月別売上(収入)金額及び仕入金額
    depreciation: DepreciationSchedule  # 3面: 減価償却費の計算
    balance_sheet: BalanceSheet  # 4面: 貸借対照表
    manufacturing_cost: ManufacturingCost  # 4面: 製造原価の計算

    @property
    def merchandise_cost_of_sales(self) -> Decimal:
        """商品売上原価 — the 売上原価 lines that are *not* 製造原価 (期首商品 + 仕入 - 期末商品)."""
        return sum(
            (
                line.amount
                for line in self.profit_and_loss.cost_of_goods_sold.lines
                if line.category not in MANUFACTURING_CATEGORIES
            ),
            Decimal(0),
        )

    @property
    def is_consistent(self) -> bool:
        """True when the PL/BS are internally sound *and* every 内訳 reconciles with them.

        The composed self-check: the 損益計算書 stages reconcile, the 貸借対照表 balances, each
        breakdown foots its own columns, and across statements —

        * 売上原価 = 商品売上原価 + 当期製品製造原価 (製造原価が売上原価と整合),
        * 月別売上合計 = 売上高 (月別内訳が PL と一致),
        * 減価償却の本年分償却費合計 = PL 減価償却費 (減価償却が固定資産データと整合), and
        * each 固定資産の期末簿価 equals its 貸借対照表 上の残高.
        """
        pl = self.profit_and_loss
        return (
            pl.is_consistent
            and self.balance_sheet.is_balanced
            and self.manufacturing_cost.is_consistent
            and self.monthly.is_consistent
            and self.depreciation.is_consistent
            and pl.cost_of_goods_sold.subtotal
            == self.merchandise_cost_of_sales + self.manufacturing_cost.cost_of_goods_manufactured
            and self.monthly.sales_total == pl.sales.subtotal
            and self._depreciation_ties_to_balance_sheet()
        )

    def _depreciation_ties_to_balance_sheet(self) -> bool:
        """True when every 減価償却 line's 期末簿価 equals its 固定資産 line on the B/S."""
        fixed_asset_balances = {
            line.code: line.balance
            for section in self.balance_sheet.assets
            if section.category is StatementCategory.FIXED_ASSETS
            for line in section.lines
        }
        return all(
            fixed_asset_balances.get(line.code) == line.closing_book_value
            for line in self.depreciation.lines
        )
