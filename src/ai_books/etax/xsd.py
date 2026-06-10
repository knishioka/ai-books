"""Validate a rendered e-Tax ``.xtx`` against the official 国税庁 ``.xsd`` (Issues #79 / #103 / #163).

Validates a rendered 青色申告決算書 ``.xtx`` (KOA210 一般用 / KOA220 不動産所得用 / KOA240 農業所得用)
against the **official 国税庁 .xsd** (the form schema plus the 共通 ``General.xsd`` closure). Those
schemas are 著作物 and are **not** committed (see ``docs/etax/manifest.json``); they are fetched on
demand by ``scripts/etax/fetch_etax_spec.py`` into ``.cache/etax/schema/``. So this module *gates on
their presence*: :func:`xsd_available` reports whether the tree has been fetched, and callers skip
(the XSD-gated tests) or report ``skipped`` (the ``etax_preflight`` MCP tool, #163) when it is absent
— never fail, so the offline path keeps working. CI fetches the schema first so the gate is live
there. ``xmlschema`` is a *dev-only* dependency — the .xtx *renderer* itself is stdlib — so the import
is local to :func:`validate_xtx` and a caller that wants to degrade gracefully when it is missing can
catch :class:`ImportError`.

Each KOA2x0 is declared as a *local* element inside its ``KOA2x0-<v>group`` (the real 手続 envelope
references that group), so it cannot be validated as a document root directly. The fetch step writes a
tiny harness wrapper per form (``<form>_doc.xsd``) that exposes the group as a global ``KOA2x0SET``
element; here we wrap the generated ``<KOA2x0>`` in ``<KOA2x0SET>`` before validating.

This module is shared between ``tests/etax_xsd.py`` (the XSD-gated test harness) and the
``etax_preflight`` MCP tool (#163) so the offline-skip semantics are identical in both.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from functools import cache
from pathlib import Path
from typing import Any

#: Repo root (…/src/ai_books/etax/xsd.py → parents[3]).
_REPO_ROOT = Path(__file__).resolve().parents[3]
#: Default location of the fetched .xsd validation tree; override with ``AI_BOOKS_ETAX_SCHEMA_DIR``.
_DEFAULT_SCHEMA_DIR = _REPO_ROOT / ".cache" / "etax" / "schema"
#: form_id → (wrapper filename, form schema path under the tree) — written by fetch_etax_spec.py.
_FORM_SCHEMAS = {
    "KOA210": ("koa210_doc.xsd", "shotoku/KOA210-011.xsd"),
    "KOA220": ("koa220_doc.xsd", "shotoku/KOA220-008.xsd"),
    "KOA240": ("koa240_doc.xsd", "shotoku/KOA240-008.xsd"),
}


def schema_dir() -> Path:
    """Directory holding the fetched .xsd tree (shotoku/ + general/ + wrappers)."""
    override = os.environ.get("AI_BOOKS_ETAX_SCHEMA_DIR")
    return Path(override) if override else _DEFAULT_SCHEMA_DIR


def form_id_of(xtx: str) -> str:
    """The 様式 (``KOA210`` / ``KOA220`` / ``KOA240``) of a rendered ``.xtx`` — its root local name.

    The 様式 is read from the generated root element's tag (namespace-stripped), the same key
    :func:`xsd_available` and :func:`validate_xtx` use to locate the form's schema.
    """
    return ET.fromstring(xtx).tag.rpartition("}")[2]


def xsd_available(form_id: str = "KOA210") -> bool:
    """Whether ``form_id``'s official .xsd validation tree has been fetched (the gate to validate)."""
    if form_id not in _FORM_SCHEMAS:
        return False
    wrapper, schema_file = _FORM_SCHEMAS[form_id]
    base = schema_dir()
    return (base / wrapper).is_file() and (base / schema_file).is_file()


def skip_reason() -> str:
    """Human-readable reason shown when the .xsd gate skips, with how to enable it."""
    return (
        f"official e-Tax .xsd not found under {schema_dir()}; "
        "run `python scripts/etax/fetch_etax_spec.py --out .cache/etax` to enable XSD validation "
        "(国税庁 著作物のため raw は非同梱)"
    )


@cache
def _wrapper_schema(form_id: str) -> Any:
    """Load ``form_id``'s validation-harness schema once (imports resolve relative to ``schema_dir``)."""
    import xmlschema  # local import: dev-only dependency

    wrapper, _ = _FORM_SCHEMAS[form_id]
    return xmlschema.XMLSchema(str(schema_dir() / wrapper))


def validate_xtx(xtx: str) -> list[str]:
    """Validate a rendered ``.xtx`` against its 様式's official schema; return error messages.

    An empty list means the document is schema-valid. The 様式 (KOA210/KOA220/KOA240) is read from the
    generated root element, which is wrapped in ``<KOA2x0SET>`` (that form's harness global element) so
    ``xmlschema`` has a document root to match. Raises :class:`ImportError` if the dev-only
    ``xmlschema`` is not installed — callers that must degrade gracefully should catch it.
    """
    root = ET.fromstring(xtx)
    namespace, _, form_id = root.tag.rpartition("}")
    namespace = namespace.lstrip("{")
    document = ET.Element(f"{{{namespace}}}{form_id}SET")
    document.append(root)
    return [error.reason or str(error) for error in _wrapper_schema(form_id).iter_errors(document)]
