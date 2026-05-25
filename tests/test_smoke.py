"""M0 smoke tests: package imports, MCP server initialises, hello logic works."""

from __future__ import annotations

import ai_books
from ai_books import server


def test_package_has_version() -> None:
    assert isinstance(ai_books.__version__, str)
    assert ai_books.__version__


def test_mcp_server_initialised() -> None:
    assert server.mcp is not None
    assert server.mcp.name == "ai-books"


def test_greet_default() -> None:
    result = server._greet("world")
    assert "Hello, world" in result
    assert "ai-books" in result


def test_greet_custom_name() -> None:
    result = server._greet("ken")
    assert result.startswith("Hello, ken")
