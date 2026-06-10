"""One-shot: migrate + seed verification fixtures into AI_BOOKS_DB_URL.

Used only by the local web golden cross-check (web/scripts/verify-golden.ts) — it applies every
migration to a throwaway Postgres, then loads the public sample years through the production
write path. Not part of the app; safe to delete.
"""

from __future__ import annotations

import argparse

from tests.fixtures.seed_fy import load_fiscal_year
from tests.fixtures.seed_fy.public_sample import load_public_sample_years

from ai_books.db import migrate, transaction


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fy2025-only",
        action="store_true",
        help="seed only the original FY2025 KOA210 fixture",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help=(
            "skip migrate.run() and only seed — for the E2E stack where `supabase start` "
            "has already applied supabase/migrations (re-applying via our own "
            "schema_migrations table would conflict)"
        ),
    )
    return parser


if __name__ == "__main__":
    args = _parser().parse_args()
    if args.seed_only:
        print("seed-only: skipping migrate (schema applied by `supabase start`)")
    else:
        applied = migrate.run()
        print(f"applied {len(applied)} migration(s)")
    with transaction() as conn:
        if args.fy2025_only:
            result = load_fiscal_year(conn)
            print(
                "seed koa210: "
                f"inserted={result.inserted} skipped={result.skipped} total={result.total}"
            )
        else:
            public_result = load_public_sample_years(conn)
            for name, item in public_result._asdict().items():
                print(
                    f"seed {name}: inserted={item.inserted} skipped={item.skipped} "
                    f"total={item.total}"
                )
