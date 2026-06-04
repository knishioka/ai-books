"""会計年度 / 会計期間 — fiscal year and period domain models.

Mirror the ``fiscal_years`` and ``periods`` tables. These are the basis for
monthly aggregation and 期首/期末 (opening/closing) boundaries. The date-order
constraints from the migrations are re-expressed here: a fiscal year must span at
least one day (end strictly after start), a period may be a single day.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import model_validator

from .base import DomainModel


class FiscalYear(DomainModel):
    """会計年度 — a fiscal year (e.g. ``FY2026``)."""

    id: int | None = None
    name: str
    start_date: date  # 期首
    end_date: date  # 期末
    created_at: datetime | None = None

    @model_validator(mode="after")
    def _check_date_order(self) -> FiscalYear:
        if self.end_date <= self.start_date:
            raise ValueError("fiscal year end_date must be after start_date")
        return self


class Period(DomainModel):
    """会計期間 — an accounting period within a fiscal year (e.g. ``2026-04``)."""

    id: int | None = None
    fiscal_year_id: int
    name: str
    start_date: date
    end_date: date
    created_at: datetime | None = None

    @model_validator(mode="after")
    def _check_date_order(self) -> Period:
        if self.end_date < self.start_date:
            raise ValueError("period end_date must not be before start_date")
        return self
