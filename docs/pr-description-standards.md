# PR Description Standards — ai-books

このリポジトリの **PR 本文の書き方の SSOT (Single Source of Truth)**。
[AGENTS.md](../AGENTS.md) の "PR conventions" と
[.github/PULL_REQUEST_TEMPLATE.md](../.github/PULL_REQUEST_TEMPLATE.md) は、ここを指す。

マルチエージェント開発 (Claude Code / Codex / Copilot / Cursor 等) で
PR 記述を一貫させるため、ルールは **in-repo で自己完結** させる。外部 workspace の
ドキュメントには依存しない。

## 言語ルール

- **PR 本文は日本語**。
- **ブランチ名 / コミットメッセージ / PR タイトルは英語** ([Conventional Commits](https://www.conventionalcommits.org/))。
- コマンド / パス / 識別子は英語のまま (翻訳しない)。

## 必須セクション

PR 本文は [.github/PULL_REQUEST_TEMPLATE.md](../.github/PULL_REQUEST_TEMPLATE.md)
の構成に従う。各セクションの意図は以下のとおり。

### 概要

1〜3 文で「何を」「なぜ」変えたかを述べる。実装の手順説明ではなく、**変更の目的と効果**を書く。
関連 Issue を `Closes #<n>` で閉じる (複数あれば各行に 1 つ)。

### 変更内容

- ファイル / モジュール単位の主要変更を箇条書き。
- 自動生成ファイル (`uv.lock` / golden snapshot 等) を含む場合は「自動更新」と明示する。

### 動作確認 (Verification)

検証は [`./scripts/verify.sh`](../scripts/verify.sh) が唯一のエントリポイント
(lint / format / typecheck / test を順に実行)。テンプレートの表を**必ず埋める**。

- **結果は隠さない**。pass は `✅`、未検証は `❌`、警告付き pass は `⚠️` を使い、
  「未検証」を空欄や曖昧表現でごまかさない。
- DB 連携テストを含む全テストは [`./scripts/test.sh`](../scripts/test.sh) で実行する
  (詳細は [AGENTS.md](../AGENTS.md) の "Verification")。
- 実行ログ要約に、自己修正で直したエラー / 許容した warning / 未対応の理由を書く。
  問題がなければ「初回成功」と書く。

### 受け入れ条件 (Acceptance Criteria)

Issue の AC を**一行ずつ転記**し、それぞれ満たした方法を添える。
チェックボックスは実際に満たしたものだけを `[x]` にする。

### スコープ外 (Non-goals)

本 PR で意図的に対応しなかったことを書く。なければ「なし」。
追って対応すべき項目は follow-up Issue 化し、その番号を添える。

### 影響範囲 / リスク

- 影響 API / 互換性: 破壊的変更の有無と移行手順。内部のみなら「なし」。
- ロールバック: `git revert` で十分か、追加手順が要るか。

### レビュー観点 (Review Focus)

レビュアーに最初に見てほしい箇所を `path/to/file:L<N>` 形式 (クリック可能) で示し、
各 1 文で観点を添える。

## チェックリスト (PR を出す前に)

- [ ] PR 本文は日本語、タイトル / ブランチ / コミットは英語 (Conventional Commits)
- [ ] `Closes #<n>` で対象 Issue を閉じている
- [ ] 動作確認の表を埋め、未検証は `❌` / `⚠️` で明示している
- [ ] Issue の AC を一行ずつ転記し、満たした方法を添えている
- [ ] [`./scripts/verify.sh`](../scripts/verify.sh) が green (または未 green の理由を明記)
