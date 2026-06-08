#!/usr/bin/env python3
"""Derive a 青色申告決算書 様式 element-tree *layout* from its official XSD — Issues #79 / #103.

The .xtx renderer (:func:`ai_books.etax.export.render_etax_xtx`) must place each 項目コード at its
exact spot in the e-Tax 様式's **nested** element tree (pages → groups → leaves), in XSD sequence
order, or the official schema rejects the file. Rather than hard-coding those trees in Python, this
script extracts each one once from the official ``.xsd`` into a small, committed **derived** artifact
(``src/ai_books/etax/<form>_layout.json``) the renderer reads at runtime.

It is form-agnostic: the 様式 identity (form_id / 版 / 名前空間 / ページ複合型) is read from the XSD
itself, so the same script builds KOA210 (一般用 v11.0), KOA220 (不動産所得用 v8.0) and KOA240
(農業所得用 v8.0) — the three forms share the page-typed envelope ``KOA<form>-<n>-<vmaj>-<vmin>type``.

Like ``field_catalog.json`` (#76), a layout is *derived facts* (element names + nesting + order +
repeat + 金額/否), not the 国税庁 著作物 raw — so it is committed while the .xsd itself is not. The
.xsd is fetched on demand by ``fetch_etax_spec.py``; re-run this script after a 様式 update to refresh
the layout.

Usage::

    python scripts/etax/fetch_etax_spec.py --out .cache/etax            # get the .xsd first
    python scripts/etax/build_etax_layout.py \
        --xsd .cache/etax/extracted/KOA210-011.xsd \
        --out src/ai_books/etax/koa210_layout.json
"""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

XSD_NS = "http://www.w3.org/2001/XMLSchema"
_NS = {"x": XSD_NS}

#: e-Tax 金額 type — a leaf carrying this type is rendered as 整数円 (others as text/コード).
KINGAKU_TYPE = "gen:kingaku"

#: The 様式 envelope group, e.g. ``KOA210-11-0group`` / ``KOA220-8-0group`` — names the form & 版.
_GROUP_RE = re.compile(r"^(?P<form>KOA\d+)-(?P<vmaj>\d+)-(?P<vmin>\d+)group$")


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


def _form_identity(root: ET.Element) -> tuple[str, str, str]:
    """Read ``(form_id, version, namespace)`` from the XSD's 様式 envelope group + targetNamespace.

    The envelope group is named ``KOA<form>-<vmaj>-<vmin>group`` (e.g. ``KOA210-11-0group``); the
    page complexTypes that hang off it are ``KOA<form>-<n>-<vmaj>-<vmin>type``. Deriving identity
    from the schema (not flags) keeps the layout a faithful function of the official .xsd.
    """
    namespace = root.get("targetNamespace")
    if not namespace:
        raise SystemExit("XSD has no targetNamespace")
    for group in root.findall("x:group", _NS):
        match = _GROUP_RE.match(group.get("name", ""))
        if match:
            form_id = match.group("form")
            version = f"{match.group('vmaj')}.{match.group('vmin')}"
            return form_id, version, namespace
    raise SystemExit("could not find the 様式 envelope group (KOA<form>-<vmaj>-<vmin>group) in XSD")


def build_layout(xsd_path: Path) -> dict[str, Any]:
    """Parse a 青色申告決算書 ``.xsd`` into the renderer's layout dict (pages → groups → leaves)."""
    root = ET.parse(xsd_path).getroot()
    named = _build_named_complex_types(root)
    form_id, version, namespace = _form_identity(root)
    vmaj, vmin = version.split(".")
    page_re = re.compile(rf"^{re.escape(form_id)}-(\d+)-{vmaj}-{vmin}type$")
    page_numbers = sorted(int(match.group(1)) for name in named if (match := page_re.match(name)))
    if not page_numbers:
        raise SystemExit(f"no page complexTypes ({form_id}-<n>-{vmaj}-{vmin}type) found in XSD")
    pages = [
        {
            "tag": f"{form_id}-{page}",
            "children": _walk(named[f"{form_id}-{page}-{vmaj}-{vmin}type"], named),
        }
        for page in page_numbers
    ]
    return {
        "form_id": form_id,
        "version": version,
        "namespace": namespace,
        "generated_by": "scripts/etax/build_etax_layout.py",
        "source": f"{xsd_path.name} (e-tax19.CAB; 国税庁 著作物, raw 非同梱 — manifest.json 参照)",
        "note": (
            "Derived element tree for the .xtx renderer (#79/#103): tag = ＸＭＬタグ(項目コード), "
            "amount=true は gen:kingaku(整数円), repeat=true は maxOccurs>1 の繰返しブロック. "
            "leaf は children を持たない。日本語ラベルは field_catalog.json 参照。"
        ),
        "pages": pages,
    }


def _count_leaves(nodes: list[dict[str, Any]]) -> int:
    """Count leaf nodes (no ``children``) in the layout — a quick sanity figure for the CLI."""
    total = 0
    for node in nodes:
        if "children" in node:
            total += _count_leaves(node["children"])
        else:
            total += 1
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xsd", required=True, type=Path, help="path to a KOA2x0-0xx.xsd")
    parser.add_argument("--out", required=True, type=Path, help="output layout JSON path")
    args = parser.parse_args()

    layout = build_layout(args.xsd)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    leaves = _count_leaves(layout["pages"])
    pages = len(layout["pages"])
    print(
        f"wrote {args.out}  ({layout['form_id']} v{layout['version']}, {leaves} leaf 項目, {pages} pages)"
    )


if __name__ == "__main__":
    main()
