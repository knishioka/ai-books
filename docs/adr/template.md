# ADR NNNN — <短い決定の名前 (kebab でファイル名にもなる)>

<!--
このファイルをコピーして `docs/adr/NNNN-kebab-title.md` を作る。
NNNN は `docs/adr/README.md` の索引で次の連番を確認して採る (ゼロ埋め4桁)。
書き終えたら索引 (docs/adr/README.md) に 1 行追加する (未登録の孤立 ADR を作らない)。
記述スタイルは既存 ADR (0001〜) に合わせる: 英語の散文 + 会計/ドメイン用語は日本語のまま。
-->

- Status: Proposed <!-- Proposed → Accepted → (後に) Superseded by NNNN / Deprecated -->
- Date: YYYY-MM-DD <!-- 決定が確定した日。遡及記録なら下に Retroactively recorded を併記 -->
- Deciders: <意思決定者 (例: ai-books maintainers / 人間レビュー点)>
- Relates to: <#issue / #PR / 関連 ADR>
<!-- 遡及記録の場合は次の行を足す:
- Retroactively recorded: YYYY-MM-DD (#90). 決定の初出は <#PR>。 -->

## Context

なぜこの決定が必要だったか。背景・制約・検討時点で分かっていた事実を書く。
「どの不変条件 (AGENTS.md) と関係するか」「何がトレードオフだったか」を明示する。
将来の読み手が**同じ論点を蒸し返さない**ために必要な前提をここに残す。

## Decision

何を決めたか。実装に対応する具体的な選択 (テーブル/カラム/関数/既定値など) を、
コードの該当箇所 (`path:line` または `path` 内の識別子) と対応づけて書く。
代替案を検討した場合は「採らなかった案」と却下理由も短く残す。

## Consequences

### Positive

- この決定で得られた利点。

### Negative / costs

- 受け入れたコスト・制約・運用上の負担。

### Neutral / unchanged

- 変わらないこと (誤解されやすい「これは変えていない」を明示)。

## Implementation references

- `path/to/file.py` — 役割 (例: 既定値の定義 / 検証ロジック)
- `supabase/migrations/NNNN_*.sql` — スキーマ上の対応
- 守りのテスト (退行検知): `tests/test_*.py`
