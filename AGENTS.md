# AGENTS.md — ai-books

## Mission

AI-first accounting MCP server. Primary interface is Model Context Protocol (MCP) tools that expose double-entry bookkeeping (chart of accounts, journal entries, trial balance, financial statements). Human UI is intentionally minimal — at most a static aggregation dashboard.

## Stack

- 言語: Python 3.12+
- フレームワーク: FastMCP (Model Context Protocol server)
- パッケージマネージャ: uv
- 主要ツール: ruff (lint + format), mypy (typecheck, strict), pytest (test)
- ランタイム/インフラ: stdio MCP server, SQLite local file (default `~/.ai-books/db.sqlite`, override via `AI_BOOKS_DB_PATH`)

## Verification

このリポの検証エントリポイントは `./scripts/verify.sh` に統一する。

```bash
./scripts/verify.sh           # 人間可読 (text)
./scripts/verify.sh --json    # 構造化結果 (CI / Codex 用)
```

- 内部で lint / format / typecheck / test を順に実行する (build は library なので `n/a`)。
- 失敗時の exit code: `0` 全 pass / `1` 1 つ以上 fail / `2` 環境エラー。
- 個別実行: `uv run ruff check .` / `uv run ruff format --check .` / `uv run mypy src tests` / `uv run pytest -q`

## PR conventions

PR 本文は **日本語**、ブランチ名 / コミット / PR タイトルは英語。
本文は `.github/PULL_REQUEST_TEMPLATE.md` のテンプレに従う (workspace `docs/codex-playbook.md` "PR Description Standards" が SSOT)。

## Never touch

- `LICENSE` — 一度確定したら触らない
- `uv.lock` — `uv sync` 経由でのみ更新。手動編集禁止
- `~/.ai-books/` 配下 — 実データ。リポ内には絶対に commit しない (`.gitignore` で `.ai-books/` も除外済)
- 過去 migration: `src/ai_books/db/migrations/` の applied 済 SQL は編集せず、新規ファイルで forward-only に変更

## Architectural invariants

1. **No human-facing web UI**. インターフェースは MCP tool / CLI / 生成された static report のみ。Flask/FastAPI による HTML 配信を導入しない
2. **Server-side validation absolute**: 借方貸方バランス、Decimal 精度、account FK 検証は MCP tool 入口の Pydantic schema で実施。クライアント信頼ゼロ
3. **SQLite single-file storage**: PostgreSQL / multi-tenant / RLS は持たない。スケール要件はそもそも対象外
4. **No ORM until justified**: 生 SQL + `sqlite3` モジュール。Drizzle / SQLAlchemy 等は別 Issue で必要性を立証してから
5. **Audit log は append-only**: `audit_logs` テーブルから既存行を削除/上書きするコードを書かない

## Quality bar

- テストカバレッジ目標: line 80% / branch 70% (Issue #1 以降の機能コードに対して)
- 必須チェック: lint / format / typecheck / test がすべて pass
- セキュリティ: 秘密情報をコード/コミットに含めない。設定は環境変数経由のみ
- README は変更時に Roadmap セクションの整合性を維持
