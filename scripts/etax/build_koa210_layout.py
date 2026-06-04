#!/usr/bin/env python3
"""Derive the KOA210 element-tree *layout* from the official XSD — Issue #79.

The .xtx renderer (:func:`ai_books.etax.export.render_etax_xtx`) must place each 項目コード at its
exact spot in the e-Tax 様式's **nested** element tree (pages → groups → leaves), in XSD sequence
order, or the official schema rejects the file. Rather than hard-coding that 314-element tree in
Python, this script extracts it once from the official ``KOA210-011.xsd`` into a small, committed
**derived** artifact (``src/ai_books/etax/koa210_layout.json``) the renderer reads at runtime.

Like ``field_catalog.json`` (#76), the layout is *derived facts* (element names + nesting + order +
repeat + 金額/否), not the 国税庁 著作物 raw — so it is committed while the .xsd itself is not. The
.xsd is fetched on demand by ``fetch_etax_spec.py``; re-run this script after a 様式 update to refresh
the layout.

Usage::

    python scripts/etax/fetch_etax_spec.py --out .cache/etax            # get the .xsd first
    python scripts/etax/build_koa210_layout.py \
        --xsd .cache/etax/extracted/KOA210-011.xsd \
        --out src/ai_books/etax/koa210_layout.json
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

XSD_NS = "http://www.w3.org/2001/XMLSchema"
_NS = {"x": XSD_NS}

#: The form's 名前空間 and 版 — the renderer stamps these onto the .xtx root.
FORM_NAMESPACE = "http://xml.e-tax.nta.go.jp/XSD/shotoku"
FORM_VERSION = "11.0"
#: e-Tax 金額 type — a leaf carrying this type is rendered as 整数円 (others as text/コード).
KINGAKU_TYPE = "gen:kingaku"


def _build_named_complex_types(root: ET.Element) -> dict[str, ET.Element]:
    """Index the top-level named ``complexType``s (the page types are referenced by name)."""
    return {ct.get("name", ""): ct for ct in root.findall("x:complexType", _NS)}


def _sequence_of(node: ET.Element) -> ET.Element | None:
    """The ``xsd:sequence`` of a complexType (named) or an element's inline complexType."""
    direct = node.find("x:sequence", _NS)
    if direct is not None:
        return direct
    return node.find("x:complexType/x:sequence", _NS)


def _walk(node: ET.Element, named: dict[str, ET.Element]) -> list[dict[str, Any]]:
    """Turn a complexType/inline-complexType's ``sequence`` into ordered layout child nodes.

    A child is a **group** when it has an inline ``complexType`` or its type names one of the
    top-level ``complexType``s (recurse into it); otherwise it is a **leaf** (``gen:*`` or a named
    ``simpleType`` such as ``AMF00060-11-0Rtype``). ``maxOccurs`` > 1 marks a repeating group.
    """
    sequence = _sequence_of(node)
    if sequence is None:
        return []
    children: list[dict[str, Any]] = []
    for element in sequence.findall("x:element", _NS):
        tag = element.get("name")
        if tag is None:
            continue
        type_name = element.get("type")
        inline = element.find("x:complexType", _NS)
        named_complex = named.get(type_name or "")
        if inline is not None:
            group: dict[str, Any] = {"tag": tag, "children": _walk(inline, named)}
        elif named_complex is not None:
            group = {"tag": tag, "children": _walk(named_complex, named)}
        else:
            children.append({"tag": tag, "amount": type_name == KINGAKU_TYPE})
            continue
        max_occurs = element.get("maxOccurs")
        if max_occurs is not None and max_occurs != "1":
            group["repeat"] = True
        children.append(group)
    return children


def build_layout(xsd_path: Path) -> dict[str, Any]:
    """Parse ``KOA210-011.xsd`` into the renderer's layout dict (pages → groups → leaves)."""
    root = ET.parse(xsd_path).getroot()
    named = _build_named_complex_types(root)
    pages = [
        {"tag": f"KOA210-{page}", "children": _walk(named[f"KOA210-{page}-11-0type"], named)}
        for page in (1, 2, 3, 4)
    ]
    return {
        "form_id": "KOA210",
        "version": FORM_VERSION,
        "namespace": FORM_NAMESPACE,
        "generated_by": "scripts/etax/build_koa210_layout.py",
        "source": "KOA210-011.xsd (e-tax19.CAB; 国税庁 著作物, raw 非同梱 — manifest.json 参照)",
        "note": (
            "Derived element tree for the .xtx renderer (#79): tag = ＸＭＬタグ(項目コード), "
            "amount=true は gen:kingaku(整数円), repeat=true は maxOccurs>1 の繰返しブロック. "
            "leaf は children を持たない。日本語ラベルは field_catalog.json 参照。"
        ),
        "pages": pages,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xsd", required=True, type=Path, help="path to KOA210-011.xsd")
    parser.add_argument("--out", required=True, type=Path, help="output layout JSON path")
    args = parser.parse_args()

    layout = build_layout(args.xsd)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    leaves = _count_leaves(layout["pages"])
    print(f"wrote {args.out}  ({leaves} leaf 項目, 4 pages)")


def _count_leaves(nodes: list[dict[str, Any]]) -> int:
    """Count leaf nodes (no ``children``) in the layout — a quick sanity figure for the CLI."""
    total = 0
    for node in nodes:
        if "children" in node:
            total += _count_leaves(node["children"])
        else:
            total += 1
    return total


if __name__ == "__main__":
    main()
