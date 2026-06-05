# ai-books ドキュメントハブ

> このリポジトリの**全ドキュメントの索引**と、**何が一次情報 (SSOT) か**のマップ。
> 「どこに何が書いてあるか」「正はどれか」をここで一本化する。

`ai-books` は **AI-first な会計 MCP サーバー** (MCP = 書込/検証インターフェース ·
Supabase/Postgres = 保管 · Vercel = read-only ビュー)。文書は **人間向け** と
**AI/開発者向け** に分かれる。入口を間違えないよう、まず下の 2 つの表から辿ること。

---

## 1. 人間向け (製品で何ができるか・どう使うか)

製品の位置付け・セットアップ・画面で「何ができるか」を知りたいときの入口。

| ドキュメント                                                                | 内容                                                       | 種別            |
| --------------------------------------------------------------------------- | ---------------------------------------------------------- | --------------- |
| [README.md](../README.md)                                                   | プロダクトの位置付け・Why・Quick start・Roadmap・Non-goals | 製品概要 (SSOT) |
| [web/README.md](../web/README.md)                                           | read-only Vercel ビューアの画面一覧・ローカル/デプロイ手順 | 利用ガイド      |
| [docs/etax/aoiro-65man-requirements.md](./etax/aoiro-65man-requirements.md) | 65万円控除 / 優良電子帳簿の要件 (令和7年分)                | ドメイン要件    |
| [docs/etax/handoff-runbook.md](./etax/handoff-runbook.md)                   | e-Tax 引き継ぎ runbook・手動取込の受け入れ手順 (WEB版)     | 運用手順        |

> 製品ゴールは **青色申告決算書 + e-Tax 取込データの出力** まで。税額計算・申告そのものは
> 下流ツールの担当 (README の Non-goals 参照)。

## 2. AI/開発者向け (アーキ・規約・意思決定・効率化の仕組み)

開発に参加する人/エージェント (Claude Code / Codex / Copilot / Cursor) の入口。
**規約・検証・触ってはいけない領域の SSOT は [AGENTS.md](../AGENTS.md)**。

| ドキュメント                                                            | 内容                                                                                  | 種別               |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------- | ------------------ |
| [AGENTS.md](../AGENTS.md)                                               | Mission / Stack / Verification / Never touch / Architectural invariants / Quality bar | **開発規約 SSOT**  |
| [CLAUDE.md](../CLAUDE.md)                                               | Claude Code 向けの入口 (中身は AGENTS.md を指す薄いポインタ)                          | エージェント設定   |
| [.github/copilot-instructions.md](../.github/copilot-instructions.md)   | GitHub Copilot 向けの薄いポインタ                                                     | エージェント設定   |
| [.cursor/rules/ai-books.mdc](../.cursor/rules/ai-books.mdc)             | Cursor 向けの薄いポインタ                                                             | エージェント設定   |
| [.claude/settings.json](../.claude/settings.json)                       | AI エージェント権限 (allow/deny) の **SSOT**                                          | 権限設定           |
| [docs/adr/README.md](./adr/README.md)                                   | ADR 索引 + 運用プロセス (連番/ステータス/起票基準) + [テンプレ](./adr/template.md)    | **意思決定 (ADR)** |
| [docs/adr/](./adr/)                                                     | 個別の Architecture Decision Records (0001 pivot, 0002–0007 retro-ADR)                | **意思決定 (ADR)** |
| [docs/pr-description-standards.md](./pr-description-standards.md)       | PR 本文の書き方の **SSOT**                                                            | 貢献規約           |
| [.github/PULL_REQUEST_TEMPLATE.md](../.github/PULL_REQUEST_TEMPLATE.md) | PR 本文テンプレ (中身の正は pr-description-standards)                                 | テンプレ           |
| [docs/etax/README.md](./etax/README.md)                                 | e-Tax 所得税関係 XML 仕様の調査スパイク・フィールドカタログ                           | 技術調査           |
| [tests/fixtures/seed_fy/README.md](../tests/fixtures/seed_fy/README.md) | 合成仕訳 seed とゴールデンスナップショットの設計意図                                  | テスト基盤         |

---

## 3. ドキュメント分類 (taxonomy) と SSOT の所在

各ドメインの**一次情報 (SSOT)** は 1 つに固定し、他は**そこを指す薄いポインタ**に徹する
(内容を重複させない)。「これはどこに書く/直すのが正か」はこの表で判断する。

| ドメイン                              | 一次情報 (SSOT)                                                                                                                          | これを指すだけのポインタ                                                                                                               |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| 製品概要・使い方                      | [README.md](../README.md)                                                                                                                | —                                                                                                                                      |
| 開発規約・検証・Never touch・不変条件 | [AGENTS.md](../AGENTS.md)                                                                                                                | [CLAUDE.md](../CLAUDE.md) · [copilot-instructions](../.github/copilot-instructions.md) · [Cursor rules](../.cursor/rules/ai-books.mdc) |
| 検証コマンド                          | [scripts/verify.sh](../scripts/verify.sh) / [scripts/test.sh](../scripts/test.sh) (実体) · 説明は [AGENTS.md#verification](../AGENTS.md) | [.claude/commands/](../.claude/commands) (`/verify` `/test` `/test-all`)                                                               |
| アーキテクチャ上の意思決定            | [docs/adr/](./adr/) (ADR 連番)                                                                                                           | README / AGENTS.md の該当箇所                                                                                                          |
| PR 本文ルール                         | [docs/pr-description-standards.md](./pr-description-standards.md)                                                                        | [PULL_REQUEST_TEMPLATE](../.github/PULL_REQUEST_TEMPLATE.md) · [AGENTS.md#pr-conventions](../AGENTS.md)                                |
| エージェント権限 (allow/deny)         | [.claude/settings.json](../.claude/settings.json)                                                                                        | AGENTS.md / CLAUDE.md の該当箇所                                                                                                       |
| e-Tax 様式・仕様                      | [docs/etax/](./etax/) (`manifest.json` / `field_catalog.json` ほか) · 実装は [src/ai_books/etax/spec.py](../src/ai_books/etax/spec.py)   | README の e-Tax 節                                                                                                                     |
| DB スキーマ (システムオブレコード)    | [supabase/migrations/](../supabase/migrations) (forward-only)                                                                            | README の Schema 節                                                                                                                    |
| テスト用 seed / golden                | [tests/fixtures/seed_fy/](../tests/fixtures/seed_fy/README.md)                                                                           | README の該当箇所                                                                                                                      |

**原則:**

- 一次情報は **1 箇所**。重複した記述を見つけたら、SSOT に寄せてポインタ化する。
- ポインタ文書 (CLAUDE.md / copilot / cursor) は**内容を持たない**。常に SSOT を正とする。
- 過去の決定を覆すときは ADR を**新規追加**する (既存 ADR は編集しない)。
- 「触ってはいけない領域」(`LICENSE` / `uv.lock` / applied 済 migration / 秘密情報) は
  [AGENTS.md#never-touch](../AGENTS.md) が正。

---

## 4. `docs/` のディレクトリ規約

`docs/` 配下は**用途別**にディレクトリを分ける。新規ドキュメントは以下のどれかに置き、
本ハブ ([docs/README.md](./README.md)) の索引に登録する (**未登録の孤立文書を作らない**)。

| ディレクトリ         | 用途                                                                                                                          | 状態           |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------- | -------------- |
| `docs/` (直下)       | このハブ ([README.md](./README.md)) と、分類しきれない単独文書 ([pr-description-standards.md](./pr-description-standards.md)) | 運用中         |
| `docs/adr/`          | Architecture Decision Records。`NNNN-kebab-title.md` の連番。一度確定したら編集せず、覆す決定は新番号で追加                   | 運用中         |
| `docs/etax/`         | e-Tax 様式の調査成果・フィールドカタログ・マッピング。国税庁の著作物 (CAB/.xlsx/.xsd) は**同梱しない** (派生事実データのみ)   | 運用中         |
| `docs/usage/`        | 人間向けの使い方ガイド (画面・操作の how-to) を README から切り出すとき                                                       | 予約 (#88–#90) |
| `docs/architecture/` | アーキテクチャ詳細 (データフロー・モジュール構成) を ADR から派生して厚くするとき                                             | 予約           |
| `docs/ai/`           | AI 開発効率化の仕組み (エージェント運用・Wave・自動化) の文書                                                                 | 予約 (#91–#93) |

> **予約**ディレクトリは後続 Issue で実体ができたタイミングで作成する。空ディレクトリは
> 先行して切らない (本 Issue は「最上流の土台」= ハブ + 分類 + 規約の確定が目的)。

### 命名・登録のルール

- ファイル名は英小文字 + ハイフン (`kebab-case.md`)。ADR のみ連番プレフィックス。
- 文書を追加/移動したら**必ず本ハブの表に 1 行追加**し、相対リンクが解決することを確認する
  (`rg -o '\]\(([^)]+)\)' docs/README.md` で抽出 → 各パスの存在を確認)。
- 言語は日本語を基本とし、コマンド/パス/識別子は英語のまま (PR 本文ルールに準拠)。

---

## 関連 Issue

このハブは docs 整理の**最上流**。後続の整理 (#88–#90) と AI 開発効率化 (#91–#93) は、
新設文書をこのハブに登録していく。進捗: [open issues](https://github.com/knishioka/ai-books/issues)。
