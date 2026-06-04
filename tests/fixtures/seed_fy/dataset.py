"""合成シードデータ — one fictional fiscal year of 個人事業/青色申告 journal entries.

This is the synthetic dataset the golden-snapshot harness (and every downstream
report Issue — 集計 #18 / 帳簿 #19 / PL・BS #20・#21 / 決算書 #23 / e-Tax #24) replays to
verify their output against a fixed expected snapshot. It contains **no real data**
— a wholly fictional 製造業の個人事業主 for FY2025 — so it is safe to commit.

Entries are described against 勘定科目コード (not DB ids), so the dataset is a pure
value that can be reduced offline (to generate golden values without a database)
*and* loaded into Postgres (:mod:`.loader` resolves codes → ids at insert time). Each
:class:`SeedEntry` carries a unique ``voucher_no`` so re-loading is idempotent, and
every entry is internally balanced (借方合計 = 貸方合計), which makes the whole year
balance by construction.

The scenario is deliberately small and round-numbered so a human can re-derive every
figure by hand — see ``README.md`` for the narrative and the expected key balances.

Coverage (the 青色申告で一通り発生する取引 the issue calls out):

* 期首残高 (元入金) — opening assets/liabilities and owner's capital
* 売上 — 現金売上 / 掛売上 / 売掛金回収
* 仕入 — 掛仕入 / 買掛金支払
* 製造原価 — 原材料仕入 / 賃金 / 製造減価償却 (the 6xxx accounts #23 needs)
* 経費 (販管費) — 水道光熱費 / 通信費 / 旅費交通費 / 消耗品費 / 地代家賃
* 固定資産 — 工具器具備品の取得
* 借入 — 元金返済 + 支払利息、受取利息
* 事業主勘定 — 事業主借 (私費投入) / 事業主貸 (家事按分)
* 期末整理 — 家事按分 / 減価償却 / 商品の期首・期末棚卸振替
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import NamedTuple

from ai_books.models import (
    YEAR_END_ADJUSTMENT_SOURCE,
    AccountType,
    EntrySide,
    NormalSide,
    StatementCategory,
)
from ai_books.seed.accounts import CHART_OF_ACCOUNTS

#: The fiscal year this dataset models. Used to label golden snapshots.
FISCAL_YEAR = "FY2025"
FY_START = date(2025, 1, 1)
FY_END = date(2025, 12, 31)

#: ``code → SeedAccount`` over the canonical chart, so the dataset borrows the real
#: 科目名 / 正常残高 instead of redefining them (a referenced code that is not in the
#: chart is therefore a bug :func:`validate_dataset` catches).
_BY_CODE = {account.code: account for account in CHART_OF_ACCOUNTS}


class SeedLine(NamedTuple):
    """One debit or credit line of a :class:`SeedEntry`, keyed by 勘定科目コード."""

    account_code: str
    side: EntrySide
    amount: Decimal


class SeedEntry(NamedTuple):
    """One balanced journal entry (伝票) of the synthetic year.

    ``voucher_no`` is unique across the dataset; :mod:`.loader` uses it both to assign
    ``journal_entries.voucher_no`` and to skip already-loaded entries (idempotency).
    ``source`` is the 起票元 stored on the entry: ``"seed"`` for an operating transaction,
    :data:`~ai_books.models.YEAR_END_ADJUSTMENT_SOURCE` for a 期末整理仕訳 so the 精算表 (#22)
    can split it into the 修正記入 columns.
    """

    voucher_no: str
    entry_date: date
    description: str
    lines: tuple[SeedLine, ...]
    source: str = "seed"


def _yen(amount: int) -> Decimal:
    """A whole-yen :class:`Decimal` (the dataset uses round numbers, no sub-yen)."""
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


# ── The fiscal year, in chronological order ─────────────────────────────────────
# Every entry is balanced on its own; the README re-derives the resulting balances.
FY_ENTRIES: tuple[SeedEntry, ...] = (
    # 期首残高 (2025-01-01): 元入金 = 期首資産 - 期首負債 = 3,900,000 - 700,000.
    _entry(
        "FY2025-000",
        date(2025, 1, 1),
        "期首残高 (開業/繰越)",
        _d("1110", 200_000),  # 現金
        _d("1141", 2_200_000),  # 普通預金
        _d("1180", 300_000),  # 商品 (期首棚卸)
        _d("1530", 1_200_000),  # 機械装置
        _c("2510", 700_000),  # 長期借入金
        _c("3110", 3_200_000),  # 元入金
    ),
    # 事業主借: 私費を事業用口座へ投入。
    _entry(
        "FY2025-001",
        date(2025, 1, 20),
        "事業主借 (私費を事業口座へ)",
        _d("1141", 100_000),
        _c("3120", 100_000),  # 事業主借
    ),
    # 仕入 (掛).
    _entry(
        "FY2025-002",
        date(2025, 2, 1),
        "商品仕入 (掛)",
        _d("5120", 600_000),  # 仕入高
        _c("2120", 600_000),  # 買掛金
    ),
    # 原材料仕入 (製造原価, 預金払い).
    _entry(
        "FY2025-003",
        date(2025, 2, 5),
        "原材料仕入 (預金払)",
        _d("6120", 300_000),  # 原材料仕入高
        _c("1141", 300_000),
    ),
    # 現金売上.
    _entry(
        "FY2025-004",
        date(2025, 2, 15),
        "売上 (現金)",
        _d("1110", 220_000),
        _c("4110", 220_000),  # 売上高
    ),
    # 掛売上 (春).
    _entry(
        "FY2025-005",
        date(2025, 3, 20),
        "売上 (掛)",
        _d("1160", 550_000),  # 売掛金
        _c("4110", 550_000),
    ),
    # 買掛金支払.
    _entry(
        "FY2025-006",
        date(2025, 3, 31),
        "買掛金支払 (預金)",
        _d("2120", 600_000),
        _c("1141", 600_000),
    ),
    # 売掛金回収 (春分).
    _entry(
        "FY2025-007",
        date(2025, 4, 30),
        "売掛金回収 (預金)",
        _d("1141", 550_000),
        _c("1160", 550_000),
    ),
    # 水道光熱費.
    _entry(
        "FY2025-008",
        date(2025, 5, 10),
        "水道光熱費 (預金)",
        _d("7130", 120_000),
        _c("1141", 120_000),
    ),
    # 固定資産取得 (工具器具備品).
    _entry(
        "FY2025-009",
        date(2025, 6, 1),
        "工具器具備品 取得 (預金)",
        _d("1550", 480_000),
        _c("1141", 480_000),
    ),
    # 賃金 (製造労務費).
    _entry(
        "FY2025-010",
        date(2025, 6, 25),
        "賃金 支払 (預金)",
        _d("6210", 400_000),  # 賃金
        _c("1141", 400_000),
    ),
    # 通信費.
    _entry(
        "FY2025-011",
        date(2025, 7, 15),
        "通信費 (預金)",
        _d("7150", 60_000),
        _c("1141", 60_000),
    ),
    # 旅費交通費 (現金).
    _entry(
        "FY2025-012",
        date(2025, 8, 20),
        "旅費交通費 (現金)",
        _d("7140", 80_000),
        _c("1110", 80_000),
    ),
    # 掛売上 (秋) — 年末まで売掛金として残る。
    _entry(
        "FY2025-013",
        date(2025, 9, 10),
        "売上 (掛, 期末未回収)",
        _d("1160", 880_000),
        _c("4110", 880_000),
    ),
    # 消耗品費 (現金).
    _entry(
        "FY2025-014",
        date(2025, 10, 5),
        "消耗品費 (現金)",
        _d("7200", 40_000),
        _c("1110", 40_000),
    ),
    # 地代家賃 (年額; 家事按分は期末整理で計上).
    _entry(
        "FY2025-015",
        date(2025, 11, 1),
        "地代家賃 (年額, 預金)",
        _d("7250", 600_000),
        _c("1141", 600_000),
    ),
    # 借入金返済 + 支払利息.
    _entry(
        "FY2025-016",
        date(2025, 12, 20),
        "借入金返済 (元金 + 利息)",
        _d("2510", 100_000),  # 長期借入金 (元金)
        _d("8210", 21_000),  # 利子割引料 (利息)
        _c("1141", 121_000),
    ),
    # 受取利息 (預金利息).
    _entry(
        "FY2025-017",
        date(2025, 12, 31),
        "受取利息 (預金)",
        _d("1141", 500),
        _c("8110", 500),  # 受取利息
    ),
    # ── 期末整理仕訳 (year-end adjustments) ──────────────────────────────────
    # 家事按分: 地代家賃の 40% を 事業主貸 へ振替 (事業使用分 60% を残す)。
    _entry(
        "FY2025-018",
        date(2025, 12, 31),
        "期末整理: 地代家賃 家事按分 40%",
        _d("1290", 240_000),  # 事業主貸
        _c("7250", 240_000),
        source=YEAR_END_ADJUSTMENT_SOURCE,
    ),
    # 減価償却 (機械装置, 製造原価, 直接法).
    _entry(
        "FY2025-019",
        date(2025, 12, 31),
        "期末整理: 減価償却 (機械装置, 製造)",
        _d("6330", 240_000),  # 減価償却費 (製造)
        _c("1530", 240_000),
        source=YEAR_END_ADJUSTMENT_SOURCE,
    ),
    # 減価償却 (工具器具備品, 販管費, 直接法).
    _entry(
        "FY2025-020",
        date(2025, 12, 31),
        "期末整理: 減価償却 (工具器具備品, 販管)",
        _d("7210", 60_000),  # 減価償却費 (販管)
        _c("1550", 60_000),
        source=YEAR_END_ADJUSTMENT_SOURCE,
    ),
    # 期末整理: 期首商品棚卸高への振替 (期首商品 → 売上原価)。
    _entry(
        "FY2025-021",
        date(2025, 12, 31),
        "期末整理: 期首商品棚卸高 振替",
        _d("5110", 300_000),  # 期首商品棚卸高
        _c("1180", 300_000),
        source=YEAR_END_ADJUSTMENT_SOURCE,
    ),
    # 期末整理: 期末商品棚卸高の計上 (売上原価の控除)。
    _entry(
        "FY2025-022",
        date(2025, 12, 31),
        "期末整理: 期末商品棚卸高 計上",
        _d("1180", 350_000),
        _c("5130", 350_000),  # 期末商品棚卸高 (貸方/控除)
        source=YEAR_END_ADJUSTMENT_SOURCE,
    ),
)


def account_name(code: str) -> str:
    """The 科目名 for ``code`` from the canonical chart."""
    return _BY_CODE[code].name


def normal_side(code: str) -> NormalSide:
    """The 正常残高 for ``code`` from the canonical chart."""
    return _BY_CODE[code].normal_balance


def account_type(code: str) -> AccountType:
    """The 科目区分 for ``code`` from the canonical chart (routes 精算表 P/L vs B/S)."""
    return _BY_CODE[code].account_type


def statement_category(code: str) -> StatementCategory:
    """The 表示区分 (決算書集計分類) for ``code`` from the canonical chart."""
    return _BY_CODE[code].statement_category


def referenced_codes() -> list[str]:
    """Every 勘定科目コード the dataset touches, sorted, de-duplicated."""
    codes = {line.account_code for entry in FY_ENTRIES for line in entry.lines}
    return sorted(codes)


def validate_dataset(entries: tuple[SeedEntry, ...] = FY_ENTRIES) -> None:
    """Raise ``ValueError`` if the dataset is internally inconsistent.

    Collects *all* problems before raising so the whole dataset can be fixed in one
    pass. Checks: unique ``voucher_no``; every referenced code exists in the chart;
    every amount is positive; every entry has both a debit and a credit and balances
    (借方合計 = 貸方合計).
    """
    problems: list[str] = []
    seen_vouchers: set[str] = set()

    for entry in entries:
        if entry.voucher_no in seen_vouchers:
            problems.append(f"duplicate voucher_no {entry.voucher_no!r}")
        seen_vouchers.add(entry.voucher_no)

        debit = sum((ln.amount for ln in entry.lines if ln.side is EntrySide.DEBIT), Decimal(0))
        credit = sum((ln.amount for ln in entry.lines if ln.side is EntrySide.CREDIT), Decimal(0))
        has_debit = any(ln.side is EntrySide.DEBIT for ln in entry.lines)
        has_credit = any(ln.side is EntrySide.CREDIT for ln in entry.lines)

        if not (has_debit and has_credit):
            problems.append(f"{entry.voucher_no}: needs both a debit and a credit line")
        if debit != credit:
            problems.append(f"{entry.voucher_no}: 借方 {debit} != 貸方 {credit}")

        for line in entry.lines:
            if line.account_code not in _BY_CODE:
                problems.append(f"{entry.voucher_no}: unknown account code {line.account_code!r}")
            if line.amount <= 0:
                problems.append(f"{entry.voucher_no}: non-positive amount on {line.account_code}")

    if problems:
        raise ValueError("synthetic seed dataset is inconsistent:\n  - " + "\n  - ".join(problems))
