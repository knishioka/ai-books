# e-Tax 所得税関係 XML 仕様 — 調査スパイク (#76)

実 e-Tax への申告データ出力 (#78 / #79) の**確定前提を固める**ための調査成果物。
現状の `src/ai_books/etax/spec.py` は明示的に「synthetic(非公式・教育用)」様式であり、実申告に
使うには国税庁の**正式な所得税関係 XML 様式**が必要。本スパイクでその仕様を取得・解凍・抽出し、
後続実装が参照できる**機械可読なフィールドカタログ**・**snapshot↔項目コードのマッピング草案**・
**再現可能な取得手順**を用意した。

> ⚠️ **再配布について**: 取得元の CAB/.xlsx/.xsd/.docx は国税庁の著作物のため**本リポジトリには
> 同梱しない**。代わりに (a) 出所・版・日付・SHA256 を [`manifest.json`](./manifest.json) に記録し、
> (b) checksum 検証付きの取得スクリプトを用意した。本リポにコミットするのは、そこから**派生した
> 事実データ**(項目コード・桁数・必須・繰返し)のカタログのみ。

## 成果物

| ファイル                                           | 内容                                                                   |
| -------------------------------------------------- | ---------------------------------------------------------------------- |
| [`manifest.json`](./manifest.json)                 | 取得元 URL / 版 / 公開日 / SHA256 / 各帳票の版・名前空間               |
| [`field_catalog.json`](./field_catalog.json)       | 機械可読フィールドカタログ (KOA210/220/240, 計 897 項目)               |
| [`snapshot_mapping.json`](./snapshot_mapping.json) | `financial_statements_snapshot` → 実項目コードの**草案** (#78 入力)    |
| `scripts/etax/fetch_etax_spec.py`                  | CAB を取得 → SHA256 検証 → 対象 .xlsx/.xsd を解凍 (標準ライブラリのみ) |
| `scripts/etax/build_field_catalog.py`              | 解凍済 .xlsx から `field_catalog.json` を再生成                        |

## 取得した仕様 (確定版)

出所: **e-Tax 仕様書一覧** <https://www.e-tax.nta.go.jp/shiyo/shiyo3.htm> (登録不要・開発者向け公開)
提出データ形式は XML(交換ファイル拡張子 **.xtx**)。受付開始 令和8年1月5日(= **令和7年分**申告)。

| #   | 仕様書                                              | ファイル              | 公開日          | SHA256(先頭) |
| --- | --------------------------------------------------- | --------------------- | --------------- | ------------ |
| 9   | XML構造設計書及び帳票フィールド仕様書【所得税関係】 | `e-tax09.CAB` (8.1MB) | 令和7年10月30日 | `5f1014bf…`  |
| 19  | XMLスキーマ (.xsd, 全税目)                          | `e-tax19.CAB` (9.3MB) | 令和8年5月18日  | `573a5b87…`  |
| 1   | データ形式等に関する仕様書                          | `e-tax01.CAB` (559KB) | 令和7年10月30日 | `e1abd372…`  |

### 青色申告決算書の帳票・版 (令和7年分の現行版)

帳票バージョンは年度ではなく**様式改定**で増える(過年分申告のため旧版も同梱)。確定版パッケージ
内で各帳票の最高版が現行版:

| 帳票ID        | 様式                         | 版       | .xsd                                      | 様式文書日付 |
| ------------- | ---------------------------- | -------- | ----------------------------------------- | ------------ |
| **KOA210**    | 青色申告決算書(一般用)       | **11.0** | `shotoku/KOA210-011.xsd` (001–011 を確認) | 2023-09-27   |
| **KOA220**    | 青色申告決算書(不動産所得用) | **8.0**  | `shotoku/KOA220-008.xsd` (001–008)        | 2020-08-31   |
| **KOA240**    | 青色申告決算書(農業所得用)   | **8.0**  | `shotoku/KOA240-008.xsd` (001–008)        | 2020-08-31   |
| (参考) KOA230 | 青色申告決算書(現金主義用)   | 10.0     | `shotoku/KOA230-010.xsd`                  | —            |

- **名前空間**: `http://xml.e-tax.nta.go.jp/XSD/shotoku` (`general` / `kyotsu` を import)。
- **製造原価の計算は独立帳票ではなく KOA210(一般用)に内包**される (項目 `AMH00010`–`AMH00220`)。
- 一般用フォームは 4 ページ (`KOA210-1`〜`KOA210-4`)、各 page 要素に様式バージョン属性 `VR="11.0"` が required。

> 版↔年度の注記: KOA210 v11 の様式文書日付は 2023-09-27 だが、令和7年10月30日の確定版パッケージ内で
> v11 が KOA210 の最高版であり、これが**令和7年分の現行版**(以降様式変更なし)。不動産/農業は v8 で安定。

### 様式別 spec 実装状況

| 帳票       | EtaxFormatSpec   | .xtx layout | 備考                                                                                                        |
| ---------- | ---------------- | ----------- | ----------------------------------------------------------------------------------------------------------- |
| **KOA210** | ✅ `2025`        | ✅          | 一般用。営業外マッピング方針も確定 (利子割引料→AMF00330 橋渡し, #83)                                        |
| **KOA220** | ✅ `2025-KOA220` | ✅ (#103)   | 不動産所得用。**収入側 spec 登録 + 実データ .xtx golden/XSD 完了 (#126)**。経費/BS/減価償却は未供給で対象外 |
| **KOA240** | ✅ `2025-KOA240` | ✅ (#103)   | 農業所得用。**収入側 spec 登録 + 実データ .xtx golden/XSD 完了 (#126)**。経費/BS/減価償却は未供給で対象外   |

**#103 で完了したこと (stage 3)**: `.xtx` レンダラ (`render_etax_xtx`) を様式別 layout に対応させ、
KOA220-008 / KOA240-008 の XSD 由来 layout (`koa220_layout.json` / `koa240_layout.json`) を追加した。
レンダラは EtaxExport の項目コード族 (一般用 `AMF*` / 不動産 `ANF*` / 農業 `APF*` は様式間で重複なし)
から様式を自動判定し、各様式の最小 `.xtx` が**公式 .xsd を pass** することを CI で機械保証する。
KOA210 は不変 (golden / XSD とも green)。

**#124 で完了したこと (KOA220 収入側 data-supply)**: 不動産所得ドメイン (`RealEstateIncome` +
`assemble_real_estate_income` + `real_estate_income_snapshot`) を追加し、KOA220 の収入側 内訳
(不動産所得の収入の内訳 / 地代家賃の内訳 / 借入金利子の内訳) の供給経路を実装した。**金額は仕訳
(受取家賃 4210/4220・地代家賃 7250・借入金 2510・利子 8210 の科目残高) から集計**し、賃借人/賃貸
契約期間/物件等の**仕訳が持てない契約メタは committed fixture** が補う (この分担は #124 で固定)。
seed_fy に不動産賃貸の合成年度 (`RE_ENTRIES`) と golden (`real_estate_income.json`) を足し、
`*_from_dataset` / `*_from_db` の二経路一致で突合する。契約内訳が受取家賃残高に foot しなければ
fail-loud。engine/renderer は不変。

**#125 で完了したこと (KOA240 収入側 data-supply)**: 農業所得ドメイン (`AgriculturalIncome` +
`assemble_agricultural_income` + `agricultural_income_snapshot`) を追加し、KOA240 の収入側 内訳
(農産物の収入の内訳 田畑/果樹/特殊施設 / 畜産物その他 / 雑収入 / 収入金額 / 未収穫農産物 / 販売用動物 /
果樹・牛馬等の育成費用の計算) の供給経路を実装した。**収入金額は仕訳 (農産物売上高 4310-4330・畜産物
売上高 4340・家事消費 4350・雑収入 4360・農産物棚卸 1185 の科目残高) から集計**し、区分/作付面積/収穫量等
の**仕訳が持てない記述メタと棚卸/育成費用の明細は committed fixture** が補う (#124 と同じ分担)。seed_fy に
農業の合成年度 (`AG_ENTRIES`) と golden (`agricultural_income.json`) を足し、`*_from_dataset` /
`*_from_db` の二経路一致で突合する。カテゴリ内訳が科目残高に foot しなければ fail-loud。engine/renderer は不変。

**#126 で完了したこと (stage 4: KOA220/240 spec 登録 + e2e)**: 収入側 snapshot を様式の 内訳ブロックへ
写す `EtaxFormatSpec` を登録した (`_SPEC_2025_KOA220` / `_SPEC_2025_KOA240`、version キー `2025-KOA220`
/ `2025-KOA240`)。三〜七つの 内訳 (賃貸/地代/利子・農産物/畜産/雑収入/棚卸/育成) はいずれも様式上の繰返し
ブロックなので `EtaxSection`、各 計 と収入金額 summary は `EtaxScalarField`。engine は KOA210 と同じ
data-driven のままで、入口 `build_real_estate_etax_export` / `build_agricultural_etax_export` が収入側
snapshot を選ぶ。seed_fy に 4 つの golden (`etax_export_koa220` / `etax_xtx_koa220` /
`etax_export_koa240` / `etax_xtx_koa240`) を足し、**実データ由来の `.xtx` が公式 KOA220-008 /
KOA240-008 の `.xsd` を pass** する (最小 `.xtx` だけでなく)。金額 (整数円) と区分・名称・数量表記
(文字) のみを写し、小数許容の数量・面積や和暦複合型の月は除外する。

**残り (follow-up)**: KOA220/240 の **経費・貸借対照表・減価償却** (ANF00120 / ANG\* / ANF00880 ・
APF00190 / APF02040) は本様式向けの data-supply が未実装のため未マッピング。供給後は KOA210 と同じ
`EtaxFixedSection` (#78) / `EtaxComputedField` (#83) 機構で同様に流用できる。MCP `export_etax` ツールは
現状 KOA210 (FinancialStatements) のみを公開しており、KOA220/240 を公開するには各所得の repository 経路が要る。

## 申告者ヘッダ プロフィール (#160)

KOA210 のヘッダ欄は様式上「任意欄」で、#78 の spec は値マッピングに集中し空欄のまま出力していた。
その結果、住所等を e-Tax ソフト側で毎年手入力する必要があった。#160 で、手編集前提の TOML
プロフィールから **平文ヘッダセルを自動供給**できるようにした。

**置き場所**: 既定は `~/.ai-books/etax/profile.toml` (秘匿情報を repo に入れない既存方針と整合)。
環境変数 `AI_BOOKS_ETAX_PROFILE` で上書き可 (主にテスト用)。未存在なら従来通り空欄で出力しエラーに
しない (既存利用者の後方互換 + 様式上も任意欄)。MCP `export_etax` は呼び出しごとに自動ロードする
(シグネチャ不変)。ローダは**読むだけ**でファイルを書かない。

**テンプレート** (`~/.ai-books/etax/profile.toml`):

```toml
[filer]
address = "東京都千代田区一番町1-1"          # 住所 (納税地) → AMB00010
business_office = "東京都港区芝公園4-2-8"     # 事業所所在地 → AMB00050
member_organization = "○○商工会"            # 加入団体名 (20 文字以内) → AMB00110
```

**供給できるのは平文セル 3 つだけ** (`AMB00010` 住所 / `AMB00050` 事業所所在地 / `AMB00110`
加入団体名)。これは `.xtx` が公式 `.xsd` を通る必要がある (#79) ため: KOA210 ヘッダの **氏名
(AMB00040) / フリガナ (AMB00030) / 業種名 (AMB00090) / 屋号 (AMB00100)** は `.xsd` 上 `gen:*ref`
型で、値ではなく**申告書本体 (e-Tax 送信の封筒側) で定義される ID を `IDREF` 属性で参照**する
(値テキストは KOA210 ファイル内に存在し得ない — e-Tax ソフトの**利用者情報**が一度だけ供給する)。
**電話 (AMB00070/00080)** は `gen:tel-number` 複合型 (tel1/tel2/tel3 の子要素) でテキストを直接持て
ない。いずれも様式内に平文セルが無いので本機能の対象外で、e-Tax ソフト側入力のまま。様式が将来これ
らを平文セル化したら `src/ai_books/etax/profile.py` の `HEADER_FIELDS` に 1 行足すだけで追従できる。

**検証**: 各値は emit 前に検証し、制御文字 (`\t\n\r` 以外) は**不正文字**、`maxLength` 超過は
**桁あふれ**として `EtaxValidationError` に全件まとめて報告する。プロフィール **あり** 経路は
`tests/test_etax_profile.py` (fixture profile + CSV/XML/.xtx 期待値 + XSD pass)、プロフィール
**なし** の golden byte 不変も同テストで pin する。

## 再現手順

```bash
# 1. 仕様 CAB を取得 → SHA256 検証 → 対象 .xlsx/.xsd を解凍 (cabextract/7z 不要)。
#    .xsd 検証ツリー (.cache/etax/schema/, shotoku+general+wrapper) も併せて用意される (#79)。
uv run python scripts/etax/fetch_etax_spec.py --out .cache/etax

# 2. 解凍済ワークブックからフィールドカタログを再生成 (committed JSON と一致するはず)
uv run python scripts/etax/build_field_catalog.py \
    --spec-dir .cache/etax/extracted --out docs/etax/field_catalog.json

# 3. .xsd から各様式レイアウト (.xtx renderer 用) を再生成 (committed と一致するはず, #79/#103)
uv run python scripts/etax/build_etax_layout.py \
    --xsd .cache/etax/extracted/KOA210-011.xsd --out src/ai_books/etax/koa210_layout.json
uv run python scripts/etax/build_etax_layout.py \
    --xsd .cache/etax/extracted/KOA220-008.xsd --out src/ai_books/etax/koa220_layout.json
uv run python scripts/etax/build_etax_layout.py \
    --xsd .cache/etax/extracted/KOA240-008.xsd --out src/ai_books/etax/koa240_layout.json

# 4. Vercel web root 用の committed 生成物を src から同期 (#141)
uv run python scripts/etax/sync_web_layouts.py
```

`.cache/` は生成物 (国税庁 著作物を含む) であり commit しないこと。SHA256 不一致時はスクリプトが
失敗する(国税庁の再公開時は `manifest.json` の sha256/日付を更新する → 年度追従 #78 のフック)。

## .xtx (実 e-Tax 交換ファイル) 出力と XSD 検証 (#79)

`ai_books.etax.export.render_etax_xtx`(MCP は `export_etax(format="xtx")`)は 決算書 を実様式の
XML ツリー(.xtx)として描画する。項目コードの**入れ子・順序・繰返し**は、上記 step 3 で .xsd から
導出した committed な派生物 `src/ai_books/etax/{koa210,koa220,koa240}_layout.json` が様式別に定義する
(コードに様式をハードコードしない — 様式改定はレイアウト再生成だけで追従, #79/#103)。レンダラは
EtaxExport の項目コード族(一般用 `AMF*` / 不動産 `ANF*` / 農業 `APF*` は様式間で重複なし)から様式を
自動判定し、対応する layout を選ぶ。どの様式 layout にも無い項目コードは fail loud で拒否する。ルート
`<KOA2x0>` は `VR`(様式バージョン)と `gen:FormAttribute`(softNM/sakuseiNM/sakuseiDay)を持ち、
名前空間は `http://xml.e-tax.nta.go.jp/XSD/shotoku`。

Vercel viewer は Root Directory が `web/` のため `../src` に依存できない。`web/lib/etax/layouts/`
の JSON は手編集対象ではなく、`scripts/etax/sync_web_layouts.py` が `src/ai_books/etax/*_layout.json`
から作る committed 生成物。pre-commit hook と `web/lib/etax/layouts.test.ts` が未同期を検出する。

**形式妥当性 (.xsd) の機械検証**: `tests/test_etax_xtx.py` が生成 .xtx を国税庁の各様式 .xsd
(`KOA210-011.xsd` / `KOA220-008.xsd` / `KOA240-008.xsd`、いずれも + 共通 `General.xsd` クロージャ)で
検証し、名前空間/必須属性/桁あふれ等の形式不正を機械検出する(検証は純Python の `xmlschema`、外部
バイナリ非依存)。KOA220/240 は spec 登録前 (#103) でも layout から生成した最小 .xtx が公式 .xsd を
pass することを検証し、layout の入れ子/順序/版が様式定義と一致していることを担保する。.xsd は 著作物のため非同梱なので、
**取得済みのとき(`.cache/etax/schema/`)のみ検証が走り**、未取得なら skip する(DB 連携テストが
`AI_BOOKS_DB_URL` 未設定で skip するのと同じ作法)。CI の **`etax-xsd` ジョブ**が毎 PR で取得→検証する
ため、形式ゲートは CI で常時 live。ローカルは step 1 を一度実行すれば `./scripts/test.sh -k etax` で
検証込みになる。スキーマの場所は `AI_BOOKS_ETAX_SCHEMA_DIR` で上書き可。

> 注: 各 KOA2x0 は `KOA2x0-<版>group` 内の **局所要素**(実 手続 電文がこのグループを参照する)で、文書
> ルートとして直接は検証できない。取得スクリプトが様式ごとに薄い検証用ラッパ `{koa210,koa220,koa240}_doc.xsd`
> (group を大域要素 `KOA2x0SET` として公開)を併せて書き出し、検証時に生成 `<KOA2x0>` を `<KOA2x0SET>` で包む。
> 完全な送信用 .xtx 電文(共通部・識別情報・手続)への封入と e-Taxソフト WEB版での実取込確認は **#80**
> (人間)で行う(手順・チェックリストは [`handoff-runbook.md`](./handoff-runbook.md))。本 Issue (#79)
> は **様式データの形式妥当性**を機械保証する最終ゲート。

## フィールドカタログ概要 (スポット確認用)

`field_catalog.json` は各帳票につき `{seq, item_code(=ＸＭＬタグ), group, name, kind, format,
int_digits, repeat, input_required, value_range, note}` を持つ。

- 項目数: KOA210=314 / KOA220=226 / KOA240=357 (計 897)。
- 金額項目の標準書式は `Z,ZZZ,ZZZ,ZZZ,ZZZ` = **整数13桁** (synthetic 既定の `DEFAULT_MAX_INT_DIGITS=13` と一致)。
- `int_digits` は書式マスク (`Z`/`9`) の桁数から導出。
- `input_required` は帳票フィールド仕様書の入力チェック(○)列。**XMLスキーマ上の `minOccurs` とは別**
  (多くの金額項目は `minOccurs=0`)。

KOA210(一般用) の損益計算書(1ページ目)主要項目 (スポット確認の起点):

| 項目コード                       | 項目名                                        | 桁  |
| -------------------------------- | --------------------------------------------- | --- |
| AMF00100                         | 売上（収入）金額                              | 13  |
| AMF00120 / 00130 / 00150 / 00160 | 期首商品棚卸 / 仕入 / 期末商品棚卸 / 差引原価 | 13  |
| AMF00170                         | 差引金額１ (売上総利益相当)                   | 13  |
| AMF00190–00370                   | 経費(固定勘定科目: 租税公課…雑費)             | 13  |
| AMF00380                         | 経費 計                                       | 13  |
| AMF00500 / 00510 / 00530         | 青色申告特別控除前所得 / 控除額 / 所得金額    | 13  |
| AMH00030–00220                   | 製造原価の計算 (期首原材料…製品製造原価)      | 13  |
| AMG00440 / 00760                 | 資産の部(期末)合計 / 負債・資本の部(期末)合計 | 13  |

## snapshot ↔ 実項目コード マッピング (#78 への引き継ぎ)

詳細は [`snapshot_mapping.json`](./snapshot_mapping.json)。**実装上重要な構造差分**:

1. **経費・資産・負債は固定勘定科目行**(+少数の追加科目枠)であり、synthetic の自由な繰返し内訳とは
   異なる。snapshot の `lines` を固定行へ割り付けるマッピング表が #78 で必須。
2. **貸借対照表は期首・期末の2列**。snapshot は期末残高のみ → 期首の供給方法を #78 で要件化。
3. **負債と純資産が「負債・資本の部」に統合**(純資産小計/負債小計の独立項目が無い)。
4. **損益に段階表示(営業利益/営業外/経常利益)が無い**: 差引金額1 → 経費 → 差引金額2 →
   各種引当金繰戻/繰入 → 青色申告特別控除前所得 → 控除額 → 所得金額。
5. **売上原価は内訳行ではなく** 期首+仕入−期末 の固定計算。仕入(AMF00130)は製造原価(AMH00220)と連動。
6. **ヘッダ必須メタ**(元号/年分・納税者住所/氏名・提出年月日・業種名・依頼税理士等)は snapshot に
   無いため、#78 で入力経路が必要。

## 受け入れ条件の対応

- [x] 令和7年分の確定版仕様(構造設計書/フィールド仕様/.xsd)を取得し、出所・版・日付を記録 → `manifest.json`
- [x] 青色申告決算書(一般/不動産/農業 + 製造原価)のフィールドカタログ(項目コード・桁・必須・繰返し)が
      機械可読形式で `docs/etax/` に存在 → `field_catalog.json` (897項目)
- [x] snapshot→実項目コードのマッピング草案 → `snapshot_mapping.json`
- [x] .xsd を検証に使える形で取り込み → **入手手順を文書化** (国税庁 著作物のため raw 非同梱、
      checksum 検証付き `fetch_etax_spec.py` で都度取得; #79 の xsd 検証はこの取得物を使う)
- [x] `./scripts/verify.sh` を壊さない(調査成果物 + ruff 準拠スクリプトの追加のみ)
