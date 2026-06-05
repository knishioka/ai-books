#!/usr/bin/env python3
"""PreToolUse guard — systematizes AGENTS.md "Never touch" as a hook.

Claude Code invokes this before every Edit / Write / MultiEdit. It reads the
tool call as JSON on stdin and blocks (exit code 2) edits that would violate a
repo invariant, printing the reason + escape hatch to stderr.

Guarded targets (see AGENTS.md#never-touch / #architectural-invariants):

* ``supabase/migrations/`` — applied migrations are immutable; change forward-only
  by adding a NEW file (invariant #3). Editing/overwriting an existing migration
  is blocked; creating a new one is allowed.
* ``.env`` / ``.env.*`` — local secrets (``.env.example`` is allowed).
* ``*.xsd`` — official 国税庁 schemas (著作物 / fetched, never committed or hand-edited).
* ``uv.lock`` — update only via ``uv sync``.
* ``~/.ai-books/`` and repo-local ``.ai-books/`` — real production data, never touched.

Escape hatch (documented in docs/ai/hooks-and-guardrails.md): for a genuine
false positive, re-run the single edit with ``AI_BOOKS_ALLOW_GUARDED_EDIT=1``
set in the environment. The bypass is logged to stderr so it stays visible.

Only stdlib is used so the hook runs without ``uv sync`` and stays fast.
"""

from __future__ import annotations

import json
import os
import sys

# Exit codes per Claude Code hooks contract: 0 = allow, 2 = block (stderr shown
# to the model). Any other unexpected failure falls through to 0 (fail-open) so a
# guard bug never wedges the editing loop — the deny permissions in
# .claude/settings.json remain the hard backstop.
ALLOW = 0
BLOCK = 2


def _project_root() -> str:
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return os.path.realpath(root)


def _norm(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def _deny(reason: str, fix: str) -> None:
    sys.stderr.write(f"BLOCKED (guard-never-touch): {reason}\n{fix}\n")
    sys.exit(BLOCK)


def _check(file_path: str, tool_name: str, root: str) -> None:
    abs_path = _norm(file_path)
    base = os.path.basename(abs_path)

    # ~/.ai-books/ or repo-local .ai-books/ — real data, never in the repo.
    home_data = _norm("~/.ai-books")
    repo_data = os.path.join(root, ".ai-books")
    data_roots = (home_data, repo_data)
    if abs_path in data_roots or any(abs_path.startswith(d + os.sep) for d in data_roots):
        _deny(
            f"{file_path} は実データ (~/.ai-books / .ai-books) — リポでは絶対に編集/コミットしない。",
            "MCP tool / CLI 経由で扱うこと。",
        )

    # uv.lock — only `uv sync` may change it.
    if base == "uv.lock":
        _deny(
            f"{file_path} は手動編集禁止 (Never touch)。",
            "依存変更は pyproject.toml を編集し `uv sync` で再生成すること。",
        )

    # .env / .env.* (but .env.example is the committed template).
    if base == ".env" or (base.startswith(".env.") and base != ".env.example"):
        _deny(
            f"{file_path} は秘匿設定 (gitignore 済) — 編集/追加をブロック。",
            "期待される変数は .env.example を参照。実値は手動で .env に置くこと。",
        )

    # *.xsd — official schemas, fetched not authored.
    if base.endswith(".xsd"):
        _deny(
            f"{file_path} は公式 .xsd (国税庁 著作物 / fetch 対象) — 手編集しない。",
            "様式更新は scripts/etax/ の fetch 経路を使うこと。",
        )

    # supabase/migrations/ — forward-only; existing files are immutable.
    migrations_dir = os.path.join(root, "supabase", "migrations") + os.sep
    if abs_path.startswith(migrations_dir):
        exists = os.path.exists(abs_path)
        editing = tool_name in ("Edit", "MultiEdit") or (tool_name == "Write" and exists)
        if editing:
            _deny(
                f"{file_path} は適用済み migration の可能性 — forward-only (invariant #3) により"
                " 既存 migration の編集は禁止。",
                "新しい migration ファイルを追加して前進すること"
                " (DDL 変更時は `uv run python -m ai_books.db.schema_snapshot --update` で golden 更新)。",
            )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return ALLOW  # fail-open on malformed input

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    if not file_path:
        return ALLOW

    if os.environ.get("AI_BOOKS_ALLOW_GUARDED_EDIT") == "1":
        sys.stderr.write(
            f"guard-never-touch: bypass (AI_BOOKS_ALLOW_GUARDED_EDIT=1) for {file_path}\n"
        )
        return ALLOW

    _check(file_path, tool_name, _project_root())
    return ALLOW


if __name__ == "__main__":
    sys.exit(main())
