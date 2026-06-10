"""申告者ヘッダ メタを ローカル プロフィールから供給する — Issue #160 のテスト.

受入条件 (issue #160) を一通り pin する:

* fixture profile を指す ``AI_BOOKS_ETAX_PROFILE`` 下で CSV / XML / .xtx のヘッダ欄に値が出る。
* プロフィール未存在時に既存 golden (``etax_export.json``) が byte 不変 (ライブラリは
  ``~/.ai-books/`` を **自動で読まない** ので、env が立っていても素の build は不変)。
* 桁あふれ・不正文字 のプロフィール値が :class:`~ai_books.errors.EtaxValidationError` で **全件** 報告。
* テストは実 ``~/.ai-books/`` に一切触れない (``tmp_path`` のみ。loader は読むだけで書かない)。
* ヘッダ値入りでも .xtx が公式 .xsd を通る (xsd 取得済みのときのみ・gated)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_books.errors import DomainValidationError, EtaxValidationError
from ai_books.etax import (
    EtaxFormat,
    EtaxProfile,
    build_etax_export,
    etax_export_snapshot,
    export_etax,
    load_etax_profile,
    profile_header_records,
    profile_path,
    render_etax,
)
from ai_books.etax.profile import HEADER_FIELDS, HEADER_FORM
from ai_books.models import EtaxValueKind
from tests.etax_xsd import skip_reason, validate_xtx, xsd_available
from tests.fixtures.seed_fy import load_golden
from tests.fixtures.seed_fy.reports import financial_statements_from_dataset

_requires_xsd = pytest.mark.skipif(not xsd_available(), reason=skip_reason())

#: 代表的な値 — 平文 (.xsd 単純文字列型) で .xtx に載せられる 3 セルぶん。
_ADDRESS = "東京都千代田区一番町1-1"
_OFFICE = "東京都港区芝公園4-2-8"
_MEMBER_ORG = "○○商工会連合会"


def _write_profile(tmp_path: Path, body: str) -> Path:
    """``[filer]`` プロフィールを ``tmp_path`` に書き、そのパスを返す (実 ~/.ai-books には触れない)。"""
    path = tmp_path / "profile.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _full_profile() -> EtaxProfile:
    return EtaxProfile(address=_ADDRESS, business_office=_OFFICE, member_organization=_MEMBER_ORG)


# ── ローダ (TOML → EtaxProfile) ───────────────────────────────────────────────


def test_loads_filer_table_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_profile(
        tmp_path,
        f'[filer]\naddress = "{_ADDRESS}"\nbusiness_office = "{_OFFICE}"\n'
        f'member_organization = "{_MEMBER_ORG}"\n',
    )
    monkeypatch.setenv("AI_BOOKS_ETAX_PROFILE", str(path))
    profile = load_etax_profile()
    assert profile == _full_profile()


def test_missing_profile_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "absent.toml"
    monkeypatch.setenv("AI_BOOKS_ETAX_PROFILE", str(missing))
    assert load_etax_profile() is None
    # loader は読むだけ — 未存在ファイルを作らない (Never touch ガードと整合)。
    assert not missing.exists()


def test_default_path_is_under_home(monkeypatch: pytest.MonkeyPatch) -> None:
    # env 未設定時の既定は ~/.ai-books/etax/profile.toml (秘匿情報を repo に入れない置き場所)。
    monkeypatch.delenv("AI_BOOKS_ETAX_PROFILE", raising=False)
    assert profile_path() == Path.home() / ".ai-books" / "etax" / "profile.toml"


def test_env_overrides_default_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_BOOKS_ETAX_PROFILE", str(tmp_path / "p.toml"))
    assert profile_path() == tmp_path / "p.toml"


def test_unknown_key_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_profile(tmp_path, '[filer]\nname = "山田太郎"\n')
    monkeypatch.setenv("AI_BOOKS_ETAX_PROFILE", str(path))
    with pytest.raises(DomainValidationError, match=r"unknown.*key.*name"):
        load_etax_profile()


def test_non_string_value_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_profile(tmp_path, "[filer]\naddress = 123\n")
    monkeypatch.setenv("AI_BOOKS_ETAX_PROFILE", str(path))
    with pytest.raises(DomainValidationError, match="must be a string"):
        load_etax_profile()


def test_malformed_toml_reported_with_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 手編集タイポ (不正 TOML) は どのファイルが壊れているか を含めて報告する。
    path = _write_profile(tmp_path, "[filer\naddress = ")
    monkeypatch.setenv("AI_BOOKS_ETAX_PROFILE", str(path))
    with pytest.raises(DomainValidationError, match="not valid TOML") as excinfo:
        load_etax_profile()
    assert str(path) in excinfo.value.message


def test_filer_must_be_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_profile(tmp_path, 'filer = "oops"\n')
    monkeypatch.setenv("AI_BOOKS_ETAX_PROFILE", str(path))
    with pytest.raises(DomainValidationError, match="must be a table"):
        load_etax_profile()


def test_empty_or_absent_filer_is_blank_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_profile(tmp_path, "# no filer table\n")
    monkeypatch.setenv("AI_BOOKS_ETAX_PROFILE", str(path))
    assert load_etax_profile() == EtaxProfile()


# ── ヘッダ records (検証 + マッピング) ────────────────────────────────────────


def test_header_records_in_spec_order() -> None:
    records = profile_header_records(_full_profile())
    assert [r.item_code for r in records] == [f.item_code for f in HEADER_FIELDS]
    assert all(r.form == HEADER_FORM and r.kind is EtaxValueKind.TEXT for r in records)
    assert [r.value for r in records] == [_ADDRESS, _OFFICE, _MEMBER_ORG]


def test_empty_profile_emits_no_records() -> None:
    assert profile_header_records(EtaxProfile()) == []


def test_blank_and_whitespace_values_are_skipped() -> None:
    # 空文字・空白のみ は「未指定」扱い (= 従来通り空欄)。
    records = profile_header_records(EtaxProfile(address=_ADDRESS, business_office="   "))
    assert [r.item_code for r in records] == ["AMB00010"]


def test_overflow_and_invalid_char_reported_together() -> None:
    # 桁あふれ (加入団体名 > 20) と 不正文字 (制御文字) を 1 回で全件報告する。
    bad = EtaxProfile(address="住所\x07ベル", member_organization="あ" * 21)
    with pytest.raises(EtaxValidationError) as excinfo:
        profile_header_records(bad)
    problems = excinfo.value.problems
    assert {p["item_code"] for p in problems} == {"AMB00010", "AMB00110"}
    messages = " ".join(p["message"] for p in problems)
    assert "不正文字" in messages
    assert "桁あふれ" in messages


def test_programmatic_non_string_value_reported_not_crash() -> None:
    # 公開 API としての防衛: プログラムから str 以外を渡しても AttributeError で落とさず
    # EtaxValidationError で全件報告する。
    bad = EtaxProfile(address=123)  # type: ignore[arg-type]
    with pytest.raises(EtaxValidationError) as excinfo:
        profile_header_records(bad)
    problems = excinfo.value.problems
    assert problems[0]["item_code"] == "AMB00010"
    assert "型不正" in problems[0]["message"]


def test_overflow_at_boundary_ok_but_over_fails() -> None:
    # 加入団体名 maxLength=20: 20 文字は通り、21 文字で 桁あふれ。
    assert profile_header_records(EtaxProfile(member_organization="あ" * 20))
    with pytest.raises(EtaxValidationError):
        profile_header_records(EtaxProfile(member_organization="あ" * 21))


# ── export 結合 (build_etax_export / export_etax) ─────────────────────────────


def test_header_appears_in_csv_xml_xtx() -> None:
    fs = financial_statements_from_dataset()
    export = build_etax_export(fs, profile=_full_profile())
    # ヘッダ records が先頭に prepend される。
    assert [r.item_code for r in export.records[:3]] == ["AMB00010", "AMB00050", "AMB00110"]
    for fmt in (EtaxFormat.CSV, EtaxFormat.XML, EtaxFormat.XTX):
        rendered = render_etax(export, fmt)
        assert _ADDRESS in rendered
        assert _OFFICE in rendered
        assert _MEMBER_ORG in rendered


def test_export_etax_one_call_passes_profile_through() -> None:
    fs = financial_statements_from_dataset()
    out = export_etax(fs, fmt=EtaxFormat.XTX, profile=_full_profile())
    assert _ADDRESS in out


def test_no_profile_is_byte_invariant_golden() -> None:
    # AC: プロフィール未存在時に既存 golden が byte 不変。
    snapshot = etax_export_snapshot(build_etax_export(financial_statements_from_dataset()))
    assert snapshot == load_golden("etax_export")


def test_library_never_auto_reads_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 実 profile を env で指していても、profile を渡さない build は golden と byte 不変
    # (ライブラリは ~/.ai-books を自動で読まない — 自動ロードは MCP 境界のみ)。
    path = _write_profile(tmp_path, f'[filer]\naddress = "{_ADDRESS}"\n')
    monkeypatch.setenv("AI_BOOKS_ETAX_PROFILE", str(path))
    snapshot = etax_export_snapshot(build_etax_export(financial_statements_from_dataset()))
    assert snapshot == load_golden("etax_export")


def test_synthetic_export_gets_no_header() -> None:
    # ヘッダ 項目コード は KOA210 様式のみ — synthetic には注入しない。
    export = build_etax_export(
        financial_statements_from_dataset(), version="synthetic", profile=_full_profile()
    )
    assert "AMB00010" not in {r.item_code for r in export.records}


def test_invalid_header_value_fails_build() -> None:
    fs = financial_statements_from_dataset()
    with pytest.raises(EtaxValidationError):
        build_etax_export(fs, profile=EtaxProfile(member_organization="x" * 21))


# ── 形式妥当性 (XSD) — 取得済みのときだけ ─────────────────────────────────────


@_requires_xsd
def test_xtx_with_header_passes_official_xsd() -> None:
    fs = financial_statements_from_dataset()
    xtx = render_etax(build_etax_export(fs, profile=_full_profile()), EtaxFormat.XTX)
    assert validate_xtx(xtx) == []
