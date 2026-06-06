# ADR — Architecture Decision Records

> このリポジトリの**アーキテクチャ上の意思決定の SSOT**。
> 「なぜこうなっているか」を 1 決定 = 1 ファイルで残し、将来の AI/人間が
> **同じ論点を蒸し返さない**ようにする。決定の散在 (Issue/PR/会話) を一本化する場所。

docs ハブ ([docs/README.md](../README.md)) の「アーキテクチャ上の意思決定」欄の実体がここ。
README / AGENTS.md の該当箇所は ADR を**指すだけ**のポインタに徹する (内容を重複させない)。

---

## 索引 (Index)

| #                                                                    | タイトル                                                                               | Status   | 初出    |
| -------------------------------------------------------------------- | -------------------------------------------------------------------------------------- | -------- | ------- |
| [0001](./0001-pivot-to-supabase-and-vercel-viewer.md)                | Pivot to Supabase storage + Vercel read-only viewer                                    | Accepted | #9–#11  |
| [0002](./0002-journal-write-model-soft-void-and-sequence-voucher.md) | Journal write model — soft-void (`status=voided`) + SEQUENCE 伝票番号                  | Accepted | #35     |
| [0003](./0003-csv-import-draft-suspense-and-hash-dedup.md)           | Bank/CSV import — draft 起票・suspense 科目・`import_hash` 重複排除                    | Accepted | #38     |
| [0004](./0004-pooler-safe-db-client-default.md)                      | Pooler-safe DB client default (`prepare_threshold=None`)                               | Accepted | #52     |
| [0005](./0005-statement-category-on-accounts-base-table.md)          | `statement_category` を accounts 基本テーブルに置く                                    | Accepted | #10     |
| [0006](./0006-etax-real-koa210-and-uncommitted-xsd.md)               | e-Tax — synthetic→実 KOA210 様式・`.xsd` 非コミット (fetch+checksum)                   | Accepted | #76/#79 |
| [0007](./0007-etax-scope-general-form-and-delegated-filing.md)       | e-Tax スコープ — 一般用 KOA210 に集中・税額計算/署名/送信は委譲                        | Accepted | #24/#83 |
| [0008](./0008-remote-mcp-single-tenant-auth.md)                      | Remote MCP posture — Streamable HTTP・Supabase Auth (OAuth/JWT)・single-user allowlist | Accepted | #105    |

> ADR 0002–0007 は**遡及記録 (retro-ADR)**: 決定はそれぞれの初出 PR で既に確定・実装済み。
> 本 Issue (#90) で根拠を ADR として一本化した。各 ADR ヘッダの `Retroactively recorded` を参照。
>
> ADR 0008 は #105 で起票した**事前 ADR (forward-looking)**: remote 公開の posture を実装前に固定する。

---

## ADR とは / いつ書くか

ADR (Architecture Decision Record) は、**後から覆すのが高くつく/論点が再燃しやすい**
設計判断を、文脈・決定・理由・影響つきで 1 ファイルに固定する記録。

### ADR を起こす基準 (いつ書くか)

次のいずれかに当てはまる決定は ADR にする:

- **不変条件 (AGENTS.md "Architectural invariants") に触れる/それを変える** — 例: storage の選択、
  read-only ビューの方針、forward-only migration、ORM 不採用の境界。
- **後から覆すのが高コスト** — スキーマ設計 (適用後は forward-only)、外部 I/F の様式、採番方式。
- **「なぜ別の自然な選択肢にしなかったのか」を将来必ず聞かれる** — 例: 物理削除でなく soft-void、
  prepared statement を無効化する既定、著作物を同梱しない取得方式。
- **複数 Issue/PR にまたがる前提を確定する** — 下流が依存する語彙・契約。

**ADR にしないもの:** 局所的な実装詳細、命名の好み、1 PR で完結し再燃しない選択。
これらは PR 本文・コード comment・docstring で十分 (SSOT を増やさない)。

迷ったら: 「半年後の自分/別エージェントが同じ判断を**やり直したくなる**か？」が Yes なら ADR。

### 書き方

1. [template.md](./template.md) をコピーして `docs/adr/NNNN-kebab-title.md` を作る。
2. `NNNN` は上の索引で**次の連番** (ゼロ埋め4桁) を採る。一度採番したら再利用しない。
3. 本文スタイルは既存 ADR (0001〜) に合わせる: 英語の散文 + 会計/ドメイン用語は日本語のまま。
   実装に対応する `path` / 識別子 / 守りのテストを `Implementation references` に列挙する。
4. **索引 (この README) に 1 行追加**する (未登録の孤立 ADR を作らない)。
5. `./scripts/verify.sh` を壊さないこと (ADR は docs のみ。リンクが解決することを確認)。

---

## ステータスのライフサイクル

| Status         | 意味                                                                     |
| -------------- | ------------------------------------------------------------------------ |
| **Proposed**   | 提案中。レビュー/合意の前。                                              |
| **Accepted**   | 採択され、実装が従う (= 現行の正)。                                      |
| **Superseded** | 後続 ADR が覆した。ヘッダに `Superseded by NNNN` を書き、新 ADR を指す。 |
| **Deprecated** | もはや有効でないが代替 ADR がない (将来 Proposed で置換予定)。           |

**確定した ADR の本文は編集しない (immutable)。** 決定を覆すときは**新しい番号で ADR を追加**し、
旧 ADR の Status を `Superseded by NNNN` に更新する (Status 行の変更のみ許可)。
これは AGENTS.md invariant #3 (applied 済 migration は forward-only) と同じ規律 ——
**「過去の決定を書き換えず、前進で覆す」**を ADR にも適用したもの。

---

## 関連

- [docs/README.md](../README.md) — ドキュメントハブ (本 ADR 群の上位索引)
- [AGENTS.md](../../AGENTS.md) — 開発規約 SSOT / Architectural invariants (ADR が根拠を補う)
- [template.md](./template.md) — 新規 ADR の雛形
