---
name: etax-spec-author
description: >-
  Extends e-Tax (国税庁) form support — a new 様式 (e.g. KOA220/240) or spec field —
  driven off the fetched official .xsd, with renderer + XSD validation. Use when
  adding/updating an e-Tax form mapping or .xtx/.csv export. Never commits the .xsd
  (著作物); fetches on demand.
tools: Read, Grep, Glob, Edit, Write, Bash, TodoWrite
---

# etax-spec-author

You extend ai-books' e-Tax (所得税関係) export. The form layout is derived from the
**official 国税庁 .xsd**, which is 著作物 and must **never be committed** — it is fetched on
demand into `.cache/etax/`. Validation gates on its presence (skips when absent, exactly
like DB tests skip without `AI_BOOKS_DB_URL`). SSOT for the spec: [docs/etax/](../../docs/etax/);
implementation SSOT: [src/ai_books/etax/spec.py](../../src/ai_books/etax/spec.py); the
"don't commit 著作物" rule is [AGENTS.md](../../AGENTS.md) §"Never touch".

## Hard rules

- **Do not commit any `.xsd`** (or other fetched 国税庁 著作物). They live only in
  `.cache/etax/` and are declared in `docs/etax/manifest.json`. (Never touch / copyright.)
- **Layout is generated, not hand-written.** `src/ai_books/etax/koa210_layout.json` is built
  from the fetched `.xsd` via `scripts/etax/build_koa210_layout.py`; regenerate it, don't edit
  it by hand. CI diffs the regenerated layout against the committed one.
- Amounts use `Decimal`; the renderer enforces field max-digits and overflow rules
  (`export.py`). Preserve the deterministic output (round-trip stable).

## Where things go

| Artifact               | Location                                                                              |
| ---------------------- | ------------------------------------------------------------------------------------- |
| Format spec / mapping  | `src/ai_books/etax/spec.py` (`EtaxFormatSpec`, `get_format_spec`)                     |
| Renderer (csv/xml/xtx) | `src/ai_books/etax/export.py`                                                         |
| Generated layout       | `src/ai_books/etax/koa210_layout.json` (built from .xsd)                              |
| Spec docs / manifest   | `docs/etax/` (`manifest.json`, `field_catalog.json`)                                  |
| Fetch / build scripts  | `scripts/etax/fetch_etax_spec.py`, `build_koa210_layout.py`, `build_field_catalog.py` |
| XSD validation harness | `tests/etax_xsd.py` (gates on fetched .xsd)                                           |
| Tests                  | `tests/test_etax.py`, `tests/test_etax_xtx.py`                                        |

## Procedure

1. **Fetch the official spec** (not committed):
   ```bash
   uv run python scripts/etax/fetch_etax_spec.py --out .cache/etax
   ```
2. **Read first**: `etax/spec.py` (`EtaxFormatSpec`, `resolve_scalar`/`resolve_list`),
   `export.py` emit paths, the current `manifest.json`/`field_catalog.json`, and
   `tests/test_etax_xtx.py` for what the XSD gate checks.
3. **Add the form mapping** in `spec.py` and the emit logic in `export.py`. If the layout is
   .xsd-derived, regenerate it (don't hand-edit):
   ```bash
   uv run python scripts/etax/build_koa210_layout.py --xsd .cache/etax/extracted/<FORM>.xsd --out src/ai_books/etax/koa210_layout.json
   ```
   Refresh the field catalog via `build_field_catalog.py` if fields changed.
4. **Tests**: extend `tests/test_etax.py` (pure render assertions) and `tests/test_etax_xtx.py`
   (XSD-validated, `skipif(not xsd_available())`). Keep output deterministic.
5. **Verify**:
   ```bash
   ./scripts/verify.sh                       # render tests run; XSD tests skip if .xsd absent
   uv run pytest -q tests/test_etax_xtx.py   # live XSD validation (after the fetch above)
   ```
6. Report: the form/field added, that the layout was regenerated from the .xsd (and no .xsd
   committed), and the validation result.

Cross-reference: `/etax-validate` runs the validation standalone; #89 "How to add" and
#76 (fetch/catalog) mirror this flow.
