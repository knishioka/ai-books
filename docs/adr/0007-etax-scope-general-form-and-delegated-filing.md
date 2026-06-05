# ADR 0007 — e-Tax スコープ: 一般用 KOA210 に集中・税額計算/署名/送信は公式ツールに委譲

- Status: Accepted
- Date: 2026-06-05
- Deciders: ai-books maintainers
- Relates to: #24/#84/#85 (e-Tax export), #83 (不動産/農業 様式), ADR [0001](./0001-pivot-to-supabase-and-vercel-viewer.md)
- Retroactively recorded: 2026-06-05 (#90). 初出はスコープ確定 #24、様式分割 #83。

## Context

青色申告決算書 / e-Tax 取込データ の生成は ADR 0001 で in-scope になった。だが「どこまでやるか」
には 2 つの線引きが要る:

1. **どの様式を実装するか。** 国税庁の決算書様式は複数ある —
   一般用 (KOA210, v11.0)、不動産所得用 (KOA220)、農業所得用 (KOA240)。全部を同時に正しく
   実装すると面が広がりすぎる。
2. **申告フローのどこまでを担うか。** 決算書を作ることと、税額を計算し・電子署名し・e-Tax に
   送信することは別物。後者は法的・セキュリティ的な責務が重い。

## Decision

### 1. 一般用 KOA210 に集中する。不動産/農業は #83 に繰り延べ

- 実装・golden・XSD 検証は **一般用 KOA210 (v11.0)** に集中する。`spec.py` が登録するのは KOA210
  のみ (field catalog には 3 様式 = 897 項目が載るが、コードが回すのは一般用)。
- **不動産所得用 (KOA220) / 農業所得用 (KOA240)** は別 Issue **#83** に繰り延べる。新しい様式/年度を
  足すときは**それぞれを独自の spec として登録**する (既存 spec を太らせない)。
- 一般用に無い項目は**意図的に未マッピング**とする。例: 損益の段階表示 (営業利益/営業外/経常利益) は
  KOA210(一般用) に居場所が無く、営業外収益・費用は 所得 (AMF00500) に net_income 経由で吸収される。

### 2. 税額計算・電子署名・送信は公式 e-Tax ツールに委譲する (ADR 0001 の線を維持)

- `ai-books` が作るのは **ledger と filing-ready な 決算書 / 取込データ** まで。
- **out-of-scope (ADR 0001 から継続):** 所得税/住民税の**税額計算**そのもの、控除最適化、電子署名、
  e-Tax への**送信**。これらは公式 e-Tax ソフト / WEB版に委譲する (引き継ぎ手順は
  [docs/etax/handoff-runbook.md](../etax/handoff-runbook.md))。

### Alternatives not taken

- **3 様式を一気に実装**: rejected — 検証面が 3 倍。最頻 (一般用) を完成させてから様式ごとに足す。
- **税額計算・送信まで内製**: rejected — 申告・署名・送信は法的/セキュリティ責務が重く、公式ツールが
  正。`ai-books` の責務は取込データまで (ADR 0001 の Non-goal を維持)。

## Consequences

### Positive

- スコープが明確: 「一般用の決算書 + 取込データまで」。検証も一般用に集中でき golden が締まる。
- 税額計算/送信を持たないことで、申告の正当性・署名鍵・送信の責務を負わない (リスク・法的面が軽い)。

### Negative / costs

- 不動産/農業所得の申告者は現状この repo だけでは完結しない (#83 待ち)。
- 利用者は最後の一手 (税額確定・署名・送信) を公式ツールで行う必要がある — 完全自動申告ではない。

### Neutral / unchanged

- 様式の中身 (synthetic→実 KOA210, .xsd 非コミット) は ADR
  [0006](./0006-etax-real-koa210-and-uncommitted-xsd.md) が governing。
- ADR 0001 の Non-goal (税額計算・申告は下流) を具体化したもので、矛盾しない。

## Implementation references

- `src/ai_books/etax/spec.py` — KOA210(一般用) のみ登録。営業外の未マッピングを docstring で明示。
- `docs/etax/README.md` — KOA210/KOA220/KOA240 の版数と「コードは一般用のみ」の対応。
- `docs/etax/handoff-runbook.md` — 税額確定・署名・送信を公式 e-Tax (WEB版) に渡す手順。
- ADR [0001](./0001-pivot-to-supabase-and-vercel-viewer.md) — 税額計算・申告を下流に置く Non-goal の初出。
