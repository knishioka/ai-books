"""標準勘定科目マスタ — the seedable chart of accounts and its loader.

This is the foundational reference data every other track FK-references (#13 仕訳,
#17 取引, #18/#20/#21/#23 決算書). It encodes, for a 個人事業主 filing 青色申告:

* the account taxonomy (科目区分) and its normal balance (正常残高),
* where each account rolls up on the 決算書 (:class:`StatementCategory`), so P/L and
  B/S generation is a mechanical group-by rather than per-account special-casing, and
* the 製造原価 accounts #23 needs.

The data lives as :class:`SeedAccount` rows. :func:`validate_chart` checks the two
invariants the issue calls out — 区分 ↔ 正常残高 consistency and 表示区分 coverage —
*before* anything is written, and :func:`seed_accounts` applies the rows idempotently
(``ON CONFLICT (code) DO NOTHING``) so re-running never duplicates.

Run as a module against ``AI_BOOKS_DB_URL``::

    uv run python -m ai_books.seed.accounts

Raw SQL only — no ORM (AGENTS.md invariant #4).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, NamedTuple

from psycopg.rows import dict_row

from ai_books import db
from ai_books.db.repository import AccountRepository
from ai_books.errors import SeedIntegrityError
from ai_books.models import (
    STATEMENT_CATEGORY_ACCOUNT_TYPE,
    AccountType,
    NormalSide,
    StatementCategory,
    normal_side_for,
)

if TYPE_CHECKING:
    import psycopg


class SeedAccount(NamedTuple):
    """One row of seed data, before it is given a DB id.

    ``normal_balance`` is carried explicitly (rather than always derived) so that an
    *inconsistent* row can be represented and therefore *detected* by
    :func:`validate_chart`. ``parent_code`` references another row's ``code``; it is
    resolved to a ``parent_id`` at load time (#内訳親子), so a parent must appear
    earlier in :data:`CHART_OF_ACCOUNTS` than its children.
    """

    code: str
    name: str
    account_type: AccountType
    normal_balance: NormalSide
    statement_category: StatementCategory
    parent_code: str | None = None


def _acct(
    code: str,
    name: str,
    statement_category: StatementCategory,
    *,
    parent_code: str | None = None,
) -> SeedAccount:
    """Build a consistent :class:`SeedAccount`: type comes from the 表示区分, normal
    balance from the type. Keeps the canonical chart free of hand-typed redundancy."""
    account_type = STATEMENT_CATEGORY_ACCOUNT_TYPE[statement_category]
    return SeedAccount(
        code=code,
        name=name,
        account_type=account_type,
        normal_balance=normal_side_for(account_type),
        statement_category=statement_category,
        parent_code=parent_code,
    )


_C = StatementCategory

#: The standard 個人事業/青色申告 chart of accounts. Ordered so parents precede
#: children and codes group by 区分 (1xxx 資産 / 2xxx 負債 / 3xxx 純資産 /
#: 4xxx 売上 / 5xxx 売上原価 / 6xxx 製造原価 / 7xxx 経費 / 8xxx 営業外).
CHART_OF_ACCOUNTS: tuple[SeedAccount, ...] = (
    # ── 資産: 流動資産 ──────────────────────────────────────────────
    _acct("1110", "現金", _C.CURRENT_ASSETS),
    _acct("1140", "預金", _C.CURRENT_ASSETS),
    _acct("1141", "普通預金", _C.CURRENT_ASSETS, parent_code="1140"),
    _acct("1142", "定期預金", _C.CURRENT_ASSETS, parent_code="1140"),
    _acct("1150", "受取手形", _C.CURRENT_ASSETS),
    _acct("1160", "売掛金", _C.CURRENT_ASSETS),
    _acct("1170", "有価証券", _C.CURRENT_ASSETS),
    _acct("1180", "商品", _C.CURRENT_ASSETS),
    _acct("1190", "前払金", _C.CURRENT_ASSETS),
    _acct("1200", "貸付金", _C.CURRENT_ASSETS),
    # 仮払金 — 相手科目未確定の出金を一時退避する suspense 科目 (#14 CSV 取込)。
    # 後で正しい費用/資産科目へ振替える前提の clearing account。
    _acct("1210", "仮払金", _C.CURRENT_ASSETS),
    _acct("1290", "事業主貸", _C.CURRENT_ASSETS),
    # ── 資産: 固定資産 ──────────────────────────────────────────────
    _acct("1510", "建物", _C.FIXED_ASSETS),
    _acct("1520", "建物附属設備", _C.FIXED_ASSETS),
    _acct("1530", "機械装置", _C.FIXED_ASSETS),
    _acct("1540", "車両運搬具", _C.FIXED_ASSETS),
    _acct("1550", "工具器具備品", _C.FIXED_ASSETS),
    _acct("1560", "土地", _C.FIXED_ASSETS),
    # ── 負債: 流動負債 ──────────────────────────────────────────────
    _acct("2110", "支払手形", _C.CURRENT_LIABILITIES),
    _acct("2120", "買掛金", _C.CURRENT_LIABILITIES),
    _acct("2130", "未払金", _C.CURRENT_LIABILITIES),
    _acct("2140", "前受金", _C.CURRENT_LIABILITIES),
    _acct("2150", "預り金", _C.CURRENT_LIABILITIES),
    _acct("2160", "短期借入金", _C.CURRENT_LIABILITIES),
    # 仮受金 — 相手科目未確定の入金を一時退避する suspense 科目 (#14 CSV 取込)。
    # 後で正しい収益/負債科目へ振替える前提の clearing account。
    _acct("2170", "仮受金", _C.CURRENT_LIABILITIES),
    # ── 負債: 固定負債 ──────────────────────────────────────────────
    _acct("2510", "長期借入金", _C.FIXED_LIABILITIES),
    # ── 純資産 ──────────────────────────────────────────────────────
    _acct("3110", "元入金", _C.EQUITY),
    _acct("3120", "事業主借", _C.EQUITY),
    # ── 売上(収入)金額 ────────────────────────────────────────────
    _acct("4110", "売上高", _C.SALES),
    # ── 売上原価 ────────────────────────────────────────────────────
    _acct("5110", "期首商品棚卸高", _C.COST_OF_GOODS_SOLD),
    _acct("5120", "仕入高", _C.COST_OF_GOODS_SOLD),
    _acct("5130", "期末商品棚卸高", _C.COST_OF_GOODS_SOLD),
    # ── 製造原価: 材料費 (#23) ─────────────────────────────────────
    _acct("6110", "期首原材料棚卸高", _C.MANUFACTURING_MATERIALS),
    _acct("6120", "原材料仕入高", _C.MANUFACTURING_MATERIALS),
    _acct("6130", "期末原材料棚卸高", _C.MANUFACTURING_MATERIALS),
    # ── 製造原価: 労務費 (#23) ─────────────────────────────────────
    _acct("6210", "賃金", _C.MANUFACTURING_LABOR),
    _acct("6220", "法定福利費", _C.MANUFACTURING_LABOR),
    # ── 製造原価: 製造経費 (#23) ───────────────────────────────────
    _acct("6310", "外注工賃", _C.MANUFACTURING_OVERHEAD),
    _acct("6320", "電力費", _C.MANUFACTURING_OVERHEAD),
    _acct("6330", "減価償却費", _C.MANUFACTURING_OVERHEAD),
    _acct("6340", "修繕費", _C.MANUFACTURING_OVERHEAD),
    # ── 経費 (販管費) ───────────────────────────────────────────────
    _acct("7110", "租税公課", _C.SELLING_ADMIN_EXPENSES),
    _acct("7120", "荷造運賃", _C.SELLING_ADMIN_EXPENSES),
    _acct("7130", "水道光熱費", _C.SELLING_ADMIN_EXPENSES),
    _acct("7140", "旅費交通費", _C.SELLING_ADMIN_EXPENSES),
    _acct("7150", "通信費", _C.SELLING_ADMIN_EXPENSES),
    _acct("7160", "広告宣伝費", _C.SELLING_ADMIN_EXPENSES),
    _acct("7170", "接待交際費", _C.SELLING_ADMIN_EXPENSES),
    _acct("7180", "損害保険料", _C.SELLING_ADMIN_EXPENSES),
    _acct("7190", "修繕費", _C.SELLING_ADMIN_EXPENSES),
    _acct("7200", "消耗品費", _C.SELLING_ADMIN_EXPENSES),
    _acct("7210", "減価償却費", _C.SELLING_ADMIN_EXPENSES),
    _acct("7220", "福利厚生費", _C.SELLING_ADMIN_EXPENSES),
    _acct("7230", "給料賃金", _C.SELLING_ADMIN_EXPENSES),
    _acct("7240", "外注工賃", _C.SELLING_ADMIN_EXPENSES),
    _acct("7250", "地代家賃", _C.SELLING_ADMIN_EXPENSES),
    _acct("7260", "専従者給与", _C.SELLING_ADMIN_EXPENSES),
    _acct("7290", "雑費", _C.SELLING_ADMIN_EXPENSES),
    # ── 営業外収益 ──────────────────────────────────────────────────
    _acct("8110", "受取利息", _C.NON_OPERATING_INCOME),
    _acct("8120", "雑収入", _C.NON_OPERATING_INCOME),
    # ── 営業外費用 ──────────────────────────────────────────────────
    _acct("8210", "利子割引料", _C.NON_OPERATING_EXPENSES),
    _acct("8220", "雑損失", _C.NON_OPERATING_EXPENSES),
)

#: Every 表示区分 a complete chart must populate (青色申告決算書 coverage).
REQUIRED_CATEGORIES: frozenset[StatementCategory] = frozenset(StatementCategory)


class SeedResult(NamedTuple):
    """Outcome of a seed run: how many rows were newly inserted vs. already present."""

    inserted: int
    total: int


def validate_chart(rows: tuple[SeedAccount, ...] = CHART_OF_ACCOUNTS) -> None:
    """Check the chart's internal consistency; raise :class:`SeedIntegrityError` if bad.

    Verifies, collecting *all* problems before raising:

    * **区分 ↔ 正常残高**: each row's ``normal_balance`` matches its ``account_type``.
    * **区分 ↔ 表示区分**: each row's 表示区分 sits on the right ``account_type``.
    * **codes are unique** and **parent references resolve** (and point backwards).
    * **表示区分 coverage**: every :data:`REQUIRED_CATEGORIES` member has ≥1 account.
    """
    problems: list[str] = []
    seen: set[str] = set()
    covered: set[StatementCategory] = set()

    for row in rows:
        if row.code in seen:
            problems.append(f"duplicate code {row.code!r}")
        seen.add(row.code)
        covered.add(row.statement_category)

        expected_side = normal_side_for(row.account_type)
        if row.normal_balance != expected_side:
            problems.append(
                f"{row.code} {row.name}: {row.account_type.value} accounts are "
                f"{expected_side.value}-normal, got {row.normal_balance.value}"
            )

        expected_type = STATEMENT_CATEGORY_ACCOUNT_TYPE[row.statement_category]
        if row.account_type != expected_type:
            problems.append(
                f"{row.code} {row.name}: 表示区分 {row.statement_category.value} requires "
                f"account_type {expected_type.value}, got {row.account_type.value}"
            )

        if row.parent_code is not None and row.parent_code not in seen:
            problems.append(
                f"{row.code} {row.name}: parent {row.parent_code!r} is undefined or "
                "appears after this row"
            )

    missing = REQUIRED_CATEGORIES - covered
    problems.extend(
        f"表示区分 {category.value} has no account assigned"
        for category in sorted(missing, key=lambda c: c.value)
    )

    if problems:
        raise SeedIntegrityError(problems)


def seed_accounts(
    conn: psycopg.Connection[Any], rows: tuple[SeedAccount, ...] = CHART_OF_ACCOUNTS
) -> SeedResult:
    """Idempotently load ``rows`` into ``accounts``; return how many were inserted.

    Validates first (so a bad chart never touches the DB), then inserts each row with
    ``ON CONFLICT (code) DO NOTHING``. A re-run inserts nothing — the unique ``code``
    makes the operation idempotent. ``parent_code`` is resolved to the parent's
    DB-assigned id, looked up whether the parent was just inserted or already present.
    """
    validate_chart(rows)
    repo = AccountRepository(conn)
    code_to_id: dict[str, int] = {}
    inserted = 0

    for row in rows:
        parent_id = code_to_id[row.parent_code] if row.parent_code is not None else None
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO accounts
                    (code, name, account_type, statement_category, normal_balance,
                     parent_id, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, true)
                ON CONFLICT (code) DO NOTHING
                RETURNING id
                """,
                (
                    row.code,
                    row.name,
                    row.account_type.value,
                    row.statement_category.value,
                    row.normal_balance.value,
                    parent_id,
                ),
            )
            returned = cur.fetchone()

        if returned is not None:
            account_id = int(returned["id"])
            inserted += 1
        else:
            # Already present (re-run): fetch its id so children can still reference it.
            existing = repo.get_by_code(row.code)
            if existing is None or existing.id is None:  # pragma: no cover - defensive
                raise SeedIntegrityError([f"{row.code}: conflicted on insert but not found"])
            account_id = existing.id
        code_to_id[row.code] = account_id

    return SeedResult(inserted=inserted, total=len(rows))


def main(argv: list[str] | None = None) -> int:
    """Apply the chart-of-accounts seed against ``AI_BOOKS_DB_URL`` (idempotent)."""
    try:
        with db.transaction() as conn:
            result = seed_accounts(conn)
    except RuntimeError as exc:  # AI_BOOKS_DB_URL unset
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SeedIntegrityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    already = result.total - result.inserted
    print(
        f"chart of accounts seeded: {result.inserted} inserted, "
        f"{already} already present ({result.total} total)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
