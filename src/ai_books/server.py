"""FastMCP server entry point.

M0 scaffold: only a single ``hello`` smoke-test tool is registered. Real
accounting tools (accounts / journal / reports) land in Issues #1-#4.
"""

from __future__ import annotations

from fastmcp import FastMCP

mcp: FastMCP = FastMCP(
    name="ai-books",
    instructions=(
        "AI-first accounting MCP server. Provides double-entry bookkeeping primitives "
        "(chart of accounts, journal entries, trial balance, financial statements). "
        "M0 only exposes a `hello` smoke-test tool."
    ),
)


def _greet(name: str) -> str:
    """Pure greeting helper. Kept separate from the MCP tool wrapper so unit tests
    can exercise the logic without going through FastMCP dispatch."""
    return f"Hello, {name}! ai-books server is alive."


@mcp.tool
def hello(name: str = "world") -> str:
    """Return a greeting. M0 smoke-test tool only."""
    return _greet(name)


def main() -> None:
    """Run the MCP server over stdio (FastMCP default transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
