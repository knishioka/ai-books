import { describe, expect, it } from "vitest";

import type { FinancialStatementsSnapshot } from "../reports/financial-statements";
import {
  buildEtaxExport,
  etaxExportSnapshot,
  EtaxValidationError,
  parseEtaxFormat,
  renderEtax,
  renderEtaxCsv,
  renderEtaxXml,
  type EtaxExport,
} from "./export";

/**
 * A minimal 決算書 snapshot that satisfies every *required* scalar in the 2025 様式 with valid
 * whole-yen amounts, plus one row in a few sections so section records / account codes / month
 * values are exercised. Tests clone-and-mutate this to provoke specific validation faults.
 */
function validSnapshot(): FinancialStatementsSnapshot {
  return {
    report: "financial_statements",
    fiscal_year: "FY2025",
    start_date: "2025-01-01",
    end_date: "2025-12-31",
    profit_and_loss: {
      sales: { subtotal: "420000.00" },
      cost_of_goods_sold: {
        subtotal: "100000.00",
        lines: [{ code: "5110", name: "材料費", amount: "100000.00" }],
      },
      gross_profit: "320000.00",
      selling_admin_expenses: { subtotal: "20000.00", lines: [] },
      operating_income: "300000.00",
      non_operating_income: { subtotal: "0.00" },
      non_operating_expenses: { subtotal: "0.00" },
      ordinary_income: "300000.00",
      net_income: "300000.00",
    },
    monthly: {
      sales_total: "420000.00",
      purchases_total: "100000.00",
      rows: [{ month: "2025-01", sales: "420000.00", purchases: "100000.00" }],
    },
    depreciation: { total_depreciation: "5000.00", lines: [] },
    manufacturing_cost: {
      materials: { subtotal: "0.00" },
      labor: { subtotal: "0.00" },
      overhead: { subtotal: "0.00" },
      total_manufacturing_cost: "0.00",
      cost_of_goods_manufactured: "0.00",
    },
    balance_sheet: {
      total_assets: "500000.00",
      total_liabilities: "100000.00",
      total_equity: "400000.00",
      net_income: "300000.00",
      assets: [
        { lines: [{ code: "1110", name: "現金", balance: "500000.00" }] },
      ],
      liabilities: [],
      equity: [],
    },
  } as any;
}

describe("buildEtaxExport — happy path", () => {
  it("maps the snapshot to records and carries the form header", () => {
    const exported = buildEtaxExport(validSnapshot());
    expect(exported.formatVersion).toBe("2025");
    expect(exported.fiscalYear).toBe("FY2025");
    expect(exported.startDate).toBe("2025-01-01");
    expect(exported.endDate).toBe("2025-12-31");
    expect(exported.records.length).toBeGreaterThan(0);
  });

  it("renders amounts as whole yen (端数なし整数円)", () => {
    const exported = buildEtaxExport(validSnapshot());
    const sales = exported.records.find((r) => r.itemCode === "AMF00100");
    expect(sales?.value).toBe("420000"); // "420000.00" → integer string
  });

  it("attaches the account code to a 固定勘定科目行", () => {
    const exported = buildEtaxExport(validSnapshot());
    const cash = exported.records.find((r) => r.itemCode === "AMG00260"); // 現金 ← 1110
    expect(cash?.row).toBeNull(); // 固定行 は スカラ扱い
    expect(cash?.accountCode).toBe("1110");
    expect(cash?.value).toBe("500000");
  });

  it("maps a month's sales to its fixed per-month code", () => {
    const exported = buildEtaxExport(validSnapshot());
    const jan = exported.records.find((r) => r.itemCode === "AMF00600"); // 1月 売上
    expect(jan?.value).toBe("420000");
  });
});

describe("buildEtaxExport — 営業外 mapping policy (#83)", () => {
  function withNonOperating(
    lines: Array<{ code: string; name: string; amount: string }>,
  ): FinancialStatementsSnapshot {
    const snap = validSnapshot();
    // selling_admin に 経費 を1つ置き、橋渡しと合算する形をつくる.
    snap.profit_and_loss.selling_admin_expenses = {
      subtotal: "20000.00",
      lines: [{ code: "7250", name: "地代家賃", amount: "20000.00" }],
    } as never;
    (
      snap.profit_and_loss.non_operating_expenses as {
        subtotal: string;
        lines?: unknown[];
      }
    ).lines = lines;
    return snap;
  }

  it("bridges 利子割引料 (営業外費用 8210) into 経費 AMF00330", () => {
    const exported = buildEtaxExport(
      withNonOperating([
        { code: "8210", name: "利子割引料", amount: "21000.00" },
      ]),
    );
    const interest = exported.records.find((r) => r.itemCode === "AMF00330");
    expect(interest?.accountCode).toBe("8210");
    expect(interest?.value).toBe("21000");
  });

  it("folds the homed 営業外費用 into 経費計 / 差引金額２", () => {
    const exported = buildEtaxExport(
      withNonOperating([
        { code: "8210", name: "利子割引料", amount: "21000.00" },
      ]),
    );
    const byCode = Object.fromEntries(
      exported.records.map((r) => [r.itemCode, Number(r.value)]),
    );
    expect(byCode.AMF00380).toBe(41000); // 20000 (地代家賃) + 21000 (利子割引料)
    expect(byCode.AMF00390).toBe(300000 - 21000); // 営業利益 - 利子割引料
  });

  it("drops 営業外 with no home on 一般用 (雑損失 8220) without error", () => {
    const exported = buildEtaxExport(
      withNonOperating([
        { code: "8210", name: "利子割引料", amount: "21000.00" },
        { code: "8220", name: "雑損失", amount: "9999.00" },
      ]),
    );
    expect(exported.records.some((r) => r.accountCode === "8220")).toBe(false);
    const total = exported.records.find((r) => r.itemCode === "AMF00380");
    expect(Number(total?.value)).toBe(41000); // 雑損失 は寄与しない
  });
});

describe("buildEtaxExport — validation", () => {
  it("rejects a 端数 amount as not whole yen", () => {
    const snap = validSnapshot();
    snap.profit_and_loss.sales.subtotal = "420000.50";
    expect(() => buildEtaxExport(snap)).toThrow(EtaxValidationError);
  });

  it("reports a missing required scalar", () => {
    const snap = validSnapshot();
    // @ts-expect-error deliberately drop a required value
    snap.balance_sheet.total_assets = null;
    try {
      buildEtaxExport(snap);
      expect.unreachable("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(EtaxValidationError);
      const problems = (err as EtaxValidationError).problems;
      expect(problems.some((p) => p.itemCode === "AMG00440")).toBe(true);
    }
  });

  it("collects ALL problems in a single throw", () => {
    const snap = validSnapshot();
    snap.profit_and_loss.sales.subtotal = "1.5"; // not whole yen
    // @ts-expect-error drop a required value
    snap.monthly.sales_total = null; // missing
    snap.balance_sheet.assets[0].lines[0].code = "12"; // bad 科目コード (needs 3-4 digits)
    try {
      buildEtaxExport(snap);
      expect.unreachable("should have thrown");
    } catch (err) {
      const problems = (err as EtaxValidationError).problems;
      const codes = problems.map((p) => p.itemCode);
      expect(codes).toContain("AMF00100"); // 売上 端数
      expect(codes).toContain("AMF00980"); // 月別売上 計 欠落
      expect(codes).toContain("BS_ASSETS"); // 資産 固定行 section の不正コード
      expect(problems.length).toBeGreaterThanOrEqual(3);
    }
  });

  it("rejects a 科目コード that is not 3-4 digits", () => {
    const snap = validSnapshot();
    snap.balance_sheet.assets[0].lines[0].code = "11";
    expect(() => buildEtaxExport(snap)).toThrowError(/勘定科目コード/);
  });

  it("rejects an amount exceeding the integer-digit ceiling", () => {
    const snap = validSnapshot();
    snap.balance_sheet.total_assets = "99999999999999"; // 14 digits > 13
    expect(() => buildEtaxExport(snap)).toThrowError(
      /exceeds 13 integer digits/,
    );
  });

  it("rejects a malformed month (synthetic 月別 section emits a MONTH cell)", () => {
    // 実様式は月別を固定スカラで持つため、MONTH 種別の検証は合成様式の繰返し節で確かめる.
    const snap = validSnapshot();
    snap.monthly.rows[0].month = "2025/01";
    expect(() => buildEtaxExport(snap, "synthetic")).toThrowError(
      /invalid month/,
    );
  });

  it("error message names every problem", () => {
    const err = new EtaxValidationError([
      { itemCode: "PL010", row: "", message: "boom" },
      { itemCode: "BS010", row: "2", message: "bad code" },
    ]);
    expect(err.name).toBe("EtaxValidationError");
    expect(err.message).toContain("PL010");
    expect(err.message).toContain("BS010[row 2]");
    expect(err.message).toContain("2 problem(s)");
  });
});

describe("parseEtaxFormat", () => {
  it("accepts csv and xml", () => {
    expect(parseEtaxFormat("csv")).toBe("csv");
    expect(parseEtaxFormat("xml")).toBe("xml");
  });

  it("throws on an unknown format", () => {
    expect(() => parseEtaxFormat("pdf")).toThrowError(/format must be one of/);
  });
});

describe("renderEtaxCsv", () => {
  const exported: EtaxExport = {
    formatVersion: "2025",
    formId: "test",
    fiscalYear: "FY2025",
    startDate: "2025-01-01",
    endDate: "2025-12-31",
    records: [
      {
        form: "PL",
        itemCode: "PL010",
        label: "売上(収入)金額",
        kind: "amount",
        value: "420000",
        row: null,
        accountCode: null,
      },
      {
        form: "BS",
        itemCode: "BS011",
        label: 'a "quoted", comma',
        kind: "text",
        value: "現金",
        row: 1,
        accountCode: "1110",
      },
    ],
  };

  it("uses a header row and CRLF line endings, trailing newline", () => {
    const csv = renderEtaxCsv(exported);
    const lines = csv.split("\r\n");
    expect(lines[0]).toBe("面,項目コード,項目名,行,勘定科目コード,値");
    expect(csv.endsWith("\r\n")).toBe(true);
    expect(csv).not.toContain("\n\n");
  });

  it("renders a scalar row with empty 行 / 科目コード columns", () => {
    const csv = renderEtaxCsv(exported);
    expect(csv).toContain("PL,PL010,売上(収入)金額,,,420000");
  });

  it("quotes cells containing commas or quotes (RFC 4180)", () => {
    const csv = renderEtaxCsv(exported);
    expect(csv).toContain('"a ""quoted"", comma"');
  });
});

describe("renderEtaxXml", () => {
  const exported: EtaxExport = {
    formatVersion: "2025",
    formId: "form&<>",
    fiscalYear: "FY2025",
    startDate: "2025-01-01",
    endDate: "2025-12-31",
    records: [
      {
        form: "PL",
        itemCode: "PL010",
        label: "売上 <金額>",
        kind: "amount",
        value: "420000",
        row: null,
        accountCode: null,
      },
      {
        form: "BS",
        itemCode: "BS011",
        label: "現金",
        kind: "text",
        value: "A & B",
        row: 2,
        accountCode: "1110",
      },
    ],
  };

  it("emits a declaration and a root carrying the form header", () => {
    const xml = renderEtaxXml(exported);
    expect(xml.startsWith('<?xml version="1.0" encoding="UTF-8"?>\n')).toBe(
      true,
    );
    expect(xml).toContain('<etaxExport version="2025"');
    expect(xml.endsWith("</etaxExport>\n")).toBe(true);
  });

  it("escapes special characters in attributes and text", () => {
    const xml = renderEtaxXml(exported);
    expect(xml).toContain('form="form&amp;&lt;&gt;"');
    expect(xml).toContain("売上 &lt;金額&gt;");
    expect(xml).toContain("A &amp; B");
  });

  it("omits row/accountCode attrs for a scalar record but includes them for section rows", () => {
    const xml = renderEtaxXml(exported);
    const scalarLine = xml
      .split("\n")
      .find((l) => l.includes('itemCode="PL010"'))!;
    expect(scalarLine).not.toContain("row=");
    expect(scalarLine).not.toContain("accountCode=");
    const sectionLine = xml
      .split("\n")
      .find((l) => l.includes('itemCode="BS011"'))!;
    expect(sectionLine).toContain('row="2"');
    expect(sectionLine).toContain('accountCode="1110"');
  });
});

describe("renderEtax / etaxExportSnapshot", () => {
  it("dispatches to the requested concrete format", () => {
    const exported = buildEtaxExport(validSnapshot());
    expect(renderEtax(exported, "csv")).toBe(renderEtaxCsv(exported));
    expect(renderEtax(exported, "xml")).toBe(renderEtaxXml(exported));
  });

  it("produces the canonical snake_case JSON shape", () => {
    const exported = buildEtaxExport(validSnapshot());
    const snap = etaxExportSnapshot(exported);
    expect(snap.report).toBe("etax_export");
    expect(snap.format_version).toBe("2025");
    expect(snap.fiscal_year).toBe("FY2025");
    expect(snap.records[0]).toHaveProperty("item_code");
    expect(snap.records[0]).toHaveProperty("account_code");
  });
});
