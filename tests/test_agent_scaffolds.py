"""Smoke tests for the project subagents + slash-command scaffolds (Issue #92).

These guard the `.claude/agents/` and `.claude/commands/` scaffolds the way the rest of
the suite guards the code: a malformed frontmatter, a renamed/removed scaffold, or a
scaffold that forgets to point at its verification command fails here rather than silently
shipping a broken developer-experience surface. Pure (no DB) — runs under
``./scripts/verify.sh``.

The adoption decision and the exact set of scaffolds come from the #91 best-practices
survey (``docs/ai/best-practices-survey.md`` §6); the invariants every scaffold must defer
to live in ``AGENTS.md``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AGENTS_DIR = _REPO_ROOT / ".claude" / "agents"
_COMMANDS_DIR = _REPO_ROOT / ".claude" / "commands"

#: The subagents #92 adopts (YAGNI: only the four recurring tasks).
EXPECTED_AGENTS = {
    "migration-author",
    "mcp-tool-author",
    "report-author",
    "etax-spec-author",
}
#: The scaffold commands #92 adds (the thin entrypoints to the agents above).
EXPECTED_SCAFFOLD_COMMANDS = {
    "new-migration",
    "new-mcp-tool",
    "new-report",
    "etax-validate",
}


def _parse_frontmatter(path: Path) -> tuple[dict[str, object], str]:
    """Split a Markdown file into its YAML frontmatter mapping and the body."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.name}: missing YAML frontmatter"
    _, fm, body = text.split("---\n", 2)
    data = yaml.safe_load(fm)
    assert isinstance(data, dict), f"{path.name}: frontmatter is not a mapping"
    return data, body


def _assert_nonempty_str(data: dict[str, object], key: str, name: str) -> None:
    value = data.get(key)
    assert isinstance(value, str), f"{name}: {key} must be a string"
    assert value.strip(), f"{name}: non-empty {key} required"


def _agent_files() -> list[Path]:
    return sorted(p for p in _AGENTS_DIR.glob("*.md") if p.stem != "README")


def _command_files() -> list[Path]:
    return sorted(_COMMANDS_DIR.glob("*.md"))


def test_expected_agents_present() -> None:
    assert {p.stem for p in _agent_files()} == EXPECTED_AGENTS


def test_expected_scaffold_commands_present() -> None:
    present = {p.stem for p in _command_files()}
    assert present >= EXPECTED_SCAFFOLD_COMMANDS


@pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.stem)
def test_agent_frontmatter_and_grounding(path: Path) -> None:
    """Each subagent: parseable frontmatter, name == filename, and it grounds itself in
    AGENTS.md + a concrete verification command (生成物・必須テスト・検証手順)."""
    data, body = _parse_frontmatter(path)
    assert data.get("name") == path.stem, f"{path.name}: frontmatter name must match filename"
    _assert_nonempty_str(data, "description", path.name)
    # Defers to the SSOT rather than duplicating invariants, and names a verify entrypoint.
    assert "AGENTS.md" in body, f"{path.name}: must point at the AGENTS.md SSOT"
    assert "verify.sh" in body or "pytest" in body, (
        f"{path.name}: must document a verification command"
    )


@pytest.mark.parametrize("path", _command_files(), ids=lambda p: p.stem)
def test_command_frontmatter(path: Path) -> None:
    """Every slash command (existing + scaffold) has a non-empty description."""
    data, _ = _parse_frontmatter(path)
    _assert_nonempty_str(data, "description", path.name)


@pytest.mark.parametrize(
    ("command", "agent"),
    [
        ("new-migration", "migration-author"),
        ("new-mcp-tool", "mcp-tool-author"),
        ("new-report", "report-author"),
    ],
    ids=lambda v: v,
)
def test_scaffold_command_delegates_to_agent(command: str, agent: str) -> None:
    """The generative scaffolds are thin wrappers that delegate to their subagent."""
    _, body = _parse_frontmatter(_COMMANDS_DIR / f"{command}.md")
    assert agent in body, f"{command}.md should delegate to the {agent} subagent"
