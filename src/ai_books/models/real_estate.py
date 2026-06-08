"""不動産所得 (real-estate income) 内訳 models — KOA220 data-supply (Issue #124).

The 青色申告決算書(不動産所得用) (e-Tax 様式 **KOA220**) carries 内訳 (breakdowns) the general
form (KOA210) has no place for: the property-by-property 賃貸料収入, the 地代家賃 paid, and the
借入金利子 paid. KOA220's income side cannot be produced from the journal alone — a 賃貸料 figure is
journal-derivable, but a 賃借人 (tenant), a 賃貸契約期間, or a 物件所在地 is **not** an attribute of any
勘定科目. So this module is the shape of that supply path: each 内訳 row pairs a *journal-derived amount*
with the *contract metadata* the form needs, and the composed :class:`RealEstateIncome` reconciles the
two so a drift between the books and the contract breakdown surfaces rather than hides.

Like every other report shape these are code-oriented (科目コード inline, no DB ids) and keep amounts as
:class:`~decimal.Decimal` end to end (浮動小数禁止), so the same object is produced identically offline
(golden generation, no DB) and from Postgres. This stage supplies the **収入側** 内訳 the issue calls out
(不動産所得の収入の内訳 / 地代家賃の内訳 / 借入金利子の内訳); registering the KOA220
:class:`~ai_books.etax.spec.EtaxFormatSpec` and the end-to-end ``.xtx`` golden is the follow-up (stage 4).

The journal/contract boundary, per row:

* **賃貸料収入の内訳** — the property's *total* rental income (賃貸料 + 礼金 + 権利金 + 更新料 +
  名義書換料その他) is the journal balance of its 受取家賃 勘定科目 (本年中の収入金額); how that total
  splits across the form's columns, plus 賃借人 / 契約期間 / 物件, is contract metadata. 保証金・敷金 is a
  returnable deposit (not income) carried for the form, not part of the journal total.
* **地代家賃の内訳** — 賃借料 (and the 必要経費算入額 portion) is journal-derived; 支払先 / 賃借物件 /
  権利金・更新料 are contract metadata.
* **借入金利子の内訳** — 期末借入金残高 and 本年中の借入金利子 are journal-derived; 支払先 is metadata.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import Field

from .base import DomainModel

# ── 不動産所得の収入の内訳 (rental income breakdown) ─────────────────────────────────


class RentalIncomeLine(DomainModel):
    """One rented property's row of the 不動産所得の収入の内訳 (KOA220 ANF003xx-005xx).

    The form splits 本年中の収入金額 into columns (賃貸料年額 / 礼金 / 権利金 / 更新料 / 名義書換料
    その他 / 保証金・敷金). The first five are *income* and their sum is :attr:`income_subtotal`, which the
    aggregation pins to the property's 受取家賃 勘定科目 journal balance (本年中の収入金額) — so the
    contract split cannot silently disagree with the books. ``deposit`` (保証金・敷金) is a returnable
    deposit shown on the form but excluded from income, hence not part of ``income_subtotal``.

    Everything else is contract metadata the journal does not carry: ``property_type`` (貸家貸地等の別),
    ``usage`` (用途: 住宅用 / 住宅用以外), ``location`` (不動産の所在地), ``tenant_*`` (賃借人), and the
    ``contract_start_month`` / ``contract_end_month`` of the 賃貸契約期間.
    """

    account_code: str  # 受取家賃 勘定科目コード (the journal source of 本年中の収入金額)
    property_type: str  # 貸家貸地等の別
    usage: str  # 用途 (住宅用 / 住宅用以外 等)
    location: str  # 不動産の所在地
    tenant_address: str  # 賃借人の住所
    tenant_name: str  # 賃借人の氏名
    contract_start_month: int  # 賃貸契約期間 (自) — 月
    contract_end_month: int  # 賃貸契約期間 (至) — 月
    rent_annual: Decimal  # 賃貸料年額
    key_money: Decimal  # 礼金
    right_money: Decimal  # 権利金
    renewal_fee: Decimal  # 更新料
    name_change_other: Decimal  # 名義書換料その他
    deposit: Decimal  # 保証金・敷金 (返還預り金; 収入ではない)

    @property
    def income_subtotal(self) -> Decimal:
        """本年中の収入金額 — 賃貸料年額 + 礼金 + 権利金 + 更新料 + 名義書換料その他 (保証金除く)."""
        return (
            self.rent_annual
            + self.key_money
            + self.right_money
            + self.renewal_fee
            + self.name_change_other
        )


# ── 地代家賃の内訳 (rent paid breakdown) ─────────────────────────────────────────────


class RentPaidLine(DomainModel):
    """One 支払先's row of the 地代家賃の内訳 (KOA220 ANF0118x-0125x).

    ``rent`` (賃借料) and ``deductible_expense`` (左の賃借料のうち必要経費算入額) are journal-derived
    (the gross 地代家賃 posting and the portion left after any 家事按分); 支払先 / 賃借物件 and the
    one-off ``right_money`` / ``renewal_fee`` are contract metadata.
    """

    account_code: str  # 地代家賃 勘定科目コード
    payee_address: str  # 支払先の住所
    payee_name: str  # 支払先の氏名
    leased_property: str  # 賃借物件
    right_money: Decimal  # 権利金
    renewal_fee: Decimal  # 更新料
    rent: Decimal  # 賃借料 (本年中の賃借料, gross)
    deductible_expense: Decimal  # 左の賃借料のうち必要経費算入額


# ── 借入金利子の内訳 (loan interest breakdown) ───────────────────────────────────────


class LoanInterestLine(DomainModel):
    """One 支払先's row of the 借入金利子の内訳 (KOA220 ANF0128x-0132x).

    ``year_end_balance`` (期末現在の借入金等の金額) is the lender's 借入金 勘定科目 期末残高,
    ``interest_paid`` (本年中の借入金利子) the year's interest posting, and ``deductible_interest``
    (左のうち必要経費算入額) the portion deductible — all journal-derived; 支払先 is metadata.
    """

    payee_address: str  # 支払先の住所
    payee_name: str  # 支払先の氏名
    year_end_balance: Decimal  # 期末現在の借入金等の金額
    interest_paid: Decimal  # 本年中の借入金利子
    deductible_interest: Decimal  # 左のうち必要経費算入額


# ── 不動産所得の収入側 内訳 (the composed supply path) ───────────────────────────────


class RealEstateIncome(DomainModel):
    """不動産所得 (KOA220) の収入側 内訳 — the three breakdowns plus their footings, reconciled.

    Gathers the 収入側 内訳 KOA220 needs: 不動産所得の収入の内訳 (per property), 地代家賃の内訳, and
    借入金利子の内訳. Each breakdown is derived from the same books the KOA220 損益計算書 would be, so
    :attr:`is_consistent` ties every column footing back to its rows — that reconciliation is the value of
    the supply path and the one thing the golden harness freezes.

    ``gross_income`` (不動産所得の収入金額) foots the income breakdown's income columns; the deposit total
    is carried separately because 保証金・敷金 is not income.
    """

    fiscal_year: str  # 会計年度名 (例: 'FY2025')
    start_date: date  # 期首
    end_date: date  # 期末
    rental_income_lines: list[RentalIncomeLine] = Field(default_factory=list)
    rent_paid_lines: list[RentPaidLine] = Field(default_factory=list)
    loan_interest_lines: list[LoanInterestLine] = Field(default_factory=list)
    rent_annual_total: Decimal  # 賃貸料年額 計 (ANF00570)
    key_right_renewal_total: Decimal  # 礼金・権利金・更新料 計 (ANF00580)
    name_change_other_total: Decimal  # 名義書換料その他 計 (ANF00590)
    deposit_total: Decimal  # 保証金・敷金 計 (ANF00600; 収入ではない)
    gross_income: Decimal  # 不動産所得の収入金額 (収入列の総計)
    rent_paid_total: Decimal  # 地代家賃 (賃借料) 計
    rent_paid_deductible_total: Decimal  # 地代家賃 必要経費算入額 計
    loan_year_end_balance_total: Decimal  # 期末現在の借入金等の金額 計
    loan_interest_total: Decimal  # 本年中の借入金利子 計
    loan_interest_deductible_total: Decimal  # 借入金利子 必要経費算入額 計

    @property
    def is_consistent(self) -> bool:
        """True when every column footing equals the sum of its rows (内訳が計と一致).

        * 賃貸料年額計 / 礼金権利金更新料計 / 名義書換料計 / 保証金敷金計 each foot the income rows,
        * 収入金額 = 賃貸料年額計 + 礼金権利金更新料計 + 名義書換料計 (保証金は収入外), and
        * the 地代家賃 / 借入金利子 footings each foot their own rows.
        """
        income = self.rental_income_lines
        rent = self.rent_paid_lines
        loans = self.loan_interest_lines
        return (
            self.rent_annual_total == sum((line.rent_annual for line in income), Decimal(0))
            and self.key_right_renewal_total
            == sum(
                (line.key_money + line.right_money + line.renewal_fee for line in income),
                Decimal(0),
            )
            and self.name_change_other_total
            == sum((line.name_change_other for line in income), Decimal(0))
            and self.deposit_total == sum((line.deposit for line in income), Decimal(0))
            and self.gross_income
            == self.rent_annual_total + self.key_right_renewal_total + self.name_change_other_total
            and self.gross_income == sum((line.income_subtotal for line in income), Decimal(0))
            and self.rent_paid_total == sum((line.rent for line in rent), Decimal(0))
            and self.rent_paid_deductible_total
            == sum((line.deductible_expense for line in rent), Decimal(0))
            and self.loan_year_end_balance_total
            == sum((line.year_end_balance for line in loans), Decimal(0))
            and self.loan_interest_total == sum((line.interest_paid for line in loans), Decimal(0))
            and self.loan_interest_deductible_total
            == sum((line.deductible_interest for line in loans), Decimal(0))
        )
