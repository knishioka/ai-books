"""Scheduled XSD-revision watcher (#161) — pure issue-planning logic.

``check_sha`` itself fetches the 著作物 .xsd from 国税庁, so its network path is exercised by the
``etax-spec-watch`` workflow (and the CI ``etax-xsd`` fetch). What is unit-testable offline — and
where the acceptance criteria actually live — is ``plan_issues``: a drift report → the GitHub issues
to open. These tests pin that wording/behaviour (form ID + 旧/新 SHA present, fetch failure kept
distinct from a SHA mismatch, a clean report opens nothing).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_SPEC_PATH = Path(__file__).resolve().parents[1] / "scripts" / "etax" / "fetch_etax_spec.py"
_spec = importlib.util.spec_from_file_location("fetch_etax_spec", _SPEC_PATH)
assert _spec is not None
assert _spec.loader is not None
fetch_etax_spec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch_etax_spec)

plan_issues = fetch_etax_spec.plan_issues

_PINNED = "806d4a5e3ee8e33ef82ec5904e12088e6c1f9e37ac0eedeb549facecadf60313"
_FETCHED = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def _mismatch_form() -> dict[str, str]:
    return {
        "form_id": "KOA210",
        "xsd": "KOA210-011.xsd",
        "status": "mismatch",
        "expected_sha256": _PINNED,
        "actual_sha256": _FETCHED,
        "source_url": "https://www.e-tax.nta.go.jp/shiyo/download/e-tax19.CAB",
    }


def test_clean_report_opens_no_issue() -> None:
    report = {
        "forms": [
            {"form_id": "KOA210", "xsd": "KOA210-011.xsd", "status": "match"},
            {"form_id": "KOA220", "xsd": "KOA220-008.xsd", "status": "match"},
        ],
        "fetch_errors": [],
    }
    assert plan_issues(report) == []


def test_skipped_form_opens_no_issue() -> None:
    # A form whose package failed to fetch is "skipped" — its signal is the fetch_error, not a drift.
    report = {"forms": [{"form_id": "KOA240", "xsd": "KOA240-008.xsd", "status": "skipped"}]}
    assert plan_issues(report) == []


def test_mismatch_issue_has_form_id_and_both_shas() -> None:
    issues = plan_issues({"forms": [_mismatch_form()], "fetch_errors": []})
    assert len(issues) == 1
    issue = issues[0]
    assert issue["kind"] == "drift"
    # Title is stable/ASCII so the workflow's exact-title dedupe is robust.
    assert issue["title"] == "[etax-watch] e-Tax XSD revision detected: KOA210"
    # AC: 起票メッセージに form ID / 旧新 SHA を含む.
    assert "KOA210" in issue["body"]
    assert _PINNED in issue["body"]  # 旧 (pinned)
    assert _FETCHED in issue["body"]  # 新 (fetched)


def test_fetch_error_issue_is_distinct_from_drift() -> None:
    report = {
        "forms": [],
        "fetch_errors": [
            {
                "package": "e-tax19.CAB",
                "url": "https://www.e-tax.nta.go.jp/shiyo/download/e-tax19.CAB",
                "error": "URLError: <urlopen error [Errno -2] Name or service not known>",
            }
        ],
    }
    issues = plan_issues(report)
    assert len(issues) == 1
    issue = issues[0]
    assert issue["kind"] == "fetch_error"
    assert issue["title"] == "[etax-watch] e-Tax XSD fetch failed: e-tax19.CAB"
    assert "e-tax19.CAB" in issue["body"]
    # The fetch-failure wording must not masquerade as a confirmed revision.
    assert "revision detected" not in issue["title"]


def test_distinct_titles_per_form_allow_independent_dedupe() -> None:
    forms = [
        _mismatch_form(),
        {**_mismatch_form(), "form_id": "KOA220", "xsd": "KOA220-008.xsd"},
    ]
    titles = {i["title"] for i in plan_issues({"forms": forms, "fetch_errors": []})}
    assert titles == {
        "[etax-watch] e-Tax XSD revision detected: KOA210",
        "[etax-watch] e-Tax XSD revision detected: KOA220",
    }


# --- check_sha branching (network/extract stubbed) -----------------------------------------------
# check_sha fetches 著作物 .xsd, so the real download is exercised only by the workflow / CI. Here we
# stub download + extract_cab to lock in its branching: clean→green, simulate→every 様式 drifts,
# fetch failure→a distinct issue, all without committing any .xsd (AC: .xsd がリポジトリに残らない).


def _manifest() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "docs" / "etax" / "manifest.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _stub_fetch_matching_pins(monkeypatch: pytest.MonkeyPatch, manifest: dict[str, Any]) -> None:
    """Make extract_cab yield .xsd whose SHA equals each form's pin → the 'no change' path."""

    def fake_extract(
        cab: Path, out: Path, basenames: list[str], keep_dirs: bool = False
    ) -> list[Path]:
        out.mkdir(parents=True, exist_ok=True)
        written = []
        for base in basenames:
            dest = out / base
            dest.write_bytes(base.encode())  # deterministic content per basename
            written.append(dest)
        return written

    monkeypatch.setattr(fetch_etax_spec, "download", lambda url, dest: dest.write_bytes(b"CAB"))
    monkeypatch.setattr(fetch_etax_spec, "extract_cab", fake_extract)
    for form in manifest["青色申告決算書_forms"]:
        form["xsd_sha256"] = hashlib.sha256(Path(form["xsd"]).name.encode()).hexdigest()


def test_check_sha_clean_is_green(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _manifest()
    _stub_fetch_matching_pins(monkeypatch, manifest)
    report = fetch_etax_spec.check_sha(tmp_path / "c", manifest, simulate_drift=False)
    assert report["ok"] is True
    assert report["issues"] == []
    assert {f["status"] for f in report["forms"]} == {"match"}


def test_check_sha_simulate_drift_flags_every_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest()
    _stub_fetch_matching_pins(monkeypatch, manifest)
    report = fetch_etax_spec.check_sha(tmp_path / "s", manifest, simulate_drift=True)
    assert report["ok"] is False
    assert {f["status"] for f in report["forms"]} == {"mismatch"}
    assert len(report["issues"]) == len(manifest["青色申告決算書_forms"])
    assert all(i["kind"] == "drift" for i in report["issues"])


def test_check_sha_captures_fetch_failure_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest()

    def boom(url: str, dest: Path) -> None:
        raise OSError("dns failure")

    monkeypatch.setattr(fetch_etax_spec, "download", boom)
    report = fetch_etax_spec.check_sha(tmp_path / "f", manifest, simulate_drift=False)
    assert report["ok"] is False
    assert [e["package"] for e in report["fetch_errors"]] == ["e-tax19.CAB"]
    assert [i["kind"] for i in report["issues"]] == ["fetch_error"]
    # Forms whose package went dark are 'skipped', not falsely reported as a confirmed drift.
    assert {f["status"] for f in report["forms"]} == {"skipped"}


def test_check_sha_flags_missing_xsd_after_successful_extract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 上流が .xsd をリネーム/削除した場合: CAB の取得・解凍は成功するが期待ファイルが無い。
    # サイレントに skip せず fetch_error として 1 件起票する (gemini-code-assist #169 指摘)。
    manifest = _manifest()

    def extract_nothing(
        cab: Path, out: Path, basenames: list[str], keep_dirs: bool = False
    ) -> list[Path]:
        out.mkdir(parents=True, exist_ok=True)
        return []  # download ok, but the expected .xsd are absent from the archive

    monkeypatch.setattr(fetch_etax_spec, "download", lambda url, dest: dest.write_bytes(b"CAB"))
    monkeypatch.setattr(fetch_etax_spec, "extract_cab", extract_nothing)
    report = fetch_etax_spec.check_sha(tmp_path / "m", manifest, simulate_drift=False)
    assert report["ok"] is False
    # One aggregated fetch_error for the package, naming every missing .xsd (no duplicate titles).
    assert [e["package"] for e in report["fetch_errors"]] == ["e-tax19.CAB"]
    error = report["fetch_errors"][0]["error"]
    assert "not found" in error
    for form in manifest["青色申告決算書_forms"]:
        assert Path(form["xsd"]).name in error
    assert [i["kind"] for i in report["issues"]] == ["fetch_error"]
    assert {f["status"] for f in report["forms"]} == {"skipped"}
