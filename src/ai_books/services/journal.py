"""Journal entry write service — the double-entry core's write path (Issue #13).

Every mutation of a 仕訳 flows through here so the accounting invariants are enforced
in exactly one place (AGENTS.md invariant #2, "server-side validation absolute"):

- **balance** (借方合計 = 貸方合計) and **Decimal precision** — re-checked by building
  the domain :class:`~ai_books.models.JournalEntry`, whose validators reject an
  imbalance or an over-precision 金額; a Pydantic ``ValidationError`` is translated
  into a machine-readable :class:`DomainValidationError`;
- **account references** — each line's 勘定科目コード must resolve to an existing,
  *active* account (missing → :class:`RecordNotFoundError`, retired →
  :class:`InactiveAccountError`);
- **会計期間** — when fiscal years are defined, an entry's 取引日 must fall inside one;
- **lifecycle** — ``posted`` entries are immutable (訂正は逆仕訳 or 取消で), only a
  ``draft`` can be edited or posted, and a 取消済 entry cannot be voided again
  (:class:`EntryStateError`).

Each operation runs in one transaction together with its ``audit_logs`` append, so
the before/after trail (invariant #5) is never out of step with the change it
records. Voiding never deletes — it flips the status to ``voided`` and keeps the row,
preserving 帳簿の連続性 and the 電子帳簿保存 訂正・削除履歴.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from ai_books.audit import record_audit
from ai_books.db.repository import AccountRepository, JournalRepository
from ai_books.errors import (
    DomainValidationError,
    EntryStateError,
    InactiveAccountError,
    RecordNotFoundError,
)
from ai_books.models import (
    EntryStatus,
    JournalEntry,
    JournalEntryInput,
    JournalLine,
)

#: Default actor recorded in the audit log when a tool does not name one.
DEFAULT_ACTOR = "ai-agent"


def _snapshot(entry: JournalEntry) -> dict[str, Any]:
    """JSON-serialisable snapshot of an entry for the audit ``before``/``after``."""
    return entry.model_dump(mode="json")


class JournalService:
    """Transactional write operations over 仕訳 (journal entries)."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn
        self._accounts = AccountRepository(conn)
        self._journals = JournalRepository(conn)

    # --- create ---------------------------------------------------------------

    def create_entry(
        self,
        data: JournalEntryInput,
        *,
        actor: str = DEFAULT_ACTOR,
        tool_name: str = "create_journal_entry",
        import_hash: str | None = None,
    ) -> JournalEntry:
        """Validate, persist, and audit a new entry; return it as stored.

        The entry must balance and reference only active accounts; its 取引日 must sit
        within a defined fiscal year (when any exist). A 伝票番号 is auto-assigned from
        the sequence when the caller does not supply one. ``import_hash`` is set by the
        CSV import path (#14) to fingerprint the source row for 二重取込検知; the
        ``journal_entries.import_hash`` partial-unique index backs it.
        """
        with self._conn.transaction():
            entry = self._build_entry(data, import_hash=import_hash)
            if entry.voucher_no is None:
                entry = entry.model_copy(update={"voucher_no": self._journals.next_voucher_no()})
            stored = self._journals.insert_entry(entry)
            record_audit(
                self._conn,
                actor=actor,
                action="insert",
                tool_name=tool_name,
                table_name="journal_entries",
                record_id=stored.id,
                after=_snapshot(stored),
            )
        return stored

    # --- update (draft only) --------------------------------------------------

    def update_entry(
        self,
        entry_id: int,
        data: JournalEntryInput,
        *,
        actor: str = DEFAULT_ACTOR,
        tool_name: str = "update_journal_entry",
    ) -> JournalEntry:
        """Replace a *draft* entry's header and lines wholesale; return it as stored.

        Posting freezes an entry (訂正は逆仕訳 or 取消で), so a non-draft is refused with
        :class:`EntryStateError`. The pre-image and post-image are both audited.
        """
        existing = self._get_or_raise(entry_id)
        if existing.status is not EntryStatus.DRAFT:
            raise EntryStateError(
                entry_id, existing.status.value, "update", "only draft entries can be edited"
            )
        with self._conn.transaction():
            before = _snapshot(existing)
            new_entry = self._build_entry(data, voucher_no_override=existing.voucher_no)
            stored = self._journals.replace_entry(entry_id, new_entry)
            record_audit(
                self._conn,
                actor=actor,
                action="update",
                tool_name=tool_name,
                table_name="journal_entries",
                record_id=entry_id,
                before=before,
                after=_snapshot(stored),
            )
        return stored

    # --- post (draft -> posted) -----------------------------------------------

    def post_entry(
        self,
        entry_id: int,
        *,
        actor: str = DEFAULT_ACTOR,
        tool_name: str = "post_journal_entry",
    ) -> JournalEntry:
        """Confirm a draft entry into the books (記帳確定); return it as stored.

        Only a ``draft`` can be posted, and it must carry balanced lines — an empty or
        unbalanced draft cannot be confirmed.
        """
        existing = self._get_or_raise(entry_id)
        if existing.status is not EntryStatus.DRAFT:
            raise EntryStateError(
                entry_id, existing.status.value, "post", "only draft entries can be posted"
            )
        if not existing.lines:
            raise DomainValidationError(
                f"cannot post entry {entry_id}: it has no lines",
                [{"field": "lines", "message": "a posted entry must have lines", "type": "value"}],
            )
        # Stored drafts are always balanced (the model enforces it on write), but
        # re-assert here so posting can never confirm an imbalance into the books.
        if not existing.is_balanced:  # pragma: no cover - unreachable for stored entries
            raise DomainValidationError(
                f"cannot post entry {entry_id}: 借方 {existing.total_debit} "
                f"!= 貸方 {existing.total_credit}",
                [{"field": "lines", "message": "debit/credit imbalance", "type": "value"}],
            )
        with self._conn.transaction():
            before = _snapshot(existing)
            stored = self._journals.mark_posted(entry_id)
            record_audit(
                self._conn,
                actor=actor,
                action="post",
                tool_name=tool_name,
                table_name="journal_entries",
                record_id=entry_id,
                before=before,
                after=_snapshot(stored),
            )
        return stored

    # --- void (取消) -----------------------------------------------------------

    def void_entry(
        self,
        entry_id: int,
        *,
        reason: str,
        actor: str = DEFAULT_ACTOR,
        tool_name: str = "void_journal_entry",
    ) -> JournalEntry:
        """Cancel an entry (取消) without deleting it; return it as stored.

        A ``draft`` or ``posted`` entry can be voided; an already-voided one cannot.
        The row survives (帳簿の連続性維持) and the before/after is audited, together
        satisfying the 電子帳簿保存 訂正・削除履歴 requirement.
        """
        reason = reason.strip()
        if not reason:
            raise DomainValidationError(
                "void requires a reason",
                [{"field": "reason", "message": "reason must not be empty", "type": "value"}],
            )
        existing = self._get_or_raise(entry_id)
        if existing.status is EntryStatus.VOIDED:
            raise EntryStateError(
                entry_id, existing.status.value, "void", "entry is already voided"
            )
        with self._conn.transaction():
            before = _snapshot(existing)
            stored = self._journals.mark_voided(entry_id, reason)
            record_audit(
                self._conn,
                actor=actor,
                action="void",
                tool_name=tool_name,
                table_name="journal_entries",
                record_id=entry_id,
                before=before,
                after=_snapshot(stored),
            )
        return stored

    # --- internals ------------------------------------------------------------

    def _get_or_raise(self, entry_id: int) -> JournalEntry:
        entry = self._journals.get_entry(entry_id)
        if entry is None:
            raise RecordNotFoundError("journal_entry", entry_id)
        return entry

    def _build_entry(
        self,
        data: JournalEntryInput,
        *,
        voucher_no_override: str | None = None,
        import_hash: str | None = None,
    ) -> JournalEntry:
        """Turn validated input into a domain :class:`JournalEntry` ready to store.

        Resolves each line's 勘定科目コード to an active account id, checks the 取引日 is
        within a defined fiscal year, and constructs the entry so its balance / precision
        validators fire — translating any Pydantic failure into a machine-readable
        :class:`DomainValidationError`.
        """
        self._assert_within_fiscal_year(data.entry_date)
        lines = [
            JournalLine(
                account_id=self._resolve_active_account_id(line.account_code),
                side=line.side,
                amount=line.amount,
                tax_category=line.tax_category,
                sub_account=line.sub_account,
                line_description=line.line_description,
            )
            for line in data.lines
        ]
        try:
            return JournalEntry(
                entry_date=data.entry_date,
                recorded_date=data.recorded_date,
                description=data.description,
                voucher_no=voucher_no_override
                if voucher_no_override is not None
                else data.voucher_no,
                source=data.source,
                import_hash=import_hash,
                status=data.status,
                lines=lines,
            )
        except ValidationError as exc:
            raise DomainValidationError.from_pydantic(exc) from exc

    def _resolve_active_account_id(self, code: str) -> int:
        account = self._accounts.get_by_code(code)
        if account is None or account.id is None:
            raise RecordNotFoundError("account", code)
        if not account.is_active:
            raise InactiveAccountError(code)
        return account.id

    def _assert_within_fiscal_year(self, entry_date: date) -> None:
        """Reject a 取引日 outside every defined fiscal year (期間外).

        Fiscal-year seeding is independent of posting, so when none are defined the
        check is skipped rather than blocking all writes.
        """
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT count(*) AS n FROM fiscal_years")
            count_row = cur.fetchone()
            if count_row is None or int(count_row["n"]) == 0:
                return
            cur.execute(
                "SELECT 1 FROM fiscal_years WHERE %s BETWEEN start_date AND end_date LIMIT 1",
                (entry_date,),
            )
            if cur.fetchone() is None:
                raise DomainValidationError(
                    f"entry_date {entry_date.isoformat()} is outside every defined fiscal year",
                    [
                        {
                            "field": "entry_date",
                            "message": "取引日 must fall within a defined fiscal year (会計期間)",
                            "type": "value",
                        }
                    ],
                )
