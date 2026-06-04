"""Hypothesis strategies that synthesise *balanced* fiscal years (Issue #57).

The golden harness pins one hand-traced year (:data:`~tests.fixtures.seed_fy.dataset.FY_ENTRIES`);
these strategies instead generate *arbitrary* balanced years so the property tests can assert the
double-entry invariants hold for every example, not just the fixed one. The output is the same
value the golden harness consumes — a ``tuple[SeedEntry, ...]`` keyed on 勘定科目コード — so it flows
straight through the existing offline reducers (``*_from_dataset``) and the production write path
(:func:`~tests.fixtures.seed_fy.loader.load_fiscal_year`) without a second representation.

Two properties are guaranteed *by construction* so a generated year is always valid input
(:func:`~tests.fixtures.seed_fy.dataset.validate_dataset` never rejects it):

* **Every entry balances.** Each entry is built from one or more *transfers* — a (借方科目, 貸方科目,
  金額) triple that contributes one debit and one credit line of the same amount — so 借方合計 = 貸方
  合計 holds line-for-line, and therefore the whole year balances (借貸平均 by construction). This is
  the precondition the invariants assume, not one of the invariants under test.
* **Amounts honour ``numeric(18, 2)``.** Amounts are whole-cent :class:`~decimal.Decimal` values in
  ``[0.01, _MAX_AMOUNT]`` (positive, ≤ 2 dp, well within 18 significant digits), so each line passes
  :func:`ai_books.models.journal.validate_amount` and round-trips through Postgres without rounding.

Vouchers are assigned positionally (``PROP-0000``…) so they are unique within a generated year, and
dates fall inside FY2025 so the year tiles the same 12 months the reports expect.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import strategies as st

from ai_books.models import EntrySide
from ai_books.seed.accounts import CHART_OF_ACCOUNTS

from .dataset import FY_END, FY_START, SeedEntry, SeedLine

#: Every 勘定科目コード a generated entry may reference — the canonical chart, so a generated
#: line always resolves to a real account (the same constraint :func:`validate_dataset` checks).
CHART_CODES: tuple[str, ...] = tuple(account.code for account in CHART_OF_ACCOUNTS)

#: Largest amount a single line may carry, in cents. 100,000,000.00 円 keeps any plausible Σ of a
#: generated year comfortably inside ``numeric(18, 2)``'s 18 significant digits while still letting
#: a year accumulate large footings.
_MAX_CENTS = 10_000_000_000


def amounts() -> st.SearchStrategy[Decimal]:
    """A positive ``numeric(18, 2)`` 金額 strategy: whole cents in ``[0.01, 100,000,000.00]``.

    Drawn as an integer number of cents and divided by 100 so the value is exactly 2 dp with no
    binary-float detour (``Decimal(1) / 100`` ⇒ ``Decimal('0.01')``). The shrink target (1 cent)
    deliberately exercises the 端数 path — a counterexample collapses toward sub-yen amounts.
    """
    return st.integers(min_value=1, max_value=_MAX_CENTS).map(lambda cents: Decimal(cents) / 100)


def _codes() -> st.SearchStrategy[str]:
    return st.sampled_from(CHART_CODES)


@st.composite
def _entry_lines(draw: st.DrawFn) -> tuple[SeedLine, ...]:
    """One balanced entry's lines: 1-3 transfers, each a matched 借方/貸方 pair of equal amount.

    Bundling several transfers into one 伝票 exercises multi-line entries (and the 諸口 counter-account
    path) while keeping 借方合計 = 貸方合計 trivially true. The two endpoints of a transfer may coincide
    (a line that debits and credits the same account); that still balances and is left in on purpose, as
    a degenerate case the aggregation must tolerate.
    """
    transfers = draw(st.integers(min_value=1, max_value=3))
    lines: list[SeedLine] = []
    for _ in range(transfers):
        debit_code = draw(_codes())
        credit_code = draw(_codes())
        amount = draw(amounts())
        lines.append(SeedLine(debit_code, EntrySide.DEBIT, amount))
        lines.append(SeedLine(credit_code, EntrySide.CREDIT, amount))
    return tuple(lines)


@st.composite
def balanced_datasets(
    draw: st.DrawFn, *, min_size: int = 0, max_size: int = 10
) -> tuple[SeedEntry, ...]:
    """Generate a balanced fiscal year of ``[min_size, max_size]`` entries.

    Each entry is internally balanced (see :func:`_entry_lines`), dated within FY2025, and carries a
    positionally-unique ``voucher_no`` — so the whole year balances by construction and passes
    :func:`~tests.fixtures.seed_fy.dataset.validate_dataset`. ``min_size=0`` lets the empty year fall
    out as a boundary example. Entries are returned in generation order (vouchers reflect it); a
    consumer that needs 取引日 order (e.g. 仕訳帳) sorts for itself.
    """
    specs = draw(
        st.lists(
            st.tuples(st.dates(min_value=FY_START, max_value=FY_END), _entry_lines()),
            min_size=min_size,
            max_size=max_size,
        )
    )
    return tuple(
        SeedEntry(
            voucher_no=f"PROP-{index:04d}",
            entry_date=entry_date,
            description=f"generated entry {index}",
            lines=lines,
        )
        for index, (entry_date, lines) in enumerate(specs)
    )


@st.composite
def datasets_with_voided(
    draw: st.DrawFn, *, min_size: int = 1, max_size: int = 8
) -> tuple[tuple[SeedEntry, ...], frozenset[str]]:
    """A balanced year plus a subset of its ``voucher_no`` to be voided (取消).

    Returns ``(entries, voided_vouchers)`` where ``voided_vouchers`` ⊆ the entries' vouchers (possibly
    empty, possibly all). The DB property test loads the year, marks exactly these entries 取消, and
    checks the 記帳確定 reports equal the offline reduction over the *remaining* entries — i.e. that a
    取消 leaves no trace in 残高/試算表/PL/BS.
    """
    entries = draw(balanced_datasets(min_size=min_size, max_size=max_size))
    vouchers = [entry.voucher_no for entry in entries]
    voided = draw(st.sets(st.sampled_from(vouchers))) if vouchers else set()
    return entries, frozenset(voided)
