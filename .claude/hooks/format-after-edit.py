#!/usr/bin/env python3
"""PostToolUse formatter — auto-applies the repo formatter after each edit.

Claude Code invokes this after every Edit / Write / MultiEdit. It reformats the
just-edited file so the working tree matches what pre-commit / CI would enforce,
eliminating format-only churn and pre-commit retry loops.

* ``*.py``  → ``ruff format <file>`` (prefers ``uv run`` to match the pinned ruff;
  falls back to a ``ruff`` on PATH).
* ``web/**`` ``*.ts|*.tsx|*.js|*.jsx|*.mjs|*.cjs`` → ``eslint --fix <file>`` (only
  when ``web/node_modules`` exists, so we never pay an ``npx`` download cost).

Formatting is advisory: this hook always exits 0. A formatter that is missing,
errors, or finds an unfixable lint issue must not block the edit (lint/format is
still enforced by ``./scripts/verify.sh`` and CI). Only stdlib is used.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys

PY_EXT = (".py",)
WEB_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def _project_root() -> str:
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return os.path.realpath(root)


def _run(cmd: list[str], cwd: str) -> None:
    # advisory only — never block the edit on a formatter failure.
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )


def _format_python(abs_path: str, root: str) -> None:
    if shutil.which("uv"):
        _run(["uv", "run", "--quiet", "ruff", "format", abs_path], cwd=root)
    elif shutil.which("ruff"):
        _run(["ruff", "format", abs_path], cwd=root)


def _format_web(abs_path: str, root: str) -> None:
    web_dir = os.path.join(root, "web")
    local_eslint = os.path.join(web_dir, "node_modules", ".bin", "eslint")
    if not os.path.exists(local_eslint):
        return  # deps not installed — skip silently to stay fast
    _run([local_eslint, "--fix", abs_path], cwd=web_dir)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    if not file_path:
        return 0

    root = _project_root()
    abs_path = os.path.realpath(os.path.expanduser(file_path))
    if not os.path.isfile(abs_path):
        return 0

    _, ext = os.path.splitext(abs_path)
    if ext in PY_EXT:
        _format_python(abs_path, root)
    elif ext in WEB_EXT and (abs_path + os.sep).startswith(os.path.join(root, "web") + os.sep):
        _format_web(abs_path, root)

    return 0


if __name__ == "__main__":
    sys.exit(main())
