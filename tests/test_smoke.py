"""M0 smoke tests: package imports, MCP server initialises, hello logic works."""

from __future__ import annotations

import ai_books
from ai_books import server


async def test_chart_of_accounts_tools_registered() -> None:
    tools = {tool.name for tool in await server.mcp.list_tools()}
    assert {"list_accounts", "get_account", "search_accounts"} <= tools


async def test_profit_and_loss_tool_registered() -> None:
    # Issue #20: the 損益計算書 generation tool is exposed over MCP.
    tools = {tool.name for tool in await server.mcp.list_tools()}
    assert "profit_and_loss" in tools


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
