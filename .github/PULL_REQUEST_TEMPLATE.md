<!--
ai-books PR template. 記述ルールの SSOT は docs/pr-description-standards.md (in-repo)。
本文は日本語、コマンド / パス / コミットは英語。未検証は ❌ / ⚠️ / 「未検証」で隠さず明示。
-->

## 概要

{1〜3文で「何を」「なぜ」変えたか}

Closes #

## 変更内容

- {ファイル / モジュール単位の主要変更}
- {自動生成ファイル (uv.lock / snapshot 等) があれば「自動更新」と明示}

## 動作確認 (Verification)

| 項目      | コマンド              | 結果 |
| --------- | --------------------- | ---- |
| Build     | n/a (library)         | n/a  |
| Lint      | `./scripts/verify.sh` |      |
| Format    | `./scripts/verify.sh` |      |
| Typecheck | `./scripts/verify.sh` |      |
| Test      | `./scripts/verify.sh` |      |

実行ログ要約: {自己修正で直したエラー / 許容した warning / 未対応の理由。なければ「初回成功」}

## 受け入れ条件 (Acceptance Criteria)

- [ ] {Issue の AC を一行ずつ転記し、満たした方法を添える}

## スコープ外 (Non-goals)

- {本 PR で対応していないこと。なければ「なし」}

## 影響範囲 / リスク

- 影響API / 互換性: {破壊的変更の有無、移行手順。内部のみなら「なし」}
- ロールバック: {`git revert` で十分か / 追加手順}

## レビュー観点 (Review Focus)

- `{path/to/file}:L{N}` — {1 文で観点}
