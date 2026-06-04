# CLAUDE.md — ai-books

このリポでの開発ルール / 検証手順 / 触ってはいけない領域は **[AGENTS.md](./AGENTS.md) が SSOT**。
Claude Code / Codex / その他 AI エージェントはすべて `AGENTS.md` の規約に従うこと。

## 最初に読むもの

1. [AGENTS.md](./AGENTS.md) — Mission / Stack / Verification / Never touch / Architectural invariants
2. [README.md](./README.md) — プロダクトの位置付けと Roadmap
3. [.github/PULL_REQUEST_TEMPLATE.md](./.github/PULL_REQUEST_TEMPLATE.md) — PR 本文の型

## 検証の唯一のエントリポイント

```bash
./scripts/verify.sh           # lint / format / typecheck / test を順に
./scripts/verify.sh --json    # CI / Codex 用の構造化出力
```

個別コマンドは [AGENTS.md#verification](./AGENTS.md#verification) 参照。

`verify.sh` は DB 連携テストを skip する (`AI_BOOKS_DB_URL` 未設定時)。**DB 連携も含めた
全テスト**は軽量 Postgres コンテナ 1 個で実行する (フルの `supabase start` は不要):

```bash
./scripts/test.sh          # postgres:17-alpine で全テスト (DB 連携含む)
./scripts/test.sh --web    # + Vercel viewer の golden 数値一致チェック
./scripts/test.sh --down   # テスト用コンテナ停止
```

## ローカル開発フロー

```bash
uv sync                 # 依存解決
uv run pre-commit install  # コミット時 hook を有効化 (初回のみ)
supabase start          # ローカル Postgres + Studio (Docker)。要 Supabase CLI
cp .env.example .env    # AI_BOOKS_DB_URL を supabase start の出力値に設定
./scripts/verify.sh     # 変更後に必ず実行
```

ローカル Postgres のセットアップ手順 (接続情報の `.env` 転記、`SELECT 1` スモーク
テスト) は [README.md](./README.md#local-postgres-supabase) を参照。`AI_BOOKS_DB_URL`
未設定でも `verify.sh` は green (DB スモークテストは skip される)。

`pre-commit` は ruff (check + format) + 基本 hygiene hooks を実行する。
詳細は [.pre-commit-config.yaml](./.pre-commit-config.yaml) を参照。

## PR 規約 (要点)

- 本文は **日本語**、ブランチ名 / コミット / PR タイトルは英語 (Conventional Commits)
- 動作確認の表は埋めること。未検証は `❌` / `⚠️` で隠さず明示
- 詳細は [AGENTS.md#pr-conventions](./AGENTS.md#pr-conventions)
