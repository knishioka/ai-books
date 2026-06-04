"""Postgres connectivity smoke test.

Skips when ``AI_BOOKS_DB_URL`` is unset, so the default ``./scripts/verify.sh``
run stays green without a live Postgres. CI sets ``AI_BOOKS_DB_URL`` to a
Postgres service container, so ``test_ping_returns_one`` actually runs there.
"""

from __future__ import annotations

import os

import pytest

from ai_books import db


@pytest.mark.skipif(
    not os.environ.get(db.DB_URL_ENV),
    reason=f"{db.DB_URL_ENV} not set; skipping live Postgres smoke test",
)
def test_ping_returns_one() -> None:
    assert db.ping() == 1


def test_get_db_url_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(db.DB_URL_ENV, "postgresql://example/db")
    assert db.get_db_url() == "postgresql://example/db"


def test_get_db_url_raises_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(db.DB_URL_ENV, raising=False)
    with pytest.raises(RuntimeError):
        db.get_db_url()
