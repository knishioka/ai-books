"""Public sample seed bundle for the read-only viewer.

The report engine scopes data by date range rather than by a ledger id. Keep the
existing KOA210 manufacturing sample exactly on FY2025, then shift the fictional
landlord/farmer samples into later fiscal years so selecting one sample year in
the viewer never mixes three unrelated businesses.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, NamedTuple

from .agricultural import AG_ENTRIES
from .dataset import FY_END, FY_ENTRIES, FY_START, SeedEntry
from .loader import LoadResult, load_fiscal_year
from .real_estate import RE_ENTRIES

if TYPE_CHECKING:
    import psycopg


KOA210_SAMPLE_YEAR = "FY2025"
KOA220_SAMPLE_YEAR = "FY2023-KOA220"
KOA240_SAMPLE_YEAR = "FY2024-KOA240"


class PublicSampleLoadResult(NamedTuple):
    """Load results for each public sample year."""

    koa210: LoadResult
    koa220: LoadResult
    koa240: LoadResult


def _shift_date(value: date, years: int) -> date:
    """Shift a fixture date by whole years; the committed sample has no Feb 29."""
    return value.replace(year=value.year + years)


def _shift_entries(entries: tuple[SeedEntry, ...], years: int) -> tuple[SeedEntry, ...]:
    """Move a sample dataset to another fiscal year without changing its amounts."""
    return tuple(
        entry._replace(entry_date=_shift_date(entry.entry_date, years)) for entry in entries
    )


def load_public_sample_years(conn: psycopg.Connection[object]) -> PublicSampleLoadResult:
    """Seed all public demo years through the same production write path.

    ``FY2025`` remains the original KOA210 sample. KOA220/KOA240 use the already
    committed fictional data from #124/#125, shifted to isolated fiscal years for
    the viewer's date-range based report queries.
    """
    koa210 = load_fiscal_year(conn, FY_ENTRIES)
    koa220 = load_fiscal_year(
        conn,
        _shift_entries(RE_ENTRIES, -2),
        fiscal_year=KOA220_SAMPLE_YEAR,
        start=_shift_date(FY_START, -2),
        end=_shift_date(FY_END, -2),
    )
    koa240 = load_fiscal_year(
        conn,
        _shift_entries(AG_ENTRIES, -1),
        fiscal_year=KOA240_SAMPLE_YEAR,
        start=_shift_date(FY_START, -1),
        end=_shift_date(FY_END, -1),
    )
    return PublicSampleLoadResult(koa210=koa210, koa220=koa220, koa240=koa240)
