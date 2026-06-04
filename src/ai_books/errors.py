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
    """Base class for every error raised by ai-books application code.

    The write tools surface failures to the calling agent *machine-readably*: every
    subclass returns a stable ``{"error": <code>, "message": ...}`` payload from
    :meth:`to_dict`, which a tool can serialise verbatim. Subclasses extend the
    payload (e.g. field-level ``details``) but never change those two base keys.
    """

    #: Stable machine-readable error code for this class (overridden per subclass).
    error_code = "error"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable payload suitable for an MCP tool response."""
        return {"error": self.error_code, "message": str(self)}


class DomainValidationError(AiBooksError):
    """A domain-model validation failure, in a form MCP can return machine-readably.

    ``errors`` is a list of ``{"field": str, "message": str, "type": str}`` dicts
    — one per failed constraint — mirroring (a trimmed view of) Pydantic's own
    error report so the structure is stable across models.
    """

    error_code = "validation_error"

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

    error_code = "not_found"

    def __init__(self, entity: str, key: object) -> None:
        super().__init__(f"{entity} not found: {key!r}")
        self.entity = entity
        self.key = key

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.error_code,
            "message": str(self),
            "entity": self.entity,
            "key": str(self.key),
        }


class InactiveAccountError(AiBooksError):
    """A write referenced an account that exists but is no longer active (無効科目).

    Distinct from :class:`RecordNotFoundError` so a caller can tell "no such code"
    from "that code is retired" — both are rejected, but the fix differs.
    """

    error_code = "inactive_account"

    def __init__(self, code: str) -> None:
        super().__init__(f"account {code!r} is inactive and cannot be used on a journal line")
        self.code = code

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.error_code, "message": str(self), "code": self.code}


class CsvImportError(AiBooksError):
    """A bank/CC CSV could not be parsed or mapped into draft entries (#14).

    Raised before anything is written when the CSV is empty, its header matches no
    known format (and none was specified), a required column is missing, or a row's
    日付 / 金額 cannot be parsed. ``row`` is the 1-based source line (excluding the
    header) when the problem is row-specific, ``None`` for a whole-file problem, so a
    caller can point the user at the offending line machine-readably.
    """

    error_code = "csv_import_error"

    def __init__(self, message: str, *, row: int | None = None) -> None:
        super().__init__(message)
        self.row = row

    def to_dict(self) -> dict[str, Any]:
        payload = {"error": self.error_code, "message": str(self)}
        if self.row is not None:
            payload["row"] = str(self.row)
        return payload


class EntryStateError(AiBooksError):
    """A journal-entry lifecycle transition that the 状態 does not allow.

    Posted entries are immutable (訂正は逆仕訳 or 取消で); a draft must exist and be
    balanced before it can be posted; a 取消済 entry cannot be voided again. Each such
    rejection raises this with the entry id, its current status, and the attempted
    action so the caller knows exactly why the transition was refused.
    """

    error_code = "invalid_state"

    def __init__(self, entry_id: int, current_status: str, action: str, detail: str) -> None:
        super().__init__(f"cannot {action} entry {entry_id} (status={current_status}): {detail}")
        self.entry_id = entry_id
        self.current_status = current_status
        self.action = action

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.error_code,
            "message": str(self),
            "entry_id": self.entry_id,
            "current_status": self.current_status,
            "action": self.action,
        }
