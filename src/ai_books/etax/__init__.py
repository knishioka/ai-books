"""e-Tax 電子申告 取込データ出力 (Issue #24) — the last hop after the 青色申告決算書 (#23).

Turns a :class:`~ai_books.models.FinancialStatements` into the CSV / XML files e-Tax imports,
driven by a versioned, data-driven 様式 spec so 年度ごとの様式変更には spec の差し替えだけで追従
できる (コードは不変). The pipeline:

    決算書 → build_etax_export (map + schema validate) → EtaxExport → render_etax (CSV/XML)

* :func:`export_etax` — the one-call entry (決算書 → rendered string).
* :func:`build_etax_export` / :func:`render_etax` — the two stages, split so the structured
  :class:`~ai_books.models.EtaxExport` can be snapshotted for golden (#17).
* :func:`etax_export_snapshot` — canonical JSON shape the golden harness freezes.
* :class:`~ai_books.etax.spec.EtaxFormatSpec` / :func:`get_format_spec` — the versioned 様式.

Schema validation (必須項目欠落・桁・コード値・月) raises
:class:`~ai_books.errors.EtaxValidationError`. 生成物は秘密情報を含みうるため、出力ファイルは
リポジトリにコミットしない (運用は README 参照)。
"""

from __future__ import annotations

from .export import (
    EtaxFormat,
    build_etax_export,
    etax_export_snapshot,
    export_etax,
    koa210_layout,
    parse_etax_format,
    render_etax,
    render_etax_csv,
    render_etax_xml,
    render_etax_xtx,
)
from .spec import (
    ETAX_FORMAT_SPECS,
    LATEST_ETAX_VERSION,
    EtaxFixedRow,
    EtaxFixedSection,
    EtaxFormatSpec,
    EtaxScalarField,
    EtaxSection,
    EtaxSectionField,
    get_format_spec,
)

__all__ = [
    "ETAX_FORMAT_SPECS",
    "LATEST_ETAX_VERSION",
    "EtaxFixedRow",
    "EtaxFixedSection",
    "EtaxFormat",
    "EtaxFormatSpec",
    "EtaxScalarField",
    "EtaxSection",
    "EtaxSectionField",
    "build_etax_export",
    "etax_export_snapshot",
    "export_etax",
    "get_format_spec",
    "koa210_layout",
    "parse_etax_format",
    "render_etax",
    "render_etax_csv",
    "render_etax_xml",
    "render_etax_xtx",
]
