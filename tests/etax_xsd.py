"""Backwards-compatible re-export of the e-Tax XSD validation harness (Issues #79 / #163).

The harness moved to :mod:`ai_books.etax.xsd` (production) so the ``etax_preflight`` MCP tool (#163)
can share the exact offline-skip semantics the XSD-gated tests use. The historical import site
``from tests.etax_xsd import ...`` is kept stable here so existing tests need no change.
"""

from __future__ import annotations

from ai_books.etax.xsd import form_id_of, schema_dir, skip_reason, validate_xtx, xsd_available

__all__ = ["form_id_of", "schema_dir", "skip_reason", "validate_xtx", "xsd_available"]
