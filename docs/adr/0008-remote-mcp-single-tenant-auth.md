# ADR 0008 — Remote MCP posture: single-tenant, Supabase Auth (OAuth/JWT)

- Status: Accepted
- Date: 2026-06-06
- Deciders: ai-books maintainers (human review point — Wave 0, serial)
- Relates to: #105 / Epic #104. Blocks every Wave 1 / Wave 2 issue of the
  remote-publish track (`track:remote`). Builds on
  [ADR 0001](./0001-pivot-to-supabase-and-vercel-viewer.md) (Supabase + Vercel pivot).

## Context

`ai-books` ships the MCP server over **stdio** only — a local process a single
Claude Desktop / CLI client launches and talks to on the same machine
(`src/ai_books/server.py:613`, `run_stdio` → `mcp.run()`, FastMCP's default
transport). There is no network listener, so "who may call the tools" has so far
been answered implicitly by OS process boundaries: if you can spawn the process,
you are the (sole) user.

The remote-publish track (Epic #104) wants the same MCP tools reachable over the
**Web** so a hosted client can drive bookkeeping without a local process. Moving
from a local stdio process to a network-reachable endpoint is a **posture
change**, not a feature: the moment the server is reachable over the network, the
implicit "OS decides who calls" guarantee disappears and must be replaced by an
**explicit authentication + authorization** decision. In the spirit of
[ADR 0001](./0001-pivot-to-supabase-and-vercel-viewer.md) (record the intent and
the boundary before building), we fix that decision here, on the serial Wave 0
path, before any Wave 1/2 implementation begins.

Two of the architectural invariants (AGENTS.md "Architectural invariants") frame
the boundary:

- **Invariant #3 — single-user, no multi-tenant / RLS / horizontal scale.**
  Adding network auth must **not** be read as a license to become multi-tenant.
  The natural-but-wrong inference — "we added login, so now we serve many
  isolated tenants" — is exactly the論点 a future reader would re-litigate. We
  pin it: **authentication ≠ multi-tenancy.**
- **Invariant #2 — server-side validation absolute.** Auth gates _who_ may call;
  it does not touch _what_ is validated. Debit/credit balance, Decimal precision,
  and account-FK checks stay at the MCP tool entry via Pydantic, with zero client
  trust, exactly as before.

The trade-off considered: leaving the server stdio-only (no remote surface, no
auth needed, but no Web reach — blocks the epic) versus exposing it remotely
(unblocks the epic, but requires owning an auth boundary and its operational
cost). The epic's goal makes remote reach in-scope; this ADR accepts the auth
cost and bounds it to the **smallest posture that is still safe** —
single-user, allowlisted.

## Decision

### 1. Remote exposure — opt-in, **Streamable HTTP**; stdio stays the default

- The MCP server gains an **optional remote transport: Streamable HTTP** (FastMCP's
  HTTP transport), selected explicitly at launch.
- **stdio remains the default and is preserved verbatim.** Local Claude
  Desktop / CLI usage is unchanged; nothing about the existing `run_stdio` path
  is removed or altered. Remote is an additive second way to run, never a
  replacement.
- The remote endpoint is **not** enabled implicitly. Running over the network is
  a deliberate operator choice (a distinct entry point / flag), so a local
  install never accidentally opens a listener.

### 2. Authentication — **Supabase Auth (OAuth / JWT)**

- When the server runs remotely, **every request must carry a valid credential**.
  Authentication is delegated to **Supabase Auth** (the same Supabase project
  that is already the system of record per ADR 0001) using **OAuth → JWT**: the
  client obtains a Supabase-issued JWT, the server **verifies that JWT** (issuer,
  signature, expiry) at the request boundary before any tool runs.
- **Fail closed.** A missing, malformed, expired, or unverifiable token is
  rejected; no tool body executes. There is no anonymous / no-op fallback on the
  remote transport.
- Secrets (Supabase URL, JWT secret / JWKS, keys) come **only** from environment
  (see `.env.example`: `SUPABASE_URL`, `SUPABASE_ANON_KEY`,
  `SUPABASE_SERVICE_ROLE_KEY`); never committed (AGENTS.md "Secret scanning").

### 3. Authorization — **single-user allowlist** (not multi-tenant)

- A verified JWT only proves _a_ Supabase identity. Authorization is a separate,
  **explicit single-user allowlist**: the request's identity (e.g. the token's
  `sub` / email) must match the configured owner. **One principal is authorized;
  everyone else — even with a valid Supabase token — is denied.**
- This is deliberately **not** per-tenant row isolation. There is **one** data
  set and **one** owner. We do **not** introduce RLS, tenant IDs, or per-user
  data partitioning. **Authentication (proving identity) ≠ multi-tenancy
  (isolating many tenants' data).** Invariant #3 (single-user posture) is held
  exactly; auth is the gate in front of the one tenant, not a step toward many.

### 4. Server-side validation — **unchanged (invariant #2)**

- Auth is layered **in front of** the existing validated write path; it does not
  relax or relocate it. All mutations still pass the MCP tool entry's Pydantic
  validation with zero client trust. A request that authenticates and is
  authorized is still fully validated like any local stdio call.

### Posture vocabulary (for downstream issues)

| Layer              | Decision                                                                  |
| ------------------ | ------------------------------------------------------------------------- |
| **Transport**      | stdio (default, local) **+** optional Streamable HTTP (remote, opt-in)    |
| **Authentication** | Supabase Auth, OAuth → JWT, verified at the request boundary, fail-closed |
| **Authorization**  | single-user allowlist (one owner authorized; all others denied)           |
| **Tenancy**        | single-tenant — **unchanged**. Auth ≠ multi-tenant. No RLS / tenant IDs   |
| **Validation**     | MCP-entry Pydantic, zero client trust — **unchanged** (invariant #2)      |

## Consequences

### Positive

- The remote-publish epic (#104, `track:remote`) is unblocked with a fixed,
  referenceable contract: every Wave 1/2 issue builds on "Streamable HTTP +
  Supabase JWT + single-user allowlist, fail-closed".
- The "auth ≠ multi-tenant" boundary is recorded once, so downstream work does
  not drift into RLS / tenant-isolation scope by accident.
- Reuses the existing Supabase dependency for identity — no new auth provider.

### Negative / costs

- A network listener and a verified-auth boundary are new operational surface:
  JWT verification config (issuer / JWKS), allowlist configuration, and the
  failure modes (expired tokens, clock skew) must be owned and tested.
- Two transports now exist; the run/deploy story and its docs must cover both
  stdio (local) and remote (HTTP) explicitly.

### Neutral / unchanged

- Invariant #2 (server-side validation at the MCP entry) — unchanged.
- Invariant #3 (single-user; no multi-tenant / RLS / horizontal scale) — held;
  this ADR adds authentication **without** adding tenancy.
- Invariant #1 (read-only viewer; writes only via MCP) and the audit-log
  append-only discipline (invariant #5) — untouched.
- stdio local usage — unchanged and remains the default.

## Implementation references

> Forward-looking ADR: the decision is fixed here; the code lands in the
> remote-publish track's Wave 1/2 issues (Epic #104). Pointers to where it
> attaches:

- `src/ai_books/server.py` — FastMCP entry point. `run_stdio` (the current
  default transport) stays; an opt-in Streamable HTTP entry point is added here.
- `.env.example` — `SUPABASE_URL` / `SUPABASE_ANON_KEY` /
  `SUPABASE_SERVICE_ROLE_KEY` supply the Supabase Auth config; the single-user
  allowlist (owner identity) is configured via env, never committed.
- [ADR 0001](./0001-pivot-to-supabase-and-vercel-viewer.md) — establishes
  Supabase as the system of record and the single-user, no-multi-tenant posture
  this ADR preserves.
- AGENTS.md "Architectural invariants" #2 (server-side validation) and #3
  (single-user, no multi-tenant) — the invariants this ADR is checked against.
- Defending tests land with the implementation (remote transport rejects
  unauthenticated / non-allowlisted requests, fail-closed).
