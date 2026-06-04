"""Unit tests for the shared exception hierarchy — no database required."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_books.errors import (
    AiBooksError,
    DomainValidationError,
    RecordNotFoundError,
    RepositoryError,
)
from ai_books.models import Account, AccountType, NormalSide


def test_hierarchy() -> None:
    assert issubclass(DomainValidationError, AiBooksError)
    assert issubclass(RepositoryError, AiBooksError)
    assert issubclass(RecordNotFoundError, RepositoryError)


def test_domain_validation_error_to_dict() -> None:
    err = DomainValidationError("bad", [{"field": "amount", "message": "x", "type": "y"}])
    payload = err.to_dict()
    assert payload["error"] == "validation_error"
    assert payload["message"] == "bad"
    assert payload["details"][0]["field"] == "amount"


def test_domain_validation_error_from_pydantic() -> None:
    with pytest.raises(ValidationError) as caught:
        Account(
            code="1110",
            name="現金",
            account_type=AccountType.ASSET,
            normal_balance=NormalSide.CREDIT,  # inconsistent → triggers model validator
        )

    err = DomainValidationError.from_pydantic(caught.value)
    assert err.errors  # at least one structured error
    assert all({"field", "message", "type"} <= set(item) for item in err.errors)
    # The payload is JSON-shaped for an MCP tool to return verbatim.
    assert err.to_dict()["error"] == "validation_error"


def test_record_not_found_error_carries_context() -> None:
    err = RecordNotFoundError("account", 42)
    assert err.entity == "account"
    assert err.key == 42
    assert "42" in str(err)
