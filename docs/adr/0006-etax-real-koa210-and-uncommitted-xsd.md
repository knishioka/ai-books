# ADR 0006 — e-Tax: synthetic→実 KOA210 様式・`.xsd` は非コミット (fetch + checksum)

- Status: Accepted
- Date: 2026-06-05
- Deciders: ai-books maintainers
- Relates to: #76 (spec spike), #79/#84/#85 (real KOA210 renderer), `src/ai_books/etax/`
- Retroactively recorded: 2026-06-05 (#90). 決定の初出は #76 → #84/#85。

## Context

e-Tax 取込データ export started (#24) against a **synthetic, 非公式・教育用** 決算書 layout — enough
to build mapping/validation/golden machinery without the real form. To produce a **filing-grade**
artifact, the output had to target the **official 所得税関係 XML 様式 KOA210 (青色申告決算書, 一般用,
v11.0)** and validate against the 国税庁 `.xsd`.

That collides with licensing: the 国税庁 publishes the XML 仕様 as Microsoft CAB archives (`.xlsx` /
`.xsd`) that are **著作物** — they cannot be redistributed in this repo. But the renderer must place
each 項目コード at its exact spot in the form's nested element tree, in XSD sequence order, or the
official schema rejects the file. We need the schema's structure without committing the schema.

## Decision

### 1. Target the real KOA210 form; isolate synthetic off the 年度 axis

- The `EtaxFormatSpec` for version key **`"2025"`** maps the snapshot onto the official KOA210
  (一般用, v11.0). The earlier **synthetic** layout is retained under a separate `"synthetic"`
  version key — so its machinery still runs but **cannot be mistaken for the real 様式** (年度 軸に
  混ざらない).
- `EtaxFormat.XTX` renders the **real e-Tax 交換ファイル形式** (the KOA210 XML tree), validatable
  against the official `.xsd`. `CSV`/`XML` remain 補助 (debug / 人間確認) serializations.
  実申告に渡すのは `XTX`。

### 2. The `.xsd`/CAB 著作物 are **not committed** — fetched on demand + checksum-verified

- `scripts/etax/fetch_etax_spec.py` downloads the CAB packages on demand and **verifies each against
  the SHA256** recorded in `docs/etax/manifest.json`; a mismatch fails loudly (the spec may have been
  re-published → update the manifest). The fetched/extracted artifacts (incl. `.xsd`) live under
  `.cache/`, which is **`.gitignore`d**.
- What **is** committed are **derived facts**, not the 著作物 raw:
  - `docs/etax/field_catalog.json` — field catalog (#76).
  - `src/ai_books/etax/koa210_layout.json` — the KOA210 element tree (names + nesting + order +
    repeat + 金額/否) extracted once from `KOA210-011.xsd` by `scripts/etax/build_etax_layout.py`
    (form-agnostic since #103 — also builds `koa220_layout.json` / `koa240_layout.json`). The renderer
    reads the committed layout at runtime; the `.xsd` itself is never in the tree.

### Alternatives not taken

- **Commit the `.xsd`/CAB for convenience**: rejected — they are 国税庁 著作物; redistribution is not
  permitted.
- **Hard-code the 314-element tree in Python**: rejected — brittle and unverifiable against the
  source; a derived, regenerable artifact keeps a checkable link to the official schema.
- **Keep emitting the synthetic layout as the real output**: rejected — cannot be submitted; risks
  being mistaken for the official form.

## Consequences

### Positive

- Output is filing-grade: validates against the official KOA210 `.xsd`.
- No 著作物 in the repo; only derived, regenerable facts — checksum-pinned to a known spec revision.
- Synthetic machinery survives for tests without contaminating the real 年度 axis.

### Negative / costs

- Producing/validating the real form requires a network fetch of the spec (CI does this in a
  dedicated `etax-xsd` job, #79). Offline builds rely on the committed derived artifacts only.
- A re-published 国税庁 spec breaks the checksum by design — someone must refresh
  `docs/etax/manifest.json` and regenerate the 様式 layouts (`koa210/koa220/koa240_layout.json`).

### Neutral / unchanged

- The scope boundary (一般用 only; tax calc/sign/transmit delegated) is governed separately by ADR
  [0007](./0007-etax-scope-general-form-and-delegated-filing.md).

## Implementation references

- `src/ai_books/etax/spec.py` — `"2025"` (real KOA210 v11.0) vs `"synthetic"` version keys.
- `src/ai_books/etax/export.py` — `EtaxFormat` (`XTX` = real 交換ファイル形式), `render_etax_xtx`.
- `src/ai_books/etax/koa210_layout.json` — committed derived element tree (KOA220/240 since #103).
- `scripts/etax/fetch_etax_spec.py` — on-demand fetch + SHA256 verify against `docs/etax/manifest.json`.
- `scripts/etax/build_etax_layout.py` — extracts a 様式 layout from its `.xsd` (KOA210/KOA220/KOA240).
- `.gitignore` — `.cache/` (国税庁 著作物 取得物は非コミット).
