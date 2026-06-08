"""One-shot: migrate + seed public sample fixtures into AI_BOOKS_DB_URL.

Used only by the local web golden cross-check (web/scripts/verify-golden.ts) — it applies every
migration to a throwaway Postgres, then loads the public sample years through the production
write path. Not part of the app; safe to delete.
"""

from __future__ import annotations

from tests.fixtures.seed_fy.public_sample import load_public_sample_years

from ai_books.db import migrate, transaction

if __name__ == "__main__":
    applied = migrate.run()
    print(f"applied {len(applied)} migration(s)")
    with transaction() as conn:
        result = load_public_sample_years(conn)
    for name, item in result._asdict().items():
        print(f"seed {name}: inserted={item.inserted} skipped={item.skipped} total={item.total}")
