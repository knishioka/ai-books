"""Shared exception hierarchy for ai-books.

These types give the MCP layer a *machine-readable* way to surface failures.
Per AGENTS.md invariant #2 ("server-side validation absolute") all writes flow
through Pydantic validation at the MCP tool entry; when that validation fails we
translate Pydantic's error report into :class:`DomainValidationError`, whose
:meth:`~DomainValidationError.to_dict` payload an MCP tool can return verbatim so
the calling agent can reason about *which* field failed and *why* — not just that
"something" went wrong.

Repository / persistence problems raise :class:`RepositoryError` (and the more
specific :class:`RecordNotFoundError`) so callers can distinguish a *bad input*
(client's fault, fixable by re-prompting) from a *storage* failure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic import ValidationError


class AiBooksError(Exception):
    """Base class for every error raised by ai-books application code."""


class DomainValidationError(AiBooksError):
    """A domain-model validation failure, in a form MCP can return machine-readably.

    ``errors`` is a list of ``{"field": str, "message": str, "type": str}`` dicts
    — one per failed constraint — mirroring (a trimmed view of) Pydantic's own
    error report so the structure is stable across models.
    """

    def __init__(self, message: str, errors: list[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.errors: list[dict[str, str]] = errors or []

    @classmethod
    def from_pydantic(cls, exc: ValidationError) -> DomainValidationError:
        """Build a :class:`DomainValidationError` from a Pydantic ``ValidationError``."""
        errors = [
            {
                "field": ".".join(str(part) for part in err["loc"]),
                "message": err["msg"],
                "type": err["type"],
            }
            for err in exc.errors()
        ]
        title = getattr(exc, "title", "validation")
        return cls(f"{title}: {len(errors)} validation error(s)", errors)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable payload suitable for an MCP tool response."""
        return {"error": "validation_error", "message": self.message, "details": self.errors}


class SeedIntegrityError(AiBooksError):
    """The chart-of-accounts seed data is internally inconsistent.

    Raised by ``ai_books.seed.accounts.validate_chart`` *before* any row is written
    when a seed entry's normal balance disagrees with its account type, a 表示区分
    sits on the wrong account type, codes collide, a parent reference dangles, or a
    required 表示区分 has no account mapped to it. ``problems`` lists every issue
    found (validation does not stop at the first) so the whole seed can be fixed in
    one pass.
    """

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("chart-of-accounts seed is inconsistent:\n  - " + "\n  - ".join(problems))


class RepositoryError(AiBooksError):
    """A persistence-layer failure (connection, SQL, integrity constraint, …)."""


class RecordNotFoundError(RepositoryError):
    """A lookup that was expected to find a row found none."""

    def __init__(self, entity: str, key: object) -> None:
        super().__init__(f"{entity} not found: {key!r}")
        self.entity = entity
        self.key = key
