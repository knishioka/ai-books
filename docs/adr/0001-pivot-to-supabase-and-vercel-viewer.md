# ADR 0001 — Pivot to Supabase storage + Vercel read-only viewer

- Status: Accepted
- Date: 2026-06-04
- Deciders: ai-books maintainers (human review point — Wave 0, serial)
- Supersedes / re-scopes: roadmap issues #1–#5 (see table below)

## Context

`ai-books` shipped as an **AI-first accounting MCP server** whose SSOT
(`AGENTS.md`) declared three load-bearing invariants:

1. **No human-facing web UI** — interface is MCP tool / CLI / generated static report only.
2. **Server-side validation absolute** — debit/credit balance, Decimal precision, account FK
   checks happen at the MCP tool entry via Pydantic schemas. Zero client trust.
3. **SQLite single-file storage forever** — no PostgreSQL / multi-tenant / RLS.

A new product goal has emerged: **produce 青色申告 (blue-return) filing artifacts —
the 青色申告決算書 and the e-Tax import data — at the 65万円 special-deduction level.**

This goal collides head-on with two of the three invariants:

- Delivering a usable filing-season summary effectively requires a **browsable,
  shareable read view** of aggregated numbers (decennial 決算書 layout, per-account drill-down).
  A "static report file" is too thin for the iteration loop a filing season demands.
- 青色申告 at the 65万円 level presumes **reliable, durable, query-friendly storage**
  with proper migrations and the ability to back the read view. A single local SQLite
  file is awkward to surface to a hosted viewer and to operate over a multi-year horizon.

Because reversing direction is a **judgment call that needs human review**, we concentrate
that decision here (Wave 0, serial). Once accepted, downstream issues proceed on the new
contract with machine verification and can run in parallel.

Invariant #2 (server-side validation) is **not** in tension with the new goal and is retained
verbatim — it is, if anything, more important once a read-only viewer exists.

## Decision

### 1. Storage: SQLite → **Supabase (Postgres)**

- Move the system of record from a local SQLite file to **Supabase (managed Postgres)**.
- **MCP remains the sole write/validation interface.** All mutations (journal entries,
  chart-of-accounts edits, imports) go through MCP tools where Pydantic validation runs.
  Nothing writes to Postgres except through that validated path.
- Migrations are **forward-only**: applied migrations are never edited; changes ship as new
  migration files. (Same discipline as before, now expressed against Postgres.)
- Multi-tenant / RLS / horizontal scale remain **out of scope** — single-user posture is
  unchanged. Supabase is chosen for durable Postgres + a clean path to the hosted viewer,
  not for SaaS multi-tenancy.

### 2. Viewing: add a **read-only dashboard on Vercel**

- Add a **read-only aggregation viewer** deployed on **Vercel**, reading from Supabase.
- The viewer is **strictly read-only**: it renders trial balance, P/L, B/S, the
  青色申告決算書 layout, and filing-season summaries. **It performs no data entry or editing.**
- The "no data-entry UI" rule is **preserved**: writes still flow exclusively through MCP.
  We relax only the "no web delivery at all" stance — and only for read-only aggregated views.

### 3. Goal: 青色申告 output is now **in-scope**

- **In-scope (new):** generate the **青色申告決算書** and **e-Tax import data**
  (the structured artifacts a filer submits / imports).
- **Out-of-scope (line preserved):** the **tax-amount computation itself** —
  income-tax/resident-tax calculation, deduction optimization, and the act of filing —
  remain downstream. `ai-books` produces the ledger and the filing-ready 決算書/取込データ;
  it does not compute or submit the tax owed.

### New architecture vocabulary (for downstream issues)

| Term                    | Role                                                                                  |
| ----------------------- | ------------------------------------------------------------------------------------- |
| **MCP**                 | The **write** + validation interface. All mutations pass through validated MCP tools. |
| **Supabase (Postgres)** | The **storage** / system of record. Forward-only migrations.                          |
| **Vercel viewer**       | The **read-only** aggregation dashboard. No data entry — render only.                 |

## Consequences

### Positive

- 青色申告 (65万円) artifact generation has a home in the roadmap and a contract to build on.
- A hosted read view shortens the filing-season iteration loop without weakening write controls.
- Postgres + forward-only migrations gives a durable, multi-year-friendly system of record.

### Negative / costs

- Operational surface grows: a Supabase project and a Vercel deployment now exist
  (previously zero hosted dependencies). Connection/secrets management is required.
- Local-only, offline-by-default development is no longer the single story; tooling must
  support a Supabase connection (local Supabase or a dev project).
- Existing/in-flight SQLite-oriented schema work (#1) must be re-expressed against Postgres.

### Neutral / unchanged

- Invariant #2 (server-side validation at the MCP entry) is retained unchanged.
- Single-user posture, no multi-tenant / RLS, audit-log append-only discipline: all unchanged.
- The read view stays read-only; MCP stays the only writer.

## Superseded / re-scoped issues

Relationship of this ADR to the original roadmap (README "Roadmap", issues #1–#5):

| #   | Original title                                                  | Disposition             | Note                                                                                                             |
| --- | --------------------------------------------------------------- | ----------------------- | ---------------------------------------------------------------------------------------------------------------- |
| 1   | feat: bootstrap SQLite schema and minimal migration runner      | **Re-scoped**           | Now **Supabase (Postgres) schema + forward-only migration runner**. SQLite assumption superseded.                |
| 2   | feat: read-side MCP tools (accounts, journal entries, balances) | **Retained**            | Still valid; reads now hit Postgres. Also backs the Vercel viewer.                                               |
| 3   | feat: write-side MCP tools with debit/credit validation         | **Retained**            | Unchanged contract — MCP stays the only validated writer.                                                        |
| 4   | feat: aggregation tools (trial balance, P/L, B/S)               | **Retained / extended** | Aggregations feed both MCP responses and the Vercel read-only viewer; extended toward the 青色申告決算書 layout. |
| 5   | docs: README, Claude Desktop integration, synthetic seed data   | **Retained / extended** | Plus Vercel viewer setup and Supabase connection docs.                                                           |

New downstream work (Supabase provisioning/schema, Vercel viewer, 青色申告決算書 + e-Tax
export) is tracked by issues #9, #10, #11 and later, which build on this ADR.
