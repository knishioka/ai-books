"""合成シードデータ (農業所得) — one fictional 農業の個人事業主 for KOA240 (Issue #125).

The main synthetic year (:mod:`.dataset`) is a 製造業 個人事業主 and :mod:`.real_estate` a landlord;
neither populates the 農業所得用 (KOA240) 収入側. This module adds the parallel scenario KOA240's 収入側
data-supply needs: a fictional farmer's FY2025 journal (:data:`AG_ENTRIES`) **plus** the descriptive
metadata the journal cannot carry (:data:`CROPS` / :data:`LIVESTOCK` / :data:`MISC_INCOME` and the carried
:data:`UNHARVESTED` / :data:`SALE_ANIMALS` / :data:`CULTIVATION_COSTS` schedules).

The split mirrors the choice #124 fixed (journal amounts + descriptive fixture): every *income amount* is a
勘定科目 balance the two golden paths derive independently (offline reduction vs. the SQL engine) —
販売金額 per 農産物売上高 / 畜産物売上高 category (4310-4340), 家事消費・事業消費 (4350), 雑収入(農業)
(4360), and 農産物期末棚卸高 (農産物 棚卸資産 1185) — while the *metadata* (区分 / 作付面積 / 収穫量 /
頭羽数) and the carried 棚卸 / 育成費用 schedules are committed reference data.
:func:`agricultural_income_from_dataset` / :func:`agricultural_income_from_db` pair the two and hand them to
the production :func:`ai_books.aggregation.assemble_agricultural_income`, which fails loud if a category's
内訳 does not foot to its 勘定科目 balance — so the breakdown cannot drift from the books. The 農産物期首
棚卸高 is the prior-year carryover (committed metadata, not a current-year posting); the synthetic farm
carries it nonzero to exercise the 収入金額 計 = 小計 - 期首 + 期末 formula. No real data: a wholly
fictional farmer, safe to commit.

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
    AgriculturalIncome,
    EntrySide,
    EntryStatus,
)

from .dataset import FISCAL_YEAR, FY_END, FY_START, SeedEntry, SeedLine
from .reports import trial_balance_from_dataset, trial_balance_from_db

if TYPE_CHECKING:
    import psycopg

# 家事消費・事業消費 / 雑収入 / 農産物棚卸 を集計する勘定科目 (内訳が foot する先)。
HOME_CONSUMPTION_ACCOUNT = "4350"
INVENTORY_ACCOUNT = "1185"


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


# ── The farmer's fiscal year, in chronological order ────────────────────────────
# 農産物販売 (田畑 4310 / 果樹 4320 / 特殊施設 4330) と畜産物 (4340)、家事消費・事業消費 (4350)、
# 雑収入 (4360)、期末農産物棚卸高 (期末整理で 農産物 1185 へ計上) を仕訳で表す。各カテゴリの販売金額は
# 下の fixture が宣言する内訳の合計と一致し (foot)、期末棚卸 1,185 残高も内訳と一致する。各仕訳は自己均衡。
AG_ENTRIES: tuple[SeedEntry, ...] = (
    # 期首残高 (農業 開業/繰越): 普通預金 = 元入金。
    _entry(
        "AG2025-000",
        date(2025, 1, 1),
        "期首残高 (農業 開業/繰越)",
        _d("1141", 1_000_000),  # 普通預金
        _c("3110", 1_000_000),  # 元入金
    ),
    # 農産物販売 — 田畑 (4310): 米 800,000 + 麦 300,000 = 1,100,000。
    _entry(
        "AG2025-001",
        date(2025, 9, 30),
        "農産物販売 米 (田畑)",
        _d("1141", 800_000),
        _c("4310", 800_000),
    ),
    _entry(
        "AG2025-002",
        date(2025, 10, 31),
        "農産物販売 麦 (田畑)",
        _d("1141", 300_000),
        _c("4310", 300_000),
    ),
    # 農産物販売 — 果樹 (4320): みかん 400,000。
    _entry(
        "AG2025-003",
        date(2025, 11, 30),
        "農産物販売 みかん (果樹)",
        _d("1141", 400_000),
        _c("4320", 400_000),
    ),
    # 農産物販売 — 特殊施設 (4330): トマト 500,000。
    _entry(
        "AG2025-004",
        date(2025, 7, 31),
        "農産物販売 トマト (特殊施設)",
        _d("1141", 500_000),
        _c("4330", 500_000),
    ),
    # 畜産物販売 (4340): 肉用牛 1,200,000 + 鶏卵 600,000 = 1,800,000。
    _entry(
        "AG2025-005",
        date(2025, 6, 30),
        "畜産物販売 肉用牛",
        _d("1141", 1_200_000),
        _c("4340", 1_200_000),
    ),
    _entry(
        "AG2025-006",
        date(2025, 12, 20),
        "畜産物販売 鶏卵",
        _d("1141", 600_000),
        _c("4340", 600_000),
    ),
    # 家事消費・事業消費 (4350): 農産物 110,000 + 畜産物 30,000 = 140,000 (事業主貸へ)。
    _entry(
        "AG2025-007",
        date(2025, 12, 31),
        "家事消費・事業消費 (農産物)",
        _d("1290", 110_000),
        _c("4350", 110_000),
    ),
    _entry(
        "AG2025-008",
        date(2025, 12, 31),
        "家事消費・事業消費 (畜産物)",
        _d("1290", 30_000),
        _c("4350", 30_000),
    ),
    # 雑収入 (4360): 共済受取金 80,000 + 作業受託収入 120,000 = 200,000。
    _entry(
        "AG2025-009", date(2025, 8, 31), "雑収入 共済受取金", _d("1141", 80_000), _c("4360", 80_000)
    ),
    _entry(
        "AG2025-010",
        date(2025, 11, 30),
        "雑収入 作業受託収入",
        _d("1141", 120_000),
        _c("4360", 120_000),
    ),
    # 期末整理: 農産物期末棚卸高の計上 (農産物 1185 へ; 期首棚卸は前年繰越メタなので仕訳しない)。
    _entry(
        "AG2025-011",
        date(2025, 12, 31),
        "期末整理: 農産物 期末棚卸高 計上",
        _d("1185", 250_000),
        _c("4370", 250_000),
        source=YEAR_END_ADJUSTMENT_SOURCE,
    ),
)


# ── 内訳メタデータ (the descriptive fixture the journal cannot carry) ─────────────


class CropMeta(NamedTuple):
    """One crop's 農産物の収入の内訳 row: 販売金額/家事消費/期末棚卸 の内訳 + 区分/面積/収穫量 メタ.

    ``sales_amount`` foots (per ``account_code``) to the 農産物売上高 balance, ``home_consumption`` (across
    all crops + livestock) to 4350, and ``closing_inventory_amount`` (across all crops) to 1185.
    ``opening_inventory_amount`` is the prior-year carryover (metadata, not journal-anchored).
    """

    account_code: str
    category: str
    crop_name: str
    planted_area: Decimal
    harvest_quantity: Decimal
    opening_inventory_qty: Decimal
    opening_inventory_amount: Decimal
    sales_amount: Decimal
    home_consumption: Decimal
    closing_inventory_qty: Decimal
    closing_inventory_amount: Decimal


class LivestockMeta(NamedTuple):
    """One 畜産物その他 row: 販売金額/家事消費 の内訳 + 区分/飼育・生産頭羽数 メタ."""

    account_code: str
    category_name: str
    raised_count: Decimal
    produced_count: Decimal
    sales_amount: Decimal
    home_consumption: Decimal


class MiscMeta(NamedTuple):
    """One 雑収入 row: 金額 の内訳 + 区分 メタ."""

    account_code: str
    category_name: str
    amount: Decimal


class InventoryScheduleMeta(NamedTuple):
    """One 未収穫農産物 / 販売用動物 棚卸明細 row (carried; reconciled internally)."""

    category_name: str
    opening_qty: str
    opening_amount: Decimal
    closing_qty: str
    closing_amount: Decimal


class CultivationCostMeta(NamedTuple):
    """One 果樹・牛馬等の育成費用 row (carried; 小計/翌年繰越 は集計側で導出)."""

    name: str
    opening_carryover: Decimal
    seedling_cost: Decimal
    fertilizer_cost: Decimal
    income_from_growing: Decimal
    matured_acquisition_cost: Decimal
    added_to_acquisition_cost: Decimal


#: 農産物の収入の内訳 — 田畑 (4310) / 果樹 (4320) / 特殊施設 (4330); 販売金額/家事消費/期末棚卸 は科目残高。
CROPS: tuple[CropMeta, ...] = (
    CropMeta(
        account_code="4310",
        category="田畑",
        crop_name="米",
        planted_area=Decimal(120),
        harvest_quantity=Decimal(6_000),
        opening_inventory_qty=Decimal(400),
        opening_inventory_amount=Decimal(80_000),
        sales_amount=Decimal(800_000),
        home_consumption=Decimal(50_000),
        closing_inventory_qty=Decimal(500),
        closing_inventory_amount=Decimal(100_000),
    ),
    CropMeta(
        account_code="4310",
        category="田畑",
        crop_name="麦",
        planted_area=Decimal(80),
        harvest_quantity=Decimal(3_200),
        opening_inventory_qty=Decimal(200),
        opening_inventory_amount=Decimal(40_000),
        sales_amount=Decimal(300_000),
        home_consumption=Decimal(20_000),
        closing_inventory_qty=Decimal(250),
        closing_inventory_amount=Decimal(50_000),
    ),
    CropMeta(
        account_code="4320",
        category="果樹",
        crop_name="みかん",
        planted_area=Decimal(60),
        harvest_quantity=Decimal(4_000),
        opening_inventory_qty=Decimal(300),
        opening_inventory_amount=Decimal(50_000),
        sales_amount=Decimal(400_000),
        home_consumption=Decimal(30_000),
        closing_inventory_qty=Decimal(360),
        closing_inventory_amount=Decimal(60_000),
    ),
    CropMeta(
        account_code="4330",
        category="特殊施設",
        crop_name="トマト (ハウス)",
        planted_area=Decimal(20),
        harvest_quantity=Decimal(8_000),
        opening_inventory_qty=Decimal(150),
        opening_inventory_amount=Decimal(30_000),
        sales_amount=Decimal(500_000),
        home_consumption=Decimal(10_000),
        closing_inventory_qty=Decimal(200),
        closing_inventory_amount=Decimal(40_000),
    ),
)

#: 畜産物その他 — 販売金額/家事消費 は 4340 / 4350 残高、頭羽数は内訳メタ。
LIVESTOCK: tuple[LivestockMeta, ...] = (
    LivestockMeta(
        account_code="4340",
        category_name="肉用牛",
        raised_count=Decimal(10),
        produced_count=Decimal(5),
        sales_amount=Decimal(1_200_000),
        home_consumption=Decimal(0),
    ),
    LivestockMeta(
        account_code="4340",
        category_name="鶏卵",
        raised_count=Decimal(500),
        produced_count=Decimal(0),
        sales_amount=Decimal(600_000),
        home_consumption=Decimal(30_000),
    ),
)

#: 雑収入 — 金額は 4360 残高、区分は内訳メタ。
MISC_INCOME: tuple[MiscMeta, ...] = (
    MiscMeta(account_code="4360", category_name="共済受取金", amount=Decimal(80_000)),
    MiscMeta(account_code="4360", category_name="作業受託収入", amount=Decimal(120_000)),
)

#: 未収穫農産物 (carried; 計が内訳と一致するよう内部で reconcile)。
UNHARVESTED: tuple[InventoryScheduleMeta, ...] = (
    InventoryScheduleMeta(
        category_name="米 (未収穫)",
        opening_qty="10a",
        opening_amount=Decimal(20_000),
        closing_qty="12a",
        closing_amount=Decimal(25_000),
    ),
)

#: 販売用動物 (carried)。
SALE_ANIMALS: tuple[InventoryScheduleMeta, ...] = (
    InventoryScheduleMeta(
        category_name="子牛",
        opening_qty="2頭",
        opening_amount=Decimal(300_000),
        closing_qty="1頭",
        closing_amount=Decimal(180_000),
    ),
)

#: 果樹・牛馬等の育成費用の計算 (carried; 小計 = 前年繰越 + 投下費用, 翌年繰越 = 小計 - 成熟取得価額)。
CULTIVATION_COSTS: tuple[CultivationCostMeta, ...] = (
    CultivationCostMeta(
        name="みかん幼木",
        opening_carryover=Decimal(150_000),
        seedling_cost=Decimal(30_000),
        fertilizer_cost=Decimal(20_000),
        income_from_growing=Decimal(5_000),
        matured_acquisition_cost=Decimal(0),
        added_to_acquisition_cost=Decimal(0),
    ),
    CultivationCostMeta(
        name="繁殖牛 (育成中)",
        opening_carryover=Decimal(200_000),
        seedling_cost=Decimal(100_000),
        fertilizer_cost=Decimal(10_000),
        income_from_growing=Decimal(0),
        matured_acquisition_cost=Decimal(310_000),
        added_to_acquisition_cost=Decimal(0),
    ),
)


def _agricultural_income_from_balances(balances: dict[str, Decimal]) -> AgriculturalIncome:
    """Pair the per-account journal balances with the descriptive fixture and assemble KOA240's 収入側.

    ``balances`` is 勘定科目コード → 正常残高方向の残高 (a trial-balance row's balance), so a 販売金額 /
    家事消費 / 雑収入 / 農産物棚卸 figure is already signed positive. Shared by both golden paths — only how
    ``balances`` is computed differs (offline vs. SQL).
    """
    crops = [
        aggregation.CropIncomeTotals(
            account_code=m.account_code,
            category=m.category,
            crop_name=m.crop_name,
            planted_area=m.planted_area,
            harvest_quantity=m.harvest_quantity,
            opening_inventory_qty=m.opening_inventory_qty,
            opening_inventory_amount=m.opening_inventory_amount,
            sales_amount=m.sales_amount,
            home_consumption=m.home_consumption,
            closing_inventory_qty=m.closing_inventory_qty,
            closing_inventory_amount=m.closing_inventory_amount,
        )
        for m in CROPS
    ]
    livestock = [
        aggregation.LivestockIncomeTotals(
            account_code=m.account_code,
            category_name=m.category_name,
            raised_count=m.raised_count,
            produced_count=m.produced_count,
            sales_amount=m.sales_amount,
            home_consumption=m.home_consumption,
        )
        for m in LIVESTOCK
    ]
    misc = [
        aggregation.MiscIncomeTotals(
            account_code=m.account_code,
            category_name=m.category_name,
            amount=m.amount,
        )
        for m in MISC_INCOME
    ]
    unharvested = [
        aggregation.InventoryScheduleTotals(
            category_name=m.category_name,
            opening_qty=m.opening_qty,
            opening_amount=m.opening_amount,
            closing_qty=m.closing_qty,
            closing_amount=m.closing_amount,
        )
        for m in UNHARVESTED
    ]
    sale_animals = [
        aggregation.InventoryScheduleTotals(
            category_name=m.category_name,
            opening_qty=m.opening_qty,
            opening_amount=m.opening_amount,
            closing_qty=m.closing_qty,
            closing_amount=m.closing_amount,
        )
        for m in SALE_ANIMALS
    ]
    cultivation = [
        aggregation.CultivationCostTotals(
            name=m.name,
            opening_carryover=m.opening_carryover,
            seedling_cost=m.seedling_cost,
            fertilizer_cost=m.fertilizer_cost,
            income_from_growing=m.income_from_growing,
            matured_acquisition_cost=m.matured_acquisition_cost,
            added_to_acquisition_cost=m.added_to_acquisition_cost,
        )
        for m in CULTIVATION_COSTS
    ]
    return aggregation.assemble_agricultural_income(
        crops,
        livestock,
        misc,
        unharvested,
        sale_animals,
        cultivation,
        balances=balances,
        home_consumption_account=HOME_CONSUMPTION_ACCOUNT,
        inventory_account=INVENTORY_ACCOUNT,
        fiscal_year=FISCAL_YEAR,
        start_date=FY_START,
        end_date=FY_END,
    )


def agricultural_income_from_dataset(
    entries: tuple[SeedEntry, ...] = AG_ENTRIES,
) -> AgriculturalIncome:
    """Reduce the in-memory farmer dataset into KOA240's 収入側 内訳 — no database required.

    Reduces the dataset to a trial balance offline (sharing the production signing rule), pairs each needed
    account balance with its descriptive fixture, and assembles via the production engine — so this
    generates the committed golden while staying a pure function of the dataset.
    """
    balances = {row.code: row.balance for row in trial_balance_from_dataset(entries).rows}
    return _agricultural_income_from_balances(balances)


def agricultural_income_from_db(
    conn: psycopg.Connection[Any], *, status: EntryStatus | None = EntryStatus.POSTED
) -> AgriculturalIncome:
    """Compute KOA240's 収入側 内訳 from the DB via the production engine.

    Reads the trial balance through :meth:`ai_books.db.repository.LedgerRepository.trial_balance` and pairs
    the same account balances with the same descriptive fixture, so there is no second arithmetic path to
    drift from :func:`agricultural_income_from_dataset` — a divergence pins a storage/aggregation bug
    upstream.
    """
    balances = {row.code: row.balance for row in trial_balance_from_db(conn, status=status).rows}
    return _agricultural_income_from_balances(balances)
