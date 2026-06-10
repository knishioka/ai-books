#!/usr/bin/env python3
"""Lint Claude Code settings.json permission rules.

Claude Code silently skips invalid permission rules at startup and shows an
interactive "Settings Warning" prompt. In unattended sessions (worktree waves)
that prompt blocks the agent before it starts, and a skipped *deny* rule means
the guardrail it encoded is simply gone. This caught a real incident: the rule
"Bash(git push:* --force)" was skipped (the `:*` pattern must be at the end),
which both stalled a 4-pane wave and silently disabled a force-push guard.

Checks per rule in permissions.allow / permissions.deny / permissions.ask:
  - file parses as JSON
  - rule is `Tool` or `Tool(specifier)`
  - `:*` appears only as the specifier suffix (use bare `*` mid-pattern)
  - specifier is non-empty (`Tool()` is invalid)
  - duplicate rules within a list (warning only, does not fail)

Usage:
    python scripts/lint_claude_settings.py .claude/settings.json [...]

Wired into pre-commit (files: ^\\.claude/settings.*\\.json$), so it also runs
in CI via the pre-commit job.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RULE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(?:\((?P<spec>.*)\))?$")


def lint_file(path: Path) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for one settings file."""
    errors: list[str] = []
    warnings: list[str] = []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{path}: not valid JSON ({exc})"], []

    permissions = data.get("permissions", {})
    for list_name in ("allow", "deny", "ask"):
        rules = permissions.get(list_name, [])
        seen: set[str] = set()
        for rule in rules:
            loc = f"{path} ({list_name})"
            if rule in seen:
                warnings.append(f'{loc}: duplicate rule "{rule}"')
            seen.add(rule)

            match = RULE_RE.match(rule)
            if match is None:
                errors.append(f'{loc}: "{rule}" is not `Tool` or `Tool(specifier)`')
                continue

            spec = match.group("spec")
            if spec is None:
                continue  # bare tool name
            if spec == "":
                errors.append(f'{loc}: "{rule}" has an empty specifier — use a bare tool name')
                continue

            core = spec.removesuffix(":*")
            if ":*" in core:
                errors.append(
                    f'{loc}: "{rule}" — the :* pattern must be at the end '
                    "(use * for mid-pattern wildcards)"
                )

    return errors, warnings


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: lint_claude_settings.py <settings.json> [...]", file=sys.stderr)
        return 2

    total_errors = 0
    for arg in argv:
        errors, warnings = lint_file(Path(arg))
        for message in errors:
            print(f"ERROR {message}")
        for message in warnings:
            print(f"WARN  {message}")
        total_errors += len(errors)

    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
