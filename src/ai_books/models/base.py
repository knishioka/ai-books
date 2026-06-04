"""Shared Pydantic base for ai-books domain models.

Centralises the model configuration so every entity validates the same way:
``Decimal`` amounts are never coerced from floats silently, unknown fields are
rejected (the type contract is closed), and assignment is re-validated.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Base class for all ai-books domain models.

    Configuration rationale:
        ``extra="forbid"`` — the model is the *contract*; an unexpected key is a
        bug, not data to keep.
        ``validate_assignment=True`` — mutating a field re-runs validators, so an
        object cannot be edited into an invalid state after construction.
        ``str_strip_whitespace=True`` — incidental whitespace never changes a code
        or name's identity.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )
