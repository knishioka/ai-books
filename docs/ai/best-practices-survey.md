# AI エージェント開発ベストプラクティス調査 (Claude Code + Codex)

> **調査スパイク #91** — AI 開発効率化 (#92 サブエージェント/コマンド, #93 hooks/ガード/lean CLAUDE.md)
> を最新の公式ベストプラクティスに正しく合わせて導入するための、一次情報に基づく現状調査と本リポへの適用方針。

## メタ情報

| 項目           | 内容                                                                                               |
| -------------- | -------------------------------------------------------------------------------------------------- |
| 調査日         | 2026-06-05                                                                                         |
| 対象           | Claude Code (v2.1 系) / OpenAI Codex (CLI + cloud code review, 既定モデル例 `gpt-5.5`)             |
| 一次情報の方針 | 主張は公式ドキュメント (docs.claude.com / developers.openai.com / agents.md) の URL を明記         |
| 不確実性の扱い | バージョン依存・将来変更がある項目は §9 に集約し ⚠️ で明示。確証できなかった点は出典に注記         |
| 本リポの前提   | Python 3.12 / uv / FastMCP / Supabase(Postgres) / Vercel read-only viewer / single-user (RLS なし) |

機能は頻繁に更新されるため、本書は **調査時点 (2026-06-05) のスナップショット**。採否を実装に落とす
#92/#93 の着手時には、§9 の不確実点を最新ドキュメントで再確認すること。

---

## 1. エグゼクティブサマリ

- **収束先は AGENTS.md (一次標準)**。本リポは既に `CLAUDE.md → AGENTS.md (SSOT)` を採用済みで、これは
  公式の収束パターンと整合している。Claude Code は AGENTS.md を**ネイティブには読まない**ため、
  `CLAUDE.md` を入口にし `@import` または参照リンクで AGENTS.md に集約する現方針を維持・強化する (#93)。
- **#92 (scaffold)**: `.claude/agents/` (サブエージェント) と `.claude/commands/` (スラッシュコマンド) は
  どちらも現行の正式機能。本リポの頻出作業 (migration / MCP tool / report+golden / e-Tax 様式) を型化する
  価値が高く、**採用**。既存の `/verify` `/test` `/test-all` と同じ薄いラッパ方針を踏襲する。
- **#93 (hooks/ガード/lean)**: `.claude/settings.json` の **hooks** で「PostToolUse=自動フォーマット」
  「PreToolUse=applied migration / 秘匿物の編集ブロック」が実現可能。AGENTS.md「Never touch」を**仕組み化**
  できるため**採用**。CLAUDE.md は既に 72 行と十分簡潔だが、`@import` 化と入口集約をさらに進める。
- **見送り**: output styles / statusline (個人設定、`settings.local.json` 領域)、`.mcp.json` での外部 MCP 消費
  (本リポは MCP **サーバ**側であり当面不要)、Codex `config.toml` の共有 (ユーザ個人領域 `~/.codex/`)。
- **Codex 整合**: AGENTS.md に `## Review guidelines` セクションを追加すれば、Codex の PR コードレビューが
  本リポ規約 (forward-only migration, server-side validation, read-only viewer 等) に沿って動く。**採用候補 (#93)**。

---

## 2. 一次情報源インデックス

### Claude Code (docs.claude.com / code.claude.com)

| 機能               | URL                                                                                 |
| ------------------ | ----------------------------------------------------------------------------------- |
| メモリ (CLAUDE.md) | https://code.claude.com/docs/en/claude-md                                           |
| settings.json      | https://code.claude.com/docs/en/settings                                            |
| hooks ガイド/参照  | https://code.claude.com/docs/en/hooks-guide · https://code.claude.com/docs/en/hooks |
| スキル/コマンド    | https://code.claude.com/docs/en/skills                                              |
| サブエージェント   | https://code.claude.com/docs/en/sub-agents                                          |
| output styles      | https://code.claude.com/docs/en/output-styles                                       |
| MCP                | https://code.claude.com/docs/en/mcp                                                 |
| 概要 (@import 等)  | https://code.claude.com/docs/en/overview                                            |

### Codex / AGENTS.md 標準 (developers.openai.com / agents.md)

| 機能                                | URL                                                                                                     |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------- |
| AGENTS.md 標準                      | https://agents.md/                                                                                      |
| Codex の AGENTS.md 解釈             | https://developers.openai.com/codex/guides/agents-md                                                    |
| config 基本/参照                    | https://developers.openai.com/codex/config-basic · https://developers.openai.com/codex/config-reference |
| CLI リファレンス                    | https://developers.openai.com/codex/cli/reference                                                       |
| GitHub 連携/レビュー                | https://developers.openai.com/codex/integrations/github                                                 |
| GitHub Action                       | https://github.com/openai/codex-action                                                                  |
| MCP                                 | https://developers.openai.com/codex/mcp                                                                 |
| 変更履歴 (版固定用)                 | https://developers.openai.com/codex/changelog                                                           |
| (参考) Claude の AGENTS.md 対応要望 | https://github.com/anthropics/claude-code/issues/34235                                                  |

---

## 3. Claude Code 機能サーベイ → 本リポ適用可否

各機能について「現行の公式推奨」「本リポ採否と理由」「落とし先 Issue」を記す。

### 3.1 CLAUDE.md (メモリ)

- **公式推奨**: スコープ階層 (enterprise/policy → user `~/.claude/` → project `./CLAUDE.md` →
  local `./CLAUDE.local.md`) で load。**簡潔さ重視**（目安 200 行未満、長いほど文脈を食い遵守率が下がる）。
  `@path/to/file` の **import 構文**で外部ファイルを取り込める (起動時に展開、深さ上限あり)。手続き的内容は
  Skill/サブエージェントに、規約は参照に逃がして本体を薄く保つ。出典: claude-md。
- **本リポ採否**: ✅ **採用 (強化)**。現状 `CLAUDE.md` は 72 行で既に簡潔、かつ `AGENTS.md` を SSOT と宣言して
  そこへ誘導しており方針は正しい。#93 で (a) `@AGENTS.md` import 化または参照の一貫化、(b) 「最初に読むもの＋
  権限/検証/効率化の入口」への割り切りをさらに進める。
- **落とし先**: #93。

### 3.2 settings.json — permissions & hooks

- **公式推奨 (permissions)**: `allow` / `deny` / `ask` を `Tool(pattern)` 構文で指定。`deny` が優先。
  スコープ優先順位は managed policy > local (`settings.local.json`, gitignore) > project (`settings.json`, 共有)
  > user。`$schema` で検証可能。出典: settings。→ 本リポは既に `.claude/settings.json` で allow/deny を SSOT 化
  > 済み (#60) で整合済み。
- **公式推奨 (hooks)**: ライフサイクルイベントで決定論的にシェル等を起動。中核イベントは **PreToolUse /
  PostToolUse / UserPromptSubmit / SessionStart / SessionEnd / Stop / SubagentStop / Notification / PreCompact**
  (近年のバージョンではこれ以外も追加 → §9 ⚠️)。`matcher` でツール名 (`Edit|Write`, `mcp__*` 等) を絞り込む。
  **PreToolUse は exit code 2 でツール呼び出しをブロック**でき、JSON 出力 `permissionDecision: "deny"` +
  理由文字列でも拒否できる。PostToolUse は実行後フック (整形等)。`type` は `command` が基本。出典: hooks。
- **本リポ採否**: ✅ **採用**。これが #93 の中核。
  - **PostToolUse**: Python 編集後に `ruff format` を自動適用 (pre-commit と二重化するが、コミット前の
    手戻り/差分ノイズを減らす)。web は `eslint --fix` 等。
  - **PreToolUse (ガード)**: AGENTS.md「Never touch」を仕組み化 —
    (i) `supabase/migrations/` の **applied 済みファイル編集をブロック** (forward-only 不変条件 #invariant-3)、
    (ii) `.env` / `*.xsd` / `~/.ai-books/**` / `uv.lock` の誤編集・誤コミットをブロック。
    既存 `deny` permissions と役割分担し、過剰ブロック時の回避手順を文書化する。
  - **SessionStart**: docs ハブ/不変条件の要点注入は**任意**。文脈効率とトレードオフのため #93 で要否を判断
    (まずはガード/整形を優先し、注入は最小限から)。
- **落とし先**: #93。

### 3.3 .claude/commands — スラッシュコマンド

- **公式推奨**: Markdown + frontmatter (`description` / `argument-hint` / `allowed-tools` / `model` 等)。
  `$ARGUMENTS` や `$1` で引数、`` !`cmd` `` でシェル出力をインライン注入、`@file` でファイル参照。近年
  **コマンド機能は Skills に統合**され、`.claude/commands/*.md` は後方互換で動作しつつ実体は Skills と共通
  (§3.5)。出典: skills。
- **本リポ採否**: ✅ **採用**。既存 `/verify` `/test` `/test-all` (薄いラッパ) と同方針で scaffold 系を追加。
  - `/new-migration <name>` — forward-only migration 雛形 + 適用/冪等テストの型 + schema golden 更新手順
  - `/new-mcp-tool <name>` — `server.py` への `@mcp.tool` 追加 + 入口 Pydantic 検証 + protocol/contract テスト
  - `/new-report <name>` — 集計/レポート + `seed_fy` golden + DB/Web golden 整合
  - `/etax-validate [file]` — 既存 XSD 検証を単体起動
    各 scaffold は「生成物の置き場所・必須テスト・検証コマンド (`./scripts/verify.sh` / `test.sh`)」を明示し
    #89 How-to-add と相互参照する。
- **落とし先**: #92。

### 3.4 .claude/agents — サブエージェント

- **公式推奨**: `.claude/agents/` に Markdown + frontmatter (`name` / `description` / `tools` / `model` 等)。
  **独立した context** で動き、ツール制限・委譲に向く。自然言語や `@agent-<name>` で起動。project スコープが
  最優先。出典: sub-agents。
- **本リポ採否**: ✅ **採用**。本リポの繰り返し作業を型化する具体エージェントを定義 (AGENTS.md/不変条件・
  #89 アーキ地図を前提に動く):
  - `migration-author` (forward-only + 適用/冪等テスト)
  - `mcp-tool-author` (@mcp.tool + 入口 Pydantic 検証 + protocol/contract テスト)
  - `report-author` (集計/レポート + seed_fy golden + DB/Web golden 整合)
  - `etax-spec-author` (#76 fetch/catalog を使った様式追補 KOA220/240 + .xsd 検証)
  - **YAGNI 原則**: 実際に頻出する作業のみを対象に絞り乱立を避ける。コマンド (薄いラッパ) と
    サブエージェント (独立 context の委譲) の役割を取り違えない。
- **落とし先**: #92。

### 3.5 .claude/skills — Agent Skills

- **公式推奨**: `.claude/skills/<name>/SKILL.md` (ディレクトリ単位、補助ファイルを on-demand ロード)。
  frontmatter で起動制御 (`disable-model-invocation` / `user-invocable` / `allowed-tools` / `paths` 等)。
  **コマンドとスキルは同一系統**に統合され、`.claude/commands/*.md` と `SKILL.md` はほぼ同じ frontmatter。
  進行的開示 (progressive disclosure) で文脈効率が高い。出典: skills。
- **本リポ採否**: 🟡 **限定採用 / 様子見**。#92 の scaffold は当面シンプルなコマンドで足りる。スクリプトや
  参照ドキュメントを伴う重めの scaffold (例: e-Tax 様式追補の手順 + テンプレート群) が出てきたら SKILL.md 化を
  検討。コマンド/スキルが統合されている前提で、まずはコマンドで始め必要に応じ昇格する。
- **落とし先**: #92 (将来拡張として注記)。

### 3.6 output styles / statusline

- **公式推奨**: output styles はシステムプロンプトを変えて役割/トーンを切替 (`~/.claude/output-styles/` 等)。
  statusline は `settings.json` の `statusLine` でターミナル状態表示をカスタム。出典: output-styles, settings。
- **本リポ採否**: ❌ **見送り**。チーム共有の価値が薄く個人の好み領域。必要なら各自の
  `settings.local.json` / user 設定で対応 (リポにはコミットしない)。
- **落とし先**: なし。

### 3.7 MCP 連携 (.mcp.json)

- **公式推奨**: project スコープは `.mcp.json` (transport: `http`/`stdio`/`ws`、`sse` は非推奨)。スコープは
  local > project > user。環境変数展開 `${VAR:-default}` 対応。Tool Search でツール定義を遅延ロード。
  出典: mcp。
- **本リポ採否**: ❌ **当面見送り (本リポは MCP サーバ側)**。本プロダクト自体が FastMCP **サーバ**であり、
  Claude Code から**外部** MCP サーバを消費する必要が現状ない。将来 GitHub/Slack 等の外部 MCP を開発フローに
  組み込む段階で `.mcp.json` を検討。Claude Desktop からの本サーバ利用手順は README に既出。
- **落とし先**: なし (将来検討)。

### 3.8 plan mode

- **公式推奨**: 変更前に read-only で context 収集し計画提示する権限モード (`--permission-mode plan` /
  セッション内 EnterPlanMode、組込 `Plan` サブエージェント)。出典: sub-agents。
- **本リポ採否**: ℹ️ **運用ガイドのみ (リポ設定不要)**。大きめのアーキ変更時に各自が利用。リポにコミットする
  設定対象ではない。
- **落とし先**: なし。

### 3.9 メモリ/context 管理

- **公式推奨**: CLAUDE.md は薄く、手続きは Skill/サブエージェントに逃がす。auto memory / compaction で
  プロジェクトルート CLAUDE.md は再注入される。`/context` で配分を可視化。出典: claude-md。
- **本リポ採否**: ✅ **方針として採用**。#93 の lean CLAUDE.md と一体。詳細は AGENTS.md/docs ハブに集約し、
  CLAUDE.md は入口に徹する設計を継続。
- **落とし先**: #93。

---

## 4. Codex 機能サーベイ → 本リポ適用可否

### 4.1 AGENTS.md (ネイティブ参照)

- **公式推奨**: AGENTS.md は「coding agent 向けの README」= ビルド/テスト/規約/セキュリティ/コミット規約を
  置くプレーン Markdown のオープン標準 (Linux Foundation / Agentic AI Foundation がスチュワード、20+ エージェントが
  対応)。README を人間向けに保ちつつ補完する位置づけ。Codex は **Git ルート → cwd へ降りながら各階層の
  AGENTS.md を連結** (近いものが後勝ち)、加えて **グローバル `~/.codex/AGENTS.md`** とサイズ上限
  (`project_doc_max_bytes` 既定 ~32KiB) を持つ。出典: agents.md, codex/guides/agents-md。
- **本リポ採否**: ✅ **採用済み・SSOT 維持**。本リポは既に AGENTS.md を SSOT 化済み。Codex は追加設定なしで
  これを読む。サイズ上限 (~32KiB) に収まることを #93 で確認 (現状問題なし)。
- **落とし先**: #93 (確認のみ)。

### 4.2 config.toml (~/.codex/config.toml)

- **公式推奨**: ユーザ設定 `~/.codex/config.toml` (`model` / `model_reasoning_effort` / `sandbox_mode` /
  `approval_policy` / `[mcp_servers.*]` 等)。project 設定 `.codex/config.toml` は **trusted project のみ**。
  profile は近年 **別ファイル `~/.codex/<name>.config.toml`** 形式 (⚠️ 旧来の `[profiles.x]` テーブル形式は
  レガシー → §9)。出典: config-basic, config-reference。
- **本リポ採否**: ❌ **共有はしない (個人領域)**。`~/.codex/` はユーザ個人。リポにコミットする対象ではない。
  project 単位の `.codex/config.toml` は trusted 前提のため当面導入しない。
- **落とし先**: なし。

### 4.3 CLI 利用 (approval × sandbox)

- **公式推奨**: approval (`--ask-for-approval`: `untrusted|on-request|never`) と sandbox
  (`--sandbox`: `read-only|workspace-write|danger-full-access`) は**直交する 2 軸**。旧来の
  「suggest/auto-edit/full-auto」3 モードの理解は古い (`--full-auto` は非推奨 → `--sandbox workspace-write`)。
  非対話は `codex exec`。検証コマンドは sandbox 内で approval ポリシーに従い実行。出典: cli/reference。
- **本リポ採否**: ℹ️ **運用ガイドのみ**。検証は `./scripts/verify.sh` / `test.sh` に統一済みなので、Codex でも
  これを sandbox 内で走らせる運用を README/AGENTS.md の Verification に沿わせる。リポ設定は不要。
- **落とし先**: #93 (Verification 整合の確認)。

### 4.4 Codex コードレビュー (cloud / GitHub)

- **公式推奨**: PR コメント `@codex review` (手動) / 設定で「Automatic reviews」(自動)。**変更ファイルに最も近い
  AGENTS.md の `## Review guidelines` に従う**。GitHub では P0/P1 のみ surface。CI 代替に `openai/codex-action@v1`
  (`codex exec` を proxy 経由で実行)。出典: codex/integrations/github, codex-action。
- **本リポ採否**: ✅ **採用候補 (#93)**。AGENTS.md に `## Review guidelines` を追記し、本リポ不変条件
  (forward-only migration / server-side Pydantic validation / read-only viewer / audit append-only /
  秘匿物非コミット) をレビュー観点として明文化 → Claude Code (`/code-review` 等) と Codex の**両方**が同じ
  観点で動く。自動レビュー有効化や codex-action 導入は infra/CI 判断のため**別途**(本スパイクでは方針提示のみ)。
- **落とし先**: #93 (AGENTS.md への Review guidelines 追記)。自動化/CI は follow-up。

### 4.5 MCP (Codex 側)

- **公式推奨**: `[mcp_servers.<id>]` で stdio / streamable-http を消費。`codex mcp add` で追加。逆に
  `codex mcp-server` で Codex 自体を MCP サーバとして公開可 (experimental, ⚠️ §9)。出典: codex/mcp。
- **本リポ採否**: ❌ **当面見送り**。3.7 と同理由 (本リポは MCP サーバ側)。
- **落とし先**: なし。

### 4.6 共通 SSOT 収束 (Codex ⇄ Claude Code)

- **公式/標準の答え**: **AGENTS.md が tool-agnostic な一次標準**で、Codex も Claude Code も対応エージェント一覧に
  含まれる (= 1 ファイルで多エージェントを賄う意図)。ただし **Claude Code は 2026-06 時点で AGENTS.md を
  ネイティブには読まず CLAUDE.md を読む** (ネイティブ対応は未実装の要望 issue)。実務上の収束は **CLAUDE.md から
  `@AGENTS.md` を import** または symlink で 1 SSOT 化する (⚠️ import 機構自体は公式、"CLAUDE.md→AGENTS.md を
  指す" 運用はコミュニティ慣行 → §9)。出典: agents.md, overview, anthropics/claude-code#34235。
- **本リポ採否**: ✅ **採用済みパターン**。本リポは `CLAUDE.md` が `AGENTS.md` を SSOT と宣言し誘導する形で、
  まさにこの収束パターンの実装。#93 で `@import` の明示利用に寄せるか、現行の参照リンク方式を保つかを判断
  (どちらも有効)。
- **落とし先**: #93。

---

## 5. 採否一覧 (サマリ)

| 機能 (Claude Code / Codex)                | 採否         | 主な理由                                               | 落とし先  |
| ----------------------------------------- | ------------ | ------------------------------------------------------ | --------- |
| CLAUDE.md lean + @import                  | ✅ 採用      | 文脈効率、SSOT 集約。現状方針の強化                    | #93       |
| settings.json permissions                 | ✅ 採用済み  | #60 で SSOT 化済み                                     | —         |
| hooks: PostToolUse 自動 format            | ✅ 採用      | 差分ノイズ/手戻り削減                                  | #93       |
| hooks: PreToolUse ガード                  | ✅ 採用      | 「Never touch」(applied migration/秘匿物) を仕組み化   | #93       |
| hooks: SessionStart 注入                  | 🟡 任意      | 文脈効率とトレードオフ。最小限から                     | #93       |
| .claude/commands (scaffold)               | ✅ 採用      | 頻出作業の型化。既存薄いラッパ方針                     | #92       |
| .claude/agents (subagents)                | ✅ 採用      | 委譲・独立 context で繰り返し作業を型化 (YAGNI で限定) | #92       |
| .claude/skills                            | 🟡 限定      | 重め scaffold が出たら昇格。まずコマンドで             | #92       |
| output styles / statusline                | ❌ 見送り    | 個人設定領域 (settings.local / user)                   | —         |
| .mcp.json (外部 MCP 消費)                 | ❌ 見送り    | 本リポは MCP **サーバ**側、当面不要                    | —         |
| plan mode                                 | ℹ️ 運用のみ  | リポ設定対象外                                         | —         |
| Codex AGENTS.md ネイティブ参照            | ✅ 採用済み  | SSOT。サイズ上限のみ確認                               | #93       |
| Codex `## Review guidelines` in AGENTS.md | ✅ 採用候補  | Claude/Codex 双方のレビュー観点を一本化                | #93       |
| Codex config.toml / profiles 共有         | ❌ 見送り    | `~/.codex/` 個人領域                                   | —         |
| Codex 自動レビュー / codex-action         | 🟡 follow-up | infra/CI 判断。方針提示のみ                            | follow-up |

---

## 6. #92 に渡す具体タスク候補 (subagents + slash-command scaffolds)

1. **サブエージェント定義** (`.claude/agents/`、AGENTS.md/不変条件・#89 を前提に):
   - `migration-author`: forward-only migration 雛形 + 適用/冪等テスト + `schema.sql` golden 更新手順を案内。
     applied 済みファイルは触らせない (PreToolUse ガード #93 と二重防御)。
   - `mcp-tool-author`: `server.py` への `@mcp.tool` 追加 + 入口 Pydantic 検証 (借貸バランス/Decimal/FK) +
     protocol/contract テスト雛形。
   - `report-author`: 集計/レポート層 + `seed_fy` golden + DB/Web golden 整合チェック。
   - `etax-spec-author`: #76 の fetch/catalog を用いた様式追補 (KOA220/240) + `.xsd` 検証。
2. **スラッシュコマンド** (`.claude/commands/`、薄いラッパ): `/new-migration <name>` `/new-mcp-tool <name>`
   `/new-report <name>` `/etax-validate [file]`。各々 frontmatter (`description`/`argument-hint`/`allowed-tools`)
   を持ち、生成物の置き場所・必須テスト・検証コマンドを本文に明示。
3. **発見性**: #89 (How-to-add) / #87 ハブから各 scaffold を相互参照。
4. **スモーク**: 代表コマンド (`/new-migration`) 実行で雛形+テストが生成されること、`.claude/agents` 定義が
   読み込めることを確認。
5. **YAGNI ガード**: 実際に頻出する 4 作業のみに限定し、投機的な scaffold を作らない。

## 7. #93 に渡す具体タスク候補 (hooks + guardrails + lean CLAUDE.md + Codex 整合)

1. **PostToolUse フック**: Edit/Write 後に Python は `ruff format`、web は `eslint --fix` を自動適用。
   pre-commit と整合 (ブロックでなく整形のみ)。
2. **PreToolUse ガード** (exit code 2 / `permissionDecision: deny`):
   - `supabase/migrations/` の applied 済みファイル編集をブロック (forward-only #invariant-3)。
   - `.env` / `*.xsd` / `~/.ai-books/**` / `uv.lock` の編集・追加をブロック (Never touch の仕組み化)。
   - 既存 `deny` permissions と役割分担し、**誤検知時の回避手順**を文書化。
3. **lean CLAUDE.md**: `@AGENTS.md` import 化 or 参照一貫化で「入口」に徹する (現 72 行をさらに整理)。
4. **Codex 整合**:
   - AGENTS.md に `## Review guidelines` を追記し不変条件をレビュー観点として明文化 (Claude/Codex 双方が同観点)。
   - AGENTS.md が Codex のサイズ上限 (~32KiB) に収まることを確認。
   - Verification (`verify.sh`/`test.sh`) を Codex の sandbox 実行でも踏襲する運用を明記。
5. **テスト/動作確認手順**: 故意に applied migration 編集 / `.env` 追加を試みブロックされること、編集→自動整形が
   効くこと、`./scripts/verify.sh` が壊れないことを確認。無効化/回避手順も文書化。

> **follow-up 候補 (本スパイク/#92/#93 のスコープ外)**: Codex の自動 PR レビュー有効化 / `openai/codex-action`
> の CI 導入は infra・コスト判断を伴うため別 Issue 化。

---

## 8. 検証 (本スパイクの AC)

- ✅ 一次情報引用付きで Claude Code / Codex の現行機能を整理 (§2 URL インデックス + §3/§4 各出典)。
- ✅ 各機能の採用/見送りを理由付きで一覧化 (§5)。
- ✅ #92/#93 へ渡す具体タスク候補を抽出 (§6/§7)。
- ✅ 調査文書のみで `./scripts/verify.sh` を壊さない (コード変更なし)。
- ✅ 適用方針が本リポ構成 (uv / Postgres / MCP サーバ / web viewer / single-user) と矛盾しない
  (MCP 消費・RLS・config 共有を見送る判断に反映)。

---

## 9. 不確実点・将来変更リスク (⚠️)

機能更新が速い領域。#92/#93 着手時に最新ドキュメントで再確認すること。

1. **Claude Code hooks のイベント網羅性 (バージョン依存)** — 中核イベント (PreToolUse/PostToolUse/
   UserPromptSubmit/SessionStart/SessionEnd/Stop/SubagentStop/Notification/PreCompact) は安定だが、近年の版で
   追加イベント・`type: prompt`/`type: agent` 等の実験的フックが増えている。採用前に
   https://code.claude.com/docs/en/hooks で対象版の対応状況を確認。
2. **コマンド ⇄ スキルの統合** — `.claude/commands/*.md` は後方互換で動作するが実体は Skills に統合されつつある。
   frontmatter キー (`disable-model-invocation` 等) の最新仕様を skills ドキュメントで確認。
3. **Codex profiles のファイル形式** — 公式は別ファイル `~/.codex/<name>.config.toml` 形式。旧来/コミュニティの
   `[profiles.<name>]` テーブル形式はレガシー。引用時は版に注意 (config-basic 参照)。
4. **`codex mcp-server` (Codex を MCP サーバ化)** — experimental。ツール名 (`codex`/`codex-reply`) は
   `codex mcp-server --help` で要確認。
5. **CLAUDE.md → AGENTS.md 収束** — `@import`/symlink 機構自体は公式だが、「CLAUDE.md で `@AGENTS.md` を指す」
   運用は主にコミュニティ慣行で、Anthropic 公式が名指しで推奨する形ではない (Claude Code の AGENTS.md ネイティブ
   対応は要望中)。将来ネイティブ対応されれば収束方式を簡素化できる。
6. **Codex ドキュメントの版表記** — developers.openai.com の Codex ページは per-page の日付/版表記がない
   (rolling)。版を固定したい場合は https://developers.openai.com/codex/changelog を anchor にする。
7. **モデル/既定値** — Codex の例示既定モデル (`gpt-5.5`)、Claude Code のバージョン番号は時点依存。本書の
   バージョン記述は 2026-06-05 時点。
