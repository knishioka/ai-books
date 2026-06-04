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
    STATEMENT_CATEGORY_ACCOUNT_TYPE,
    AccountType,
    EntrySide,
    EntryStatus,
    NormalSide,
    StatementCategory,
    normal_side_for,
)
from .journal import JournalEntry, JournalEntryInput, JournalLine, JournalLineInput
from .period import FiscalYear, Period
from .query import AccountBalance, AccountLedger, JournalEntryPage, LedgerRow
from .report import (
    GeneralLedger,
    GeneralLedgerAccount,
    GeneralLedgerRow,
    JournalBook,
    JournalBookEntry,
    JournalBookLine,
)

__all__ = [
    "CREDIT_NORMAL_TYPES",
    "DEBIT_NORMAL_TYPES",
    "STATEMENT_CATEGORY_ACCOUNT_TYPE",
    "Account",
    "AccountBalance",
    "AccountLedger",
    "AccountType",
    "AuditLog",
    "DomainModel",
    "EntrySide",
    "EntryStatus",
    "FiscalYear",
    "GeneralLedger",
    "GeneralLedgerAccount",
    "GeneralLedgerRow",
    "JournalBook",
    "JournalBookEntry",
    "JournalBookLine",
    "JournalEntry",
    "JournalEntryInput",
    "JournalEntryPage",
    "JournalLine",
    "JournalLineInput",
    "LedgerRow",
    "NormalSide",
    "Period",
    "StatementCategory",
    "normal_side_for",
]
