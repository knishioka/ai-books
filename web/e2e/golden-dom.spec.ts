import { expect, test, type Locator, type Page } from "@playwright/test";

import { OWNER_STORAGE_STATE } from "../playwright.config";
import { formatAmount, loadGolden } from "./helpers/golden";

/**
 * Golden figures in the DOM (issue #164). The `verify:golden` cross-check already proves the
 * viewer's *data layer* reproduces the golden snapshots byte-for-byte; this proves the orthogonal
 * thing it cannot — that those numbers actually reach the rendered page, through the production
 * formatter (`lib/format`), in the cell the reader looks at. It catches "the aggregate is right but
 * the screen shows it wrong" (a dropped cell, a swapped column, a formatter regression).
 *
 * Expected figures are read from the *same* golden files, then formatted with the *same*
 * `formatAmount` the components use — so nudging a golden by one yen flips the expectation and the
 * assertion fails (the figures are genuinely pinned, not formalistically asserted). Every page is
 * pinned to `?fy=FY2025`, the fiscal year the goldens were frozen from, so the assertions never
 * depend on which year the viewer defaults to.
 */
test.use({ storageState: OWNER_STORAGE_STATE });

const FY = "FY2025";

/** The report row whose 科目名 / 見出し (its `<th scope="row">`) is exactly `rowheader`. */
function reportRow(page: Page, rowheader: string): Locator {
  return page
    .getByRole("row")
    .filter({
      has: page.getByRole("rowheader", { name: rowheader, exact: true }),
    });
}

/** Assert the row headed `rowheader` shows `amount` (a fixed-point golden string) as a figure. */
async function expectRowFigure(
  page: Page,
  rowheader: string,
  amount: string,
): Promise<void> {
  await expect(
    reportRow(page, rowheader),
    `row 「${rowheader}」 should show ${formatAmount(amount)}`,
  ).toContainText(formatAmount(amount));
}

interface TrialBalanceGolden {
  rows: Array<{ code: string; name: string; balance: string }>;
  total_debit: string;
}

interface ProfitAndLossGolden {
  sales: { subtotal: string };
  gross_profit: string;
  net_income: string;
}

interface BalanceSheetGolden {
  total_assets: string;
  total_liabilities: string;
  net_income: string;
}

interface FinancialStatementsGolden {
  profit_and_loss: { sales: { subtotal: string } };
  monthly: { purchases_total: string };
  depreciation: { total_depreciation: string };
  balance_sheet: { total_assets: string };
}

test("合計残高試算表 shows golden 残高 and 合計", async ({ page }) => {
  const tb = loadGolden<TrialBalanceGolden>("trial_balance");
  const cash = tb.rows.find((r) => r.code === "1110")!; // 現金
  const receivable = tb.rows.find((r) => r.code === "1160")!; // 売掛金

  await page.goto(`/trial-balance?fy=${FY}`);
  await expect(
    page.getByRole("heading", { level: 1, name: "合計残高試算表" }),
  ).toBeVisible();

  await expectRowFigure(page, "現金", cash.balance);
  await expectRowFigure(page, "売掛金", receivable.balance);
  // 借貸平均 — the 合計 footer carries both totals.
  await expectRowFigure(page, "合計", tb.total_debit);
});

test("損益計算書 shows golden 売上高・段階利益 (incl. △ loss)", async ({
  page,
}) => {
  const pl = loadGolden<ProfitAndLossGolden>("profit_and_loss");

  await page.goto(`/pl?fy=${FY}`);
  await expect(
    page.getByRole("heading", { level: 1, name: "損益計算書" }),
  ).toBeVisible();

  await expectRowFigure(page, "売上高 計", pl.sales.subtotal);
  await expectRowFigure(page, "売上総利益", pl.gross_profit);
  // 当期純損失 — a negative figure must render as 三角 (△580,500), proving the accounting
  // formatter ran rather than a raw "-580500".
  expect(formatAmount(pl.net_income)).toMatch(/^△/);
  await expectRowFigure(
    page,
    "当期純利益（青色申告特別控除前）",
    pl.net_income,
  );
});

test("貸借対照表 shows golden 資産合計・負債合計・当期純利益", async ({
  page,
}) => {
  const bs = loadGolden<BalanceSheetGolden>("balance_sheet");

  await page.goto(`/bs?fy=${FY}`);
  await expect(
    page.getByRole("heading", { level: 1, name: "貸借対照表" }),
  ).toBeVisible();

  await expectRowFigure(page, "資産合計", bs.total_assets);
  await expectRowFigure(page, "負債合計", bs.total_liabilities);
  await expectRowFigure(page, "当期純利益", bs.net_income);
});

test("青色申告決算書プレビュー shows golden figures across its 面", async ({
  page,
}) => {
  const fs = loadGolden<FinancialStatementsGolden>("financial_statements");

  await page.goto(`/statements?fy=${FY}`);
  await expect(
    page.getByRole("heading", { level: 1, name: "青色申告決算書" }),
  ).toBeVisible();

  // 1面 損益計算書: 売上高 計.
  await expectRowFigure(page, "売上高 計", fs.profit_and_loss.sales.subtotal);
  // 2面 月別: the 合計 footer carries 仕入金額合計.
  await expectRowFigure(page, "合計", fs.monthly.purchases_total);
  // 3面 減価償却費: 本年分の償却費 合計.
  await expectRowFigure(
    page,
    "本年分の償却費 合計",
    fs.depreciation.total_depreciation,
  );
  // 4面 貸借対照表: 資産合計.
  await expectRowFigure(page, "資産合計", fs.balance_sheet.total_assets);
});
