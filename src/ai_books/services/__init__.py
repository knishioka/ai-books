"""Application services — the write-side orchestration layer.

Sitting between the MCP tool entry points and the repositories, a service owns one
logical operation end to end: it validates input against the domain models
(invariant #2), resolves references, performs the persistence through the
repositories, and appends the audit-log trail (invariant #5) — all inside a single
transaction so the change and its audit record commit or roll back together.

Submodules:
    ``ai_books.services.journal`` — journal entry create / update / void / post.
"""

from __future__ import annotations

from ai_books.services.journal import JournalService

__all__ = ["JournalService"]
