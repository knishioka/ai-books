"""Unit tests for the remote auth provider + single-user allowlist (Issue #107).

Network- and DB-independent: they exercise the configuration factory
(``build_auth_provider``), the allowlist parser, and the
:class:`AllowlistTokenVerifier` authorization gate against a stub inner verifier —
no live Supabase, no JWKS fetch. The end-to-end JWT signature/issuer/expiry
verification is FastMCP's own (``JWTVerifier``) concern; here we pin the project's
*authorization* layer: a verified identity is admitted only when it is the owner,
and everything else is fail-closed denied.

The fail-closed http launch guard and the audit-actor wiring live in
``ai_books.server`` and are covered in ``test_server_http`` / here
(``test_resolve_actor_*``).
"""

from __future__ import annotations

import pytest
from fastmcp.server.auth import AccessToken, AuthProvider, TokenVerifier
from fastmcp.server.auth.providers.supabase import SupabaseProvider

from ai_books import auth, server

# --- allowlist parsing --------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, frozenset()),
        ("", frozenset()),
        ("   ", frozenset()),
        (" , , ", frozenset()),
        ("owner@example.com", frozenset({"owner@example.com"})),
        ("a@b.com,sub-123", frozenset({"a@b.com", "sub-123"})),
        ("a@b.com sub-123", frozenset({"a@b.com", "sub-123"})),
        (" a@b.com , sub-123 \n c@d.com ", frozenset({"a@b.com", "sub-123", "c@d.com"})),
    ],
)
def test_parse_allowlist(raw: str | None, expected: frozenset[str]) -> None:
    assert auth.parse_allowlist(raw) == expected


# --- build_auth_provider: enablement & fail-closed configuration --------------


def _full_env(**overrides: str) -> dict[str, str]:
    env = {
        auth.ALLOWLIST_ENV: "owner@example.com",
        auth.PROJECT_URL_ENV: "https://proj.supabase.co",
        auth.BASE_URL_ENV: "https://mcp.example.com",
    }
    env.update(overrides)
    return env


def test_build_returns_none_when_allowlist_unset() -> None:
    """No allowlist → remote auth disabled (stdio / local). Nothing else is required."""
    assert auth.build_auth_provider({}) is None
    # A blank allowlist is also "unset" (treated as not configured).
    assert auth.build_auth_provider({auth.ALLOWLIST_ENV: "   "}) is None


def test_build_full_config_returns_supabase_provider() -> None:
    provider = auth.build_auth_provider(_full_env())
    assert isinstance(provider, SupabaseProvider)
    assert isinstance(provider, AuthProvider)
    # The token verifier is wrapped by the allowlist gate.
    assert isinstance(provider.token_verifier, auth.AllowlistTokenVerifier)


@pytest.mark.parametrize("drop", [auth.PROJECT_URL_ENV, auth.BASE_URL_ENV])
def test_build_fails_closed_when_required_var_missing(drop: str) -> None:
    """Allowlist set but a required var missing → raise, never silently run open."""
    env = _full_env()
    del env[drop]
    with pytest.raises(RuntimeError, match=drop):
        auth.build_auth_provider(env)


def test_build_rejects_allowlist_that_parses_empty() -> None:
    with pytest.raises(RuntimeError, match=auth.ALLOWLIST_ENV):
        auth.build_auth_provider(_full_env(**{auth.ALLOWLIST_ENV: " , , "}))


def test_build_rejects_unknown_algorithm() -> None:
    with pytest.raises(RuntimeError, match=auth.ALGORITHM_ENV):
        auth.build_auth_provider(_full_env(**{auth.ALGORITHM_ENV: "HS256"}))


@pytest.mark.parametrize("var", [auth.PROJECT_URL_ENV, auth.BASE_URL_ENV])
def test_build_rejects_url_without_scheme(var: str) -> None:
    """A scheme-less URL fails fast at startup with a clear error, not later at fetch."""
    with pytest.raises(RuntimeError, match="http"):
        auth.build_auth_provider(_full_env(**{var: "proj.supabase.co"}))


@pytest.mark.parametrize("algorithm", ["RS256", "ES256"])
def test_build_accepts_supported_algorithms(algorithm: str) -> None:
    provider = auth.build_auth_provider(_full_env(**{auth.ALGORITHM_ENV: algorithm}))
    assert isinstance(provider, SupabaseProvider)


# --- AllowlistTokenVerifier: authorization gate -------------------------------


class _StubVerifier(TokenVerifier):
    """Inner verifier stub: returns a pre-baked verification result for any token."""

    def __init__(self, result: AccessToken | None) -> None:
        super().__init__()
        self._result = result

    async def verify_token(self, token: str) -> AccessToken | None:
        return self._result


def _token(**claims: str) -> AccessToken:
    return AccessToken(token="jwt", client_id="client", scopes=[], claims=dict(claims))


def _gate(inner_result: AccessToken | None, allowlist: set[str]) -> auth.AllowlistTokenVerifier:
    return auth.AllowlistTokenVerifier(_StubVerifier(inner_result), allowlist=frozenset(allowlist))


async def test_allowlisted_sub_is_authorized() -> None:
    token = _token(sub="owner-uuid")
    gate = _gate(token, {"owner-uuid"})
    assert await gate.verify_token("anything") is token


async def test_allowlisted_email_is_authorized() -> None:
    token = _token(sub="owner-uuid", email="owner@example.com")
    gate = _gate(token, {"owner@example.com"})
    assert await gate.verify_token("anything") is token


async def test_non_allowlisted_identity_is_denied() -> None:
    """A *valid* token for a non-owner is denied (single-user authorization)."""
    token = _token(sub="intruder-uuid", email="intruder@example.com")
    gate = _gate(token, {"owner@example.com"})
    assert await gate.verify_token("anything") is None


async def test_invalid_token_stays_denied() -> None:
    """When the inner verifier rejects (None), the gate denies too (fail-closed)."""
    gate = _gate(None, {"owner@example.com"})
    assert await gate.verify_token("anything") is None


async def test_token_without_identity_claims_is_denied() -> None:
    gate = _gate(_token(role="authenticated"), {"owner@example.com"})
    assert await gate.verify_token("anything") is None


async def test_token_with_nonstring_claims_is_denied() -> None:
    """A malformed token (unhashable list/dict sub/email) is denied, never crashes."""
    token = AccessToken(
        token="jwt",
        client_id="client",
        scopes=[],
        claims={"sub": ["not", "a", "string"], "email": {"weird": "obj"}},
    )
    gate = _gate(token, {"owner@example.com"})
    assert await gate.verify_token("anything") is None


# --- audit actor resolution (server._resolve_actor) ---------------------------


def test_resolve_actor_falls_back_to_default_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stdio / unauthenticated: no token → the provided/default actor is used."""
    monkeypatch.setattr(server, "get_access_token", lambda: None)
    assert server._resolve_actor() == server._DEFAULT_ACTOR
    assert server._resolve_actor("local-cli") == "local-cli"
    # An explicit empty / None actor (e.g. a client passing actor="") falls back to
    # the default so the audit row never names an empty actor.
    assert server._resolve_actor("") == server._DEFAULT_ACTOR
    assert server._resolve_actor(None) == server._DEFAULT_ACTOR


def test_resolve_actor_prefers_email_then_sub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        server, "get_access_token", lambda: _token(sub="owner-uuid", email="owner@example.com")
    )
    assert server._resolve_actor() == "owner@example.com"


def test_resolve_actor_uses_sub_when_no_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "get_access_token", lambda: _token(sub="owner-uuid"))
    assert server._resolve_actor() == "owner-uuid"


def test_resolve_actor_authenticated_identity_overrides_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The authenticated identity wins over a client-supplied actor (no spoofing)."""
    monkeypatch.setattr(server, "get_access_token", lambda: _token(sub="owner-uuid"))
    assert server._resolve_actor("attacker") == "owner-uuid"
