import type { Sql } from "postgres";
import { describe, expect, it } from "vitest";

import { fetchGeneralLedger } from "./general-ledger";
import { fetchJournalBook } from "./journal-book";
import { fetchMonthlyTrend } from "./monthly-trend";

/**
 * See `aggregation.test.ts` — a DB-free `sql` stand-in returning canned result sets in await
 * order so the per-row running-balance / counter-account / footing logic of the 帳簿 reports can
 * be pinned without a database. (Numbers are also checked end-to-end by the golden cross-check.)
 */
function fakeSql(...resultSets: Record<string, unknown>[][]): Sql {
  let i = 0;
  const tag = () => ({
    then(
      onFulfilled: (rows: Record<string, unknown>[]) => unknown,
      onRejected?: (reason: unknown) => unknown,
    ) {
      const rows = i < resultSets.length ? resultSets[i++] : [];
      return Promise.resolve(rows).then(onFulfilled, onRejected);
    },
  });
  return tag as unknown as Sql;
}

describe("fetchJournalBook", () => {
  it("inlines each line's 科目 and foots 借方/貸方 over the listed lines", async () => {
    const headers = [
      {
        id: "1",
        entry_date: "2025-01-05",
        voucher_no: "V1",
        description: "売上",
        status: "posted",
        void_reason: null,
      },
    ];
    const lines = [
      {
        entry_id: "1",
        code: "1110",
        name: "現金",
        side: "debit",
        amount: "420000.00",
        line_description: null,
      },
      {
        entry_id: "1",
        code: "4110",
        name: "売上高",
        side: "credit",
        amount: "420000.00",
        line_description: "売上計上",
      },
    ];
    const book = await fetchJournalBook(fakeSql(headers, lines), {
      start: "2025-01-01",
      end: "2025-12-31",
    });
    expect(book.entries).toHaveLength(1);
    expect(book.entries[0].lines).toHaveLength(2);
    expect(book.entries[0].lines[1].account_name).toBe("売上高");
    expect(book.total_debit).toBe("420000.00");
    expect(book.total_credit).toBe("420000.00");
  });

  it("returns no entries and zero footings on an empty period", async () => {
    const book = await fetchJournalBook(fakeSql([]), {});
    expect(book.entries).toEqual([]);
    expect(book.total_debit).toBe("0.00");
    expect(book.total_credit).toBe("0.00");
  });
});

describe("fetchMonthlyTrend", () => {
  it("rolls the opening balance forward month by month, tiling quiet months", async () => {
    const accountMeta = [
      { id: "7", code: "1110", name: "現金", normal_balance: "debit" },
    ];
    const opening = [{ debit_total: "50000.00", credit_total: "0.00" }];
    const monthRows = [
      { month: "2025-02", debit_total: "30000.00", credit_total: "0.00" },
    ];
    const trend = await fetchMonthlyTrend(
      fakeSql(accountMeta, opening, monthRows),
      {
        codes: ["1110"],
        fiscalYear: "FY2025",
        start: "2025-01-01",
        end: "2025-03-31",
      },
    );
    const account = trend.accounts[0];
    expect(account.opening_balance).toBe("50000.00");
    expect(account.points.map((p) => p.month)).toEqual([
      "2025-01",
      "2025-02",
      "2025-03",
    ]);
    // Quiet January: balance unchanged from opening.
    expect(account.points[0].net_change).toBe("0.00");
    expect(account.points[0].closing_balance).toBe("50000.00");
    // February activity moves the running balance; March carries it forward.
    expect(account.points[1].net_change).toBe("30000.00");
    expect(account.points[1].closing_balance).toBe("80000.00");
    expect(account.points[2].closing_balance).toBe("80000.00");
    expect(account.closing_balance).toBe("80000.00");
  });

  it("throws when a requested 勘定科目コード does not exist", async () => {
    await expect(
      fetchMonthlyTrend(fakeSql([]), {
        codes: ["9999"],
        fiscalYear: "FY2025",
        start: "2025-01-01",
        end: "2025-03-31",
      }),
    ).rejects.toThrowError(/account 9999 not found/);
  });
});

describe("fetchGeneralLedger", () => {
  it("carries the 繰越 forward and moves the running balance in the normal direction", async () => {
    const accounts = [
      { id: "7", code: "1110", name: "現金", normal_balance: "debit" },
    ];
    const opening = [{ debit_total: "100000.00", credit_total: "0.00" }];
    const lineRows = [
      {
        entry_id: "1",
        line_no: 1,
        entry_date: "2025-02-01",
        voucher_no: "V1",
        description: "売上",
        line_description: null,
        side: "debit",
        amount: "420000.00",
      },
      {
        entry_id: "2",
        line_no: 1,
        entry_date: "2025-03-01",
        voucher_no: "V2",
        description: "支払",
        line_description: null,
        side: "credit",
        amount: "20000.00",
      },
    ];
    // Counter rows include a duplicate 4110 to exercise order-preserving dedup.
    const counters = [
      { entry_id: "1", code: "4110" },
      { entry_id: "1", code: "4110" },
      { entry_id: "2", code: "5110" },
    ];
    const gl = await fetchGeneralLedger(
      fakeSql(accounts, opening, lineRows, counters),
      { accountCode: "1110", start: "2025-01-01", end: "2025-12-31" },
    );
    const account = gl.accounts[0];
    expect(account.opening_balance).toBe("100000.00");
    expect(account.rows[0].running_balance).toBe("520000.00"); // +420000 借方
    expect(account.rows[0].counter_accounts).toEqual(["4110"]); // dup collapsed
    expect(account.rows[1].running_balance).toBe("500000.00"); // −20000 貸方
    expect(account.rows[1].counter_accounts).toEqual(["5110"]);
    expect(account.closing_balance).toBe("500000.00");
  });

  it("throws when the requested account does not exist", async () => {
    await expect(
      fetchGeneralLedger(fakeSql([]), { accountCode: "9999" }),
    ).rejects.toThrowError(/account 9999 not found/);
  });
});
