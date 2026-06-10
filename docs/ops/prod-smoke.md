# 本番スモークチェック (prod-smoke) — #167

CI が green でも、デプロイ済みの Vercel viewer は **環境変数ミス / `viewer_ro` 権限ドリフト /
Supabase Auth 設定変更** で壊れうる。[`prod-smoke` workflow](../../.github/workflows/prod-smoke.yml)
が本番 URL を日次 (+ `workflow_dispatch`) で probe し、「本番が実際に動いていて、かつ未認証では
何も漏れない」を継続保証する。チェック本体はローカルでも実行できる
[`scripts/prod_smoke/check.sh`](../../scripts/prod_smoke/check.sh)。

## モード (`PROD_SMOKE_MODE` repo variable)

デプロイの運用形態に合わせて期待値を切り替える。**variable が消えた場合のデフォルトは `gated`**
(fail-closed — 「公開のつもりがないのに公開」側に倒して検知する)。

| モード   | 想定デプロイ                                                     | 期待値                                                                               |
| -------- | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `public` | 公開サンプルデモ (`AI_BOOKS_VIEWER_PUBLIC=true`, 合成データのみ) | 全レポートページが 200 + 見出し h1 + read-only フッター。`/login` 200。5xx なし      |
| `gated`  | single-user 認証ゲート (#108 / ADR-0008)                         | 保護ページが `/login` へリダイレクトし、本文にレポート見出しが漏れない。`/login` 200 |

公開デモをやめて認証ゲート運用に切り替えたら、`gh variable set PROD_SMOKE_MODE --body gated`
を実行するだけでよい (workflow / script の変更は不要)。

## 必要な設定 ([admin])

| 種別     | 名前                  | 値                                     |
| -------- | --------------------- | -------------------------------------- |
| secret   | `PROD_SMOKE_BASE_URL` | Vercel 本番 URL                        |
| variable | `PROD_SMOKE_MODE`     | `public` または `gated` (現在: public) |

認証込みチェック (gated 運用でサインインして閲覧できることの確認) は将来拡張。現状の gated
チェックは「未認証で漏れない」境界のみを見る。

## 失敗時

- 固定タイトル `ops(web): production smoke failed — 要調査` の issue が自動起票される
  (同タイトルの open issue があれば重複起票しない)。
- issue 本文に失敗したチェックの一覧と該当 run へのリンクが入る。レスポンス本文は
  **ログにも issue にも出さない** (gated 運用では実数値を含むため)。

## 手元での実行 / 訓練

```bash
# 手元から本番を probe (モードは明示)
BASE_URL=https://<prod-url> MODE=public bash scripts/prod_smoke/check.sh

# 起票経路の訓練 (issue は作らず内容だけ表示)
gh workflow run prod-smoke.yml -f simulate_failure=true -f dry_run=true
```
