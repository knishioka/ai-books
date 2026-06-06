# リモート公開 Runbook — MCP を Web に公開し Claude から接続する (#109)

> **このドキュメントの目的**
> ローカル stdio 専用だった MCP サーバーを **Web 上にリモート公開**し、**Claude (Web / Desktop / Code)**
> からリモートコネクタ経由で接続できる状態にするための、デプロイ・secret 投入・Supabase Auth 設定・
> コネクタ登録の手順 SSOT。設計判断は [ADR 0008](../adr/0008-remote-mcp-single-tenant-auth.md)、
> 実装は #106 (HTTP transport) / #107 (認証) / #108 (viewer 認証) でマージ済み。
>
> **シングルテナント前提は不変** (AGENTS.md invariant #3)。本書は「公開はするが利用者は自分 1 人・帳簿は 1 冊」
> の構成であり、マルチテナント化 (`tenant_id` / RLS) は導入しない。**認証 ≠ マルチテナント**。

- **参照日**: 2026-06-06
- **対象実装**: `AI_BOOKS_MCP_TRANSPORT=http` (Streamable HTTP) + Supabase Auth (OAuth/JWT) + single-user allowlist

---

## 0. スコープ境界 (本書の対象 / 対象外)

| 領域                                             | 担い手                             | 対象                                               |
| ------------------------------------------------ | ---------------------------------- | -------------------------------------------------- |
| HTTP transport / 認証ゲートの**コード**          | ai-books (#106/#107/#108 マージ済) | ✅ (実装済)                                        |
| MCP サーバーの**公開デプロイ + TLS**             | ユーザー (本書の手順)              | ✅ (手順提供)                                      |
| **secret 投入** (DB URL / Supabase / allowlist)  | ユーザー (本書の手順)              | ✅ (手順提供)                                      |
| **Supabase Auth 有効化 + アカウント作成**        | ユーザー (Supabase ダッシュボード) | ✅ (手順提供)                                      |
| Vercel viewer の **Supabase Auth 設定**          | ユーザー (本書の手順)              | ✅ (手順提供)                                      |
| **Claude リモートコネクタ登録 + OAuth ログイン** | ユーザー + Claude クライアント     | ✅ (手順提供)                                      |
| マルチテナント / RLS / 複数事業者                | —                                  | ❌ (恒久 non-goal)                                 |
| 税額計算・電子署名・e-Tax 送信                   | 公式ツール (別 runbook)            | ❌ ([handoff-runbook](../etax/handoff-runbook.md)) |

> ⚠️ **最後の「Claude から実際にツールが通る」確認は、ユーザー + 実 Supabase プロジェクト + 公開 URL が
> 必須**で自動化できない (本リポで #109 のみ)。実接続の受入結果は本書の
> [受入確認の記録](#5-受入確認の記録-manual-acceptance) に残す。

---

## 1. 全体像

```
            ┌──────────── Supabase Auth (IdP / OAuth・JWT) ────────────┐
            │  自分 1 人だけを許可 (allowlist: 自分の email / sub)        │
            └──────────────────────────────────────────────────────────┘
                 ▲ OAuth (JWT)                         ▲ OAuth (session)
   ┌─────────────┴──────────────┐        ┌─────────────┴──────────────┐
   │  Remote MCP (FastMCP/HTTP) │        │  Web viewer (Next.js/Vercel)│
   │  Claude → 認証後にツール    │        │  自分 → 認証後に read-only  │
   │  host:port + TLS (前段)     │        │  proxy.ts ゲート (fail closed)│
   └─────────────┬──────────────┘        └─────────────┬──────────────┘
        AI_BOOKS_DB_URL (書込可ロール)        AI_BOOKS_DB_URL (viewer_ro)
                 └──────────── 同じ Supabase / Postgres (帳簿 1 冊) ──────────┘
                          tenant_id なし / RLS なし (single-tenant)
```

- MCP サーバーの http 経路は **fail-closed**: 認証 (allowlist + `SUPABASE_URL` + `AI_BOOKS_MCP_BASE_URL`)
  が未設定だと**起動を拒否**する (`src/ai_books/server.py` `main()`)。未認証エンドポイントは決して開かない。
- stdio 経路は従来どおり**認証なしで動作** (ローカル専用)。本書の設定は http 経路にのみ効く。

---

## 2. 前提 (ユーザー側)

- [ ] **Supabase プロジェクト** (ADR 0001 のストレージと同一でよい)。Auth を使える状態。
- [ ] **公開デプロイ先**: TLS 終端付きで HTTP を公開できる基盤 (例: Google Cloud Run / Fly.io / TLS 付き VPS)。
- [ ] **Vercel プロジェクト** (既存の read-only viewer)。
- [ ] 自分の **owner email** (allowlist に入れる 1 件)。
- [ ] `AI_BOOKS_DB_URL`: MCP サーバー用は**書込可ロール**、viewer 用は**`viewer_ro`** (読取専用)。

---

## 3. 環境変数リファレンス (実装準拠)

実体は [.env.example](../../.env.example) / [web/.env.example](../../web/.env.example)。本番値は `.env` /
各基盤の secret に置き、**コミットしない** (AGENTS.md「Never touch」)。

### 3.1 MCP サーバー (`src/ai_books/server.py` / `src/ai_books/auth.py`)

| 変数                              | 必須            | 既定        | 説明                                                                      |
| --------------------------------- | --------------- | ----------- | ------------------------------------------------------------------------- |
| `AI_BOOKS_MCP_TRANSPORT`          | http 公開時     | `stdio`     | `http` で Streamable HTTP を開く                                          |
| `AI_BOOKS_MCP_HOST`               | -               | `127.0.0.1` | http の bind host。コンテナ公開時は `0.0.0.0`                             |
| `AI_BOOKS_MCP_PORT`               | -               | `8000`      | http の bind port (Cloud Run 等は基盤の `$PORT` に合わせる)               |
| `AI_BOOKS_MCP_AUTH_ALLOWLIST`     | **http で必須** | (なし)      | 許可する `sub` / email をカンマ/空白区切り。**設定の有無が認証を ON/OFF** |
| `SUPABASE_URL`                    | **http で必須** | (なし)      | JWT 検証の JWKS / issuer 元 (ADR 0001 と同じ値)                           |
| `AI_BOOKS_MCP_BASE_URL`           | **http で必須** | (なし)      | この MCP の公開 URL。OAuth クライアントに保護リソースとして広告           |
| `AI_BOOKS_MCP_AUTH_JWT_ALGORITHM` | -               | `ES256`     | `RS256` or `ES256`。Supabase の JWT 署名鍵に合わせる                      |
| `AI_BOOKS_DB_URL`                 | **必須**        | (なし)      | **書込可**の Postgres 接続文字列 (MCP は書込経路)                         |

> 起動時ガード: `AI_BOOKS_MCP_TRANSPORT=http` かつ allowlist 未設定 → `RuntimeError` で起動拒否。
> allowlist はあるが `SUPABASE_URL` / `AI_BOOKS_MCP_BASE_URL` が欠ける → 明示エラー。`SUPABASE_URL` が
> scheme 無し (`my-project.supabase.co`) でも fail-fast。**「設定漏れで素通り」は起きない設計**。

### 3.2 Web viewer (`web/`, Vercel — #108)

| 変数                            | 必須 | 説明                                                                                 |
| ------------------------------- | ---- | ------------------------------------------------------------------------------------ |
| `AI_BOOKS_DB_URL`               | 必須 | **`viewer_ro`** 読取専用ロール (書込不能を DB レベルで強制・多層防御)                |
| `NEXT_PUBLIC_SUPABASE_URL`      | 必須 | Supabase プロジェクト URL (公開・ブラウザに届く)                                     |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | 必須 | anon (publishable) key (公開・secret ではない)                                       |
| `AUTH_ALLOWED_EMAIL`            | 推奨 | 閲覧を許可する owner email 1 件。未設定なら「認証のみ」 (任意の Supabase ユーザー可) |

> viewer ゲート (`web/proxy.ts`, Next 16 proxy 規約) は **fail-closed**: Supabase Auth 未設定なら全保護ルートを
> `/login` にリダイレクトし、匿名でデータを出さない。`service_role` key はブラウザに**渡さない**。

---

## 4. 手順

### Part A — Supabase Auth を有効化し自分のアカウントを作る

1. Supabase ダッシュボード → **Authentication** で利用するサインイン方式を有効化
   (Email + Password / Magic Link、または OAuth プロバイダ)。
2. 自分の owner アカウントを 1 つ作成 (招待 or サインアップ)。**このメールを allowlist に使う**。
3. JWT 署名アルゴリズムを確認 (Project Settings → Auth / JWT)。`ES256` か `RS256` かを控え、
   MCP の `AI_BOOKS_MCP_AUTH_JWT_ALGORITHM` を合わせる (既定 `ES256`)。
4. `SUPABASE_URL` (= `https://<project>.supabase.co`) を控える。

### Part B — MCP サーバーを公開デプロイする

例として Cloud Run / Fly.io を想定 (TLS は基盤が edge で終端)。

1. コンテナ/サービスに以下の secret/env を投入:
   ```
   AI_BOOKS_MCP_TRANSPORT=http
   AI_BOOKS_MCP_HOST=0.0.0.0
   AI_BOOKS_MCP_PORT=<基盤が指定するport (例 Cloud Run は 8080)>
   AI_BOOKS_DB_URL=<書込可ロールの接続文字列>
   SUPABASE_URL=https://<project>.supabase.co
   AI_BOOKS_MCP_BASE_URL=https://<この MCP の公開 URL>
   AI_BOOKS_MCP_AUTH_ALLOWLIST=<自分の email または sub>
   AI_BOOKS_MCP_AUTH_JWT_ALGORITHM=<ES256 or RS256>
   ```
2. 起動コマンドは `uv run python -m ai_books.server` (= `main()`)。
3. デプロイ後、**TLS 付き公開 URL** が `AI_BOOKS_MCP_BASE_URL` と一致することを確認。
4. 起動ログで「http で listen している」こと、認証未設定なら**起動拒否**されることを確認
   (fail-closed の動作確認: allowlist を一時的に外すと `RuntimeError`)。

> 🔒 **`AI_BOOKS_MCP_HOST` を `0.0.0.0` にするのは TLS 終端の内側に限る**。基盤の edge が TLS を張らない
> 構成で素の `0.0.0.0:port` を晒さないこと。VPS 直なら nginx/caddy 等で TLS を前段に置く。

### Part C — Vercel viewer に認証を入れる

1. Vercel プロジェクト (Root = `web`) の Preview/Production env に:
   ```
   AI_BOOKS_DB_URL=<viewer_ro の接続文字列>
   NEXT_PUBLIC_SUPABASE_URL=https://<project>.supabase.co
   NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon key>
   AUTH_ALLOWED_EMAIL=<自分の email>
   ```
2. 再デプロイし、未ログインで任意の画面 → `/login` にリダイレクトされることを確認。
3. 自分のアカウントでログイン → 全画面が従来どおり表示されることを確認
   (許可外メールでログインすると拒否= fail closed)。

### Part D — Claude にリモートコネクタとして登録する

1. Claude (Web `claude.ai` / Desktop / Code) の**コネクタ追加 (カスタム/リモート MCP)** で、
   `AI_BOOKS_MCP_BASE_URL` を登録する。
2. Claude がこの MCP を **OAuth 保護リソース**として検出し、認可サーバー (Supabase) のログインに誘導する。
3. 自分の Supabase アカウントでログイン → トークン発行 → MCP がトークンを検証し、
   **allowlist の自分のみ通過**。
4. ツール一覧が見えること、`trial_balance` など読取ツール、続いて書込ツールが通ることを確認。
5. 書込後、`audit_logs.actor` に**認証済みユーザー** (token の email→sub) が入ることを確認 (#107)。

> Claude クライアントによってコネクタ追加の OAuth フロー対応に差がある。うまく繋がらない場合は §6 を参照。

---

## 5. 受入確認の記録 (manual acceptance)

#109 受け入れ条件「公開 URL に Claude のリモートコネクタを登録 → OAuth → ツール呼び出しが通る」「runbook 通りに
再セットアップできる」を、**実接続で 1 回**確認し結果を残す。

| 日付       | 基盤         | MCP 公開 URL | Claude クライアント | OAuth ログイン | ツール呼び出し | audit.actor | 備考 |
| ---------- | ------------ | ------------ | ------------------- | -------------- | -------------- | ----------- | ---- |
| YYYY-MM-DD | (Cloud Run…) |              | (Web/Desktop/Code)  | ⬜             | ⬜             | ⬜          |      |

> ⚠️ 実 Supabase プロジェクト・公開 URL・マイナンバー不要 (税送信とは別) だが、**実トークンでの疎通は
> 自動テストの対象外**。ここを埋めて初めて #109 はクローズ可能。

---

## 6. トラブルシュート

| 症状                                               | 原因 / 対処                                                                                             |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| http 起動が `RuntimeError` で落ちる                | fail-closed。`AI_BOOKS_MCP_AUTH_ALLOWLIST` / `SUPABASE_URL` / `AI_BOOKS_MCP_BASE_URL` の欠落を確認      |
| 有効ログインでも 401/403                           | allowlist の値 (email or `sub`) が token と一致しているか。JWT alg が Supabase の署名鍵と一致するか     |
| `SUPABASE_URL` 関連の起動エラー                    | scheme 付き (`https://…`) になっているか (scheme 無しは fail-fast)                                      |
| Claude がコネクタを追加できない / OAuth に進まない | `AI_BOOKS_MCP_BASE_URL` が公開 URL と一致し TLS で到達可能か。クライアントのリモート MCP/OAuth 対応可否 |
| viewer が全部 `/login` に飛ぶ                      | `NEXT_PUBLIC_SUPABASE_*` 未設定 (fail-closed)。Vercel env を確認                                        |
| viewer でログインできるのに表示されない            | `AUTH_ALLOWED_EMAIL` と実ログイン email の不一致                                                        |

---

## 7. セキュリティ注意

- **public 公開＝認証強度がそのまま帳簿の安全性**。allowlist と Supabase Auth の設定をケチらない。
- 書込経路を晒しても **invariant #2 (サーバー側検証)** は不変 → 不正仕訳は DB に直接入らない。だが
  「正当に認証された自分」は書けるので、認証の堅牢性が要。
- viewer は認証 (閲覧者の限定) と `viewer_ro` (書込不能) の**二層**。どちらも外さない。
- secret はすべて env / 基盤 secret 経由。`service_role` key はブラウザに出さない。`.env` は gitignore。

---

## 参照

- [ADR 0008 — Remote MCP posture (single-tenant, Supabase Auth)](../adr/0008-remote-mcp-single-tenant-auth.md)
- [.env.example](../../.env.example) / [web/.env.example](../../web/.env.example) — 変数の正
- 実装: `src/ai_books/server.py` (transport/起動ガード), `src/ai_books/auth.py` (provider/allowlist), `web/proxy.ts` (viewer ゲート)
- 関連 runbook: [e-Tax handoff-runbook](../etax/handoff-runbook.md)
