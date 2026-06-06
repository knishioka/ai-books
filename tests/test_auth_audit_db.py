"""DB-backed end-to-end test of the auth → audit-actor wiring (Issue #110).

The unit layer already pins each half in isolation:

- ``test_auth.py`` proves the *authorization* gate (allowlist-外 / 失効・改竄トークンは
  ``None`` で拒否) and that ``server._resolve_actor`` returns the token's identity
  (``email`` then ``sub``), overriding any client-supplied actor.
- ``test_server_http.py`` proves the HTTP boundary is fail-closed (未認証 → 401) and
  refuses to start without auth configured.

What no test pinned until now is the *whole chain over the real protocol path*: an
authenticated identity flowing through a tool call all the way into the
``audit_logs.actor`` column — and, conversely, that the stdio path (no token) still
writes audits under the fallback actor, i.e. it keeps working without auth (#110 AC:
"有効トークンで主要ツールが通り、``audit_logs.actor`` に認証ユーザーが入る" / "stdio 経路の
非退行"). These exercise the tool through the in-memory FastMCP ``Client`` (the same
path ``test_mcp_client`` uses) and assert against the committed DB row.

Skips when ``AI_BOOKS_DB_URL`` is unset (so ``./scripts/verify.sh`` stays green without
a live Postgres); runs under ``./scripts/test.sh``.
"""

from __future__ import annotations

import os
from typing import Any

import psycopg
import pytest
from fastmcp import Client
from fastmcp.server.auth import AccessToken

from ai_books import db, server
from ai_books.db.repository import AccountRepository
from ai_books.models import Account, AccountType, NormalSide, StatementCategory

pytestmark = pytest.mark.skipif(
    not os.environ.get(db.DB_URL_ENV),
    reason=f"{db.DB_URL_ENV} not set; skipping DB-backed auth/audit-actor tests",
)


def _token(**claims: str) -> AccessToken:
    """A verified access token carrying the given identity claims."""
    return AccessToken(token="jwt", client_id="client", scopes=[], claims=dict(claims))


@pytest.fixture
def accounts(migrated_conn: psycopg.Connection[Any]) -> None:
    """Seed the two accounts a balanced one-line-each entry needs (現金 / 売上高).

    No fiscal year is created: the 取引日 period rule only applies *when fiscal years
    exist*, so leaving the table empty keeps the seed minimal while still letting a
    write succeed (the point here is the actor, not the period check).
    """
    repo = AccountRepository(migrated_conn)
    repo.insert(
        Account(
            code="1110",
            name="現金",
            account_type=AccountType.ASSET,
            normal_balance=NormalSide.DEBIT,
            statement_category=StatementCategory.CURRENT_ASSETS,
        )
    )
    repo.insert(
        Account(
            code="4000",
            name="売上高",
            account_type=AccountType.REVENUE,
            normal_balance=NormalSide.CREDIT,
            statement_category=StatementCategory.SALES,
        )
    )


_ENTRY: dict[str, Any] = {
    "entry_date": "2026-06-01",
    "description": "auth actor test",
    "lines": [
        {"account_code": "1110", "side": "debit", "amount": "1000.00"},
        {"account_code": "4000", "side": "credit", "amount": "1000.00"},
    ],
}


def _audit_actors(conn: psycopg.Connection[Any]) -> list[str]:
    """Every ``audit_logs.actor`` written so far, in insertion order."""
    rows = conn.execute("SELECT actor FROM audit_logs ORDER BY id").fetchall()
    return [row["actor"] for row in rows]


async def test_authenticated_token_passes_and_records_authed_actor(
    mcp_client: Client[Any],
    patched_connect: None,
    accounts: None,
    migrated_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有効トークンで主要ツールが通り、``audit_logs.actor`` に認証ユーザーが入る (#110)."""
    monkeypatch.setattr(server, "get_access_token", lambda: _token(email="owner@example.com"))

    result = await mcp_client.call_tool("create_journal_entry", {"entry": _ENTRY})

    # The valid-token write goes through (主要ツールが通る).
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["status"] == "draft"

    # …and the audit trail names the authenticated user, not the default actor.
    assert _audit_actors(migrated_conn) == ["owner@example.com"]


async def test_authenticated_identity_overrides_client_supplied_actor(
    mcp_client: Client[Any],
    patched_connect: None,
    accounts: None,
    migrated_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller cannot spoof the audit subject over an authenticated channel (invariant #5).

    The token has only a ``sub`` (no email), and the client passes ``actor="attacker"``;
    the audit row must still name the authenticated ``sub`` — end-to-end through the DB,
    not just at ``_resolve_actor``.
    """
    monkeypatch.setattr(server, "get_access_token", lambda: _token(sub="owner-uuid"))

    result = await mcp_client.call_tool(
        "create_journal_entry", {"entry": _ENTRY, "actor": "attacker"}
    )

    assert not result.is_error
    assert _audit_actors(migrated_conn) == ["owner-uuid"]


async def test_unauthenticated_write_falls_back_to_default_actor(
    mcp_client: Client[Any],
    patched_connect: None,
    accounts: None,
    migrated_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stdio 経路の非退行: with no token the write still works and audits the default actor.

    This is the audit-layer counterpart to ``test_server_stdio.py`` — the local/stdio
    path needs no authentication and behaves as before, recording the fallback actor.
    """
    monkeypatch.setattr(server, "get_access_token", lambda: None)

    result = await mcp_client.call_tool("create_journal_entry", {"entry": _ENTRY})

    assert not result.is_error
    assert _audit_actors(migrated_conn) == [server._DEFAULT_ACTOR]
