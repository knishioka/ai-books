# ai-books 能力マップ & 使い方ガイド (人間向け)

> **このリポジトリで「今 何ができるか」と「どう使うか」を 1 本にまとめた人間向けガイド。**
> 機能の一覧 (能力マップ) → 提供インターフェース (MCP ツール / Vercel viewer) → 代表的な
> 操作手順 → できないこと (スコープ境界)、の順で読めば全体像がつかめる。

`ai-books` は **AI-first な会計 MCP サーバー**。複式簿記のプリミティブ (勘定科目・仕訳・
試算表・決算書) を [Model Context Protocol](https://modelcontextprotocol.io/) のツールとして
公開する。設計の前提は **「機械向けインターフェースが優秀なら、人間 UI は薄い read-only の
集計ダッシュボードで足りる」**。

アーキテクチャを一言で言うと:
**MCP = 書込/検証インターフェース · Supabase (Postgres) = 保管 · Vercel = read-only ビュー**
(背景は [ADR 0001](../adr/0001-pivot-to-supabase-and-vercel-viewer.md))。

- **書込・検証は MCP ツール経由のみ** — 借方貸方の一致・Decimal 精度・科目 FK・会計期間は
  サーバー側で検証される ([AGENTS.md](../../AGENTS.md) invariant #2)。
- **人間は read-only の Vercel viewer を見るだけ** — viewer にデータ入力 UI は無い (invariant #1)。
- **最終出力は「青色申告決算書 + e-Tax 取込データ」** まで。税額計算・申告そのものは下流ツールの担当
  ([スコープ境界](#3-できること--できないこと-scope-boundaries))。

このガイドは索引/SSOT マップである [ドキュメントハブ](../README.md) の「人間向け」入口の 1 つ。
規約・検証・触ってはいけない領域の SSOT は [AGENTS.md](../../AGENTS.md)。

---

## 1. 能力マップ (what ai-books can do)

現行で実装済みの機能と、それぞれの**入口** (どの MCP ツール / viewer 画面 / 出力で触れるか) の一覧。
ツールの引数や用途の詳細は [2.1 MCP ツール一覧](#21-mcp-ツール一覧-提供インターフェース) を参照。

### マスタ — 勘定科目 (chart of accounts)

| 機能                 | 概要                                                             | 入口 (MCP / viewer)                                              |
| -------------------- | ---------------------------------------------------------------- | ---------------------------------------------------------------- |
| 勘定科目マスタの照会 | 個人事業/青色申告の標準科目 (区分・表示区分・正常残高・内訳親子) | `list_accounts` / `get_account` / `search_accounts` · viewer `/` |

> 標準科目は `uv run python -m ai_books.seed.accounts` でシードする (冪等)。詳細は
> [README#seed-chart-of-accounts](../../README.md#seed-chart-of-accounts)。

### 仕訳 — 入力・検証・取消 (journal CRUD)

| 機能                      | 概要                                                                      | 入口 (MCP / viewer)                                              |
| ------------------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| 仕訳の作成 (借貸検証つき) | 借方合計 = 貸方合計・実在する有効科目・桁数・会計期間をサーバー側で検証   | `create_journal_entry`                                           |
| 下書きの修正              | `draft` の仕訳ヘッダ/明細を差し替え (`posted` は不可 → 取消/逆仕訳で訂正) | `update_journal_entry`                                           |
| 記帳確定                  | `draft` → `posted` (帳簿へ確定)                                           | `post_journal_entry`                                             |
| 取消 (void)               | 行を消さず `voided` にし理由を記録 (帳簿の連続性・訂正削除履歴を保持)     | `void_journal_entry`                                             |
| 仕訳の照会                | 期間・科目・状態・摘要で絞り込み (ページング + 総件数)                    | `list_journal_entries` / `get_journal_entry` · viewer `/journal` |

### CSV 取込 (bank / card import)

| 機能          | 概要                                                                                                                                                        | 入口 (MCP)                |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------- |
| 明細 CSV 取込 | 銀行/カード明細を **下書き仕訳**に変換。相手科目は摘要から推定、不明は仮払金/仮受金へ。再取込は重複しない (フィンガープリント)。確定は `post_journal_entry` | `import_transactions_csv` |

### 帳簿 (保存義務帳簿)

| 機能       | 概要                                                                  | 入口 (MCP / viewer)                                        |
| ---------- | --------------------------------------------------------------------- | ---------------------------------------------------------- |
| 仕訳帳     | 取引日 → 伝票番号順、明細ごとに科目をインライン表示 + 借方/貸方の合計 | `journal_book` · viewer `/journal`                         |
| 総勘定元帳 | 科目別に繰越・期末残高・行ごとの running balance・相手科目            | `general_ledger` / `get_account_ledger` · viewer `/ledger` |
| 残高照会   | 指定日時点の科目残高 (正常残高方向に符号づけ)                         | `get_account_balance`                                      |

### 集計 (aggregation)

| 機能           | 概要                                                                           | 入口 (MCP / viewer)                       |
| -------------- | ------------------------------------------------------------------------------ | ----------------------------------------- |
| 合計残高試算表 | 各科目の借方計 / 貸方計 / 残高 + 合計 (借貸平均の検算)。累計 or 期間試算表     | `trial_balance` · viewer `/trial-balance` |
| 月次推移       | 1 科目の月次の増減と月末残高 (期首残高 + Σ期中増減 = 期末残高)                 | `monthly_trend` · viewer `/monthly-trend` |
| 精算表 (8 桁)  | 残高試算表 → 修正記入 → 損益計算書欄 / 貸借対照表欄 を 1 表に (決算過程の監査) | `worksheet` · viewer `/worksheet`         |

### 決算書 (financial statements)

| 機能                    | 概要                                                                                      | 入口 (MCP / viewer)                         |
| ----------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------- |
| 損益計算書 (P/L)        | 売上高 → 売上原価 (製造原価含む) → 売上総利益 → 販管費 → 営業利益 → 経常利益 → 当期純利益 | `profit_and_loss` · viewer `/pl`            |
| 貸借対照表 (B/S)        | 流動/固定資産・流動/固定負債・純資産を表示区分でロールアップ (資産 = 負債 + 純資産)       | `balance_sheet` · viewer `/bs`              |
| 青色申告決算書 (一般用) | 上記を青色申告決算書レイアウトに組み上げたプレビュー                                      | viewer `/statements` (`export_etax` の入力) |

### e-Tax 取込データ出力 (electronic filing)

| 機能                       | 概要                                                                                                          | 入口 (MCP / viewer)                                          |
| -------------------------- | ------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| e-Tax 取込データ生成       | 青色申告決算書を **実 e-Tax 交換ファイル `.xtx` (官式 KOA210 一般用)** で生成。`csv`/`xml` は人間確認用の補助 | `export_etax` · viewer `/etax` + `/etax/download`            |
| 申告前チェック (preflight) | 実データが申告可能か (申告可 / 要修正) を 1 コールで判定。任意で生成 `.xtx` を公式 .xsd で形式検証            | `etax_preflight`                                             |
| e-Tax ソフトへのハンドオフ | 生成 `.xtx` を e-Taxソフト(WEB版)へ取込 → 署名 → 送信する手順・チェックリスト・受入記録                       | → [docs/etax/handoff-runbook.md](../etax/handoff-runbook.md) |

> e-Tax の「使い方」(取込手順・署名・送信・スコープ境界・トラブルシュート) は
> [handoff-runbook](../etax/handoff-runbook.md) が SSOT。ここでは重複させずリンクで繋ぐ。

### 閲覧 (read-only viewer)

| 機能                    | 概要                                                                                                   | 入口                                                               |
| ----------------------- | ------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------ |
| Vercel read-only ビュー | 上記レポートを画面表示 + 印刷/PDF レイアウト。**入力 UI は無し**。数値は report 層 golden とバイト一致 | viewer (Next.js) · 画面一覧は [web/README.md](../../web/README.md) |

---

## 2. 使い方 (how to use)

### 2.1 MCP ツール一覧 (提供インターフェース)

`ai_books.server` が公開する MCP ツール。**読取 (read)** はデータの照会、**書込 (write)** は検証を
通した上での記帳。金額は常に**正確な Decimal を文字列で**返す (float にしない)。

| ツール                    | 種別  | 用途                                                         |
| ------------------------- | ----- | ------------------------------------------------------------ |
| `list_accounts`           | read  | 勘定科目一覧 (区分 / 表示区分 / 有効で絞り込み)              |
| `get_account`             | read  | 勘定科目コードで 1 件取得                                    |
| `search_accounts`         | read  | コード / 科目名の部分一致検索                                |
| `list_journal_entries`    | read  | 仕訳の一覧 (期間 / 科目 / 状態 / 摘要、ページング)           |
| `get_journal_entry`       | read  | 仕訳 1 件を明細つきで取得                                    |
| `get_account_balance`     | read  | 指定日時点の科目残高 (正常残高方向に符号づけ)                |
| `get_account_ledger`      | read  | 1 科目の総勘定元帳 (繰越 + running balance + 相手科目)       |
| `trial_balance`           | read  | 合計残高試算表 (借方計 / 貸方計 / 残高 + 借貸平均の検算)     |
| `monthly_trend`           | read  | 1 科目の月次推移 (会計年度名で指定、例 `FY2025`)             |
| `worksheet`               | read  | 精算表 (8 桁ワークシート、決算過程)                          |
| `profit_and_loss`         | read  | 損益計算書 (青色申告決算書の段階表示)                        |
| `balance_sheet`           | read  | 貸借対照表                                                   |
| `journal_book`            | read  | 仕訳帳 (青色申告 保存義務帳簿)                               |
| `general_ledger`          | read  | 総勘定元帳 (全科目 or 1 科目)                                |
| `export_etax`             | read  | 決算書を e-Tax 取込データ (`xtx` / `csv` / `xml`) に変換     |
| `etax_preflight`          | read  | 申告前チェック (申告可 / 要修正)＋任意の公式 .xsd 形式検証   |
| `create_journal_entry`    | write | 仕訳の作成 (借貸一致・科目 FK・桁数・期間をサーバー側で検証) |
| `update_journal_entry`    | write | 下書き仕訳の差し替え (`posted` は不可)                       |
| `post_journal_entry`      | write | 下書きを記帳確定 (`draft` → `posted`)                        |
| `void_journal_entry`      | write | 仕訳の取消 (理由を記録、行は残す)                            |
| `import_transactions_csv` | write | 銀行/カード CSV を下書き仕訳に取込                           |
| `hello`                   | read  | 死活確認用のスモークテスト                                   |

書込ツールはドメイン検証失敗時に、機械可読な JSON ペイロードを持つ `ToolError` を返す
(呼び出し側のエージェントが理由を解釈できる)。

### 2.2 MCP サーバーへの接続 (Claude Desktop 等)

MCP サーバーは stdio で起動する。Claude Desktop など MCP クライアントの設定例:

```jsonc
{
  "mcpServers": {
    "ai-books": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/ai-books",
        "run",
        "python",
        "-m",
        "ai_books.server",
      ],
      "env": {
        "AI_BOOKS_DB_URL": "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
      },
    },
  },
}
```

- `--directory` はクローンした `ai-books` のパスに合わせる。
- `AI_BOOKS_DB_URL` は接続先 Postgres。ローカルは `supabase start` の出力値
  ([README#local-postgres-supabase](../../README.md#local-postgres-supabase))。
- ターミナルで直接起動して動作確認するなら `uv run python -m ai_books.server`。

### 2.3 代表的な利用手順 (walkthroughs)

前提: マイグレーション適用済み + 標準科目シード済みの Postgres
([README#schema--migrations](../../README.md#schema--migrations) /
[README#seed-chart-of-accounts](../../README.md#seed-chart-of-accounts))。
以下は MCP クライアント上でエージェントに依頼する典型フロー。

**A. 帳簿を照会する (read のみ)**

1. `list_accounts` / `search_accounts` で科目コードを確認。
2. `journal_book` で仕訳帳、`general_ledger` (or `get_account_ledger`) で総勘定元帳を照会。
3. `trial_balance` で合計残高試算表、`get_account_balance` で残高を確認。

**B. 仕訳を記帳する (write)**

1. `create_journal_entry` で仕訳を作成 (借方合計 = 貸方合計でないとエラー)。
2. 必要なら `update_journal_entry` で下書きを修正。
3. `post_journal_entry` で記帳確定 (`draft` → `posted`)。
4. 誤りは消さず `void_journal_entry` で取消 (理由必須・訂正削除履歴に残る)。

**C. 明細 CSV から起票する**

1. `import_transactions_csv` に CSV と相手側の科目コード (例: 普通預金) を渡す。
   → 各行が**下書き**の 2 行仕訳になる (相手科目は摘要から推定、不明は仮払金/仮受金)。
2. `list_journal_entries(status="draft")` で内容を確認・必要なら修正。
3. 妥当なら `post_journal_entry` で確定。再取込しても重複しない。

**D. 決算書を出す**

1. 期末整理仕訳まで記帳確定したら `worksheet` で決算過程 (精算表) を確認。
2. `profit_and_loss` / `balance_sheet` で損益計算書・貸借対照表を取得。
3. viewer `/statements` で青色申告決算書レイアウトのプレビューを閲覧。

**E. e-Tax 取込データを出す**

1. `etax_preflight(fiscal_year="FY2025")` で申告前チェック。`status="ok"` なら申告可、
   `"error"` なら `errors[]` (未転記 draft / 会計期間外 / マッピング欠落 等) を直してから再実行。
   公式 .xsd で形式も確かめるなら `validate_xsd=True` (スキーマ未取得なら `xsd_result` は
   `skipped` + 取得手順を返すだけで、データ判定は通常どおり効く)。
2. `export_etax(fiscal_year="FY2025", format="xtx")` で実 e-Tax 交換ファイル `.xtx`
   (官式 KOA210 一般用) を生成。`csv` / `xml` は人間確認用の補助で e-Tax には取り込めない。
3. 必須項目・桁数・科目コード・月のスキーマ検証に通らないと `ToolError` が全件まとめて返る
   → 帳簿側を直して再生成。
4. **取込 → 署名 → 送信の手順**は [docs/etax/handoff-runbook.md](../etax/handoff-runbook.md)。
   生成物は事業者の確定数値 (秘密情報) を含むため**リポジトリにコミットしない**。

### 2.4 read-only viewer の起動・閲覧

ローカル Supabase に対して起動する (`supabase start` が動いている前提):

```bash
cd web
npm install
cp .env.example .env.local     # AI_BOOKS_DB_URL を supabase start の DB URL に
npm run dev                    # http://localhost:3000
```

画面一覧 (勘定科目一覧 / 試算表 / 月次推移 / 仕訳帳 / 総勘定元帳 / 損益計算書 / 貸借対照表 /
精算表 / 青色申告決算書 / e-Tax 取込データ) と Vercel デプロイ手順・read-only ロール (`viewer_ro`)
の設定は [web/README.md](../../web/README.md) を参照。viewer は **SELECT のみ**で、入力 UI は無い。

---

## 3. できること / できないこと (scope boundaries)

「何ができないか」を正直に書く。境界の一次情報は
[handoff-runbook のスコープ境界表](../etax/handoff-runbook.md#スコープ境界本プロジェクトの対象--対象外) と
[README#non-goals-forever](../../README.md#non-goals-forever)。

**できること (in scope)**

- 複式簿記の記帳: 勘定科目マスタ・仕訳 CRUD (借貸検証 + 取消) ・CSV 取込
- 帳簿・集計: 仕訳帳 / 総勘定元帳 / 合計残高試算表 / 月次推移 / 精算表
- 決算書: 損益計算書 / 貸借対照表 / 青色申告決算書 (一般用) のプレビュー
- e-Tax 取込データ生成: 実 `.xtx` (官式 KOA210 一般用) + `.xsd` 形式検証 (#79)
- 閲覧: Vercel 上の read-only 集計ビュー

**できないこと / 対象外 (out of scope)**

| 領域                                             | なぜ対象外か                                                            |
| ------------------------------------------------ | ----------------------------------------------------------------------- |
| 税額計算・確定申告書B 本体                       | 公式ツール / 下流ツールの担当 (本プロジェクトは決算書 + 取込データまで) |
| 電子署名の付与・受付システムへの送信             | e-Taxソフト(WEB版) + マイナンバーカードで実施 (自動化不可)              |
| 利用者識別番号 / 電子証明書の取得                | ユーザー側の e-Tax 利用開始手続 (本プロジェクト対象外)                  |
| 不動産所得用 (KOA220) / 農業所得用 (KOA240) 様式 | 未対応 (#83)。生成するのは一般用 KOA210 のみ                            |
| データ入力用の Web UI                            | 書込は MCP のみ。viewer は read-only (invariant #1)                     |
| マルチテナント SaaS / RLS                        | 単一ユーザー前提。Supabase は永続保管用であってマルチテナントではない   |

> ⚠️ 生成された会計データ・決算書・e-Tax 取込データは **税理士のレビューを推奨**。最終的な
> 申告内容の正しさはユーザーの責任。65 万円控除の要件充足もユーザー側の運用責任
> ([aoiro-65man-requirements](../etax/aoiro-65man-requirements.md))。

---

## 関連ドキュメント

- [ドキュメントハブ](../README.md) — 全文書の索引と SSOT マップ
- [README.md](../../README.md) — プロダクトの位置付け・Quick start・Roadmap・Non-goals
- [web/README.md](../../web/README.md) — viewer の画面一覧・ローカル/デプロイ手順
- [docs/etax/handoff-runbook.md](../etax/handoff-runbook.md) — e-Tax 取込・署名・送信の手順 (SSOT)
- [docs/etax/aoiro-65man-requirements.md](../etax/aoiro-65man-requirements.md) — 65 万円控除 / 優良電子帳簿の要件
- [AGENTS.md](../../AGENTS.md) — 開発規約・検証・不変条件の SSOT (開発参加者向け)
