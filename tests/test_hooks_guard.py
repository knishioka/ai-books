"""Tests for the Claude Code "Never touch" guard hook — Issue #93.

The PreToolUse guard (``.claude/hooks/guard-never-touch.py``) systematizes the
AGENTS.md "Never touch" / forward-only-migration invariants. These tests run it
as a subprocess (exactly as Claude Code does) against a hermetic temp project
root, asserting it blocks (exit 2) guarded edits and allows (exit 0) legitimate
ones — so a regression that weakens a guard is caught offline, without DB.

The companion PostToolUse formatter is advisory (always exit 0) and is exercised
by ``./scripts/verify.sh`` rather than asserted here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD = REPO_ROOT / ".claude" / "hooks" / "guard-never-touch.py"

BLOCK = 2
ALLOW = 0


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A fake project root with one pre-existing (=applied) migration file."""
    migrations = tmp_path / "supabase" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "20260101000001_init.sql").write_text("-- applied\n")
    (tmp_path / "src").mkdir()
    return tmp_path


def _run_guard(
    project_root: Path, tool_name: str, file_path: str, *, bypass: bool = False
) -> subprocess.CompletedProcess[str]:
    env = {
        "CLAUDE_PROJECT_DIR": str(project_root),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    }
    if bypass:
        env["AI_BOOKS_ALLOW_GUARDED_EDIT"] = "1"
    payload = {"tool_name": tool_name, "tool_input": {"file_path": file_path}}
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.parametrize(
    ("tool", "rel"),
    [
        ("Edit", "supabase/migrations/20260101000001_init.sql"),  # applied migration
        ("MultiEdit", "supabase/migrations/20260101000001_init.sql"),
        ("Write", ".env"),
        ("Write", ".env.local"),
        ("Edit", "uv.lock"),
        ("Write", "schemas/koa210.xsd"),
        ("Write", ".ai-books/books.db"),  # repo-local real data
    ],
)
def test_guarded_targets_are_blocked(project: Path, tool: str, rel: str) -> None:
    result = _run_guard(project, tool, str(project / rel))
    assert result.returncode == BLOCK, result.stderr
    assert "BLOCKED" in result.stderr


@pytest.mark.parametrize(
    ("tool", "rel"),
    [
        ("Write", "supabase/migrations/20260201000002_new.sql"),  # NEW migration
        ("Write", ".env.example"),  # committed template
        ("Edit", "src/server.py"),  # ordinary source
        ("Write", "docs/guide.md"),
    ],
)
def test_legitimate_edits_are_allowed(project: Path, tool: str, rel: str) -> None:
    result = _run_guard(project, tool, str(project / rel))
    assert result.returncode == ALLOW, result.stderr


def test_overwriting_existing_migration_via_write_is_blocked(project: Path) -> None:
    existing = project / "supabase" / "migrations" / "20260101000001_init.sql"
    result = _run_guard(project, "Write", str(existing))
    assert result.returncode == BLOCK, result.stderr


def test_home_ai_books_data_is_blocked(project: Path) -> None:
    result = _run_guard(project, "Write", "~/.ai-books/books.db")
    assert result.returncode == BLOCK, result.stderr


def test_bypass_env_allows_guarded_edit(project: Path) -> None:
    result = _run_guard(project, "Edit", str(project / "uv.lock"), bypass=True)
    assert result.returncode == ALLOW, result.stderr
    assert "bypass" in result.stderr


def test_malformed_input_fails_open(project: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        input="not json",
        capture_output=True,
        text=True,
        env={"CLAUDE_PROJECT_DIR": str(project), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == ALLOW


def test_missing_file_path_is_allowed(project: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps({"tool_name": "Edit", "tool_input": {}}),
        capture_output=True,
        text=True,
        env={"CLAUDE_PROJECT_DIR": str(project), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == ALLOW
