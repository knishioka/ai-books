# GitHub Copilot Instructions — ai-books

このリポの開発ルール / 検証手順 / 触ってはいけない領域は **[AGENTS.md](../AGENTS.md) が SSOT**。
GitHub Copilot を含むすべての AI エージェントは `AGENTS.md` の規約に従うこと。

- 検証は `./scripts/verify.sh` が唯一のエントリポイント (lint / format / typecheck / test)。
- PR 本文の書き方は [docs/pr-description-standards.md](../docs/pr-description-standards.md)。
- 権限 (allow/deny) は `.claude/settings.json` が SSOT。

このファイルは薄いポインタであり、内容は重複させない。常に AGENTS.md を正とする。
