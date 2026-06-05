# ADR 0002 — Journal write model: soft-void (`status=voided`) + Postgres SEQUENCE 伝票番号

- Status: Accepted
- Date: 2026-06-04
- Deciders: ai-books maintainers
- Relates to: #35 (write-side MCP tools), migration `20260604000006_journal_write.sql`
- Retroactively recorded: 2026-06-05 (#90). 決定の初出は #35。

## Context

Once journal entries became writable via MCP (#35), two questions had to be settled in a way
that later code would depend on:

1. **How is an entry cancelled?** Japanese bookkeeping under 電子帳簿保存法 expects a
   **訂正・削除履歴** — you do not silently erase a posted record. A hard `DELETE` destroys the
   audit trail and breaks 帳簿の連続性 (the ledger's continuity).
2. **How are 伝票番号 (voucher numbers) assigned?** Filing-grade books want voucher numbers that
   are contiguous and gap-detectable (連番付与 + 欠番検知), assigned safely under concurrent
   creates, without an application-level counter that can race.

Both interact with AGENTS.md invariant #5 (`audit_logs` is append-only) and invariant #2
(server-side validation at the MCP entry).

## Decision

### 1. Cancellation = soft-void via a `status` flag, never a hard delete

- The `entry_status` enum is extended with `'voided'` (alongside `'draft'`, `'posted'`); the row
  is **kept** and flipped to `voided`, recording `void_reason` and `voided_at`.
- Voiding is exposed as the MCP tool `void_journal_entry(entry_id, reason, actor)`. A `draft` or
  `posted` entry can be voided; an already-voided one cannot, and an empty reason is rejected.
- The before/after is written to the audit log (電子帳簿保存 訂正・削除履歴).
- Voided entries **no longer count** toward balances or the 総勘定元帳, but remain queryable as
  history.

### 2. 伝票番号 = a Postgres `SEQUENCE`, with a partial-unique backstop

- A dedicated `CREATE SEQUENCE journal_voucher_no_seq` backs voucher-number 採番. `nextval()` is
  atomic, so concurrent creates never collide.
- `journal_entries.voucher_no` is `text`, with a **partial** `UNIQUE` index
  (`WHERE voucher_no IS NOT NULL`) as the backstop — entries without an explicit voucher number
  are still allowed.
- Sequences are monotonic but **may gap on rollback** — this is accepted as the intended basis for
  連番付与 + downstream 欠番検知 (a gap is detectable, not silently reused).

### Alternatives not taken

- **Hard delete + tombstone table**: rejected — duplicates the append-only audit log and risks
  losing 連続性.
- **Application-side counter (`MAX(voucher_no)+1`)**: rejected — races under concurrency and needs
  table-level locking; a SEQUENCE is the database-native, atomic answer.

## Consequences

### Positive

- 電子帳簿保存法 の訂正・削除履歴要件と整合する取消モデル。履歴は失われない。
- Concurrency-safe, monotonic voucher numbering with gap detection, no app-level locking.

### Negative / costs

- Balance/aggregation queries must always filter out `voided` rows — a permanent obligation on the
  read path.
- Voucher numbers can have gaps (rolled-back transactions). Downstream must treat a gap as a signal
  to investigate, not as corruption.

### Neutral / unchanged

- MCP remains the sole validated writer (invariant #2). `audit_logs` stays append-only (invariant #5).

## Implementation references

- `supabase/migrations/20260604000006_journal_write.sql` — `'voided'` enum value, `void_reason` /
  `voided_at` columns, `CREATE SEQUENCE journal_voucher_no_seq`.
- `supabase/migrations/20260604000002_journal.sql` — `voucher_no` column + partial UNIQUE index.
- `src/ai_books/server.py` — `void_journal_entry` MCP tool.
- `src/ai_books/services/journal.py` — `JournalService.void_entry` (void logic + audit write).
