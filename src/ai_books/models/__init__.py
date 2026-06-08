"""Pydantic v2 domain models — the frozen type contract for ai-books.

This package is the single place every other layer (MCP tools, aggregation,
reports, the Vercel viewer) imports its types from. Models mirror the Postgres
schema and re-enforce the accounting invariants (normal-balance consistency,
debit/credit balance, Decimal precision) at the validation boundary so the MCP
entry point can reject bad input machine-readably (AGENTS.md invariant #2).
"""

from __future__ import annotations

from .account import Account
from .aggregation import (
    MonthlyTrend,
    MonthlyTrendPoint,
    TrialBalance,
    TrialBalanceRow,
)
from .agricultural import (
    AgriculturalIncome,
    CropIncomeLine,
    CultivationCostLine,
    InventoryScheduleLine,
    LivestockIncomeLine,
    MiscIncomeLine,
)
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
from .etax import EtaxExport, EtaxRecord, EtaxValueKind
from .financial_statements import (
    MANUFACTURING_CATEGORIES,
    DepreciationLine,
    DepreciationSchedule,
    FinancialStatements,
    ManufacturingCost,
    ManufacturingCostLine,
    ManufacturingCostSection,
    MonthlySalesPurchases,
    MonthlySalesPurchasesRow,
)
from .imports import ImportSummary
from .journal import (
    YEAR_END_ADJUSTMENT_SOURCE,
    JournalEntry,
    JournalEntryInput,
    JournalLine,
    JournalLineInput,
)
from .period import FiscalYear, Period
from .query import AccountBalance, AccountLedger, JournalEntryPage, LedgerRow
from .real_estate import (
    LoanInterestLine,
    RealEstateIncome,
    RentalIncomeLine,
    RentPaidLine,
)
from .report import (
    BalanceSheet,
    BalanceSheetLine,
    BalanceSheetSection,
    GeneralLedger,
    GeneralLedgerAccount,
    GeneralLedgerRow,
    JournalBook,
    JournalBookEntry,
    JournalBookLine,
)
from .statement import ProfitAndLoss, ProfitAndLossLine, ProfitAndLossSection
from .worksheet import Worksheet, WorksheetRow

__all__ = [
    "CREDIT_NORMAL_TYPES",
    "DEBIT_NORMAL_TYPES",
    "MANUFACTURING_CATEGORIES",
    "STATEMENT_CATEGORY_ACCOUNT_TYPE",
    "YEAR_END_ADJUSTMENT_SOURCE",
    "Account",
    "AccountBalance",
    "AccountLedger",
    "AccountType",
    "AgriculturalIncome",
    "AuditLog",
    "BalanceSheet",
    "BalanceSheetLine",
    "BalanceSheetSection",
    "CropIncomeLine",
    "CultivationCostLine",
    "DepreciationLine",
    "DepreciationSchedule",
    "DomainModel",
    "EntrySide",
    "EntryStatus",
    "EtaxExport",
    "EtaxRecord",
    "EtaxValueKind",
    "FinancialStatements",
    "FiscalYear",
    "GeneralLedger",
    "GeneralLedgerAccount",
    "GeneralLedgerRow",
    "ImportSummary",
    "InventoryScheduleLine",
    "JournalBook",
    "JournalBookEntry",
    "JournalBookLine",
    "JournalEntry",
    "JournalEntryInput",
    "JournalEntryPage",
    "JournalLine",
    "JournalLineInput",
    "LedgerRow",
    "LivestockIncomeLine",
    "LoanInterestLine",
    "ManufacturingCost",
    "ManufacturingCostLine",
    "ManufacturingCostSection",
    "MiscIncomeLine",
    "MonthlySalesPurchases",
    "MonthlySalesPurchasesRow",
    "MonthlyTrend",
    "MonthlyTrendPoint",
    "NormalSide",
    "Period",
    "ProfitAndLoss",
    "ProfitAndLossLine",
    "ProfitAndLossSection",
    "RealEstateIncome",
    "RentPaidLine",
    "RentalIncomeLine",
    "StatementCategory",
    "TrialBalance",
    "TrialBalanceRow",
    "Worksheet",
    "WorksheetRow",
    "normal_side_for",
]
