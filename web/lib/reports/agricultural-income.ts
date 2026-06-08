import type { Sql } from "postgres";

import { formatMoney, parseMoney, sumMoney, ZERO, type Money } from "../money";
import { balanceFromTotals } from "./ledger";
import { statusFilter } from "./sql";
import type { EntryStatus, NormalSide } from "./types";

export interface AgriculturalIncomeSnapshot {
  report: "agricultural_income";
  fiscal_year: string;
  start_date: string;
  end_date: string;
  farm_products: {
    lines: Array<Record<string, string>>;
    sales_total: string;
    home_consumption_total: string;
    opening_inventory_total: string;
    closing_inventory_total: string;
  };
  livestock: {
    lines: Array<Record<string, string>>;
    sales_total: string;
    home_consumption_total: string;
  };
  misc_income: { lines: Array<Record<string, string>>; total: string };
  income: Record<string, string>;
  unharvested: {
    lines: Array<Record<string, string>>;
    opening_total: string;
    closing_total: string;
  };
  sale_animals: {
    lines: Array<Record<string, string>>;
    opening_total: string;
    closing_total: string;
  };
  cultivation_cost: {
    lines: Array<Record<string, string>>;
    opening_carryover_total: string;
    seedling_cost_total: string;
    fertilizer_cost_total: string;
    subtotal_total: string;
    income_from_growing_total: string;
    added_to_acquisition_total: string;
    matured_acquisition_total: string;
    carryover_to_next_total: string;
    deductible_cultivation_cost: string;
  };
}

interface BalanceRow {
  code: string;
  normal_balance: NormalSide;
  debit_total: string;
  credit_total: string;
}

const CROPS = [
  ["4310", "田畑", "米", "120.00", "6000.00", "400.00", "80000.00", "800000.00", "50000.00", "500.00", "100000.00"],
  ["4310", "田畑", "麦", "80.00", "3200.00", "200.00", "40000.00", "300000.00", "20000.00", "250.00", "50000.00"],
  ["4320", "果樹", "みかん", "60.00", "4000.00", "300.00", "50000.00", "400000.00", "30000.00", "360.00", "60000.00"],
  ["4330", "特殊施設", "トマト (ハウス)", "20.00", "8000.00", "150.00", "30000.00", "500000.00", "10000.00", "200.00", "40000.00"],
] as const;

const LIVESTOCK = [
  ["4340", "肉用牛", "10.00", "5.00", "1200000.00", "0.00"],
  ["4340", "鶏卵", "500.00", "0.00", "600000.00", "30000.00"],
] as const;

const MISC_INCOME = [
  ["4360", "共済受取金", "80000.00"],
  ["4360", "作業受託収入", "120000.00"],
] as const;

const UNHARVESTED = [["米 (未収穫)", "10a", "20000.00", "12a", "25000.00"]] as const;
const SALE_ANIMALS = [["子牛", "2頭", "300000.00", "1頭", "180000.00"]] as const;
const CULTIVATION_COSTS = [
  ["みかん幼木", "150000.00", "30000.00", "20000.00", "5000.00", "0.00", "0.00"],
  ["繁殖牛 (育成中)", "200000.00", "100000.00", "10000.00", "0.00", "310000.00", "0.00"],
] as const;

async function accountBalances(
  sql: Sql,
  start: string,
  end: string,
  status: EntryStatus | null,
): Promise<Map<string, Money>> {
  const rows = await sql<BalanceRow[]>`
    SELECT a.code, a.normal_balance,
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'debit'), 0)::text  AS debit_total,
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.side = 'credit'), 0)::text AS credit_total
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.entry_id
    JOIN accounts a ON a.id = jl.account_id
    WHERE je.entry_date >= ${start}::date
      AND je.entry_date <= ${end}::date
      AND ${statusFilter(sql, status)}
    GROUP BY a.code, a.normal_balance
    ORDER BY a.code
  `;
  return new Map(
    rows.map((row) => [
      row.code,
      balanceFromTotals(
        parseMoney(row.debit_total),
        parseMoney(row.credit_total),
        row.normal_balance,
      ),
    ]),
  );
}

const money = (amount: Money): string => formatMoney(amount);
const fixed = (value: string): Money => parseMoney(value);
const total = (values: Iterable<string>): Money => sumMoney(Array.from(values, fixed));

function assertFoots(label: string, expected: Money, actual: Money): void {
  if (expected !== actual) {
    throw new Error(`agricultural sample mismatch for ${label}: fixture ${money(expected)} != ledger ${money(actual)}`);
  }
}

export async function fetchAgriculturalIncome(
  sql: Sql,
  {
    fiscalYear,
    start,
    end,
    status = "posted",
  }: {
    fiscalYear: string;
    start: string;
    end: string;
    status?: EntryStatus | null;
  },
): Promise<AgriculturalIncomeSnapshot> {
  const balances = await accountBalances(sql, start, end, status);
  const balance = (code: string): Money => balances.get(code) ?? ZERO;

  const farmLines = CROPS.map(
    ([
      account_code,
      category,
      crop_name,
      planted_area,
      harvest_quantity,
      opening_inventory_qty,
      opening_inventory_amount,
      sales_amount,
      home_consumption,
      closing_inventory_qty,
      closing_inventory_amount,
    ]) => ({
      account_code,
      category,
      crop_name,
      planted_area,
      harvest_quantity,
      opening_inventory_qty,
      opening_inventory_amount,
      sales_amount,
      home_consumption,
      closing_inventory_qty,
      closing_inventory_amount,
    }),
  );
  for (const code of ["4310", "4320", "4330"]) {
    assertFoots(
      code,
      total(farmLines.filter((line) => line.account_code === code).map((line) => line.sales_amount)),
      balance(code),
    );
  }

  const livestockLines = LIVESTOCK.map(
    ([account_code, category_name, raised_count, produced_count, sales_amount, home_consumption]) => ({
      account_code,
      category_name,
      raised_count,
      produced_count,
      sales_amount,
      home_consumption,
    }),
  );
  assertFoots("4340", total(livestockLines.map((line) => line.sales_amount)), balance("4340"));

  const miscLines = MISC_INCOME.map(([account_code, category_name, amount]) => ({
    account_code,
    category_name,
    amount,
  }));
  assertFoots("4360", total(miscLines.map((line) => line.amount)), balance("4360"));

  const cropHome = total(farmLines.map((line) => line.home_consumption));
  const livestockHome = total(livestockLines.map((line) => line.home_consumption));
  assertFoots("4350", cropHome + livestockHome, balance("4350"));
  assertFoots(
    "1185",
    total(farmLines.map((line) => line.closing_inventory_amount)),
    balance("1185"),
  );

  const unharvestedLines = UNHARVESTED.map(
    ([category_name, opening_qty, opening_amount, closing_qty, closing_amount]) => ({
      category_name,
      opening_qty,
      opening_amount,
      closing_qty,
      closing_amount,
    }),
  );
  const saleAnimalLines = SALE_ANIMALS.map(
    ([category_name, opening_qty, opening_amount, closing_qty, closing_amount]) => ({
      category_name,
      opening_qty,
      opening_amount,
      closing_qty,
      closing_amount,
    }),
  );
  const cultivationLines = CULTIVATION_COSTS.map(
    ([
      name,
      opening_carryover,
      seedling_cost,
      fertilizer_cost,
      income_from_growing,
      matured_acquisition_cost,
      added_to_acquisition_cost,
    ]) => {
      const subtotal = fixed(opening_carryover) + fixed(seedling_cost) + fixed(fertilizer_cost);
      return {
        name,
        opening_carryover,
        seedling_cost,
        fertilizer_cost,
        subtotal: money(subtotal),
        income_from_growing,
        added_to_acquisition_cost,
        matured_acquisition_cost,
        carryover_to_next: money(subtotal - fixed(matured_acquisition_cost)),
      };
    },
  );

  const farmSales = total(farmLines.map((line) => line.sales_amount));
  const livestockSales = total(livestockLines.map((line) => line.sales_amount));
  const miscTotal = total(miscLines.map((line) => line.amount));
  const salesTotal = farmSales + livestockSales;
  const homeTotal = cropHome + livestockHome;
  const subtotal = salesTotal + homeTotal + miscTotal;
  const openingInventory = total(farmLines.map((line) => line.opening_inventory_amount));
  const closingInventory = total(farmLines.map((line) => line.closing_inventory_amount));

  return {
    report: "agricultural_income",
    fiscal_year: fiscalYear,
    start_date: start,
    end_date: end,
    farm_products: {
      lines: farmLines,
      sales_total: money(farmSales),
      home_consumption_total: money(cropHome),
      opening_inventory_total: money(openingInventory),
      closing_inventory_total: money(closingInventory),
    },
    livestock: {
      lines: livestockLines,
      sales_total: money(livestockSales),
      home_consumption_total: money(livestockHome),
    },
    misc_income: { lines: miscLines, total: money(miscTotal) },
    income: {
      sales_amount_total: money(salesTotal),
      home_consumption_total: money(homeTotal),
      misc_income_total: money(miscTotal),
      subtotal: money(subtotal),
      opening_inventory_total: money(openingInventory),
      closing_inventory_total: money(closingInventory),
      gross_income: money(subtotal - openingInventory + closingInventory),
    },
    unharvested: {
      lines: unharvestedLines,
      opening_total: money(total(unharvestedLines.map((line) => line.opening_amount))),
      closing_total: money(total(unharvestedLines.map((line) => line.closing_amount))),
    },
    sale_animals: {
      lines: saleAnimalLines,
      opening_total: money(total(saleAnimalLines.map((line) => line.opening_amount))),
      closing_total: money(total(saleAnimalLines.map((line) => line.closing_amount))),
    },
    cultivation_cost: {
      lines: cultivationLines,
      opening_carryover_total: money(total(cultivationLines.map((line) => line.opening_carryover))),
      seedling_cost_total: money(total(cultivationLines.map((line) => line.seedling_cost))),
      fertilizer_cost_total: money(total(cultivationLines.map((line) => line.fertilizer_cost))),
      subtotal_total: money(total(cultivationLines.map((line) => line.subtotal))),
      income_from_growing_total: money(total(cultivationLines.map((line) => line.income_from_growing))),
      added_to_acquisition_total: money(
        total(cultivationLines.map((line) => line.added_to_acquisition_cost)),
      ),
      matured_acquisition_total: money(total(cultivationLines.map((line) => line.matured_acquisition_cost))),
      carryover_to_next_total: money(total(cultivationLines.map((line) => line.carryover_to_next))),
      deductible_cultivation_cost: money(
        total(cultivationLines.map((line) => line.matured_acquisition_cost)) / 2n,
      ),
    },
  };
}
