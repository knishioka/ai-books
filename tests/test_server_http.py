"""HTTP transport mode tests (Issue #106).

Two layers:

- **Config resolution** (DB- and network-independent): the ``AI_BOOKS_MCP_TRANSPORT``
  / ``AI_BOOKS_MCP_HOST`` / ``AI_BOOKS_MCP_PORT`` env vars resolve to the right values
  and reject bad input, and the default is always stdio (so a plain launch never opens
  a listener).
- **HTTP boot smoke test**: spawns the server exactly as an operator would —
  ``python -m ai_books.server`` with ``AI_BOOKS_MCP_TRANSPORT=http`` on an ephemeral
  port — and drives it through the FastMCP Streamable HTTP client: initialize handshake
  → ``list_tools`` → a protocol-level ``hello`` call. This is the minimum guarantee that
  the http entry point actually boots and answers over the wire, mirroring
  ``test_server_stdio.py`` for the stdio path.

DB-independent by design (only ``hello`` is exercised), so this runs on every
``./scripts/verify.sh`` without a live Postgres.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from ai_books import server

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TIMEOUT_S = 60.0


# --- config resolution --------------------------------------------------------


def test_transport_defaults_to_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset transport → stdio, so a plain launch never opens a network listener."""
    monkeypatch.delenv(server.TRANSPORT_ENV, raising=False)
    assert server._resolve_transport() == "stdio"


@pytest.mark.parametrize("value", ["", "   "])
def test_transport_blank_is_stdio(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """An empty / whitespace value is treated as unset (stdio)."""
    monkeypatch.setenv(server.TRANSPORT_ENV, value)
    assert server._resolve_transport() == "stdio"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("http", "http"), ("HTTP", "http"), (" http ", "http"), ("stdio", "stdio")],
)
def test_transport_parsed_case_insensitively(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: str
) -> None:
    """``http`` is accepted regardless of case / surrounding whitespace."""
    monkeypatch.setenv(server.TRANSPORT_ENV, value)
    assert server._resolve_transport() == expected


def test_transport_rejects_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown transport fails loudly rather than silently falling back."""
    monkeypatch.setenv(server.TRANSPORT_ENV, "websocket")
    with pytest.raises(RuntimeError, match="AI_BOOKS_MCP_TRANSPORT"):
        server._resolve_transport()


def test_host_defaults_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default host is loopback so http never binds a public interface by accident."""
    monkeypatch.delenv(server.HOST_ENV, raising=False)
    assert server._resolve_host() == "127.0.0.1"


def test_host_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit host (e.g. to expose on all interfaces) is honoured."""
    monkeypatch.setenv(server.HOST_ENV, " 0.0.0.0 ")
    assert server._resolve_host() == "0.0.0.0"


def test_port_defaults_to_8000(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(server.PORT_ENV, raising=False)
    assert server._resolve_port() == 8000


def test_port_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(server.PORT_ENV, " 9123 ")
    assert server._resolve_port() == 9123


@pytest.mark.parametrize("value", ["not-a-number", "12.5"])
def test_port_rejects_non_integer(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(server.PORT_ENV, value)
    with pytest.raises(RuntimeError, match="must be an integer"):
        server._resolve_port()


@pytest.mark.parametrize("value", ["0", "65536", "-1"])
def test_port_rejects_out_of_range(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(server.PORT_ENV, value)
    with pytest.raises(RuntimeError, match=r"1\.\.65535"):
        server._resolve_port()


# --- HTTP boot smoke test -----------------------------------------------------


def _free_port() -> int:
    """Grab an ephemeral port the OS just confirmed is free, then release it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def http_server() -> Iterator[str]:
    """Spawn ``python -m ai_books.server`` over http on a free port; yield its MCP URL.

    The subprocess is torn down on fixture teardown so no listener lingers between tests.
    """
    port = _free_port()
    env = {
        **os.environ,
        server.TRANSPORT_ENV: "http",
        server.HOST_ENV: "127.0.0.1",
        server.PORT_ENV: str(port),
    }
    # Redirect output to a temp file, NOT subprocess.PIPE: FastMCP's startup banner +
    # uvicorn request logs are written continuously, and a PIPE nobody drains would fill
    # the ~64KB OS pipe buffer and deadlock the child mid-run, hanging the test until
    # timeout. A file never blocks the writer, and we can still read it back on failure.
    # The outer ``with`` owns the file; the inner ``finally`` reaps the process first, so
    # the writer is gone before the file closes.
    with tempfile.TemporaryFile() as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "ai_books.server"],
            cwd=str(_REPO_ROOT),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        url = f"http://127.0.0.1:{port}/mcp"
        try:
            # Wait for the listener to accept connections (cold import under CI can be slow).
            deadline = time.monotonic() + _TIMEOUT_S
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    log_file.seek(0)
                    out = log_file.read().decode(errors="replace")
                    raise RuntimeError(f"server exited early (code {proc.returncode}):\n{out}")
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(0.5)
                    if sock.connect_ex(("127.0.0.1", port)) == 0:
                        break
                time.sleep(0.1)
            else:
                raise RuntimeError("server did not start listening within timeout")
            yield url
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)


def _http_client(url: str) -> Client[StreamableHttpTransport]:
    return Client(StreamableHttpTransport(url), init_timeout=_TIMEOUT_S, timeout=_TIMEOUT_S)


async def test_http_boot_initialize_and_hello(http_server: str) -> None:
    """Server boots over Streamable HTTP, lists tools, and answers ``hello`` on the wire."""
    client = _http_client(http_server)
    async with asyncio.timeout(_TIMEOUT_S), client:
        assert client.is_connected()  # initialize handshake over HTTP succeeded
        tools = await client.list_tools()
        result = await client.call_tool("hello", {"name": "http"})

    assert "hello" in {tool.name for tool in tools}
    assert not result.is_error
    assert result.data == "Hello, http! ai-books server is alive."
