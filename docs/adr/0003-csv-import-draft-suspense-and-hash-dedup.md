# ADR 0003 — Bank/CSV import: draft 起票・suspense 科目・`import_hash` 重複排除

- Status: Accepted
- Date: 2026-06-04
- Deciders: ai-books maintainers
- Relates to: #14/#38 (CSV import), migration `20260604000007_csv_import.sql`
- Retroactively recorded: 2026-06-05 (#90). 決定の初出は #38。

## Context

Importing a bank / credit-card statement (#14) raises three design questions that downstream code
and the ledger's integrity depend on:

1. **Do imports post directly, or stage for review?** An imported row is a _guess_ — the 相手科目
   is inferred, not authoritative. Posting it straight to the books would let unreviewed data
   confirm the ledger.
2. **What happens when the 相手科目 cannot be determined?** A two-line entry must still balance even
   when the offsetting account is unknown.
3. **How is re-importing the same file made safe?** Re-running an import must not create duplicate
   entries, and the dedup mechanism must not perturb the voucher-number 連番性 (ADR
   [0002](./0002-journal-write-model-soft-void-and-sequence-voucher.md)).

## Decision

### 1. Imports always produce `status=draft` entries

- `plan_import` is a **pure** function (no DB): it parses the CSV against a known format, maps each
  row to a two-line self-balancing entry, and never confirms the books directly. A human/AI reviews
  and posts the drafts separately (#13). 取込が直接帳簿を確定することはない。

### 2. Unresolved 相手科目 → suspense 科目 (仮払金 / 仮受金)

- The 相手科目 is inferred from the 摘要 via prefix keyword rules (`OUTFLOW_RULES` / `INFLOW_RULES`).
- When no rule matches, the line falls back to a **suspense account** so the entry still balances:
  - 出金 (debit side unresolved) → **仮払金** (`1210`, asset) — `SUSPENSE_DEBIT_CODE`.
  - 入金 (credit side unresolved) → **仮受金** (`2170`, liability) — `SUSPENSE_CREDIT_CODE`.
- Review then reclassifies the suspense line to the real account before posting.

### 3. Dedup via a dedicated `import_hash` column (not by reusing `voucher_no`)

- A new `journal_entries.import_hash text` column holds a deterministic SHA-256 fingerprint of the
  source row (`_import_hash`: account code, format, row index, date, side, amount, description, raw
  balance). A **partial** `UNIQUE` index (`WHERE import_hash IS NOT NULL`) enforces at-most-once
  import at the storage layer; the service's pre-insert existence check is the front-line guard.
- `import_hash` is `NULL` for hand-entered / seed entries — dedup applies **only** to imported rows.
- **Why a dedicated column, not voucher_no:** the hash is import-provenance, orthogonal to the
  human-facing voucher number. Overloading `voucher_no` with hashes would corrupt the contiguous
  采番 / 連番性 that ADR 0002 establishes. Keeping `import_hash` separate preserves both.
- Including the row's position and 残高 in the hash keeps two _legitimately_ identical transactions
  in one file distinct, while re-importing the same file reproduces the same hashes and is skipped.

### Alternatives not taken

- **Post imports directly**: rejected — lets unreviewed inference confirm the books.
- **Dedup on `(date, amount, description)`**: rejected — collapses genuinely-duplicate same-day
  transactions; the positional + 残高 fingerprint avoids that.

## Consequences

### Positive

- Inference is never trusted blindly — everything lands as reviewable draft.
- Imports are idempotent (re-import is safe) without touching voucher numbering.
- Unmatched rows are visible as 仮払金/仮受金 balances, a natural "needs review" queue.

### Negative / costs

- Keyword rules are heuristic and will mis-route; the suspense fallback + draft review is the safety
  net, but reviewers must actually reclassify suspense lines.
- The hash inputs are now a compatibility surface: changing them changes dedup identity (a re-import
  after a hash-formula change would not be recognized as a duplicate).

### Neutral / unchanged

- MCP stays the only validated writer (invariant #2). Voucher numbering (ADR 0002) is untouched.

## Implementation references

- `src/ai_books/services/csv_import.py` — `plan_import` (pure), `OUTFLOW_RULES`/`INFLOW_RULES`,
  `SUSPENSE_DEBIT_CODE`/`SUSPENSE_CREDIT_CODE`, `_import_hash`.
- `supabase/migrations/20260604000007_csv_import.sql` — `import_hash` column + partial UNIQUE index.
- `src/ai_books/seed/accounts.py` — 仮払金 (`1210`) / 仮受金 (`2170`) definitions.
