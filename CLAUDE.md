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

個別コマンドは `AGENTS.md#Verification` 参照。

## ローカル開発フロー

```bash
uv sync                 # 依存解決
uv run pre-commit install  # コミット時 hook を有効化 (初回のみ)
./scripts/verify.sh     # 変更後に必ず実行
```

`pre-commit` は ruff (check + format) + 基本 hygiene hooks を実行する。
詳細は [.pre-commit-config.yaml](./.pre-commit-config.yaml) を参照。

## PR 規約 (要点)

- 本文は **日本語**、ブランチ名 / コミット / PR タイトルは英語 (Conventional Commits)
- 動作確認の表は埋めること。未検証は `❌` / `⚠️` で隠さず明示
- 詳細は `AGENTS.md#PR conventions`
