"""Static guards for the committed read-only viewer role SQL (Issue #54).

These need no database, so they run under ``./scripts/verify.sh`` too: they pin the
committed ``supabase/roles/viewer_readonly.sql`` to its generator
(``tests/fixtures/readonly.py``) and assert it is *only* a SELECT grant — a write grant
sneaking into the read-only role would fail here long before it could reach production.
The behavioural proof (reads succeed, writes are rejected against a real Postgres) lives
in ``tests/test_readonly_db.py``.
"""

from __future__ import annotations

import re

from tests.fixtures import readonly


def test_committed_sql_matches_generator() -> None:
    """The committed file must equal ``render()`` — regenerate it if this fails."""
    assert readonly.SQL_PATH.exists(), (
        f"{readonly.SQL_PATH} is missing; run `python -m tests.fixtures.readonly --write`"
    )
    assert readonly.SQL_PATH.read_text(encoding="utf-8") == readonly.render(), (
        "supabase/roles/viewer_readonly.sql is stale; regenerate it with "
        "`python -m tests.fixtures.readonly --write`"
    )


def test_grants_select_on_existing_and_future_tables() -> None:
    """The role gets schema USAGE + SELECT on current and future tables."""
    body = readonly.render()
    assert "GRANT USAGE ON SCHEMA public TO viewer_ro;" in body
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA public TO viewer_ro;" in body
    # ALTER DEFAULT PRIVILEGES is what keeps a *future* table read-only-but-readable.
    assert "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO viewer_ro;" in body


def test_no_write_privilege_is_ever_granted() -> None:
    """No INSERT/UPDATE/DELETE/TRUNCATE (and no blanket ALL) reaches the role."""
    body = readonly.render()
    for verb in readonly.WRITE_PRIVILEGES:
        assert not re.search(rf"\bGRANT\b[^;]*\b{verb}\b", body, re.IGNORECASE), (
            f"read-only role must never be granted {verb}"
        )
    # A blanket `GRANT ALL` would smuggle in write privileges too.
    assert not re.search(r"\bGRANT\s+ALL\b", body, re.IGNORECASE)
    # The only privilege verbs present are the read-only set (+ CONNECT/USAGE/SELECT).
    assert "GRANT SELECT" in body
