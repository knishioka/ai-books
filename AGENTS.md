# AGENTS.md — ai-books

## Mission

AI-first accounting MCP server. Primary interface is Model Context Protocol (MCP) tools that expose double-entry bookkeeping (chart of accounts, journal entries, trial balance, financial statements). All writes/validation go through MCP. Human UI is read-only: a Vercel-hosted aggregation viewer over Supabase data — no data entry. Goal includes producing 青色申告決算書 + e-Tax 取込データ (tax-amount computation itself stays downstream). アーキテクチャ上の意思決定は ADR に記録する —
索引・運用プロセス (連番/ステータス/起票基準) は [docs/adr/README.md](./docs/adr/README.md)、
最初の決定は [docs/adr/0001-pivot-to-supabase-and-vercel-viewer.md](./docs/adr/0001-pivot-to-supabase-and-vercel-viewer.md)。

本書はこのリポの**開発規約 SSOT**。文書全体の索引と各ドメインの SSOT マップは
[docs/README.md](./docs/README.md) (ドキュメントハブ) を参照。

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
./scripts/test.sh --all    # ← 「ローカルで全部動く」唯一の保証コマンド (#59)
./scripts/test.sh --down   # テスト用コンテナを停止
```

コンテナ定義は [compose.yaml](./compose.yaml)。CI も同等の postgres:17 サービスで
DB 連携テストと web golden を実行する。`AI_BOOKS_DB_URL` を自前で指定した場合は
それを尊重しコンテナは起動しない。

#### `--all` — ローカルで十分動くことの単一・機械的な保証点 (#59)

**`./scripts/test.sh --all` が唯一の『全部動く』確認**。Postgres + pgbouncer を 1 回起動し、
以下の全ブロックを**まとめて 1 回**実行して最後に **PASS/FAIL サマリ**を出す。他モードと違い
途中で止めず全ブロックを走らせ、1 つでも失敗すれば非ゼロ終了する。

| ブロック                                      | 内容                                                                    | 由来                |
| --------------------------------------------- | ----------------------------------------------------------------------- | ------------------- |
| Python full suite + coverage gate (直結 DB)   | DB 連携含む全 pytest (MCP・property・read-only ロール含む) + 閾値ゲート | #50/#56/#57/#54/#58 |
| Web unit layer + coverage gate (vitest)       | DB 不要の web ユニット層 + v8 カバレッジゲート                          | #55/#58             |
| Viewer golden cross-check (直結 DB)           | viewer の数値が report 層 golden と一致                                 | #17/#25             |
| Pooler safety suite + golden (pgbouncer 越し) | pooler 安全性 (`tests/test_pooler_db.py`) + golden を pooler 越しに     | #52                 |

##### CI ↔ local guarantee mapping

`--all` の各ブロックは CI の各ジョブと 1:1 対応する (ローカルが CI と同じ保証範囲を再現する)。

| `./scripts/test.sh --all` ブロック            | 対応 CI ジョブ            |
| --------------------------------------------- | ------------------------- |
| Python full suite + coverage gate             | `verify` (matrix)         |
| Web unit layer + coverage gate                | `web`                     |
| Viewer golden cross-check (直結 DB)           | `web-golden`              |
| Pooler safety suite + golden (pgbouncer 越し) | `pooler`                  |
| (CI 専用: e-Tax .xtx 公式 .xsd 検証, #79)     | `etax-xsd`                |
| (CI 専用: lint/format/typecheck・secret scan) | `pre-commit` / `gitleaks` |

lint / format / typecheck は `./scripts/verify.sh`(= CI `pre-commit` 相当の静的検査)で担保する。
`--all` はランタイム/DB/web の動作保証に集中し、両者を合わせてローカルが CI 全ジョブを網羅する。

`--pooler` は本番の Supabase pooler (pgbouncer, transaction mode) を再現する追加サービス
([compose.yaml](./compose.yaml) の `pgbouncer`) を `db` の前段に立て、migrate / seed の
書込経路・主要レポート・viewer golden をすべて pooler 越しに実行する。pooler は各
トランザクションを別バックエンドに振り分け prepared statement を保持しないため、viewer の
`prepare: false` ([web/lib/db.ts](./web/lib/db.ts)) と prepared-statement を無効化した Python
クライアント ([src/ai_books/db/\_\_init\_\_.py](./src/ai_books/db/__init__.py)) が前提。これを
再有効化する退行は `tests/test_pooler_db.py` が検出し、CI の `pooler` ジョブで毎 PR 検証する。

### カバレッジゲート (#58)

カバレッジは常に**計測**するが、閾値ゲート (line 80 / branch 70) は **DB あり実行でのみ**課す。
DB なしの `verify.sh` は DB 連携テスト (約半数) を skip するためカバレッジを過小報告する — そこで
fail させると誤検知になるので「計測のみ・ゲートなし」とする。判定の有無は `AI_BOOKS_DB_URL` の
有無で自動分岐する。

| 実行                             | DB   | カバレッジ計測   | 閾値ゲート  |
| -------------------------------- | ---- | ---------------- | ----------- |
| `./scripts/verify.sh` (ローカル) | なし | ✅ (report のみ) | ❌ 課さない |
| `./scripts/test.sh`              | あり | ✅               | ✅ 課す     |
| CI `verify` ジョブ               | あり | ✅               | ✅ 課す     |

- Python: `verify.sh` が `pytest --cov=src/ai_books --cov-branch` を実行し、`coverage.xml` /
  `coverage.json` を生成。`AI_BOOKS_DB_URL` がある場合のみ [scripts/check_coverage.py](./scripts/check_coverage.py)
  が line / branch を**個別に**判定する (単一の `--cov-fail-under` は両者を混合した 1 値しか見られない)。
- web: `npm run test:coverage` が `@vitest/coverage-v8` で計測し、[web/vitest.config.ts](./web/vitest.config.ts)
  の `thresholds` (line 80 / branch 70) を割ると fail。CI `web` ジョブでゲート化 (#55 前提)。
- `# pragma: no cover` は到達不能な防御的分岐に限定し濫用しない。
- レポートは CI artifact として残る (`coverage-python-<ver>` / `coverage-web`)。

## PR conventions

PR 本文は **日本語**、ブランチ名 / コミット / PR タイトルは英語。
本文は [.github/PULL_REQUEST_TEMPLATE.md](./.github/PULL_REQUEST_TEMPLATE.md) のテンプレに従う。記述ルールの SSOT は in-repo の [docs/pr-description-standards.md](./docs/pr-description-standards.md)。

## Never touch

- `LICENSE` — 一度確定したら触らない
- `uv.lock` — `uv sync` 経由でのみ更新。手動編集禁止
- `~/.ai-books/` 配下 — 実データ。リポ内には絶対に commit しない (`.gitignore` で `.ai-books/` も除外済)
- 過去 migration: applied 済 migration は編集せず、新規ファイルで forward-only に変更 (Supabase/Postgres)
- Supabase / Vercel の接続情報・サービスキー — 環境変数経由のみ。コード/コミットに含めない

> この「Never touch」は **Claude Code hooks で仕組み化**されている。applied migration /
> `.env` / `*.xsd` / `uv.lock` / `~/.ai-books/` の編集は PreToolUse ガードが自動でブロックし、
> 編集後は PostToolUse が `ruff format` を自動適用する。詳細・回避手順は
> [docs/ai/hooks-and-guardrails.md](./docs/ai/hooks-and-guardrails.md)。

## Agent permissions

- AI エージェント (Claude Code 等) の権限 (allow/deny) は **`.claude/settings.json` が SSOT**。ルーチン安全コマンドは allow、破壊的操作 (`rm -rf` / force push / volumes 削除 / 本番 DB 破壊 / `~/.ai-books` 書込 等) は deny。個人差分は gitignore 済の `.claude/settings.local.json` に置き、共有しない。
- **hooks** も同 `.claude/settings.json` が SSOT。PreToolUse ガード (Never touch の機械化) と PostToolUse 自動整形を登録する。実体は [.claude/hooks/](./.claude/hooks/)、解説は [docs/ai/hooks-and-guardrails.md](./docs/ai/hooks-and-guardrails.md)。

## Architectural invariants

1. **Read-only viewer only, no data-entry UI**. データ入力/編集は MCP tool / CLI のみ。閲覧は **Vercel 上の read-only 集計ビュー**に限り許可 (trial balance / P/L / B/S / 青色申告決算書 等の render のみ)。書込 UI・HTML 入力フォームは導入しない。本番では viewer を SELECT 専用ロール (`viewer_ro`, [supabase/roles/viewer_readonly.sql](./supabase/roles/viewer_readonly.sql)) に向け、書込不能を DB レベルで強制する。read 全成功 / write 全拒否 (将来テーブル含む) は `tests/test_readonly_db.py`、grant 集合のドリフトは `tests/test_readonly_role.py` が検出する (`./scripts/test.sh -k readonly`)
2. **Server-side validation absolute**: 借方貸方バランス、Decimal 精度、account FK 検証は MCP tool 入口の Pydantic schema で実施。クライアント信頼ゼロ (read-only ビュー追加後も書込経路は MCP のみ)
3. **Supabase (Postgres) storage, forward-only migration**: システムオブレコードは Supabase (Postgres)。applied 済 migration は編集せず新規ファイルで前進のみ。multi-tenant / RLS / 水平スケールは持たない (single-user 前提は不変)。全 migration 適用後の確定スキーマは `tests/fixtures/schema/schema.sql` を golden としてドリフト検出する (`tests/test_schema_snapshot.py`)。意図的な DDL 変更時のみ `uv run python -m ai_books.db.schema_snapshot --update` で golden を更新する。この forward-only 規律 (過去の決定は書き換えず前進で覆す) は ADR にも適用される ([docs/adr/README.md](./docs/adr/README.md): 確定 ADR は編集せず新番号で Supersede)。なお **認証の追加はマルチテナント化ではない (認証 ≠ マルチテナント)**: MCP を Web リモート公開する場合は **single-user allowlist + 認証必須** (Supabase Auth / OAuth-JWT, fail-closed) とし、single-user 前提は不変のまま保つ ([ADR 0008](./docs/adr/0008-remote-mcp-single-tenant-auth.md))
4. **No ORM until justified**: 生 SQL + Postgres ドライバ (例: `psycopg`)。Drizzle / SQLAlchemy 等は別 Issue で必要性を立証してから
5. **Audit log は append-only**: `audit_logs` テーブルから既存行を削除/上書きするコードを書かない

## Quality bar

- テストカバレッジ目標: line 80% / branch 70% (Issue #1 以降の機能コードに対して)。
  この目標は **CI で機械的にゲート化**される (#58)。詳細は下記「カバレッジゲート」。
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

## Review guidelines

PR コードレビューの観点 SSOT。**Claude Code (`/code-review`) と Codex (`@codex review` / 自動レビュー)
の双方がこのセクションに従う** ([Codex は変更ファイルに最も近い AGENTS.md の `## Review guidelines`
を参照する](https://developers.openai.com/codex/integrations/github))。下記は本リポの不変条件
(上記 Architectural invariants / Never touch) をレビュー時のチェックリストに落としたもの。
多くは Claude Code hooks ([docs/ai/hooks-and-guardrails.md](./docs/ai/hooks-and-guardrails.md)) で
編集時にも機械的にブロックされるが、レビューは**最終確認**として同じ観点で見る。

優先度は P0 (マージブロッカー) / P1 (要修正) で示す。

- **P0 — forward-only migration**: `supabase/migrations/` の既存 (適用済) ファイルを編集していないか。
  スキーマ変更は新規ファイルで前進し、`tests/fixtures/schema/schema.sql` golden を更新したか (invariant #3)。
- **P0 — 秘匿物の非コミット**: `.env` / 接続情報 / サービスキー / `~/.ai-books/` 実データ / 公式 `.xsd` を
  コミットに含めていないか。設定は環境変数経由か (Never touch / Secret scanning)。
- **P0 — server-side validation**: 書込/検証は MCP tool 入口の Pydantic schema を通るか。借方貸方バランス・
  Decimal 精度・account FK 検証をクライアント信頼で省いていないか (invariant #2)。
- **P0 — read-only viewer**: web 側に書込/データ入力経路 (HTML フォーム・mutation) を追加していないか。
  閲覧は read-only 集計ビューに限るか (invariant #1)。
- **P1 — audit log append-only**: `audit_logs` の既存行を削除/上書きするコードを足していないか (invariant #5)。
- **P1 — no ORM / 生 SQL**: 正当な理由なく ORM (Drizzle / SQLAlchemy 等) を導入していないか (invariant #4)。
- **P1 — 検証の通過**: `./scripts/verify.sh` (lint/format/typecheck/test) が green か。DB 連携を含む変更は
  `./scripts/test.sh --all` で確認したか。カバレッジ目標 (line 80 / branch 70) を割っていないか。
- **P1 — uv.lock の整合**: 依存変更が `uv sync` 経由で、`uv.lock` の手動編集でないか。
