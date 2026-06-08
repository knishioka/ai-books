"""合成シードデータ (不動産所得) — one fictional 不動産賃貸の個人事業主 for KOA220 (Issue #124).

The main synthetic year (:mod:`.dataset`) is a 製造業 個人事業主; it exercises KOA210 (一般用) but
populates nothing on the 不動産所得用 (KOA220) 収入側. This module adds the parallel scenario KOA220's
収入側 data-supply needs: a fictional landlord's FY2025 journal (:data:`RE_ENTRIES`) **plus** the
contract metadata the journal cannot carry (:data:`RENTAL_PROPERTIES` / :data:`RENT_PAID_PAYEES` /
:data:`LOAN_LENDERS`).

The split mirrors the choice the issue fixed (journal amounts + contract fixture): every *amount* is a
勘定科目 balance the two golden paths derive independently (offline reduction vs. the SQL engine), while
the *metadata* (賃借人 / 賃貸契約期間 / 物件 / 支払先) is committed reference data keyed by 勘定科目コード.
:func:`real_estate_income_from_dataset` / :func:`real_estate_income_from_db` pair the two and hand them to
the production :func:`ai_books.aggregation.assemble_real_estate_income`, which fails loud if a property's
contract split does not foot to its 受取家賃 balance — so the contract breakdown cannot drift from the
books. No real data: a wholly fictional landlord, safe to commit.

This is FY2025 too (same period as :mod:`.dataset`), but a *separate* dataset, so it loads into its own
throwaway schema and never perturbs the manufacturing year's golden.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, NamedTuple

from ai_books import aggregation
from ai_books.models import (
    YEAR_END_ADJUSTMENT_SOURCE,
    EntrySide,
    EntryStatus,
    RealEstateIncome,
)

from .dataset import FISCAL_YEAR, FY_END, FY_START, SeedEntry, SeedLine
from .reports import trial_balance_from_dataset, trial_balance_from_db

if TYPE_CHECKING:
    import psycopg


def _yen(amount: int) -> Decimal:
    return Decimal(amount)


def _d(code: str, amount: int) -> SeedLine:
    """A 借方 (debit) line."""
    return SeedLine(code, EntrySide.DEBIT, _yen(amount))


def _c(code: str, amount: int) -> SeedLine:
    """A 貸方 (credit) line."""
    return SeedLine(code, EntrySide.CREDIT, _yen(amount))


def _entry(
    voucher_no: str,
    entry_date: date,
    description: str,
    *lines: SeedLine,
    source: str = "seed",
) -> SeedEntry:
    return SeedEntry(voucher_no, entry_date, description, lines, source)


# ── The landlord's fiscal year, in chronological order ──────────────────────────
# Two rented properties (4210 住宅用 / 4220 住宅用以外), ground rent paid (7250), a bank loan (2510) with
# its interest (8210), and the usual 不動産所得 経費 (固定資産税 7110 / 損害保険料 7180 / 建物減価償却
# 7210). Each property's 受取家賃 balance equals the contract split the fixture below declares, so the
# 内訳 foots to the books. Every entry balances on its own.
RE_ENTRIES: tuple[SeedEntry, ...] = (
    # 期首残高: 建物 + 普通預金 = 借入金 + 元入金 (11,000,000 = 8,000,000 + 3,000,000).
    _entry(
        "RE2025-000",
        date(2025, 1, 1),
        "期首残高 (不動産賃貸 開業/繰越)",
        _d("1510", 10_000_000),  # 建物 (賃貸物件)
        _d("1141", 1_000_000),  # 普通預金
        _c("2510", 8_000_000),  # 長期借入金
        _c("3110", 3_000_000),  # 元入金
    ),
    # 賃貸料収入 甲 (住宅用, 4210): 年額 1,200,000 + 礼金 100,000 = 本年中の収入金額 1,300,000.
    _entry(
        "RE2025-001",
        date(2025, 1, 31),
        "賃貸料収入 甲アパート (住宅用, 年額)",
        _d("1141", 1_200_000),
        _c("4210", 1_200_000),
    ),
    _entry(
        "RE2025-002",
        date(2025, 1, 31),
        "礼金 甲アパート (住宅用)",
        _d("1141", 100_000),
        _c("4210", 100_000),
    ),
    # 賃貸料収入 乙 (住宅用以外, 4220): 年額 900,000 + 更新料 60,000 = 本年中の収入金額 960,000.
    _entry(
        "RE2025-003",
        date(2025, 4, 1),
        "賃貸料収入 乙ビル (事業用, 年額)",
        _d("1141", 900_000),
        _c("4220", 900_000),
    ),
    _entry(
        "RE2025-004",
        date(2025, 4, 1),
        "更新料 乙ビル (事業用)",
        _d("1141", 60_000),
        _c("4220", 60_000),
    ),
    # 地代家賃 (底地の地代を地主へ支払): 賃借料 240,000 (全額 事業用 → 必要経費算入額も同額).
    _entry(
        "RE2025-005",
        date(2025, 6, 30),
        "地代家賃 (甲アパート底地)",
        _d("7250", 240_000),
        _c("1141", 240_000),
    ),
    # 借入金利子.
    _entry(
        "RE2025-006",
        date(2025, 7, 31),
        "借入金利子 (○○銀行)",
        _d("8210", 80_000),
        _c("1141", 80_000),
    ),
    # 借入金元金返済 (期末借入金残高 = 8,000,000 - 500,000 = 7,500,000).
    _entry(
        "RE2025-007",
        date(2025, 7, 31),
        "借入金 元金返済 (○○銀行)",
        _d("2510", 500_000),
        _c("1141", 500_000),
    ),
    # 固定資産税 (賃貸物件).
    _entry(
        "RE2025-008",
        date(2025, 8, 31),
        "固定資産税 (賃貸物件)",
        _d("7110", 150_000),
        _c("1141", 150_000),
    ),
    # 損害保険料 (賃貸物件).
    _entry(
        "RE2025-009",
        date(2025, 9, 30),
        "損害保険料 (賃貸物件)",
        _d("7180", 30_000),
        _c("1141", 30_000),
    ),
    # 期末整理: 建物 減価償却 (直接法).
    _entry(
        "RE2025-010",
        date(2025, 12, 31),
        "期末整理: 減価償却 (建物)",
        _d("7210", 400_000),
        _c("1510", 400_000),
        source=YEAR_END_ADJUSTMENT_SOURCE,
    ),
)


# ── 契約メタデータ (the fixture the journal cannot carry) ────────────────────────


class RentalPropertyMeta(NamedTuple):
    """One rented property's contract metadata + 本年中の収入金額 column split, keyed by 受取家賃 コード.

    ``account_code`` is the 受取家賃 勘定科目 whose journal balance is 本年中の収入金額; the split
    (``rent_annual`` + ``key_money`` + ``right_money`` + ``renewal_fee`` + ``name_change_other``) must
    foot to that balance. ``deposit`` (保証金・敷金) is carried for the form but is not income.
    """

    account_code: str
    property_type: str  # 貸家貸地等の別
    usage: str  # 用途
    location: str  # 不動産の所在地
    tenant_address: str  # 賃借人の住所
    tenant_name: str  # 賃借人の氏名
    contract_start_month: int  # 賃貸契約期間 (自) 月
    contract_end_month: int  # 賃貸契約期間 (至) 月
    rent_annual: Decimal  # 賃貸料年額
    key_money: Decimal  # 礼金
    right_money: Decimal  # 権利金
    renewal_fee: Decimal  # 更新料
    name_change_other: Decimal  # 名義書換料その他
    deposit: Decimal  # 保証金・敷金


class RentPaidMeta(NamedTuple):
    """One 地代家賃 支払先's contract metadata, keyed by the 地代家賃 勘定科目 holding 賃借料."""

    account_code: str
    payee_address: str  # 支払先の住所
    payee_name: str  # 支払先の氏名
    leased_property: str  # 賃借物件
    right_money: Decimal  # 権利金
    renewal_fee: Decimal  # 更新料


class LoanLenderMeta(NamedTuple):
    """One lender's 支払先 metadata + which 勘定科目 hold its 期末借入金残高 / 本年中の借入金利子."""

    loan_account_code: str  # 借入金 勘定科目 (期末現在の借入金等の金額)
    interest_account_code: str  # 借入金利子 勘定科目 (本年中の借入金利子)
    payee_address: str
    payee_name: str


#: 不動産所得の収入の内訳 — 物件ごとの契約メタ + 収入列の内訳 (本年中の収入金額 = 受取家賃 残高).
RENTAL_PROPERTIES: tuple[RentalPropertyMeta, ...] = (
    RentalPropertyMeta(
        account_code="4210",
        property_type="貸家",
        usage="住宅用",
        location="東京都杉並区○○1-2-3 甲アパート101",
        tenant_address="東京都杉並区○○4-5-6",
        tenant_name="賃借 一郎",
        contract_start_month=1,
        contract_end_month=12,
        rent_annual=Decimal(1_200_000),
        key_money=Decimal(100_000),
        right_money=Decimal(0),
        renewal_fee=Decimal(0),
        name_change_other=Decimal(0),
        deposit=Decimal(200_000),
    ),
    RentalPropertyMeta(
        account_code="4220",
        property_type="貸事務所",
        usage="住宅用以外",
        location="東京都中野区△△7-8-9 乙ビル2F",
        tenant_address="東京都新宿区△△10-11-12",
        tenant_name="乙商事株式会社",
        contract_start_month=4,
        contract_end_month=3,
        rent_annual=Decimal(900_000),
        key_money=Decimal(0),
        right_money=Decimal(0),
        renewal_fee=Decimal(60_000),
        name_change_other=Decimal(0),
        deposit=Decimal(0),
    ),
)

#: 地代家賃の内訳 — 賃借料 (と必要経費算入額) は 7250 残高、支払先/物件は契約メタ。
RENT_PAID_PAYEES: tuple[RentPaidMeta, ...] = (
    RentPaidMeta(
        account_code="7250",
        payee_address="東京都杉並区○○1-2-0",
        payee_name="底地 地主丙",
        leased_property="甲アパートの底地",
        right_money=Decimal(0),
        renewal_fee=Decimal(0),
    ),
)

#: 借入金利子の内訳 — 期末借入金残高 (2510) / 本年中の借入金利子 (8210) は残高、支払先は契約メタ。
LOAN_LENDERS: tuple[LoanLenderMeta, ...] = (
    LoanLenderMeta(
        loan_account_code="2510",
        interest_account_code="8210",
        payee_address="東京都千代田区□□1-1-1",
        payee_name="○○銀行 △△支店",
    ),
)


def _real_estate_income_from_balances(balances: dict[str, Decimal]) -> RealEstateIncome:
    """Pair the per-account journal balances with the contract metadata and assemble KOA220's 収入側.

    ``balances`` is 勘定科目コード → 正常残高方向の残高 (a trial-balance row's balance), so a 受取家賃 /
    地代家賃 / 借入金利子 figure is already signed positive and a 借入金 残高 is its 期末残高. Shared by
    both golden paths — only how ``balances`` is computed differs (offline vs. SQL).
    """

    def bal(code: str) -> Decimal:
        return balances.get(code, Decimal(0))

    rental = [
        aggregation.RentalIncomeTotals(
            account_code=p.account_code,
            income_total=bal(p.account_code),
            property_type=p.property_type,
            usage=p.usage,
            location=p.location,
            tenant_address=p.tenant_address,
            tenant_name=p.tenant_name,
            contract_start_month=p.contract_start_month,
            contract_end_month=p.contract_end_month,
            rent_annual=p.rent_annual,
            key_money=p.key_money,
            right_money=p.right_money,
            renewal_fee=p.renewal_fee,
            name_change_other=p.name_change_other,
            deposit=p.deposit,
        )
        for p in RENTAL_PROPERTIES
    ]
    rent_paid = [
        aggregation.RentPaidTotals(
            account_code=r.account_code,
            payee_address=r.payee_address,
            payee_name=r.payee_name,
            leased_property=r.leased_property,
            right_money=r.right_money,
            renewal_fee=r.renewal_fee,
            rent=bal(r.account_code),
            deductible_expense=bal(r.account_code),  # 全額 事業用 (家事按分なし)
        )
        for r in RENT_PAID_PAYEES
    ]
    loans = [
        aggregation.LoanInterestTotals(
            payee_address=lender.payee_address,
            payee_name=lender.payee_name,
            year_end_balance=bal(lender.loan_account_code),
            interest_paid=bal(lender.interest_account_code),
            deductible_interest=bal(lender.interest_account_code),  # 全額 必要経費算入
        )
        for lender in LOAN_LENDERS
    ]
    return aggregation.assemble_real_estate_income(
        rental, rent_paid, loans, fiscal_year=FISCAL_YEAR, start_date=FY_START, end_date=FY_END
    )


def real_estate_income_from_dataset(
    entries: tuple[SeedEntry, ...] = RE_ENTRIES,
) -> RealEstateIncome:
    """Reduce the in-memory landlord dataset into KOA220's 収入側 内訳 — no database required.

    Reduces the dataset to a trial balance offline (sharing the production signing rule), pairs each
    needed account balance with its contract metadata, and assembles via the production engine — so this
    generates the committed golden while staying a pure function of the dataset.
    """
    balances = {row.code: row.balance for row in trial_balance_from_dataset(entries).rows}
    return _real_estate_income_from_balances(balances)


def real_estate_income_from_db(
    conn: psycopg.Connection[Any], *, status: EntryStatus | None = EntryStatus.POSTED
) -> RealEstateIncome:
    """Compute KOA220's 収入側 内訳 from the DB via the production engine.

    Reads the trial balance through :meth:`ai_books.db.repository.LedgerRepository.trial_balance` and
    pairs the same account balances with the same contract metadata, so there is no second arithmetic
    path to drift from :func:`real_estate_income_from_dataset` — a divergence pins a storage/aggregation
    bug upstream.
    """
    balances = {row.code: row.balance for row in trial_balance_from_db(conn, status=status).rows}
    return _real_estate_income_from_balances(balances)
