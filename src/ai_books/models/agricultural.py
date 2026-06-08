"""農業所得 (agricultural income) 内訳 models — KOA240 data-supply (Issue #125).

The 青色申告決算書(農業所得用) (e-Tax 様式 **KOA240**) carries a 収入側 the general form (KOA210)
has no place for: 農産物 (田畑 / 果樹 / 特殊施設) と 畜産物 の販売・家事消費、雑収入、農産物の棚卸、
そして 未収穫農産物 / 販売用動物 / 果樹・牛馬等の育成費用 の明細. KOA240's income side cannot be produced
from the journal alone — a 販売金額 figure is journal-derivable, but a 区分 (作物名), a 作付面積, or a
収穫量 is **not** an attribute of any 勘定科目. So this module is the shape of that supply path: each 内訳
row pairs a *journal-derived amount* with the *descriptive metadata* the form needs, and the composed
:class:`AgriculturalIncome` reconciles the two so a drift between the books and the breakdown surfaces
rather than hides.

Like every other report shape these are code-oriented (科目コード inline, no DB ids) and keep amounts as
:class:`~decimal.Decimal` end to end (浮動小数禁止), so the same object is produced identically offline
(golden generation, no DB) and from Postgres. This stage supplies the **収入側** 内訳 the issue calls out;
registering the KOA240 :class:`~ai_books.etax.spec.EtaxFormatSpec` and the end-to-end ``.xtx`` golden is
the follow-up (stage 4).

The journal/metadata boundary, per breakdown:

* **農産物 (田畑 / 果樹 / 特殊施設)** — each crop's 販売金額 and 家事消費・事業消費金額 are journal-derived
  (the per-category 農産物売上高 / 家事消費 勘定科目 balances), and the per-crop 期末棚卸金額 foots to the
  農産物 (棚卸資産) balance; 区分 (作物名) / 作付面積 / 収穫量 / 棚卸数量 / 期首棚卸 are metadata.
* **畜産物その他** — 販売金額 / 家事消費・事業消費金額 are journal-derived; 区分 / 飼育頭羽数 / 生産頭羽数
  are metadata.
* **雑収入** — 金額 is journal-derived (雑収入(農業) 勘定科目); 区分 is metadata.
* **未収穫農産物 / 販売用動物 / 果樹・牛馬等の育成費用** — supporting schedules carried for the form and
  reconciled internally (計 = 内訳行の合計); these are reference detail, not P/L sales.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import Field

from .base import DomainModel

# ── 農産物の収入の内訳 (farm-product income breakdown) ────────────────────────────────


class CropIncomeLine(DomainModel):
    """One crop's row of the 農産物の収入の内訳 (KOA240 田畑 APF0069x / 果樹 APF0081x / 特殊施設 APF0093x).

    ``sales_amount`` (販売金額) and ``home_consumption`` (家事消費・事業消費金額) are journal-derived:
    the aggregation pins each category's column footing to its 農産物売上高 / 家事消費 勘定科目 balance, so
    the per-crop split cannot silently disagree with the books. ``closing_inventory_amount`` (期末棚卸金額)
    likewise foots to the 農産物 (棚卸資産) balance. Everything else — ``category`` (田畑 / 果樹 / 特殊施設),
    ``crop_name`` (区分), ``planted_area`` (作付面積), ``harvest_quantity`` (本年収穫量), and the 期首棚卸
    snapshot — is descriptive metadata the journal does not carry.
    """

    account_code: str  # 農産物売上高 勘定科目コード (the journal source of 販売金額)
    category: str  # 田畑 / 果樹 / 特殊施設
    crop_name: str  # 区分 (作物名)
    planted_area: Decimal  # 作付面積
    harvest_quantity: Decimal  # 本年収穫量
    opening_inventory_qty: Decimal  # 農産物の期首棚卸高 (数量)
    opening_inventory_amount: Decimal  # 農産物の期首棚卸高 (金額)
    sales_amount: Decimal  # 販売金額 (journal)
    home_consumption: Decimal  # 家事消費・事業消費金額 (journal)
    closing_inventory_qty: Decimal  # 農産物の期末棚卸高 (数量)
    closing_inventory_amount: Decimal  # 農産物の期末棚卸高 (金額; journal 棚卸資産)


# ── 畜産物その他の内訳 (livestock & other income breakdown) ───────────────────────────


class LivestockIncomeLine(DomainModel):
    """One row of the 畜産物その他 内訳 (KOA240 APF0111x).

    ``sales_amount`` (販売金額) and ``home_consumption`` (家事消費・事業消費金額) are journal-derived
    (畜産物売上高 / 家事消費 勘定科目); ``category_name`` (区分), ``raised_count`` (飼育頭羽数) and
    ``produced_count`` (生産頭羽数) are metadata.
    """

    account_code: str  # 畜産物売上高 勘定科目コード
    category_name: str  # 区分
    raised_count: Decimal  # 飼育頭羽数
    produced_count: Decimal  # 生産頭羽数
    sales_amount: Decimal  # 販売金額 (journal)
    home_consumption: Decimal  # 家事消費・事業消費金額 (journal)


# ── 雑収入の内訳 (miscellaneous income breakdown) ─────────────────────────────────────


class MiscIncomeLine(DomainModel):
    """One row of the 雑収入 内訳 (KOA240 APF0121x). ``amount`` is journal-derived; ``category_name`` meta."""

    account_code: str  # 雑収入(農業) 勘定科目コード
    category_name: str  # 区分
    amount: Decimal  # 金額 (journal)


# ── 未収穫農産物 / 販売用動物 の棚卸明細 (carried schedules) ──────────────────────────


class InventoryScheduleLine(DomainModel):
    """One row of the 未収穫農産物 (APF0126x) or 販売用動物 (APF0134x) 棚卸明細.

    A carried schedule: ``opening_qty`` / ``closing_qty`` are free-text quantities (the form's 数量 column
    is 文字, carrying units), while the 金額 are :class:`~decimal.Decimal`. Reconciled internally (計 =
    行の合計), not pinned to a P/L 勘定科目 — these are reference detail, not income.
    """

    category_name: str  # 区分
    opening_qty: str  # 期首棚卸高 (数量; 単位付き自由記述)
    opening_amount: Decimal  # 期首棚卸高 (金額)
    closing_qty: str  # 期末棚卸高 (数量)
    closing_amount: Decimal  # 期末棚卸高 (金額)


# ── 果樹・牛馬等の育成費用の計算 (cultivation cost schedule) ──────────────────────────


class CultivationCostLine(DomainModel):
    """One row of the 果樹・牛馬等の育成費用の計算 (KOA240 APF0233x-APF0243x).

    A carried schedule reconciled internally: ``subtotal`` (小計) foots to 前年繰越 + 本年投下費用, and
    ``carryover_to_next`` (翌年への繰越額) = ``subtotal`` - ``matured_acquisition_cost`` (本年成熟分が
    取得価額へ抜ける). ``income_from_growing`` (育成中の果樹等から生じた収入金額) and
    ``added_to_acquisition_cost`` (本年に取得価額に加算する金額) are carried for the form.
    """

    name: str  # 果樹・牛馬等の名称
    opening_carryover: Decimal  # 前年からの繰越額
    seedling_cost: Decimal  # 本年中の種苗費、種付料、素畜費
    fertilizer_cost: Decimal  # 本年中の肥料、農薬等の投下費用
    subtotal: Decimal  # 小計 (= 前年繰越 + 種苗費等 + 肥料等)
    income_from_growing: Decimal  # 育成中の果樹等から生じた収入金額
    added_to_acquisition_cost: Decimal  # 本年に取得価額に加算する金額
    matured_acquisition_cost: Decimal  # 本年中に成熟したものの取得価額
    carryover_to_next: Decimal  # 翌年への繰越額 (= 小計 - 成熟取得価額)


# ── 農業所得の収入側 内訳 (the composed supply path) ──────────────────────────────────


class AgriculturalIncome(DomainModel):
    """農業所得 (KOA240) の収入側 内訳 — every breakdown plus its footings, reconciled.

    Gathers the 収入側 内訳 KOA240 needs and ties every 計 back to its rows via :attr:`is_consistent`.
    ``gross_income`` (収入金額 計, APF00180) = 小計 (販売金額 + 家事消費 + 雑収入) - 農産物期首棚卸高 +
    農産物期末棚卸高 — the form's income computation, every component journal-anchored except the descriptive
    metadata and the carried schedules.
    """

    fiscal_year: str  # 会計年度名 (例: 'FY2025')
    start_date: date  # 期首
    end_date: date  # 期末
    crop_income_lines: list[CropIncomeLine] = Field(default_factory=list)
    livestock_income_lines: list[LivestockIncomeLine] = Field(default_factory=list)
    misc_income_lines: list[MiscIncomeLine] = Field(default_factory=list)
    unharvested_lines: list[InventoryScheduleLine] = Field(default_factory=list)
    sale_animal_lines: list[InventoryScheduleLine] = Field(default_factory=list)
    cultivation_cost_lines: list[CultivationCostLine] = Field(default_factory=list)
    # 農産物計 (APF0105x-APF0109x).
    farm_product_sales_total: Decimal  # 農産物計 販売金額
    farm_product_home_consumption_total: Decimal  # 農産物計 家事消費・事業消費金額
    farm_product_opening_inventory_total: Decimal  # 農産物計 期首棚卸高 (金額)
    farm_product_closing_inventory_total: Decimal  # 農産物計 期末棚卸高 (金額)
    # 畜産物その他 計.
    livestock_sales_total: Decimal  # 畜産物その他 販売金額 計
    livestock_home_consumption_total: Decimal  # 畜産物その他 家事消費・事業消費金額 計
    # 収入金額 (APF0011x-APF0018x).
    sales_amount_total: Decimal  # 販売金額 (APF00110 = 農産物計 + 畜産物その他)
    home_consumption_total: Decimal  # 家事消費・事業消費金額 (APF00120)
    misc_income_total: Decimal  # 雑収入 (APF00130 / APF01230)
    subtotal: Decimal  # 小計 (APF00140 = 販売 + 家事消費 + 雑収入)
    opening_inventory_total: Decimal  # 農産物の棚卸高 期首 (APF00160)
    closing_inventory_total: Decimal  # 農産物の棚卸高 期末 (APF00170)
    gross_income: Decimal  # 収入金額 計 (APF00180 = 小計 - 期首 + 期末)
    # 未収穫農産物 / 販売用動物 (carried).
    unharvested_opening_total: Decimal  # 未収穫農産物 期首棚卸高 計
    unharvested_closing_total: Decimal  # 未収穫農産物 期末棚卸高 計
    sale_animal_opening_total: Decimal  # 販売用動物 期首棚卸高 計
    sale_animal_closing_total: Decimal  # 販売用動物 期末棚卸高 計
    # 果樹・牛馬等の育成費用の計算 (計; APF0245x-APF0253x) + 経費から差し引く育成費用 (APF00440).
    cultivation_opening_carryover_total: Decimal  # 前年からの繰越額 計
    cultivation_seedling_cost_total: Decimal  # 本年中の種苗費等 計
    cultivation_fertilizer_cost_total: Decimal  # 本年中の肥料農薬等 計
    cultivation_subtotal_total: Decimal  # 小計 計
    cultivation_income_from_growing_total: Decimal  # 育成中の果樹等から生じた収入金額 計
    cultivation_added_to_acquisition_total: Decimal  # 本年に取得価額に加算する金額 計
    cultivation_matured_acquisition_total: Decimal  # 本年中に成熟したものの取得価額 計
    cultivation_carryover_to_next_total: Decimal  # 翌年への繰越額 計
    deductible_cultivation_cost: Decimal  # 経費から差し引く果樹牛馬等の育成費用 (APF00440)

    @property
    def is_consistent(self) -> bool:
        """True when every 計 equals the sum of its rows and the 収入金額 formulas hold."""
        crops = self.crop_income_lines
        livestock = self.livestock_income_lines
        misc = self.misc_income_lines
        unharv = self.unharvested_lines
        animals = self.sale_animal_lines
        cult = self.cultivation_cost_lines
        return (
            # 農産物計 が田畑/果樹/特殊施設の行を foot する.
            self.farm_product_sales_total == sum((c.sales_amount for c in crops), Decimal(0))
            and self.farm_product_home_consumption_total
            == sum((c.home_consumption for c in crops), Decimal(0))
            and self.farm_product_opening_inventory_total
            == sum((c.opening_inventory_amount for c in crops), Decimal(0))
            and self.farm_product_closing_inventory_total
            == sum((c.closing_inventory_amount for c in crops), Decimal(0))
            # 畜産物その他 計.
            and self.livestock_sales_total == sum((s.sales_amount for s in livestock), Decimal(0))
            and self.livestock_home_consumption_total
            == sum((s.home_consumption for s in livestock), Decimal(0))
            # 収入金額 ブロックの足し合わせ.
            and self.sales_amount_total
            == self.farm_product_sales_total + self.livestock_sales_total
            and self.home_consumption_total
            == self.farm_product_home_consumption_total + self.livestock_home_consumption_total
            and self.misc_income_total == sum((m.amount for m in misc), Decimal(0))
            and self.subtotal
            == self.sales_amount_total + self.home_consumption_total + self.misc_income_total
            and self.opening_inventory_total == self.farm_product_opening_inventory_total
            and self.closing_inventory_total == self.farm_product_closing_inventory_total
            and self.gross_income
            == self.subtotal - self.opening_inventory_total + self.closing_inventory_total
            # 未収穫農産物 / 販売用動物 (carried).
            and self.unharvested_opening_total
            == sum((u.opening_amount for u in unharv), Decimal(0))
            and self.unharvested_closing_total
            == sum((u.closing_amount for u in unharv), Decimal(0))
            and self.sale_animal_opening_total
            == sum((a.opening_amount for a in animals), Decimal(0))
            and self.sale_animal_closing_total
            == sum((a.closing_amount for a in animals), Decimal(0))
            # 育成費用の計算 (計) と各行の小計/翌年繰越.
            and all(
                line.subtotal == line.opening_carryover + line.seedling_cost + line.fertilizer_cost
                and line.carryover_to_next == line.subtotal - line.matured_acquisition_cost
                for line in cult
            )
            and self.cultivation_opening_carryover_total
            == sum((line.opening_carryover for line in cult), Decimal(0))
            and self.cultivation_seedling_cost_total
            == sum((line.seedling_cost for line in cult), Decimal(0))
            and self.cultivation_fertilizer_cost_total
            == sum((line.fertilizer_cost for line in cult), Decimal(0))
            and self.cultivation_subtotal_total == sum((line.subtotal for line in cult), Decimal(0))
            and self.cultivation_income_from_growing_total
            == sum((line.income_from_growing for line in cult), Decimal(0))
            and self.cultivation_added_to_acquisition_total
            == sum((line.added_to_acquisition_cost for line in cult), Decimal(0))
            and self.cultivation_matured_acquisition_total
            == sum((line.matured_acquisition_cost for line in cult), Decimal(0))
            and self.cultivation_carryover_to_next_total
            == sum((line.carryover_to_next for line in cult), Decimal(0))
            # 経費から差し引く育成費用 = 本年投下費用 - 育成中収入.
            and self.deductible_cultivation_cost
            == self.cultivation_seedling_cost_total
            + self.cultivation_fertilizer_cost_total
            - self.cultivation_income_from_growing_total
        )
