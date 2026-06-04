#!/usr/bin/env python3
"""Download, checksum-verify and extract the official e-Tax 所得税関係 spec packages.

Spike #76 artifact. The 国税庁 publishes the 所得税 XML 仕様 as Microsoft CAB archives at
https://www.e-tax.nta.go.jp/shiyo/shiyo3.htm (登録不要). Those archives are 著作物 and are **not**
redistributed in this repo; this script fetches them on demand, verifies each against the SHA256
recorded in ``docs/etax/manifest.json``, and extracts the 青色申告決算書 workbooks and ``.xsd`` so
``build_field_catalog.py`` can regenerate the committed catalog.

Implemented with the standard library only (no ``cabextract`` / ``7z`` needed): the CAB reader
handles RESERVE-present headers and both ``NONE`` and ``MSZIP`` folder compression.

Usage::

    python scripts/etax/fetch_etax_spec.py --out .cache/etax        # download + verify + extract
    python scripts/etax/fetch_etax_spec.py --out .cache/etax --no-verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import urllib.request
import zlib
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[2] / "docs" / "etax" / "manifest.json"
USER_AGENT = "ai-books-etax-spike/76 (+https://github.com/knishioka/ai-books)"

# Files to pull out of each archive after extraction (substring match on the in-CAB path).
WANTED = {
    "e-tax09.CAB": [
        "帳票フィールド仕様書(所得-申告)Ver11x",
        "帳票フィールド仕様書(所得-申告)Ver8x",
    ],
    "e-tax19.CAB": ["shotoku/KOA210-011.xsd", "shotoku/KOA220-008.xsd", "shotoku/KOA240-008.xsd"],
}

# The .xsd closure needed to XSD-validate a generated .xtx (#79): the KOA210 form schema plus the
# 共通 (general) schemas it import/includes. These are laid out under ``<out>/schema/`` preserving the
# shotoku/ + general/ directory split so the relative schemaLocation paths resolve, and a small
# validation-harness wrapper (``koa210_doc.xsd``) is written beside them (see WRAPPER_SCHEMA).
SCHEMA_FILES = {
    "e-tax19.CAB": [
        "shotoku/KOA210-011.xsd",
        "general/General.xsd",
        "general/zeimusho.xsd",
        "general/zeimoku.xsd",
        "general/ITreference.xsd",
    ],
}

#: Sub-path of ``--out`` that holds the validation-ready .xsd tree (shotoku/ + general/ + wrapper).
SCHEMA_DIRNAME = "schema"
#: The validation harness wrapper. KOA210 is a *local* element inside ``KOA210-11-0group`` (the real
#: 手続 envelope references that group), so it is not directly validatable as a document root. This
#: wrapper includes the official schema and exposes the group as a global ``KOA210SET`` element; the
#: validator wraps a generated ``<KOA210>`` in ``<KOA210SET>`` before validating. (Harness only — not
#: an e-Tax artifact.)
WRAPPER_SCHEMA = """<?xml version="1.0" encoding="UTF-8"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"
  targetNamespace="http://xml.e-tax.nta.go.jp/XSD/shotoku"
  xmlns="http://xml.e-tax.nta.go.jp/XSD/shotoku"
  elementFormDefault="qualified">
  <xsd:include schemaLocation="shotoku/KOA210-011.xsd"/>
  <xsd:element name="KOA210SET">
    <xsd:complexType>
      <xsd:group ref="KOA210-11-0group"/>
    </xsd:complexType>
  </xsd:element>
</xsd:schema>
"""


class CabError(RuntimeError):
    """Raised when a CAB archive cannot be parsed."""


def _decode_folder(data: bytes, start: int, blocks: int, data_reserve: int, compress: int) -> bytes:
    """Decode one CFFOLDER's data blocks (``compress`` 0=NONE, 1=MSZIP) into raw bytes."""
    pos = start
    out = bytearray()
    for _ in range(blocks):
        _checksum, comp_len, _uncomp_len = struct.unpack("<IHH", data[pos : pos + 8])
        pos += 8 + data_reserve
        block = data[pos : pos + comp_len]
        pos += comp_len
        if compress == 0:
            out += block
        elif compress == 1:
            if block[:2] != b"CK":
                raise CabError("bad MSZIP block header")
            history = bytes(out[-32768:])
            inflate = zlib.decompressobj(-15, zdict=history) if out else zlib.decompressobj(-15)
            out += inflate.decompress(block[2:]) + inflate.flush()
        else:
            raise CabError(f"unsupported CAB compression {compress}")
    return bytes(out)


def extract_cab(
    cab_path: Path, out_dir: Path, wanted: list[str], *, keep_dirs: bool = False
) -> list[Path]:
    """Extract files whose in-CAB path contains any ``wanted`` substring; return written paths.

    With ``keep_dirs`` the file's directory under ``19XMLスキーマ/`` is preserved (so
    ``shotoku/KOA210-011.xsd`` and ``general/General.xsd`` keep the layout their relative
    schemaLocation paths need); otherwise it is flattened to its basename.
    """
    data = cab_path.read_bytes()
    if data[:4] != b"MSCF":
        raise CabError(f"{cab_path.name}: not a CAB (missing MSCF signature)")
    coff_files = struct.unpack("<I", data[16:20])[0]
    folder_count, file_count, flags = struct.unpack("<HHH", data[26:32])
    off = 36
    folder_reserve = data_reserve = 0
    if flags & 0x4:
        cab_reserve, folder_reserve, data_reserve = struct.unpack("<HBB", data[off : off + 4])
        off += 4 + cab_reserve
    folders = []
    for _ in range(folder_count):
        coff_start, blocks, compress = struct.unpack("<IHH", data[off : off + 8])
        off += 8 + folder_reserve
        folders.append((coff_start, blocks, compress & 0x0F))
    decoded = [_decode_folder(data, c, b, data_reserve, t) for (c, b, t) in folders]

    written: list[Path] = []
    pos = coff_files
    for _ in range(file_count):
        size, uoff, ifolder = struct.unpack("<IIH", data[pos : pos + 10])
        pos += 16  # CFFILE fixed part (date/time/attribs follow the 10 parsed bytes)
        end = data.index(b"\x00", pos)
        name = data[pos:end].decode("cp932", "replace")
        pos = end + 1
        normalized = name.replace("\\", "/")
        if not any(token in normalized for token in wanted):
            continue
        if keep_dirs and "/" in normalized:
            # Drop the top-level "19XMLスキーマ/" container, keep e.g. shotoku/KOA210-011.xsd.
            relative = normalized.split("/", 1)[1] if normalized.count("/") >= 2 else normalized
            dest = out_dir / relative
        else:
            dest = out_dir / Path(normalized).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(decoded[ifolder][uoff : uoff + size])
        written.append(dest)
    return written


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def download(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response:
        dest.write_bytes(response.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="cache/extract directory")
    parser.add_argument("--no-verify", action="store_true", help="skip SHA256 verification")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    packages = {pkg["file"]: pkg for pkg in manifest["packages"]}
    args.out.mkdir(parents=True, exist_ok=True)

    for filename, pkg in packages.items():
        cab_path = args.out / filename
        if not cab_path.exists():
            print(f"downloading {filename} ...")
            download(pkg["url"], cab_path)
        actual = _sha256(cab_path)
        if actual != pkg["sha256"]:
            message = (
                f"{filename}: SHA256 mismatch\n  expected {pkg['sha256']}\n  actual   {actual}"
            )
            if args.no_verify:
                print("WARNING:", message)
            else:
                raise SystemExit(
                    message + "\n(spec may have been re-published; update manifest.json)"
                )
        else:
            print(f"verified  {filename}  sha256 ok")
        if filename in WANTED:
            extracted = extract_cab(cab_path, args.out / "extracted", WANTED[filename])
            for path in extracted:
                print(f"  extracted {path.relative_to(args.out)}")
        if filename in SCHEMA_FILES:
            schema_dir = args.out / SCHEMA_DIRNAME
            laid_out = extract_cab(cab_path, schema_dir, SCHEMA_FILES[filename], keep_dirs=True)
            for path in laid_out:
                print(f"  schema    {path.relative_to(args.out)}")
            (schema_dir / "koa210_doc.xsd").write_text(WRAPPER_SCHEMA, encoding="utf-8")
            print(f"  schema    {Path(SCHEMA_DIRNAME) / 'koa210_doc.xsd'} (validation wrapper)")

    print(f"\ndone. spec workbooks/xsd under {args.out / 'extracted'}")
    print(f"      .xsd validation tree under {args.out / SCHEMA_DIRNAME} (#79)")
    print(
        "next: python scripts/etax/build_field_catalog.py "
        f"--spec-dir {args.out / 'extracted'} --out docs/etax/field_catalog.json"
    )
    print(
        "      python scripts/etax/build_koa210_layout.py "
        f"--xsd {args.out / 'extracted' / 'KOA210-011.xsd'} "
        "--out src/ai_books/etax/koa210_layout.json"
    )


if __name__ == "__main__":
    main()
