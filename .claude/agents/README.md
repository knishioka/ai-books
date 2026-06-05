# Project subagents (`.claude/agents/`)

ai-books 固有の **サブエージェント** = 本リポで繰り返す開発作業を型化したもの。独立 context で
動き、AGENTS.md の不変条件 / Never touch を前提に作業する。設計方針の出所は
[#91 ベストプラクティス調査](../../docs/ai/best-practices-survey.md) §6、導入は #92。

> **規約・検証・不変条件・触ってはいけない領域の SSOT は [AGENTS.md](../../AGENTS.md)**。
> 各エージェントは内容を重複させず AGENTS.md / #89「How to add」へ誘導する。

| サブエージェント                          | 何を型化するか                                              | 薄いラッパ (コマンド)                            |
| ----------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------ |
| [migration-author](./migration-author.md) | forward-only migration + 適用/冪等テスト + schema golden    | [`/new-migration`](../commands/new-migration.md) |
| [mcp-tool-author](./mcp-tool-author.md)   | `@mcp.tool` + 入口 Pydantic 検証 + protocol/contract テスト | [`/new-mcp-tool`](../commands/new-mcp-tool.md)   |
| [report-author](./report-author.md)       | 集計/レポート + seed_fy golden + DB↔Web golden 整合         | [`/new-report`](../commands/new-report.md)       |
| [etax-spec-author](./etax-spec-author.md) | e-Tax 様式追補 (KOA220/240 等) + .xsd 検証                  | [`/etax-validate`](../commands/etax-validate.md) |

## 使い方

- 自然言語で起動 (例: 「新しい migration を追加して」) するか、対応するスラッシュコマンドを使う。
  コマンドは入口の薄いラッパで、実体は上記サブエージェントに委譲する。
- 検証は常に [`./scripts/verify.sh`](../../scripts/verify.sh) / [`./scripts/test.sh`](../../scripts/test.sh)
  に統一 (各エージェントの末尾に対象コマンドを明記)。

## 設計原則 (YAGNI)

実際に**頻出する 4 作業のみ**を対象にし、投機的な scaffold は作らない。重いスクリプト/参照を
伴う scaffold が必要になったら `.claude/skills/<name>/SKILL.md` への昇格を検討する
(調査 §3.5)。コマンド (入口) とサブエージェント (独立 context の委譲) の役割を取り違えない。
