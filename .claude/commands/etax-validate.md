---
description: Validate the generated e-Tax .xtx against the official 国税庁 .xsd (fetches the schema on demand).
argument-hint: "[--fetch]"
allowed-tools: Bash(uv run:*), Bash(./scripts/verify.sh:*), Read
---

Run the e-Tax XSD validation standalone.

The official 国税庁 `.xsd` is 著作物 and is **not committed** — it is fetched on demand into
`.cache/etax/` (declared in `docs/etax/manifest.json`). The validation test **skips** when the
schema tree is absent, so fetch first if you haven't this session.

```bash
# 1. Fetch the official .xsd (idempotent; skip if .cache/etax already populated)
uv run python scripts/etax/fetch_etax_spec.py --out .cache/etax

# 2. Validate the rendered .xtx against the official schema (XSD-gated tests)
uv run pytest -q tests/test_etax_xtx.py
```

- Without the fetch, `tests/test_etax_xtx.py` reports **skipped** (the XSD gate, by design) —
  that is not a failure. `./scripts/verify.sh` stays green either way.
- **Never commit** any fetched `.xsd` (or other 国税庁 著作物). Layout is generated from the
  .xsd, not hand-edited (see the **etax-spec-author** subagent and #76).

Report: whether the schema was fetched, and the pass/skip/fail of the XSD validation.
