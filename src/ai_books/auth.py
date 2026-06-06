"""Remote (HTTP) authentication + single-user authorization for the MCP server.

Per ADR 0008 the remote surface is **fail-closed**: when the server runs over
Streamable HTTP, every request must carry a valid Supabase-issued JWT (verified
at the request boundary by FastMCP's :class:`SupabaseProvider`) *and* the token's
identity must match the one configured owner (the **single-user allowlist**).
A verified JWT only proves *an* identity; authorization is the separate
allowlist check — a valid token for anyone but the owner is denied
(:meth:`AllowlistTokenVerifier.verify_token` returns ``None`` → 401).

stdio is unaffected. Locally the process is launched by its single user and
talks over stdio with no network listener, so no provider is built, no token is
required, and writes fall back to the default actor (``server`` resolves the
audit actor — authenticated identity over HTTP, the local default over stdio).

Configuration (all from the environment; secrets never committed — AGENTS.md
"Secret scanning"):

- ``SUPABASE_URL`` — the Supabase project URL (reused from ADR 0001); supplies the
  JWKS endpoint and expected issuer for JWT verification.
- ``AI_BOOKS_MCP_BASE_URL`` — the public URL this server is reachable at; advertised
  in the OAuth protected-resource metadata.
- ``AI_BOOKS_MCP_AUTH_ALLOWLIST`` — comma/whitespace-separated subject (``sub``) or
  email identifiers of the authorized owner(s). Its presence is what *enables*
  remote auth; an empty/blank value is a misconfiguration and is rejected.
- ``AI_BOOKS_MCP_AUTH_JWT_ALGORITHM`` — optional, ``RS256`` or ``ES256`` (default
  ``ES256``); must match the Supabase Auth JWT signing configuration.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal, cast

from fastmcp.server.auth import AccessToken, AuthProvider, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.auth.providers.supabase import SupabaseProvider
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

# --- environment contract -----------------------------------------------------

PROJECT_URL_ENV = "SUPABASE_URL"
BASE_URL_ENV = "AI_BOOKS_MCP_BASE_URL"
ALLOWLIST_ENV = "AI_BOOKS_MCP_AUTH_ALLOWLIST"
ALGORITHM_ENV = "AI_BOOKS_MCP_AUTH_JWT_ALGORITHM"

_DEFAULT_ALGORITHM = "ES256"
_VALID_ALGORITHMS = frozenset({"RS256", "ES256"})
# Supabase Auth's JWT route; the JWKS / issuer derive from it (provider default).
_AUTH_ROUTE = "auth/v1"


class AllowlistTokenVerifier(TokenVerifier):
    """Wrap a JWT verifier so only the configured owner is *authorized*.

    The inner verifier validates the token (signature / issuer / expiry / audience).
    Authorization is the separate single-user allowlist (ADR 0008 §3): after the
    inner verifier accepts a token, its ``sub`` or ``email`` claim must be in the
    allowlist. Any other identity — even with an otherwise-valid Supabase token —
    is denied (returns ``None``), as is a token the inner verifier already
    rejected. Fail-closed: there is no path that admits an un-allowlisted caller.
    """

    def __init__(self, inner: TokenVerifier, *, allowlist: frozenset[str]) -> None:
        # Carry the inner verifier's scope requirements so the surrounding
        # RemoteAuthProvider advertises and enforces them unchanged.
        super().__init__(required_scopes=inner.required_scopes)
        self._inner = inner
        self._allowlist = allowlist

    @property
    def scopes_supported(self) -> list[str]:  # pragma: no cover - thin delegation
        return self._inner.scopes_supported

    async def verify_token(self, token: str) -> AccessToken | None:
        access = await self._inner.verify_token(token)
        if access is None:
            return None  # invalid / expired / wrong issuer — already denied upstream
        claims = access.claims or {}
        # Only string claims are valid identifiers. A malformed token whose ``sub`` /
        # ``email`` is an unexpected type (list / dict — unhashable) must be *denied*,
        # not crash the set build with TypeError; non-str values simply never match.
        identity = {val for val in (claims.get("sub"), claims.get("email")) if isinstance(val, str)}
        if identity & self._allowlist:
            return access
        # Authenticated but not the owner: deny without echoing the identity.
        logger.warning("Denied authenticated request: identity not in allowlist")
        return None


def parse_allowlist(raw: str | None) -> frozenset[str]:
    """Parse the allowlist env value into a set of identifiers.

    Accepts comma- and/or whitespace-separated ``sub`` / email values; blank
    entries are dropped. Returns an empty set when ``raw`` is ``None``/blank.
    """
    if not raw:
        return frozenset()
    parts = (item.strip() for chunk in raw.split(",") for item in chunk.split())
    return frozenset(part for part in parts if part)


def _clean(value: str | None) -> str | None:
    """Return a stripped non-empty string, or ``None`` for unset/blank."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def build_auth_provider(env: Mapping[str, str] | None = None) -> AuthProvider | None:
    """Build the remote auth provider from the environment, or ``None`` if unset.

    Remote auth is **opt-in**: it is enabled exactly when
    ``AI_BOOKS_MCP_AUTH_ALLOWLIST`` is set (the operator's explicit intent to gate
    the network surface). When enabled it is **fail-closed on configuration** —
    ``SUPABASE_URL`` and ``AI_BOOKS_MCP_BASE_URL`` are then required and a blank /
    unparseable allowlist or an unknown algorithm raises rather than silently
    weakening the gate. Returns a :class:`SupabaseProvider` whose token verifier is
    wrapped by :class:`AllowlistTokenVerifier`.
    """
    source = os.environ if env is None else env
    allowlist_raw = _clean(source.get(ALLOWLIST_ENV))
    if allowlist_raw is None:
        return None  # auth not configured → stdio-only / unauthenticated http guarded in main()

    project_url = _clean(source.get(PROJECT_URL_ENV))
    base_url = _clean(source.get(BASE_URL_ENV))
    missing = [
        name for name, val in ((PROJECT_URL_ENV, project_url), (BASE_URL_ENV, base_url)) if not val
    ]
    if missing:
        raise RuntimeError(
            f"{ALLOWLIST_ENV} is set (remote auth enabled) but {', '.join(missing)} "
            "is/are missing; remote auth requires all of "
            f"{ALLOWLIST_ENV}, {PROJECT_URL_ENV}, {BASE_URL_ENV} (ADR 0008)."
        )
    assert project_url is not None  # narrowed by the `missing` check above
    assert base_url is not None  # narrowed by the `missing` check above

    # Fail fast on a scheme-less URL (e.g. SUPABASE_URL=my-project.supabase.co) so the
    # operator gets a clear startup error rather than a JWKS fetch that fails later.
    for name, url in ((PROJECT_URL_ENV, project_url), (BASE_URL_ENV, base_url)):
        if not (url.startswith("http://") or url.startswith("https://")):
            raise RuntimeError(f"{name} must start with http:// or https://; got {url!r}.")

    allowlist = parse_allowlist(allowlist_raw)
    if not allowlist:
        raise RuntimeError(
            f"{ALLOWLIST_ENV} is set but parses to no identifiers; list at least one "
            "subject (sub) or email so a single owner is authorized."
        )

    algorithm_raw = _clean(source.get(ALGORITHM_ENV)) or _DEFAULT_ALGORITHM
    if algorithm_raw not in _VALID_ALGORITHMS:
        allowed = ", ".join(sorted(_VALID_ALGORITHMS))
        raise RuntimeError(f"{ALGORITHM_ENV} must be one of: {allowed}; got {algorithm_raw!r}.")
    algorithm = cast(Literal["RS256", "ES256"], algorithm_raw)

    base = project_url.rstrip("/")
    verifier = JWTVerifier(
        jwks_uri=f"{base}/{_AUTH_ROUTE}/.well-known/jwks.json",
        issuer=f"{base}/{_AUTH_ROUTE}",
        algorithm=algorithm,
        audience="authenticated",
    )
    return SupabaseProvider(
        project_url=project_url,
        base_url=base_url,
        algorithm=algorithm,
        token_verifier=AllowlistTokenVerifier(verifier, allowlist=allowlist),
    )
