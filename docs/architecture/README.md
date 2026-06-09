# アーキテクチャ地図 — ai-books

> **AI/開発者が短時間で全体像を掴み、安全に開発を続けられる**ための地図。
> 「どこに何があり・何が機械保証されているか」を一望する。
>
> 規約・検証・触ってはいけない領域・不変条件の **SSOT は [AGENTS.md](../../AGENTS.md)**。
> 本書は AGENTS.md を**重複させず要約 + 参照**でつなぐ (矛盾したら AGENTS.md が正)。
> 文書全体の索引は [docs/README.md](../README.md) (ハブ)。

`ai-books` は **AI-first な会計 MCP サーバー**。書込/検証は MCP tool のみ、保管は
Supabase (Postgres)、閲覧は Vercel 上の **read-only ビューア**。ゴールは
**青色申告決算書 + e-Tax 取込データの出力**まで (税額計算は下流)。

- [1. モジュール地図 (責務と依存方向)](#1-モジュール地図-責務と依存方向)
- [2. データモデル / 不変条件](#2-データモデル--不変条件)
- [3. テスト保証インベントリ (能力 → 保証するテスト/CI)](#3-テスト保証インベントリ-能力--保証するテストci)
- [4. 新機能の足し方 (How to add)](#4-新機能の足し方-how-to-add)

---

## 1. モジュール地図 (責務と依存方向)

3 つの実行面がある。**Python (書込/検証/レポート計算)** · **Postgres (システムオブレコード)** ·
**web (read-only 閲覧)**。Python と web は同じ会計ロジックを **2 実装**で持ち、数値一致を
golden で機械保証する ([§3](#3-テスト保証インベントリ-能力--保証するテストci))。

```
            ┌──────────────────────── 書込/検証経路 (MCP のみ) ────────────────────────┐
 AI/CLI ──▶ server.py (FastMCP, 21 tools)
              │
              ├─▶ services/ ──── 書込時バリデーション (借貸/Decimal/FK/採番/監査)
              │     │
              │     ├─▶ db/repository.py ── 唯一 SQL が models に出会う層
              │     │       │
              │     │       └─▶ db/__init__.py ── 接続/トランザクション (prepare 無効)
              │     └─▶ audit.py ── append-only 監査記録
              │
              ├─▶ reports/ ──── スナップショット/CSV/text 整形 (read-side)
              └─▶ etax/  ──────  決算書 snapshot → e-Tax 様式 (KOA210) 変換/検証/描画
                    │
   models/ ◀───────┴── 純粋ドメイン型 (db/services に依存しない。全層の底)
   ledger.py / aggregation.py ── 純粋会計演算 (SQL/IO なし。単体テスト可能)

            ┌──────────────────────── 閲覧経路 (SELECT 専用) ─────────────────────────┐
 ブラウザ ─▶ web/proxy.ts (auth gate) ─▶ web/app/*/page.tsx ─▶ web/lib/reports/*
                       │                         │                    │
                       ├─ web/lib/auth/*         │                    ├─ context.ts/sql.ts/ledger.ts
                       └─ web/lib/supabase/middleware.ts              │
                                                                      ▼
                                                          web/lib/db.ts (prepare:false, max:5)
                                                                      │
                                                                      └─▶ Postgres (viewer_ro ロール)
                                          (Python と同じ符号規則を TS で再実装)

 Postgres ◀── supabase/migrations/*.sql (forward-only) ── 全層が読む保管層
```

### 1.1 Python (`src/ai_books/`)

下から上へ依存。**`models/` は最下層で db/services に依存しない**。`server.py` が全層を束ねる。

| レイヤ             | ファイル                 | 責務                                                                                                                                                                                   |
| ------------------ | ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **エントリ**       | `server.py`              | FastMCP サーバー。`@mcp.tool` で勘定科目/仕訳/集計/レポート/e-Tax/書込の **21 tool** を登録。金額は常に `Decimal` を文字列で授受                                                       |
| **サービス**       | `services/journal.py`    | 仕訳書込: `create/update/post/void`。Pydantic 検証→科目解決→期間制約→監査記録を 1 トランザクションで                                                                                   |
|                    | `services/csv_import.py` | CSV→下書き仕訳。`plan_import()` は純粋、`CsvImportService` が `import_hash` で重複排除し永続化                                                                                         |
| **データアクセス** | `db/repository.py`       | 生 SQL + psycopg の型付きラッパ。`Account/Journal/Ledger/FiscalYear` repository。読みは既定で voided 除外                                                                              |
|                    | `db/__init__.py`         | `connect()`/`transaction()`/`ping()`。pooler 互換のため prepared statement 無効                                                                                                        |
|                    | `db/migrate.py`          | forward-only マイグレーション実行 (`*.sql` をファイル名順、`schema_migrations` に記録)                                                                                                 |
|                    | `db/schema_snapshot.py`  | 確定スキーマを introspection で再生成し golden ドリフト検出                                                                                                                            |
| **純粋ロジック**   | `ledger.py`              | 符号規則 (`signed_delta`/`balance_from_totals`/`build_ledger_rows`)。SQL なし                                                                                                          |
|                    | `aggregation.py`         | 集計規則 (試算表/月次推移/精算表/P&L/B&S を型付き結果へ)。SQL なし                                                                                                                     |
|                    | `audit.py`               | `audit_logs` へ 1 行 append (不変条件 #5)                                                                                                                                              |
|                    | `errors.py`              | 例外階層。全て `.to_dict()` で機械可読 MCP エラーへ                                                                                                                                    |
| **ドメイン型**     | `models/` (14 ファイル)  | Pydantic スキーマ。`enums`(勘定区分/正常残高/状態/表示区分) · `journal`(借貸/Decimal/採番 validator) · `account` · `report`/`statement`/`worksheet`/`financial_statements`/`etax` ほか |
| **read-side 整形** | `reports/format.py`      | `money()`(Decimal→2桁文字列) + 仕訳帳/総勘定元帳/P&L/B&S/精算表/決算書の snapshot/CSV/text。byte-stable                                                                                |
| **e-Tax**          | `etax/spec.py`           | バージョン別データ駆動 様式仕様。`ETAX_FORMAT_SPECS["2025"]` = 実 様式 **KOA210 一般用 v11.0**                                                                                         |
|                    | `etax/export.py`         | `build_etax_export()`(検証) / `render_etax()`(CSV/XML/**XTX**)。整数円以外は検証エラー                                                                                                 |
| **参照データ**     | `seed/accounts.py`       | 個人事業主/青色申告の標準勘定科目。`seed_accounts()` は冪等 (`ON CONFLICT DO NOTHING`)                                                                                                 |

**依存方向の不変則:** `db/repository.py` だけが SQL と models を接続する · `services/` が全書込
検証を担う (不変条件 #2) · `models/` は `db/`/`services/` を import しない · `ledger.py`/`aggregation.py`
は SQL/IO を持たない。

### 1.2 web (`web/`) — read-only ビューア

Next.js (App Router)。**書込 UI を持たない** (不変条件 #1)。`web/proxy.ts` が Supabase Auth と
single-user allowlist の認証ゲートを担い、検証済み owner email だけを request header として root
layout へ渡す。各 page は `loadReport()` 経由で `web/lib/reports/*` の純粋関数を呼び、Python
レポート層と**同じ符号規則**を TS で再実装する。

| 層                  | パス                                                                                            | 責務                                                                                                                            |
| ------------------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| **認証ゲート**      | `web/proxy.ts` · `web/lib/auth/{allowlist,env,request-context}.ts` · `web/lib/supabase/middleware.ts` | Supabase Auth session 更新 + single-user allowlist。fail-closed で `/login` へ誘導し、検証済み email だけを layout 用 header に転送 |
| **ページ**          | `web/app/{bs,pl,statements,trial-balance,ledger,journal,monthly-trend,worksheet,etax}/page.tsx` | 各帳票の描画。`web/app/etax/download/route.ts` は e-Tax ダウンロード POST                                                       |
| **データ計算**      | `web/lib/reports/*.ts`                                                                          | `fetch{BalanceSheet,ProfitAndLoss,TrialBalance,...}`。`context.ts`(FY解決 + `unstable_cache`, 60s revalidate) `ledger.ts`(符号) `sql.ts`(voided filter) `month.ts` |
| **e-Tax**           | `web/lib/etax/{spec,export}.ts`                                                                 | Python `etax/` の TS ミラー。`buildEtaxExport()` + CSV/XML 描画                                                                 |
| **DB ゲートウェイ** | `web/lib/db.ts`                                                                                 | read-only Postgres クライアント。**`prepare: false`** + **`max:5`** の小規模 pool (pgbouncer transaction mode 安全)             |
| **整形/UI**         | `web/lib/{format,money,routes}.ts` · `web/components/*`                                         | 金額整形 · ナビ · 表コンポーネント                                                                                              |

### 1.3 scripts / supabase

| パス                                 | 責務                                                                                                                            |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| `scripts/verify.sh`                  | 検証エントリポイント (lint/format/typecheck/e-Tax layout sync/test)。`--json` で構造化出力、`--web` で web lint/typecheck/unit test も実行 → [§AGENTS#verification](../../AGENTS.md#verification) |
| `scripts/test.sh`                    | 実 Postgres で全テスト。`--web`/`--pooler`/`--all`/`--down` ([§3.3](#33-scriptstestsh---all-とブロック対応))                    |
| `scripts/check_coverage.py`          | `coverage.json` から line/branch を**個別に**ゲート (単一 `--cov-fail-under` では混合値しか見えない)                            |
| `scripts/seed_verify_db.py`          | golden クロスチェック用に FY2025 fixture を migrate + seed                                                                      |
| `scripts/etax/*.py`                  | 国税庁 公式仕様の取得 (`fetch_etax_spec.py`)・field catalog / KOA2x0 layout 生成・web 用 layout 生成物同期 (CI/手動の build 補助) |
| `supabase/migrations/*.sql`          | forward-only スキーマ (勘定科目→仕訳→会計期間→監査→索引→書込制約→CSV取込)。**applied 済は編集禁止**                             |
| `supabase/roles/viewer_readonly.sql` | 本番ビューア用 `viewer_ro` (SELECT 専用、将来テーブルにも DEFAULT PRIVILEGES)。`tests/fixtures/readonly.py` から生成            |

---

## 2. データモデル / 不変条件

不変条件の **SSOT は [AGENTS.md#architectural-invariants](../../AGENTS.md#architectural-invariants)**。
ここでは「どのコードで効いているか」の**地図**だけを示す (定義は AGENTS.md が正)。

| 不変条件                          | 何を守るか                                    | 効いている場所 / 機械保証                                                                                       |
| --------------------------------- | --------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| **借貸バランス**                  | 1 伝票の借方合計 = 貸方合計                   | `models/journal.py` validator → `services/journal.py` 入口。`test_journal_write` / `test_property_invariants`   |
| **Decimal 精度**                  | 金額は `numeric(18,2)`、float 不使用          | `models/journal.py` · `reports/format.py:money()` · 列定義 `journal_lines.amount`。`test_models` / property     |
| **append-only 監査**              | `audit_logs` を削除/上書きしない (#5)         | `audit.py` (追記のみ) + DB トリガ。`test_audit`                                                                 |
| **status=voided (取消)**          | 削除でなく `voided` で論理取消、監査に痕跡    | `models/enums.py:EntryStatus` · `services/journal.py:void_entry` · 読みは既定 voided 除外。`test_journal_write` |
| **SEQUENCE 採番**                 | 伝票番号の一意・連番 (並行安全)               | `db/repository.py` (JournalRepository) + Postgres SEQUENCE。`test_journal_write` の並行採番テスト               |
| **statement_category (表示区分)** | 勘定科目→決算書の表示位置                     | `models/enums.py:StatementCategory` · `seed/accounts.py:validate_chart()`。`test_seed`                          |
| **KOA210 様式マッピング**         | 決算書 snapshot → e-Tax 項目 (整数円)         | `etax/spec.py:ETAX_FORMAT_SPECS["2025"]` · `etax/export.py`。`test_etax` / `test_etax_xtx` (XSD)                |
| **read-only viewer のみ (#1)**    | 閲覧は SELECT 専用、書込 UI 無し              | `web/lib/db.ts` · `supabase/roles/viewer_readonly.sql`。`test_readonly_db` / `test_readonly_role`               |
| **forward-only migration (#3)**   | applied 済 migration 不変、新規ファイルで前進 | `db/migrate.py` · `supabase/migrations/`。`test_schema_snapshot` (DDL ドリフト検出)                             |
| **No ORM (#4)**                   | 生 SQL + psycopg のみ                         | `db/repository.py` (ORM 不使用)                                                                                 |

> 借貸の**符号規則**は Python (`ledger.py`) と web (`web/lib/reports/ledger.ts`) で**二重実装**され、
> golden クロスチェックで数値一致を機械保証する ([§3.1](#31-能力--保証マッピング))。

---

## 3. テスト保証インベントリ (能力 → 保証するテスト/CI)

「**何が保証されるか**」を起点に、それを担保する**テスト**と**CI ジョブ**を引けるようにする。
テスト一覧は `ls tests/*.py`、CI は [.github/workflows/ci.yml](../../.github/workflows/ci.yml)。

### 3.1 能力 → 保証マッピング

| 能力 / 保証                            | テスト (`tests/` ほか)                                                                      | CI ジョブ                   | ローカル                              |
| -------------------------------------- | ------------------------------------------------------------------------------------------- | --------------------------- | ------------------------------------- |
| **数値一致 (golden)**                  | `test_seed_fy_db.py` · `test_reports.py` (整形) · `test_aggregation_db.py`                  | `verify`                    | `test.sh`                             |
| **web ビューア golden クロスチェック** | `web/scripts/verify-golden.ts` (Python golden と byte 一致)                                 | `web-golden`                | `test.sh --web`                       |
| **MCP 経路 (client)**                  | `test_mcp_client.py` (`Client.call_tool` 往復)                                              | `verify`                    | `test.sh`                             |
| **MCP contract (schema/error)**        | `test_mcp_contract.py` · `test_server_stdio.py` (stdio 起動)                                | `verify`                    | `verify.sh`                           |
| **pooler 安全 (pgbouncer tx mode)**    | `test_pooler_db.py` (prepared statement 再有効化退行を検出)                                 | `pooler`                    | `test.sh --pooler`                    |
| **read-only ロール**                   | `test_readonly_db.py` (read 全成功/write 全拒否) · `test_readonly_role.py` (grant ドリフト) | `verify`                    | `test.sh -k readonly`                 |
| **property 不変条件**                  | `test_property_invariants.py` / `_db.py` (Hypothesis: 借貸平均/連続性/精度)                 | `verify`                    | `verify.sh` / `test.sh`               |
| **スキーマドリフト**                   | `test_schema_snapshot.py` (確定 DDL = golden)                                               | `verify`                    | `test.sh`                             |
| **e-Tax 形式 (XSD)**                   | `test_etax_xtx.py` + `tests/etax_xsd.py` (公式 .xsd で .xtx 検証)                           | `etax-xsd` (CI 専用)        | (公式 .xsd は非同梱)                  |
| **e-Tax マッピング**                   | `test_etax.py` (必須/桁/コード/月 検証, CSV/XML)                                            | `verify`                    | `verify.sh`                           |
| **CSV 取込**                           | `test_csv_import.py` (形式判定/重複排除/符号)                                               | `verify`                    | `verify.sh`                           |
| **書込ライフサイクル**                 | `test_journal_write.py` (不均衡/無効科目 reject, 取消監査, 並行採番)                        | `verify`                    | `test.sh`                             |
| **append-only 監査**                   | `test_audit.py` (update/delete reject)                                                      | `verify`                    | `test.sh`                             |
| **migration 適用**                     | `test_migrate.py` · `test_db.py` (接続) · `test_seed_db.py`                                 | `verify`                    | `test.sh`                             |
| **カバレッジ閾値 (line 80/branch 70)** | `scripts/check_coverage.py` (Python) · `web/vitest.config.ts` thresholds (web)              | `verify` · `web` · `pooler` | `test.sh` (DB あり時のみゲート)       |
| **lint/format/typecheck**              | ruff · mypy (strict)                                                                        | `pre-commit`                | `verify.sh`                           |
| **秘密情報スキャン**                   | gitleaks (履歴全体)                                                                         | `gitleaks`                  | `pre-commit run gitleaks --all-files` |

> **DB 連携テスト (約半数) は `AI_BOOKS_DB_URL` 未設定で skip** される。`verify.sh` は skip でも
> green (カバレッジは計測のみ・ゲートなし)。全保証はローカルでは `test.sh --all` が再現する。

### 3.2 CI ジョブ (8 個) と保証範囲

| ジョブ              | 実行                                                               | 保証                                             |
| ------------------- | ------------------------------------------------------------------ | ------------------------------------------------ |
| `verify`            | `verify.sh` (matrix 3.12/3.13) + DB 連携 pytest + カバレッジゲート | Python 全テスト pass + e-Tax layout sync + line≥80/branch≥70 |
| `web`               | lint/typecheck + `npm run test:coverage` + build                   | web ユニット層 (vitest) + v8 カバレッジゲート    |
| `web-vercel-build`  | `web/` だけを隔離して `npm ci && npm run build`                    | Vercel Root=web で repo-root 参照なし            |
| `web-golden`        | Postgres + seed + `npm run verify:golden`                          | ビューア数値 = Python golden (byte 一致)         |
| `pooler`            | Postgres + pgbouncer(tx) + `test_pooler_db.py` + golden 越し       | prepared statement 無し、pooler 越し golden 一致 |
| `etax-xsd`          | 公式 .xsd 取得 + `test_etax_xtx.py`                                | .xtx が公式 XSD 検証を通過                       |
| `pre-commit`        | `pre-commit run --all-files` (ruff/mypy/hygiene)                   | 静的検査 pass                                    |
| `gitleaks`          | `gitleaks detect` (履歴全体)                                       | 秘密情報の混入なし                               |

### 3.3 `./scripts/test.sh --all` とブロック対応

**`./scripts/test.sh --all` が唯一の『全部ローカルで動く』確認** (#59)。Postgres + pgbouncer を
1 回立ち上げ、全ブロックをまとめて 1 回走らせ最後に PASS/FAIL サマリを出す。各ブロックは CI
ジョブと 1:1 対応する (詳細・由来 Issue は [AGENTS.md#verification](../../AGENTS.md#verification) が正)。

| `test.sh --all` ブロック                 | 対応 CI ジョブ       |
| ---------------------------------------- | -------------------- |
| Python full suite + coverage gate (直結) | `verify`             |
| Web unit layer + coverage gate (vitest)  | `web`                |
| Web Vercel parity build                  | `web-vercel-build`   |
| e-Tax layout sync check                  | `verify` / local     |
| Pooler safety + golden (pgbouncer 越し)  | `pooler`             |
| Viewer golden cross-check (直結)         | `web-golden`         |

`etax-xsd` (公式 .xsd 非同梱) と `pre-commit`/`gitleaks` (`verify.sh` で担保) は CI 専用。
両者を合わせてローカルが CI 全ジョブを網羅する。

---

## 4. 新機能の足し方 (How to add)

代表的な 4 パターンの**標準手順とテストの置き場所**。各々 `./scripts/verify.sh` を壊さないこと、
追加した保証は [§3](#3-テスト保証インベントリ-能力--保証するテストci) の表に行を足すことが完了条件。
スラッシュコマンド scaffold ([#92](https://github.com/knishioka/ai-books/issues/92)) はこの手順を雛形化する。

### 4.1 新しいマイグレーション (DB スキーマ変更)

1. `supabase/migrations/` に**新規** `YYYYMMDDNNNNNN_<topic>.sql` を追加 (**applied 済は編集禁止**, 不変条件 #3)。
2. `uv run python -m ai_books.db.migrate` で適用。`models/` に対応する型があれば追従。
3. 確定スキーマ golden を更新: `uv run python -m ai_books.db.schema_snapshot --update`
   (意図的な DDL 変更時のみ)。`test_schema_snapshot.py` がドリフトを検出する。
4. read-only ロールに新テーブルが入る場合、`tests/test_readonly_db.py` の write 全拒否が
   自動でカバー (DEFAULT PRIVILEGES)。grant 集合を変えたら `viewer_readonly.sql` を再生成。
5. **テスト:** `tests/test_migrate.py` / 影響レポートの `*_db.py`。

### 4.2 新しい MCP ツール

1. `src/ai_books/server.py` に `@mcp.tool` 関数を追加。入力は `models/` の Pydantic スキーマで
   受け、検証は**サーバー側で絶対** (不変条件 #2)。書込なら `services/` を経由し、生 SQL を
   server.py に書かない。
2. 金額は `Decimal` を文字列で授受。エラーは `errors.py` の例外 → `ToolError` (JSON payload)。
3. **テスト:** `tests/test_mcp_contract.py` (schema/error 契約) + `tests/test_mcp_client.py`
   (`Client.call_tool` 往復) + 振る舞いの `tests/test_*.py`。stdio 起動は `test_server_stdio.py` が担保。

### 4.3 新しいレポート + golden

1. 集計規則は `aggregation.py` / 符号は `ledger.py` (純粋層) に置き、SQL は `db/repository.py` の
   `LedgerRepository` に。整形は `reports/format.py` (snapshot/CSV/text, `money()` 経由で byte-stable)。
2. golden を追加/更新: `uv run python -m tests.fixtures.seed_fy --update` →
   `tests/fixtures/seed_fy/golden/<report>.json`。
3. **web に同じレポートを出すなら** `web/lib/reports/<report>.ts` に同じ符号規則で再実装し、
   `web/scripts/verify-golden.ts` に追加して数値一致を機械保証する (`web-golden` ジョブ)。
4. **テスト:** `tests/test_reports.py` (整形) · `tests/test_aggregation_db.py` (DB 集計) ·
   web は `web/lib/reports/*.test.ts` (vitest)。

### 4.4 e-Tax 様式の追補

1. `src/ai_books/etax/spec.py` に新バージョンの `EtaxFormatSpec` を定義し `ETAX_FORMAT_SPECS` に
   登録 (年度キー、例 `"2025"` = KOA210 一般用 v11.0)。エンジン (`export.py`) と CSV/XML/XTX
   描画は**変更しない** — 仕様はデータ駆動。
2. 金額は整数円 (端数 sen は検証エラー)。公式仕様の取得/カタログ化は `scripts/etax/*.py`。
3. **web** に出すなら `web/lib/etax/spec.ts` を同期。
4. **テスト:** `tests/test_etax.py` (マッピング検証) + `tests/test_etax_xtx.py` (.xtx 構造 ·
   CI `etax-xsd` で公式 XSD 検証) + web `web/lib/etax/*.test.ts`。

---

## 関連

- 規約・不変条件・検証の SSOT: [AGENTS.md](../../AGENTS.md)
- 文書索引: [docs/README.md](../README.md)
- pivot の意思決定: [docs/adr/0001-pivot-to-supabase-and-vercel-viewer.md](../adr/0001-pivot-to-supabase-and-vercel-viewer.md)
- seed / golden の設計意図: [tests/fixtures/seed_fy/README.md](../../tests/fixtures/seed_fy/README.md)
- e-Tax 仕様: [docs/etax/README.md](../etax/README.md)
