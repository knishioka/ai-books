"""Protocol-boundary *contract* tests for the MCP tool surface (Issue #56).

``test_mcp_client`` proves the happy path round-trips over FastMCP; this module pins the
two contracts an AI client actually depends on and that a careless refactor could break
silently:

1. **Tool schema contract** — the set of registered tools, and for each one its input
   JSON schema (property names, ``required``, key parameter types), a non-empty
   description, and its output-schema *kind* (a structured object vs. a wrapped
   scalar/list). These come straight off ``Client.list_tools()`` — the wire shape an AI
   client introspects — so a renamed parameter, a dropped ``required``, a flipped type, or
   an accidentally-removed tool fails the snapshot rather than reaching production.

2. **Error contract** — that the representative 異常系 cross the protocol as a ``ToolError``
   whose message is the *machine-readable* JSON payload our :class:`AiBooksError` hierarchy
   promises (``{"error": <stable code>, ...}``), with the exact extra keys each code carries
   (``details[].field/message`` for a validation error, ``entity``/``key`` for not-found,
   …). This pins the payload an agent parses to decide how to recover.

The schema-contract and schema-stage-rejection tests need no Postgres and run under
``./scripts/verify.sh``; the error-payload golden tests need a seeded book and are guarded
with ``requires_db`` (run under ``./scripts/test.sh -k mcp_contract``).
"""

from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.types import Tool

from ai_books.db.repository import AccountRepository, JournalRepository
from ai_books.models import (
    Account,
    AccountType,
    EntrySide,
    EntryStatus,
    JournalEntry,
    JournalLine,
    NormalSide,
    StatementCategory,
)
from tests.conftest import DB_URL_ENV

requires_db = pytest.mark.skipif(
    not os.environ.get(DB_URL_ENV),
    reason=f"{DB_URL_ENV} not set; skipping DB-backed MCP contract tests",
)


# === 1. tool schema contract (no DB) ==========================================
#
# A frozen description of every registered tool's wire contract. ``required`` and ``props``
# are pinned exactly so adding/renaming/dropping a parameter fails the snapshot; ``types``
# pins the JSON-schema ``type`` of the parameters whose coercion an agent relies on (an int
# id, a string code, a bool flag); ``output`` is the *kind* of the output schema:
#   - "object": the tool returns a domain model → a structured object schema;
#   - "scalar": the tool returns ``str`` → FastMCP wraps it as ``{"result": <string>}``;
#   - "list":   the tool returns ``list[...]`` → wrapped as ``{"result": {"items": ...}}``.


CONTRACT: dict[str, dict[str, Any]] = {
    "hello": {
        "required": [],
        "props": ["name"],
        "types": {"name": "string"},
        "output": "scalar",
    },
    "list_accounts": {
        "required": [],
        "props": ["account_type", "statement_category", "is_active"],
        "types": {},
        "output": "list",
    },
    "get_account": {
        "required": ["code"],
        "props": ["code"],
        "types": {"code": "string"},
        "output": "object",
    },
    "search_accounts": {
        "required": ["query"],
        "props": ["query", "include_inactive"],
        "types": {"query": "string", "include_inactive": "boolean"},
        "output": "list",
    },
    "list_journal_entries": {
        "required": [],
        "props": [
            "start_date",
            "end_date",
            "account_code",
            "status",
            "text",
            "limit",
            "offset",
        ],
        "types": {"limit": "integer", "offset": "integer"},
        "output": "object",
    },
    "get_journal_entry": {
        "required": ["entry_id"],
        "props": ["entry_id"],
        "types": {"entry_id": "integer"},
        "output": "object",
    },
    "get_account_balance": {
        "required": ["account_code"],
        "props": ["account_code", "as_of", "status"],
        "types": {"account_code": "string"},
        "output": "object",
    },
    "get_account_ledger": {
        "required": ["account_code"],
        "props": ["account_code", "start_date", "end_date", "status"],
        "types": {"account_code": "string"},
        "output": "object",
    },
    "trial_balance": {
        "required": [],
        "props": ["as_of", "start_date", "status"],
        "types": {},
        "output": "object",
    },
    "monthly_trend": {
        "required": ["account_code", "fiscal_year"],
        "props": ["account_code", "fiscal_year", "status"],
        "types": {"account_code": "string", "fiscal_year": "string"},
        "output": "object",
    },
    "worksheet": {
        "required": ["fiscal_year"],
        "props": ["fiscal_year", "status"],
        "types": {"fiscal_year": "string"},
        "output": "object",
    },
    "profit_and_loss": {
        "required": ["fiscal_year"],
        "props": ["fiscal_year", "status"],
        "types": {"fiscal_year": "string"},
        "output": "object",
    },
    "balance_sheet": {
        "required": [],
        "props": ["as_of", "status"],
        "types": {},
        "output": "object",
    },
    "journal_book": {
        "required": [],
        "props": ["start_date", "end_date", "status"],
        "types": {},
        "output": "object",
    },
    "general_ledger": {
        "required": [],
        "props": ["account_code", "start_date", "end_date", "status"],
        "types": {},
        "output": "object",
    },
    "export_etax": {
        "required": ["fiscal_year"],
        "props": ["fiscal_year", "format", "format_version"],
        "types": {"fiscal_year": "string", "format": "string", "format_version": "string"},
        "output": "scalar",
    },
    "create_journal_entry": {
        "required": ["entry"],
        "props": ["entry", "actor"],
        "types": {"entry": "object", "actor": "string"},
        "output": "object",
    },
    "update_journal_entry": {
        "required": ["entry_id", "entry"],
        "props": ["entry_id", "entry", "actor"],
        "types": {"entry_id": "integer", "entry": "object", "actor": "string"},
        "output": "object",
    },
    "post_journal_entry": {
        "required": ["entry_id"],
        "props": ["entry_id", "actor"],
        "types": {"entry_id": "integer", "actor": "string"},
        "output": "object",
    },
    "void_journal_entry": {
        "required": ["entry_id", "reason"],
        "props": ["entry_id", "reason", "actor"],
        "types": {"entry_id": "integer", "reason": "string", "actor": "string"},
        "output": "object",
    },
    "import_transactions_csv": {
        "required": ["csv_text", "account_code"],
        "props": ["csv_text", "account_code", "csv_format", "actor"],
        "types": {"csv_text": "string", "account_code": "string", "csv_format": "string"},
        "output": "object",
    },
}


async def _tools_by_name(mcp_client: Client[Any]) -> dict[str, Tool]:
    return {tool.name: tool for tool in await mcp_client.list_tools()}


def _declared_type(schema: dict[str, Any]) -> str | None:
    """The JSON-schema ``type`` of a property, looking through an optional ``anyOf`` wrapper.

    Optional parameters are emitted as ``{"anyOf": [{"type": T}, {"type": "null"}], ...}`` —
    return ``T`` (the single non-null branch) so the table can pin a parameter's type without
    caring whether it is required.
    """
    if "type" in schema:
        return str(schema["type"])
    branches = [b for b in schema.get("anyOf", []) if b.get("type") != "null"]
    if len(branches) == 1 and "type" in branches[0]:
        return str(branches[0]["type"])
    return None


def _output_kind(tool: Tool) -> str:
    """Classify a tool's output schema as ``object`` / ``scalar`` / ``list`` (see CONTRACT)."""
    schema = tool.outputSchema
    assert schema is not None, f"{tool.name} declares no output schema"
    if not schema.get("x-fastmcp-wrap-result"):
        return "object"
    result = schema["properties"]["result"]
    return "list" if result.get("type") == "array" else "scalar"


async def test_registered_tool_set_is_exactly_expected(mcp_client: Client[Any]) -> None:
    # The whole surface is pinned: a new tool must be added to CONTRACT (and reviewed), and a
    # removed/renamed one — a breaking change for every client — fails here.
    names = set(await _tools_by_name(mcp_client))
    assert names == set(CONTRACT), {
        "added": sorted(names - set(CONTRACT)),
        "removed": sorted(set(CONTRACT) - names),
    }


@pytest.mark.parametrize("name", sorted(CONTRACT))
async def test_input_schema_contract(mcp_client: Client[Any], name: str) -> None:
    tool = (await _tools_by_name(mcp_client))[name]
    expected = CONTRACT[name]
    schema = tool.inputSchema

    properties = schema.get("properties", {})
    assert set(properties) == set(expected["props"]), name
    assert schema.get("required", []) == expected["required"], name
    # An MCP input schema must forbid unknown arguments so a typo'd parameter is rejected at
    # the boundary rather than silently ignored.
    assert schema.get("additionalProperties") is False, name

    for param, json_type in expected["types"].items():
        assert _declared_type(properties[param]) == json_type, (name, param)


@pytest.mark.parametrize("name", sorted(CONTRACT))
async def test_output_schema_kind_contract(mcp_client: Client[Any], name: str) -> None:
    tool = (await _tools_by_name(mcp_client))[name]
    assert _output_kind(tool) == CONTRACT[name]["output"], name


async def test_every_tool_has_a_description(mcp_client: Client[Any]) -> None:
    # The description is the tool's contract *for the model* — an empty one strands the agent.
    for tool in (await _tools_by_name(mcp_client)).values():
        assert tool.description is not None, tool.name
        assert tool.description.strip(), tool.name


# === 2. schema-stage rejection (no DB) ========================================
#
# A missing required argument, an uncoercible type, or an unknown key is rejected during
# argument coercion — *before* the tool body opens a connection — so these need no Postgres
# and surface as a ``ToolError`` (the protocol's "your call was malformed" signal).


async def test_missing_required_argument_is_rejected(mcp_client: Client[Any]) -> None:
    with pytest.raises(ToolError):
        await mcp_client.call_tool("get_account_balance", {})  # account_code missing


async def test_missing_one_of_several_required_args_is_rejected(
    mcp_client: Client[Any],
) -> None:
    with pytest.raises(ToolError):
        await mcp_client.call_tool("void_journal_entry", {"entry_id": 1})  # reason missing


async def test_type_mismatch_is_rejected(mcp_client: Client[Any]) -> None:
    # A non-numeric string cannot coerce to ``entry_id: int`` — rejected at the boundary.
    with pytest.raises(ToolError):
        await mcp_client.call_tool("get_journal_entry", {"entry_id": "not-an-int"})


async def test_unknown_argument_is_rejected(mcp_client: Client[Any]) -> None:
    # ``additionalProperties: false`` means a typo'd argument is a hard error, not a silent no-op.
    with pytest.raises(ToolError):
        await mcp_client.call_tool("get_account", {"code": "1110", "bogus": 1})


async def test_malformed_nested_write_payload_is_rejected(mcp_client: Client[Any]) -> None:
    # ``entry.lines`` must be an array of line objects; a string fails Pydantic coercion at the
    # boundary (before any DB work), surfacing as a schema ``ToolError``.
    with pytest.raises(ToolError):
        await mcp_client.call_tool(
            "create_journal_entry",
            {"entry": {"entry_date": "2026-06-01", "lines": "not-a-list"}},
        )


# === 3. error-payload golden (DB-backed) ======================================
#
# Each representative domain failure must cross the protocol as a ``ToolError`` whose message
# is the stable machine-readable payload from :meth:`AiBooksError.to_dict`. We pin the ``error``
# code *and* the extra keys each code carries — the structure an agent parses to recover.

_TEST_SCHEMA = "ai_books_layer_test"


class _Seed:
    """Account ids for the seeded chart, so tests can reference them by name."""

    def __init__(self, cash: int, sales: int, inactive: int) -> None:
        self.cash = cash
        self.sales = sales
        self.inactive = inactive


def _entry(debit_id: int, credit_id: int, amount: str, entry_date: date) -> JournalEntry:
    value = Decimal(amount)
    return JournalEntry(
        entry_date=entry_date,
        status=EntryStatus.POSTED,
        lines=[
            JournalLine(account_id=debit_id, side=EntrySide.DEBIT, amount=value),
            JournalLine(account_id=credit_id, side=EntrySide.CREDIT, amount=value),
        ],
    )


@pytest.fixture
def seed(migrated_conn: psycopg.Connection[Any]) -> _Seed:
    """Seed a minimal book plus an *inactive* account and a fiscal year.

    現金(1110) / 売上(4000) carry a balanced posted entry; 旧現金(1900) is seeded ``is_active=False``
    so a write referencing it triggers :class:`InactiveAccountError`. FY2026 lets ``export_etax``
    resolve a fiscal year.
    """
    accounts = AccountRepository(migrated_conn)
    cash = accounts.insert(
        Account(
            code="1110",
            name="現金",
            account_type=AccountType.ASSET,
            normal_balance=NormalSide.DEBIT,
            statement_category=StatementCategory.CURRENT_ASSETS,
        )
    )
    sales = accounts.insert(
        Account(
            code="4000",
            name="売上高",
            account_type=AccountType.REVENUE,
            normal_balance=NormalSide.CREDIT,
            statement_category=StatementCategory.SALES,
        )
    )
    inactive = accounts.insert(
        Account(
            code="1900",
            name="旧現金",
            account_type=AccountType.ASSET,
            normal_balance=NormalSide.DEBIT,
            statement_category=StatementCategory.CURRENT_ASSETS,
            is_active=False,
        )
    )
    assert cash.id is not None
    assert sales.id is not None
    assert inactive.id is not None

    migrated_conn.execute(
        "INSERT INTO fiscal_years (name, start_date, end_date) VALUES (%s, %s, %s)",
        ("FY2026", date(2026, 1, 1), date(2026, 12, 31)),
    )
    JournalRepository(migrated_conn).insert_entry(
        _entry(cash.id, sales.id, "1000", date(2026, 4, 1))
    )
    return _Seed(cash.id, sales.id, inactive.id)


def _payload(excinfo: pytest.ExceptionInfo[ToolError]) -> dict[str, Any]:
    """The machine-readable JSON payload carried by a domain ``ToolError`` message."""
    payload = json.loads(str(excinfo.value))
    assert isinstance(payload, dict)
    # Every AiBooksError payload carries a stable code and a human-readable message.
    assert isinstance(payload["error"], str)
    assert isinstance(payload["message"], str)
    return payload


@requires_db
async def test_imbalance_payload_is_validation_error_with_field_details(
    mcp_client: Client[Any], patched_connect: None, seed: _Seed
) -> None:
    imbalanced = {
        "entry_date": "2026-06-01",
        "lines": [
            {"account_code": "1110", "side": "debit", "amount": "100.00"},
            {"account_code": "4000", "side": "credit", "amount": "90.00"},
        ],
    }
    with pytest.raises(ToolError) as excinfo:
        await mcp_client.call_tool("create_journal_entry", {"entry": imbalanced})

    payload = _payload(excinfo)
    assert payload["error"] == "validation_error"
    # The field-level breakdown is what lets an agent point at *which* constraint failed.
    assert isinstance(payload["details"], list)
    assert payload["details"]
    for item in payload["details"]:
        assert set(item) >= {"field", "message"}


@requires_db
async def test_unknown_account_payload_is_not_found_with_entity_and_key(
    mcp_client: Client[Any], patched_connect: None, seed: _Seed
) -> None:
    bad = {
        "entry_date": "2026-06-01",
        "lines": [
            {"account_code": "0000", "side": "debit", "amount": "100.00"},
            {"account_code": "4000", "side": "credit", "amount": "100.00"},
        ],
    }
    with pytest.raises(ToolError) as excinfo:
        await mcp_client.call_tool("create_journal_entry", {"entry": bad})

    payload = _payload(excinfo)
    assert payload["error"] == "not_found"
    assert payload["entity"] == "account"
    assert payload["key"] == "0000"


@requires_db
async def test_inactive_account_payload_carries_the_code(
    mcp_client: Client[Any], patched_connect: None, seed: _Seed
) -> None:
    # 無効科目 is distinct from "no such account" — the payload names the retired code.
    using_inactive = {
        "entry_date": "2026-06-01",
        "lines": [
            {"account_code": "1900", "side": "debit", "amount": "100.00"},
            {"account_code": "4000", "side": "credit", "amount": "100.00"},
        ],
    }
    with pytest.raises(ToolError) as excinfo:
        await mcp_client.call_tool("create_journal_entry", {"entry": using_inactive})

    payload = _payload(excinfo)
    assert payload["error"] == "inactive_account"
    assert payload["code"] == "1900"


@requires_db
async def test_invalid_state_payload_names_status_and_action(
    mcp_client: Client[Any], patched_connect: None, seed: _Seed
) -> None:
    # Post a draft, then post it again: the second call is an illegal lifecycle transition.
    draft = {
        "entry_date": "2026-06-01",
        "lines": [
            {"account_code": "1110", "side": "debit", "amount": "200.00"},
            {"account_code": "4000", "side": "credit", "amount": "200.00"},
        ],
    }
    created = (
        await mcp_client.call_tool("create_journal_entry", {"entry": draft})
    ).structured_content
    assert created is not None
    entry_id = created["id"]
    await mcp_client.call_tool("post_journal_entry", {"entry_id": entry_id})

    with pytest.raises(ToolError) as excinfo:
        await mcp_client.call_tool("post_journal_entry", {"entry_id": entry_id})

    payload = _payload(excinfo)
    assert payload["error"] == "invalid_state"
    assert payload["entry_id"] == entry_id
    assert payload["current_status"] == "posted"
    assert payload["action"] == "post"


@requires_db
async def test_period_out_of_fiscal_year_payload_is_validation_error(
    mcp_client: Client[Any], patched_connect: None, seed: _Seed
) -> None:
    # 取引日 outside any defined fiscal year (FY2026 covers 2026) is a server-side rejection.
    out_of_period = {
        "entry_date": "2099-01-01",
        "lines": [
            {"account_code": "1110", "side": "debit", "amount": "100.00"},
            {"account_code": "4000", "side": "credit", "amount": "100.00"},
        ],
    }
    with pytest.raises(ToolError) as excinfo:
        await mcp_client.call_tool("create_journal_entry", {"entry": out_of_period})

    payload = _payload(excinfo)
    # A period fault is surfaced machine-readably (the exact code is pinned by the service).
    assert payload["error"] in {"validation_error", "not_found"}


@requires_db
async def test_etax_validation_error_is_tool_error_with_details(
    mcp_client: Client[Any], patched_connect: None, migrated_conn: psycopg.Connection[Any]
) -> None:
    # e-Tax 取込 is 整数円: a fractional-yen 売上 makes the 決算書 → e-Tax mapping fail schema
    # validation, and that EtaxValidationError must surface as a ToolError with its problem list.
    accounts = AccountRepository(migrated_conn)
    cash = accounts.insert(
        Account(
            code="1110",
            name="現金",
            account_type=AccountType.ASSET,
            normal_balance=NormalSide.DEBIT,
            statement_category=StatementCategory.CURRENT_ASSETS,
        )
    )
    sales = accounts.insert(
        Account(
            code="4000",
            name="売上高",
            account_type=AccountType.REVENUE,
            normal_balance=NormalSide.CREDIT,
            statement_category=StatementCategory.SALES,
        )
    )
    assert cash.id is not None
    assert sales.id is not None
    migrated_conn.execute(
        "INSERT INTO fiscal_years (name, start_date, end_date) VALUES (%s, %s, %s)",
        ("FY2026", date(2026, 1, 1), date(2026, 12, 31)),
    )
    # A whole-yen book would export cleanly; the .50 sen is what trips e-Tax 整数円 validation.
    JournalRepository(migrated_conn).insert_entry(
        _entry(cash.id, sales.id, "1000.50", date(2026, 4, 1))
    )

    with pytest.raises(ToolError) as excinfo:
        await mcp_client.call_tool("export_etax", {"fiscal_year": "FY2026"})

    payload = _payload(excinfo)
    assert payload["error"] == "etax_validation_error"
    assert isinstance(payload["details"], list)
    assert payload["details"]
