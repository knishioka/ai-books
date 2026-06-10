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

# The .xsd closure needed to XSD-validate a generated .xtx (#79/#103): each 様式 form schema plus the
# 共通 (general) schemas they import/include. These are laid out under ``<out>/schema/`` preserving the
# shotoku/ + general/ directory split so the relative schemaLocation paths resolve, and a small
# validation-harness wrapper (``<form>_doc.xsd``) is written beside them per form (see _wrapper_schema).
SCHEMA_FILES = {
    "e-tax19.CAB": [
        "shotoku/KOA210-011.xsd",
        "shotoku/KOA220-008.xsd",
        "shotoku/KOA240-008.xsd",
        "general/General.xsd",
        "general/zeimusho.xsd",
        "general/zeimoku.xsd",
        "general/ITreference.xsd",
    ],
}

#: Sub-path of ``--out`` that holds the validation-ready .xsd tree (shotoku/ + general/ + wrappers).
SCHEMA_DIRNAME = "schema"

#: Per-form validation-harness inputs: form_id → (schema file under ``shotoku/``, 様式 envelope group).
#: Each KOA2x0 is a *local* element inside its ``KOA2x0-<v>group`` (the real 手続 envelope references
#: that group), so it is not directly validatable as a document root. The wrapper includes the official
#: schema and exposes the group as a global ``KOA2x0SET`` element; the validator wraps a generated
#: ``<KOA2x0>`` in ``<KOA2x0SET>`` before validating. (Harness only — not an e-Tax artifact.)
SCHEMA_FORMS = {
    "KOA210": ("shotoku/KOA210-011.xsd", "KOA210-11-0group"),
    "KOA220": ("shotoku/KOA220-008.xsd", "KOA220-8-0group"),
    "KOA240": ("shotoku/KOA240-008.xsd", "KOA240-8-0group"),
}


def _wrapper_schema(form_id: str, schema_file: str, group: str) -> str:
    """The validation-harness wrapper for one 様式 — exposes its envelope group as ``<form>SET``."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"
  targetNamespace="http://xml.e-tax.nta.go.jp/XSD/shotoku"
  xmlns="http://xml.e-tax.nta.go.jp/XSD/shotoku"
  elementFormDefault="qualified">
  <xsd:include schemaLocation="{schema_file}"/>
  <xsd:element name="{form_id}SET">
    <xsd:complexType>
      <xsd:group ref="{group}"/>
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


# --- Scheduled XSD-revision watcher (#161) -------------------------------------------------------
# The 様式 .xsd is the precise revision signal: spec.py is version-managed so swapping a layout is
# easy, but *detecting* an upstream revision is otherwise manual. ``--check-sha`` fetches the
# official .xsd, compares each 様式 against its pinned SHA256 in manifest.json (the single source
# also asserted by the CI ``etax-xsd`` job — no second copy of the pins), and emits a machine-
# readable report whose ``issues`` list the watcher workflow turns into GitHub issues. The pins live
# only in ``docs/etax/manifest.json``; this code reads them, it never re-records them.


def _drift_issue(form_id: str, xsd_name: str, pinned: str, fetched: str, source_url: str) -> dict:
    """Issue spec for a 様式 whose upstream .xsd no longer matches the pinned SHA256 (a revision)."""
    title = f"[etax-watch] e-Tax XSD revision detected: {form_id}"
    body = (
        f"国税庁の公式 `.xsd` が **{form_id}** ({xsd_name}) でリポジトリの pin と不一致です。"
        "様式改訂の可能性が高いため、レイアウト再生成と pin 更新を確認してください。\n\n"
        "| 項目 | 値 |\n"
        "| --- | --- |\n"
        f"| 様式 (form ID) | `{form_id}` |\n"
        f"| .xsd | `{xsd_name}` |\n"
        f"| pin 済み SHA256 (旧) | `{pinned}` |\n"
        f"| 取得した SHA256 (新) | `{fetched}` |\n"
        f"| 取得元 | {source_url} |\n\n"
        "### 対応\n"
        "1. `docs/etax/manifest.json` の `青色申告決算書_forms[].xsd_sha256` (および対応する CAB の "
        "`packages[].sha256`/公開日) を新版に更新\n"
        "2. `scripts/etax/build_etax_layout.py` で `src/ai_books/etax/*_layout.json` を再生成し "
        "`sync_web_layouts.py` で web 側へ同期\n"
        "3. `./scripts/test.sh -k etax` (CI `etax-xsd`) で .xtx XSD 検証を pass させる\n\n"
        "_この issue は `.github/workflows/etax-spec-watch.yml` が自動起票しました。_"
    )
    return {"kind": "drift", "key": form_id, "title": title, "body": body}


def _fetch_error_issue(package: str, url: str, error: str) -> dict:
    """Issue spec for a fetch/extract failure — distinct from a SHA mismatch (URL 切れも改訂シグナル)."""
    title = f"[etax-watch] e-Tax XSD fetch failed: {package}"
    body = (
        f"国税庁の公式仕様パッケージ `{package}` の取得/解凍に失敗しました。ネットワーク障害の一過性も"
        "あり得ますが、**URL 変更も改訂シグナル**のため起票します (SHA 不一致とは区別)。\n\n"
        "| 項目 | 値 |\n"
        "| --- | --- |\n"
        f"| パッケージ | `{package}` |\n"
        f"| URL | {url} |\n"
        f"| エラー | `{error}` |\n\n"
        "### 対応\n"
        "1. 取得元 <https://www.e-tax.nta.go.jp/shiyo/shiyo3.htm> を確認 (URL/版の変更有無)\n"
        "2. URL が変わっていれば `docs/etax/manifest.json` の `packages[].url` を更新\n"
        "3. 一過性であれば `workflow_dispatch` で再実行し green を確認のうえクローズ\n\n"
        "_この issue は `.github/workflows/etax-spec-watch.yml` が自動起票しました。_"
    )
    return {"kind": "fetch_error", "key": package, "title": title, "body": body}


def plan_issues(report: dict) -> list[dict]:
    """Pure transform: a drift report → the GitHub issues that should be (de-duplicated then) opened.

    Kept side-effect free so the dedupe/create step in the workflow stays a thin gh wrapper and the
    title/body wording is unit-testable without any network or ``gh``.
    """
    issues: list[dict] = []
    for form in report.get("forms", []):
        if form.get("status") == "mismatch":
            issues.append(
                _drift_issue(
                    form["form_id"],
                    form["xsd"],
                    form["expected_sha256"],
                    form["actual_sha256"],
                    form.get("source_url", ""),
                )
            )
    for err in report.get("fetch_errors", []):
        issues.append(_fetch_error_issue(err["package"], err["url"], err["error"]))
    return issues


def check_sha(out_dir: Path, manifest: dict, *, simulate_drift: bool = False) -> dict:
    """Fetch the official .xsd and diff each 様式 against its pinned SHA256; return a drift report.

    A per-package fetch/extract failure is captured as a ``fetch_errors`` entry (not raised) so one
    様式 going dark does not hide the others. ``simulate_drift`` offsets every pin so the issue-
    creation path can be exercised end-to-end via ``workflow_dispatch`` without a real upstream
    change (AC: "SHA pin を意図的にずらした dry-run").
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    packages = {pkg["file"]: pkg for pkg in manifest["packages"]}
    forms = manifest["青色申告決算書_forms"]
    report: dict = {"forms": [], "fetch_errors": []}
    extracted: dict[str, Path] = {}

    # SCHEMA_FILES is the authoritative CAB→.xsd mapping; only those CABs hold the 様式 schemas.
    for filename in SCHEMA_FILES:
        pkg = packages[filename]
        cab_path = out_dir / filename
        try:
            if not cab_path.exists():
                download(pkg["url"], cab_path)
            basenames = [Path(p).name for p in SCHEMA_FILES[filename]]
            for path in extract_cab(cab_path, out_dir / "extracted", basenames):
                extracted[path.name] = path
        except Exception as exc:
            report["fetch_errors"].append(
                {"package": filename, "url": pkg["url"], "error": f"{type(exc).__name__}: {exc}"}
            )

    source_url = packages[next(iter(SCHEMA_FILES))]["url"]
    for form in forms:
        basename = Path(form["xsd"]).name
        pinned = form["xsd_sha256"]
        path = extracted.get(basename)
        if path is None:
            # The form's package failed to fetch — already captured as a fetch_error above.
            report["forms"].append(
                {"form_id": form["form_id"], "xsd": basename, "status": "skipped"}
            )
            continue
        fetched = _sha256(path)
        # simulate_drift pretends the pin was set to an impossible value so the live fetched SHA
        # never equals it — a faithful "pin intentionally offset" without touching manifest.json.
        compare_pin = "0" * 64 if simulate_drift else pinned
        status = "match" if fetched == compare_pin else "mismatch"
        report["forms"].append(
            {
                "form_id": form["form_id"],
                "xsd": basename,
                "status": status,
                "expected_sha256": compare_pin,
                "actual_sha256": fetched,
                "source_url": source_url,
            }
        )

    report["issues"] = plan_issues(report)
    report["ok"] = not report["issues"]
    return report


def _run_check_sha(args: argparse.Namespace, manifest: dict) -> None:
    args.out.mkdir(parents=True, exist_ok=True)
    report = check_sha(args.out, manifest, simulate_drift=args.simulate_drift)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(payload + "\n", encoding="utf-8")
    for form in report["forms"]:
        print(f"{form['status']:>8}  {form['form_id']}  {form['xsd']}")
    for err in report["fetch_errors"]:
        print(f"fetch-err  {err['package']}: {err['error']}")
    print(f"\n{len(report['issues'])} issue(s) to open; ok={report['ok']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="cache/extract directory")
    parser.add_argument("--no-verify", action="store_true", help="skip SHA256 verification")
    parser.add_argument(
        "--check-sha",
        action="store_true",
        help="watcher (#161): diff each 様式 .xsd against its pinned SHA256; emit a drift report",
    )
    parser.add_argument(
        "--report", type=Path, help="with --check-sha: write the drift report JSON to this path"
    )
    parser.add_argument(
        "--simulate-drift",
        action="store_true",
        help="with --check-sha: force every 様式 to mismatch (dry-run the issue-creation path)",
    )
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    if args.check_sha:
        _run_check_sha(args, manifest)
        return

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
            for form_id, (schema_file, group) in SCHEMA_FORMS.items():
                wrapper_name = f"{form_id.lower()}_doc.xsd"
                wrapper = _wrapper_schema(form_id, schema_file, group)
                (schema_dir / wrapper_name).write_text(wrapper, encoding="utf-8")
                print(f"  schema    {Path(SCHEMA_DIRNAME) / wrapper_name} (validation wrapper)")

    print(f"\ndone. spec workbooks/xsd under {args.out / 'extracted'}")
    print(f"      .xsd validation tree under {args.out / SCHEMA_DIRNAME} (#79)")
    print(
        "next: python scripts/etax/build_field_catalog.py "
        f"--spec-dir {args.out / 'extracted'} --out docs/etax/field_catalog.json"
    )
    for form_id, xsd_name in (
        ("KOA210", "KOA210-011.xsd"),
        ("KOA220", "KOA220-008.xsd"),
        ("KOA240", "KOA240-008.xsd"),
    ):
        print(
            "      python scripts/etax/build_etax_layout.py "
            f"--xsd {args.out / 'extracted' / xsd_name} "
            f"--out src/ai_books/etax/{form_id.lower()}_layout.json"
        )


if __name__ == "__main__":
    main()
