"""One-shot: migrate + seed the FY2025 fixture into AI_BOOKS_DB_URL (for web golden-compare).

Used only by the local web golden cross-check (web/scripts/verify-golden.ts) — it applies every
migration to a throwaway Postgres, then loads the synthetic fiscal year through the production
write path. Not part of the app; safe to delete.
"""

from __future__ import annotations

from tests.fixtures.seed_fy.loader import load_fiscal_year

from ai_books.db import migrate, transaction

if __name__ == "__main__":
    applied = migrate.run()
    print(f"applied {len(applied)} migration(s)")
    with transaction() as conn:
        result = load_fiscal_year(conn)
    print(f"seed: inserted={result.inserted} skipped={result.skipped} total={result.total}")
