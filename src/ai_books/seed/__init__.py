"""Seed (reference data) loaders for ai-books.

Currently the standard chart of accounts (:mod:`ai_books.seed.accounts`), the master
data every other track FK-references. Loaders are idempotent and validate their data
before writing — see the submodule docstrings.
"""

from __future__ import annotations

from .accounts import (
    CHART_OF_ACCOUNTS,
    REQUIRED_CATEGORIES,
    SeedAccount,
    SeedResult,
    seed_accounts,
    validate_chart,
)

__all__ = [
    "CHART_OF_ACCOUNTS",
    "REQUIRED_CATEGORIES",
    "SeedAccount",
    "SeedResult",
    "seed_accounts",
    "validate_chart",
]
