"""エッジケース合成データ — the corner years fixed golden tends to miss (Issue #57).

The main golden year (:data:`~tests.fixtures.seed_fy.dataset.FY_ENTRIES`) is deliberately *typical*:
every 表示区分 populated, round numbers, one of each transaction. These datasets are the opposite —
small years that each isolate one boundary the aggregation/帳簿 layer has to survive:

* :data:`EMPTY_FY` — a year with no entries at all (空 FY). Every report must come back empty/zero,
  not error on the missing rows.
* :data:`ONE_SIDED_FY` — accounts that only ever appear on a *single* side (片側のみ科目), including an
  asset left with a 貸方 (negative / 正常残高と逆) balance.
* :data:`FRACTIONAL_FY` — many 1-sen (0.01) amounts (端数多発), so a lost ``Decimal`` or a float detour
  would show up as drift in the footings.
* :data:`CROSS_MONTH_ADJUSTMENT_FY` — 期末整理仕訳 booked in *different* months (月跨ぎ整理), proving the
  精算表's 残高試算表 / 修正記入 split keys off ``source`` and not the entry date.

Each year balances by construction (so :func:`~tests.fixtures.seed_fy.dataset.validate_dataset` accepts
it and it loads into Postgres), and each is registered for golden snapshotting in :mod:`.golden`, so the
committed golden files pin the expected 集計 / PL / BS / 帳簿 output exactly.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ai_books.models import YEAR_END_ADJUSTMENT_SOURCE, EntrySide

from .dataset import SeedEntry, SeedLine


def _d(code: str, amount: str) -> SeedLine:
    """A 借方 (debit) line carrying an exact :class:`~decimal.Decimal` amount."""
    return SeedLine(code, EntrySide.DEBIT, Decimal(amount))


def _c(code: str, amount: str) -> SeedLine:
    """A 貸方 (credit) line carrying an exact :class:`~decimal.Decimal` amount."""
    return SeedLine(code, EntrySide.CREDIT, Decimal(amount))


def _entry(
    voucher_no: str,
    entry_date: date,
    description: str,
    *lines: SeedLine,
    source: str = "seed",
) -> SeedEntry:
    return SeedEntry(voucher_no, entry_date, description, lines, source)


# ── 空 FY ────────────────────────────────────────────────────────────────────────
#: A fiscal year with no journal entries. Boundary case: the 集計/帳簿 must yield empty rows and
#: zero footings (and the loader must seed the chart + fiscal year without inserting any 伝票).
EMPTY_FY: tuple[SeedEntry, ...] = ()


# ── 片側のみ科目 ──────────────────────────────────────────────────────────────────
#: Each account touched here appears on exactly one side across the whole year, so its 試算表 row has a
#: zero on the opposite column. 普通預金 (1141, an asset / 借方正常) is only ever credited, leaving it
#: with a 貸方残高 — the rare 正常残高と逆 (negative) balance the 貸借対照表 must still place.
ONE_SIDED_FY: tuple[SeedEntry, ...] = (
    _entry(
        "ONE-000",
        date(2025, 2, 1),
        "現金売上 (現金は借方のみ / 売上高は貸方のみ)",
        _d("1110", "100000"),  # 現金 — debit only
        _c("4110", "100000"),  # 売上高 — credit only
    ),
    _entry(
        "ONE-001",
        date(2025, 3, 1),
        "旅費交通費 (預金払 / 普通預金は貸方のみ)",
        _d("7140", "30000"),  # 旅費交通費 — debit only
        _c("1141", "30000"),  # 普通預金 — credit only ⇒ 資産が貸方残高 (負)
    ),
)


# ── 端数 (0.01) 多発 ──────────────────────────────────────────────────────────────
#: A year built almost entirely from 1-sen amounts. The footings (消耗品費 7200 借方計 / 普通預金 1141
#: 貸方計) are Σ of these exact fractions, so any precision loss surfaces as a golden mismatch. The
#: amounts are chosen so the total lands on a non-trivial fraction (¥101.16) rather than a round number.
FRACTIONAL_FY: tuple[SeedEntry, ...] = (
    _entry("FRA-000", date(2025, 1, 5), "端数 0.01", _d("7200", "0.01"), _c("1141", "0.01")),
    _entry("FRA-001", date(2025, 1, 6), "端数 0.02", _d("7200", "0.02"), _c("1141", "0.02")),
    _entry("FRA-002", date(2025, 1, 7), "端数 0.03", _d("7200", "0.03"), _c("1141", "0.03")),
    _entry("FRA-003", date(2025, 2, 9), "端数 0.33", _d("7200", "0.33"), _c("1141", "0.33")),
    _entry("FRA-004", date(2025, 2, 9), "端数 0.67", _d("7200", "0.67"), _c("1141", "0.67")),
    _entry("FRA-005", date(2025, 3, 3), "端数 1.10", _d("7200", "1.10"), _c("1141", "1.10")),
    _entry("FRA-006", date(2025, 3, 3), "端数 99.00", _d("7200", "99.00"), _c("1141", "99.00")),
)


# ── 月跨ぎ整理 ────────────────────────────────────────────────────────────────────
#: Operating entries plus 期末整理仕訳 (家事按分 / 減価償却) booked in *different* months. The 精算表 must
#: route these to the 修正記入 columns by ``source`` regardless of their date, while the 月次推移 still
#: tiles them into whichever month they fall in.
CROSS_MONTH_ADJUSTMENT_FY: tuple[SeedEntry, ...] = (
    _entry(
        "CMA-000",
        date(2025, 1, 15),
        "地代家賃 (年額, 預金)",
        _d("7250", "120000"),
        _c("1141", "120000"),
    ),
    _entry(
        "CMA-001",
        date(2025, 6, 30),  # 年央に計上された期末整理 (月跨ぎ)
        "期末整理: 減価償却 (機械装置, 製造)",
        _d("6330", "60000"),
        _c("1530", "60000"),
        source=YEAR_END_ADJUSTMENT_SOURCE,
    ),
    _entry(
        "CMA-002",
        date(2025, 12, 31),  # 期末に計上された期末整理
        "期末整理: 地代家賃 家事按分 40%",
        _d("1290", "48000"),
        _c("7250", "48000"),
        source=YEAR_END_ADJUSTMENT_SOURCE,
    ),
)


#: name → dataset, for the golden registry and the DB dual-path cross-check. The name doubles as the
#: golden filename stem (``edge/<report>__<name>.json``), so adding an edge year is: define it here,
#: list it in :data:`EDGE_DATASETS`, register its reports in :mod:`.golden`, and commit the golden.
EDGE_DATASETS: dict[str, tuple[SeedEntry, ...]] = {
    "empty": EMPTY_FY,
    "one_sided": ONE_SIDED_FY,
    "fractional": FRACTIONAL_FY,
    "cross_month_adjustment": CROSS_MONTH_ADJUSTMENT_FY,
}
