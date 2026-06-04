# AGENTS.md — ai-books

## Mission

AI-first accounting MCP server. Primary interface is Model Context Protocol (MCP) tools that expose double-entry bookkeeping (chart of accounts, journal entries, trial balance, financial statements). All writes/validation go through MCP. Human UI is read-only: a Vercel-hosted aggregation viewer over Supabase data — no data entry. Goal includes producing 青色申告決算書 + e-Tax 取込データ (tax-amount computation itself stays downstream). See [docs/adr/0001-pivot-to-supabase-and-vercel-viewer.md](./docs/adr/0001-pivot-to-supabase-and-vercel-viewer.md).

## Stack

- 言語: Python 3.12+
- フレームワーク: FastMCP (Model Context Protocol server)
- パッケージマネージャ: uv
- 主要ツール: ruff (lint + format), mypy (typecheck, strict), pytest (test)
- ランタイム/インフラ: stdio MCP server (書込/検証), Supabase (Postgres) storage, Vercel 上の read-only 集計ビュー (閲覧のみ)

## Verification

このリポの検証エントリポイントは `./scripts/verify.sh` に統一する。

```bash
./scripts/verify.sh           # 人間可読 (text)
./scripts/verify.sh --json    # 構造化結果 (CI / Codex 用)
```

- 内部で lint / format / typecheck / test を順に実行する (build は library なので `n/a`)。
- 失敗時の exit code: `0` 全 pass / `1` 1 つ以上 fail / `2` 環境エラー。
- 個別実行: `uv run ruff check .` / `uv run ruff format --check .` / `uv run mypy src tests` / `uv run pytest -q`

### DB 連携テスト (Postgres 必須)

`AI_BOOKS_DB_URL` 未設定だと DB 連携テスト (約半数) は **skip** される (`verify.sh` は
それでも green)。**全テストをローカルで実行**するには Postgres が要る。フルの
`supabase start` は不要 — テストは Postgres だけで足り、`conftest.py` がテストごとに
使い捨てスキーマを作るため、軽量な単一コンテナ 1 個を使い回せる。

```bash
./scripts/test.sh          # postgres:17-alpine を起動し全テスト実行 (DB 連携含む)
./scripts/test.sh --web    # + Vercel viewer の golden 数値一致クロスチェック
./scripts/test.sh --pooler # pgbouncer(transaction mode)経由で pooler 安全性を検証 (#52)
./scripts/test.sh --down   # テスト用コンテナを停止
```

コンテナ定義は [compose.yaml](./compose.yaml)。CI も同等の postgres:17 サービスで
DB 連携テストと web golden を実行する。`AI_BOOKS_DB_URL` を自前で指定した場合は
それを尊重しコンテナは起動しない。

`--pooler` は本番の Supabase pooler (pgbouncer, transaction mode) を再現する追加サービス
([compose.yaml](./compose.yaml) の `pgbouncer`) を `db` の前段に立て、migrate / seed の
書込経路・主要レポート・viewer golden をすべて pooler 越しに実行する。pooler は各
トランザクションを別バックエンドに振り分け prepared statement を保持しないため、viewer の
`prepare: false` ([web/lib/db.ts](./web/lib/db.ts)) と prepared-statement を無効化した Python
クライアント ([src/ai_books/db/\_\_init\_\_.py](./src/ai_books/db/__init__.py)) が前提。これを
再有効化する退行は `tests/test_pooler_db.py` が検出し、CI の `pooler` ジョブで毎 PR 検証する。

## PR conventions

PR 本文は **日本語**、ブランチ名 / コミット / PR タイトルは英語。
本文は [.github/PULL_REQUEST_TEMPLATE.md](./.github/PULL_REQUEST_TEMPLATE.md) のテンプレに従う。記述ルールの SSOT は in-repo の [docs/pr-description-standards.md](./docs/pr-description-standards.md)。

## Never touch

- `LICENSE` — 一度確定したら触らない
- `uv.lock` — `uv sync` 経由でのみ更新。手動編集禁止
- `~/.ai-books/` 配下 — 実データ。リポ内には絶対に commit しない (`.gitignore` で `.ai-books/` も除外済)
- 過去 migration: applied 済 migration は編集せず、新規ファイルで forward-only に変更 (Supabase/Postgres)
- Supabase / Vercel の接続情報・サービスキー — 環境変数経由のみ。コード/コミットに含めない

## Agent permissions

- AI エージェント (Claude Code 等) の権限 (allow/deny) は **`.claude/settings.json` が SSOT**。ルーチン安全コマンドは allow、破壊的操作 (`rm -rf` / force push / volumes 削除 / 本番 DB 破壊 / `~/.ai-books` 書込 等) は deny。個人差分は gitignore 済の `.claude/settings.local.json` に置き、共有しない。

## Architectural invariants

1. **Read-only viewer only, no data-entry UI**. データ入力/編集は MCP tool / CLI のみ。閲覧は **Vercel 上の read-only 集計ビュー**に限り許可 (trial balance / P/L / B/S / 青色申告決算書 等の render のみ)。書込 UI・HTML 入力フォームは導入しない
2. **Server-side validation absolute**: 借方貸方バランス、Decimal 精度、account FK 検証は MCP tool 入口の Pydantic schema で実施。クライアント信頼ゼロ (read-only ビュー追加後も書込経路は MCP のみ)
3. **Supabase (Postgres) storage, forward-only migration**: システムオブレコードは Supabase (Postgres)。applied 済 migration は編集せず新規ファイルで前進のみ。multi-tenant / RLS / 水平スケールは持たない (single-user 前提は不変)
4. **No ORM until justified**: 生 SQL + Postgres ドライバ (例: `psycopg`)。Drizzle / SQLAlchemy 等は別 Issue で必要性を立証してから
5. **Audit log は append-only**: `audit_logs` テーブルから既存行を削除/上書きするコードを書かない

## Quality bar

- テストカバレッジ目標: line 80% / branch 70% (Issue #1 以降の機能コードに対して)
- 必須チェック: lint / format / typecheck / test がすべて pass
- セキュリティ: 秘密情報をコード/コミットに含めない。設定は環境変数経由のみ
- README は変更時に Roadmap セクションの整合性を維持

## Secret scanning

トークン / API キーの混入は **gitleaks** で 2 段ガード。

- ローカル: `.pre-commit-config.yaml` の `gitleaks` hook がコミット時に staged 変更を scan
- CI: `.github/workflows/ci.yml` の `gitleaks` job が PR ごとに **git 履歴全体** を scan
- ルール / allowlist: [.gitleaks.toml](./.gitleaks.toml) (upstream defaults + Anthropic / OpenAI / GitHub PAT / freee / MoneyForward / Plaid / `AI_BOOKS_*` の custom rules)
- 期待される env var の一覧は [.env.example](./.env.example) を参照。実値は `.env` (gitignore 済) に置く

手元で全部 scan:

```bash
uv run pre-commit run gitleaks --all-files
```

誤検知が出たら `.gitleaks.toml` の `[allowlist]` に該当パス / regex を追加し、PR でレビューする。**個別行の `# gitleaks:allow` は使わない** (差分レビューで埋もれるため)。
