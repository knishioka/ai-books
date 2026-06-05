# Claude Code hooks & guardrails — 自動整形と「Never touch」の仕組み化

> AI 開発を**安全かつ自動で**効率化するための [Claude Code hooks](https://code.claude.com/docs/en/hooks)。
> これまで人手 / レビューで守ってきた規約 ([AGENTS.md#never-touch](../../AGENTS.md) ·
> [#architectural-invariants](../../AGENTS.md)) を**仕組みで強制・自動化**する。調査の出所と採否は
> [best-practices-survey.md §3.2 / §7](./best-practices-survey.md) を参照。

設定の **SSOT は [.claude/settings.json](../../.claude/settings.json)** の `hooks` ブロック、
実体は [.claude/hooks/](../../.claude/hooks/) の 2 スクリプト。Claude Code / Codex どちらの
エージェントが編集しても、Claude Code 経由の Edit/Write は同じガードが効く (Codex 整合は §4)。

---

## 1. 導入されている hooks

| イベント      | matcher                  | スクリプト                                                       | 役割                                                                      |
| ------------- | ------------------------ | ---------------------------------------------------------------- | ------------------------------------------------------------------------- |
| `PreToolUse`  | `Edit\|Write\|MultiEdit` | [guard-never-touch.py](../../.claude/hooks/guard-never-touch.py) | 「Never touch」「forward-only migration」違反を**ブロック**               |
| `PostToolUse` | `Edit\|Write\|MultiEdit` | [format-after-edit.py](../../.claude/hooks/format-after-edit.py) | 編集後に **`ruff format`** (Python) / **`eslint --fix`** (web) を自動適用 |

両スクリプトとも **Python 標準ライブラリのみ**で動く (= `uv sync` 不要・高速)。

### 1.1 PreToolUse ガード (`guard-never-touch.py`)

編集対象が下記に該当すると **exit code 2** でツール呼び出しをブロックし、理由と回避手順を
stderr に出す。[AGENTS.md#never-touch](../../AGENTS.md) / [invariant #3](../../AGENTS.md) を機械化したもの。

| ブロック対象                                         | 根拠                                                 | 許可される操作                          |
| ---------------------------------------------------- | ---------------------------------------------------- | --------------------------------------- |
| `supabase/migrations/` の**既存**ファイル編集/上書き | forward-only (invariant #3): 適用済 migration は不変 | **新規** migration ファイルの作成は許可 |
| `.env` / `.env.*`                                    | 秘匿設定 (gitignore 済)                              | `.env.example` (テンプレ) は許可        |
| `*.xsd`                                              | 国税庁 公式 .xsd (著作物・fetch 対象、非同梱)        | `scripts/etax/` の fetch 経路で取得     |
| `uv.lock`                                            | 手動編集禁止                                         | `uv sync` 経由で再生成                  |
| `~/.ai-books/` · リポ内 `.ai-books/`                 | 実データ (本番)                                      | MCP tool / CLI 経由で扱う               |

- **既存 `deny` permissions との役割分担**: [.claude/settings.json](../../.claude/settings.json) の
  `deny` (`uv.lock` / `~/.ai-books/**`) は**ハードな最終防壁**。本フックは加えて
  **applied migration / `.env` / `.xsd`** という _permissions のグロブだけでは表現しにくい条件_
  (「既存ファイルか新規か」「`.env.example` 除外」) を判定し、**分かりやすい理由メッセージ**を返す。
  二重化は意図的 (defense-in-depth)。
- **fail-open 設計**: 入力 JSON が壊れている / `file_path` が無い / 想定外例外のときはブロックせず
  通す (`exit 0`)。フックのバグで編集ループが固まらないようにし、ハードな保証は `deny` permissions に委ねる。

### 1.2 PostToolUse 自動整形 (`format-after-edit.py`)

編集直後のファイルだけを対象に整形を適用し、pre-commit / CI が要求する形に**先回りで**揃える
(整形差分ノイズと pre-commit のやり直しループを削減)。

- `*.py` → `uv run ruff format <file>` (`uv` が無ければ PATH 上の `ruff` にフォールバック)。
- `web/**` の `*.ts|*.tsx|*.js|*.jsx|*.mjs|*.cjs` → `web/node_modules/.bin/eslint --fix <file>`
  (依存未インストール時はスキップ。`npx` のダウンロード待ちは発生させない)。
- **整形は advisory**: 常に `exit 0`。フォーマッタが無い/失敗/未修正の lint があっても編集を
  ブロックしない (lint/format の最終強制は [`./scripts/verify.sh`](../../scripts/verify.sh) と CI)。

---

## 2. 動作確認 (テスト)

ガードの挙動は **[tests/test_hooks_guard.py](../../tests/test_hooks_guard.py)** が hermetic に検証する
(一時ディレクトリを project root に見立て、フックを subprocess 起動して exit code を assert)。
DB 不要・`./scripts/verify.sh` に含まれる。

```bash
uv run pytest tests/test_hooks_guard.py -q
```

手動で 1 ケース試す (適用済 migration 編集がブロックされること):

```bash
export CLAUDE_PROJECT_DIR="$PWD"
echo '{"tool_name":"Edit","tool_input":{"file_path":"'$PWD'/supabase/migrations/20260604000001_accounts.sql"}}' \
  | python3 .claude/hooks/guard-never-touch.py; echo "exit=$?"   # → BLOCKED, exit=2
```

整形の確認: 故意に整形崩れの Python を Edit し、保存後に `ruff format` 済みになることを見る。

---

## 3. 無効化 / 回避 (escape hatch)

正当な操作が**誤ってブロック**された場合の回避策。いずれも**狭く・一時的に**使うこと。

1. **単発の正当な編集を通す**: 環境変数 `AI_BOOKS_ALLOW_GUARDED_EDIT=1` を設定してその編集を行う。
   ガードはバイパスを stderr に記録して通す (例: applied migration の hotfix を例外的に行う等、
   ただし invariant #3 上は原則 forward-only)。
2. **個人環境でフック自体を止める**: gitignore 済の `.claude/settings.local.json` で `hooks` を
   上書き (空配列にする)。共有しない個人差分はここに置く ([CLAUDE.md#エージェント権限](../../CLAUDE.md))。
3. **ガード条件を恒久的に変える**: 誤検知が再発するなら [guard-never-touch.py](../../.claude/hooks/guard-never-touch.py)
   の条件を直し、[tests/test_hooks_guard.py](../../tests/test_hooks_guard.py) にケースを足して PR でレビューする
   (= ガードはコードとしてレビュー可能に保つ)。

> 整形フック (PostToolUse) は advisory なので「止める」必要は通常ない。重い web 整形を避けたい場合は
> `web/node_modules` 未インストールで自動スキップされる。

---

## 4. Codex / 他エージェントとの整合

- 規約の **SSOT は [AGENTS.md](../../AGENTS.md)**。本フックはその「Never touch」「forward-only」を
  *Claude Code の編集経路で*機械化したもの。Codex も同じ AGENTS.md を一次参照する
  ([best-practices-survey.md §4.1](./best-practices-survey.md))。
- Codex の **PR コードレビュー**は AGENTS.md の **[`## Review guidelines`](../../AGENTS.md)** に従う。
  本フックがブロックする不変条件 (forward-only migration / 秘匿物非コミット / read-only viewer /
  server-side validation / audit append-only) は同セクションにレビュー観点として明文化済みで、
  **Claude Code (`/code-review`) と Codex の双方が同じ観点**で動く。
- 検証は Claude / Codex とも [`./scripts/verify.sh`](../../scripts/verify.sh) / [`test.sh`](../../scripts/test.sh)
  に統一 ([AGENTS.md#verification](../../AGENTS.md))。フックは検証を置き換えるものではなく、commit 前の
  手戻りを減らす**先回り**にすぎない。
