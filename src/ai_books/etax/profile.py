"""申告者ヘッダ メタデータ を ローカル プロフィールから供給する — Issue #160.

KOA210(一般用) の ヘッダ欄 は 様式上 任意欄 で、これまで **空欄** のまま出力していた (#78 の方針:
spec は 値マッピング に集中し、住所・氏名等の 申告者メタ は埋めない)。その結果 e-Tax ソフト側で 毎年
手入力 が要り、転記ミス の元だった。本モジュールは 手編集前提の TOML プロフィール
(``~/.ai-books/etax/profile.toml``) を読み、KOA210 ヘッダの **値を直接持てる セル** へ供給する。

## 供給できるのは「平文セル」だけ — .xtx は公式 .xsd を通す必要がある (#79)

KOA210 ヘッダの大半は、見た目は任意欄でも .xsd 上は **平文ではない**:

* **氏名 (AMB00040) / フリガナ (AMB00030) / 業種名 (AMB00090) / 屋号 (AMB00100)** は
  ``gen:*ref`` 型 — 値そのものではなく、申告書本体 (e-Tax 送信の **封筒側**) で定義される ID を
  ``IDREF`` 属性で参照する。値テキストは KOA210 ファイル内には存在し得ず、e-Tax ソフトの **利用者情報**
  が一度だけ供給する。テキストとして emit すると ``missing required attribute 'IDREF'`` で .xsd 不通。
* **電話 (AMB00070/00080)** は ``gen:tel-number`` 複合型 (tel1/tel2/tel3 の子要素)。テキストを直接
  入れると ``character data between child elements not allowed`` で .xsd 不通。

よって .xtx に **値テキストとして** 載せられる ヘッダは、.xsd 上 単純文字列型の 3 セルだけ:

* ``AMB00010`` 住所 (= 納税地) — ``gen:address`` (文字列)
* ``AMB00050`` 事業所所在地 — ``gen:address`` (文字列)
* ``AMB00110`` 加入団体名 — 文字列 (maxLength 20)

氏名・フリガナ・屋号・業種名・電話 は **設計上 e-Tax ソフトの利用者情報側** が持つため、本機能の対象外
(``docs/etax/README.md`` 参照)。様式が将来これらを 様式内 平文セル化したら :data:`HEADER_FIELDS` に
1 行足すだけで追従できる。

## 値検証

各値は emit 前に検証する (AC #160): 制御文字 (``\\t\\n\\r`` 以外の ``Cc``) は **不正文字**、フィールド
固有の ``max_len`` (.xsd ``maxLength`` 由来) を超えるものは **桁あふれ** として、
:class:`~ai_books.errors.EtaxValidationError` に **全件** まとめて報告する (export エンジンと同じ
``{"item_code", "row", "message"}`` 形)。

プロフィール未存在は **従来通り空欄で出力** (エラーにしない) — 既存利用者の後方互換、かつ ヘッダは
様式上も任意欄であるため。ローダは秘匿情報 (住所・名称) を含むファイルを読むだけで **書かない**。
実 ``~/.ai-books/`` はテストでは触れない (``AI_BOOKS_ETAX_PROFILE`` で tmp_path を指す)。
"""

from __future__ import annotations

import os
import tomllib
import unicodedata
from pathlib import Path
from typing import NamedTuple

from ai_books.errors import EtaxValidationError
from ai_books.models import EtaxRecord, EtaxValueKind

#: 面 区分 (CSV/XML の「面」列) — ヘッダ records 用。.xtx は 項目コード で配置するので 値に影響しない。
HEADER_FORM = "HEADER"

#: プロフィールの値を受ける TOML テーブル名 (``[filer]``)。将来 別区分を増やせるよう namespacing する。
_FILER_TABLE = "filer"

#: ``AI_BOOKS_ETAX_PROFILE`` 未設定時の既定パス。``~/.ai-books/`` 配下 — 秘匿情報を リポジトリに入れない
#: 既存 Never touch 方針と整合する。
_DEFAULT_PROFILE_PATH = Path.home() / ".ai-books" / "etax" / "profile.toml"


class EtaxHeaderField(NamedTuple):
    """KOA210 ヘッダの 1 つの 平文セル — プロフィール属性 → e-Tax 項目コード の対応 + 制約。

    ``attr`` は :class:`EtaxProfile` の属性名、``item_code`` は KOA210 の e-Tax 項目コード、``max_len``
    は .xsd ``maxLength`` (無制約なら ``None``)。ここに載るのは .xsd 上 **単純文字列型** のセルだけ
    (``*ref`` / 複合型 は値テキストを持てないため対象外、モジュール docstring 参照)。
    """

    item_code: str
    label: str
    attr: str
    max_len: int | None = None


#: KOA210 ヘッダで .xtx に **値テキスト** として載せられる セル (= .xsd 上 単純文字列型)。
#: 並びは 様式の出現順 (住所 → 事業所所在地 → 加入団体名)。氏名・屋号・電話 等は設計上 e-Tax ソフト
#: 利用者情報側 (封筒の IDREF / 複合型) のため含めない。
HEADER_FIELDS: tuple[EtaxHeaderField, ...] = (
    EtaxHeaderField("AMB00010", "住所", "address"),
    EtaxHeaderField("AMB00050", "事業所所在地", "business_office"),
    EtaxHeaderField("AMB00110", "加入団体名", "member_organization", max_len=20),
)


class EtaxProfile(NamedTuple):
    """申告者ヘッダ メタ — KOA210 の 平文ヘッダセルへ供給する値 (すべて任意)。

    値は手編集 TOML (``[filer]`` テーブル) から読む。空 (未指定 / 空文字) のフィールドは そのセルを
    emit しない (= 従来通り空欄)。マッピング先 項目コードは :data:`HEADER_FIELDS` を正とする。
    """

    address: str | None = None  # 住所 (納税地) → AMB00010
    business_office: str | None = None  # 事業所所在地 → AMB00050
    member_organization: str | None = None  # 加入団体名 → AMB00110


def profile_path() -> Path:
    """プロフィール TOML のパス — ``AI_BOOKS_ETAX_PROFILE`` 優先、無ければ ``~/.ai-books/etax/profile.toml``。

    環境変数での上書きは テスト用 (実 ``~/.ai-books/`` に触れず tmp_path を指す) かつ 運用上の置き場所
    変更の両方を兼ねる。
    """
    override = os.environ.get("AI_BOOKS_ETAX_PROFILE")
    return Path(override) if override else _DEFAULT_PROFILE_PATH


def load_etax_profile() -> EtaxProfile | None:
    """プロフィールを読み込む。ファイルが無ければ ``None`` (= 従来通り空欄、エラーにしない)。

    TOML の ``[filer]`` テーブルを :class:`EtaxProfile` に写す。未知キーは ``ValueError`` で弾く
    (手編集のタイプミス検出)。値の桁数・文字種検証は emit 時 (:func:`profile_header_records`) に行う。
    ファイルは **読むだけ** — 書き込みは一切しない。
    """
    path = profile_path()
    if not path.is_file():
        return None
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    filer = data.get(_FILER_TABLE, {})
    if not isinstance(filer, dict):
        raise ValueError(
            f"e-Tax profile: [{_FILER_TABLE}] must be a table, got {type(filer).__name__}"
        )
    known = set(EtaxProfile._fields)
    unknown = sorted(set(filer) - known)
    if unknown:
        raise ValueError(
            f"e-Tax profile: unknown [{_FILER_TABLE}] key(s) {', '.join(unknown)}; "
            f"supported: {', '.join(sorted(known))}"
        )
    for key, value in filer.items():
        if not isinstance(value, str):
            raise ValueError(
                f"e-Tax profile: [{_FILER_TABLE}].{key} must be a string, got {type(value).__name__}"
            )
    return EtaxProfile(**filer)


def _validate_header_value(value: str, field: EtaxHeaderField) -> str | None:
    """ヘッダ値を検証し、問題があれば説明を返す (無ければ ``None``)。

    1. **不正文字**: 制御文字 (``\\t\\n\\r`` 以外の Unicode ``Cc``) は .xsd の文字集合からも外れるため拒否。
    2. **桁あふれ**: ``field.max_len`` (.xsd ``maxLength``) を超える長さを拒否。
    """
    bad = sorted({c for c in value if unicodedata.category(c) == "Cc" and c not in "\t\n\r"})
    if bad:
        rendered = ", ".join(f"U+{ord(c):04X}" for c in bad)
        return f"不正文字 ({rendered}): 制御文字は不可"
    if field.max_len is not None and len(value) > field.max_len:
        return f"桁あふれ: {len(value)} 文字 (上限 {field.max_len})"
    return None


def profile_header_records(profile: EtaxProfile) -> list[EtaxRecord]:
    """プロフィールを KOA210 ヘッダの :class:`~ai_books.models.EtaxRecord` 群へ写す (検証付き)。

    空でない各フィールドを :func:`_validate_header_value` に通し、問題は **全件** 集めて
    :class:`~ai_books.errors.EtaxValidationError` を送出する (部分出力はしない)。問題が無ければ
    :data:`HEADER_FIELDS` の宣言順に TEXT records を返す。
    """
    records: list[EtaxRecord] = []
    problems: list[dict[str, str]] = []
    for field in HEADER_FIELDS:
        raw = getattr(profile, field.attr)
        if raw is None:
            continue
        value = raw.strip()
        if not value:
            continue
        problem = _validate_header_value(value, field)
        if problem is not None:
            problems.append({"item_code": field.item_code, "row": "", "message": problem})
            continue
        records.append(
            EtaxRecord(
                form=HEADER_FORM,
                item_code=field.item_code,
                label=field.label,
                kind=EtaxValueKind.TEXT,
                value=value,
            )
        )
    if problems:
        raise EtaxValidationError(problems)
    return records
