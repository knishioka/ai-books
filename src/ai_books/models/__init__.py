"""Pydantic v2 domain models — the frozen type contract for ai-books.

This package is the single place every other layer (MCP tools, aggregation,
reports, the Vercel viewer) imports its types from. Models mirror the Postgres
schema and re-enforce the accounting invariants (normal-balance consistency,
debit/credit balance, Decimal precision) at the validation boundary so the MCP
entry point can reject bad input machine-readably (AGENTS.md invariant #2).
"""

from __future__ import annotations

from .account import Account
from .audit import AuditLog
from .base import DomainModel
from .enums import (
    CREDIT_NORMAL_TYPES,
    DEBIT_NORMAL_TYPES,
    AccountType,
    EntrySide,
    EntryStatus,
    NormalSide,
    normal_side_for,
)
from .journal import JournalEntry, JournalLine
from .period import FiscalYear, Period

__all__ = [
    "CREDIT_NORMAL_TYPES",
    "DEBIT_NORMAL_TYPES",
    "Account",
    "AccountType",
    "AuditLog",
    "DomainModel",
    "EntrySide",
    "EntryStatus",
    "FiscalYear",
    "JournalEntry",
    "JournalLine",
    "NormalSide",
    "Period",
    "normal_side_for",
]
