"""XSD validation harness for the generated e-Tax ``.xtx`` — Issue #79.

Validates a rendered KOA210 ``.xtx`` against the **official 国税庁 .xsd** (``KOA210-011.xsd`` plus the
共通 ``General.xsd`` closure). Those schemas are 著作物 and are **not** committed (see
``docs/etax/manifest.json``); they are fetched on demand by ``scripts/etax/fetch_etax_spec.py`` into
``.cache/etax/schema/``. So this helper *gates on their presence*: when the schema tree is absent the
test that uses it skips (exactly as the DB-backed tests skip without ``AI_BOOKS_DB_URL``), and CI runs
the fetch step first so the .xsd gate is live there. ``xmlschema`` is a dev-only dependency — the .xtx
*renderer* itself is stdlib.

KOA210 is declared as a *local* element inside ``KOA210-11-0group`` (the real 手続 envelope references
that group), so it cannot be validated as a document root directly. The fetch step writes a tiny
harness wrapper (``koa210_doc.xsd``) that exposes the group as a global ``KOA210SET`` element; here we
wrap the generated ``<KOA210>`` in ``<KOA210SET>`` before validating.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Any

from ai_books.etax import koa210_layout

#: Repo root (…/tests/etax_xsd.py → parents[1]).
_REPO_ROOT = Path(__file__).resolve().parents[1]
#: Default location of the fetched .xsd validation tree; override with ``AI_BOOKS_ETAX_SCHEMA_DIR``.
_DEFAULT_SCHEMA_DIR = _REPO_ROOT / ".cache" / "etax" / "schema"
#: Filename of the validation-harness wrapper schema (written by fetch_etax_spec.py).
_WRAPPER = "koa210_doc.xsd"


def schema_dir() -> Path:
    """Directory holding the fetched .xsd tree (shotoku/ + general/ + wrapper)."""
    override = os.environ.get("AI_BOOKS_ETAX_SCHEMA_DIR")
    return Path(override) if override else _DEFAULT_SCHEMA_DIR


def xsd_available() -> bool:
    """Whether the official .xsd validation tree has been fetched (gate for the validation test)."""
    base = schema_dir()
    return (base / _WRAPPER).is_file() and (base / "shotoku" / "KOA210-011.xsd").is_file()


def skip_reason() -> str:
    """Human-readable reason shown when the .xsd gate skips, with how to enable it."""
    return (
        f"official e-Tax .xsd not found under {schema_dir()}; "
        "run `python scripts/etax/fetch_etax_spec.py --out .cache/etax` to enable XSD validation "
        "(国税庁 著作物のため raw は非同梱)"
    )


@lru_cache(maxsize=1)
def _wrapper_schema() -> Any:
    """Load the validation-harness schema once (imports resolve relative to ``schema_dir``)."""
    import xmlschema  # local import: dev-only dependency

    return xmlschema.XMLSchema(str(schema_dir() / _WRAPPER))


def validate_xtx(xtx: str) -> list[str]:
    """Validate a rendered ``.xtx`` against the official KOA210 schema; return error messages.

    An empty list means the document is schema-valid. The generated root ``<KOA210>`` is wrapped in
    ``<KOA210SET>`` (the harness global element) so ``xmlschema`` has a document root to match.
    """
    namespace = koa210_layout()["namespace"]
    koa210 = ET.fromstring(xtx)
    document = ET.Element(f"{{{namespace}}}KOA210SET")
    document.append(koa210)
    return [error.reason or str(error) for error in _wrapper_schema().iter_errors(document)]
