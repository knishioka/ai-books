"""監査ログ — audit log domain model.

Mirrors the append-only ``audit_logs`` table (invariant #5). This model is a
*record of what happened*; it has no validators that could reject history. The
``before`` / ``after`` snapshots are free-form JSON objects so any table's row can
be captured. The append-only guarantee lives in the DB triggers and in
:func:`ai_books.audit.record_audit`, which only ever INSERTs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .base import DomainModel


class AuditLog(DomainModel):
    """A single append-only audit-log row."""

    id: int | None = None
    actor: str  # 実行主体 (AI agent / user 識別子)
    action: str  # 論理操作 (insert / update / delete / post ...)
    tool_name: str | None = None  # 経由した MCP tool 名
    table_name: str | None = None  # 対象テーブル
    record_id: str | None = None  # 対象行の識別子
    before: dict[str, Any] | None = None  # 変更前スナップショット
    after: dict[str, Any] | None = None  # 変更後スナップショット
    created_at: datetime | None = None
