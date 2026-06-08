import type { Sql } from "postgres";

import { formatMoney, parseMoney, sumMoney, ZERO, type Money } from "../money";
import { balanceFromTotals } from "./ledger";
import { statusFilter } from "./sql";
import type { EntryStatus, NormalSide } from "./types";

export interface RealEstateIncomeSnapshot {
  report: "real_estate_income";
  fiscal_year: string;
  start_date: string;
  end_date: string;
  rental_income: {
    lines: Array<{
      account_code: string;
      property_type: string;
      usage: string;
      location: string;
      tenant_address: string;
      tenant_name: string;
      contract_start_month: number;
      contract_end_month: number;
      rent_annual: string;
      key_money: string;
      right_money: string;
      renewal_fee: string;
      name_change_other: string;
      deposit: string;
      income_subtotal: string;
    }>;
    rent_annual_total: string;
    key_right_renewal_total: string;
    name_change_other_total: string;
    deposit_total: string;
    gross_income: string;
  };
  rent_paid: {
    lines: Array<{
      account_code: string;
      payee_address: string;
      payee_name: string;
      leased_property: string;
      right_money: string;
      renewal_fee: string;
      rent: string;
      deductible_expense: string;
    }>;
    rent_total: string;
    deductible_total: string;
  };
  loan_interest: {
    lines: Array<{
      payee_address: string;
      payee_name: string;
      year_end_balance: string;
      interest_paid: string;
      deductible_interest: string;
    }>;
    year_end_balance_total: string;
    interest_total: string;
    deductible_total: string;
  };
}

interface BalanceRow {
  code: string;
  normal_balance: NormalSide;
  debit_total: string;
  credit_total: string;
}

const RENTAL_PROPERTIES = [
  {
    accountCode: "4210",
    propertyType: "貸家",
    usage: "住宅用",
    location: "東京都杉並区○○1-2-3 甲アパート101",
    tenantAddress: "東京都杉並区○○4-5-6",
    tenantName: "賃借 一郎",
    contractStartMonth: 1,
    contractEndMonth: 12,
    rentAnnual: "1200000.00",
    keyMoney: "100000.00",
    rightMoney: "0.00",
    renewalFee: "0.00",
    nameChangeOther: "0.00",
    deposit: "200000.00",
  },
  {
    accountCode: "4220",
    propertyType: "貸事務所",
    usage: "住宅用以外",
    location: "東京都中野区△△7-8-9 乙ビル2F",
    tenantAddress: "東京都新宿区△△10-11-12",
    tenantName: "乙商事株式会社",
    contractStartMonth: 4,
    contractEndMonth: 3,
    rentAnnual: "900000.00",
    keyMoney: "0.00",
    rightMoney: "0.00",
    renewalFee: "60000.00",
    nameChangeOther: "0.00",
    deposit: "0.00",
  },
] as const;

const RENT_PAID_PAYEES = [
  {
    accountCode: "7250",
    payeeAddress: "東京都杉並区○○1-2-0",
    payeeName: "底地 地主丙",
    leasedProperty: "甲アパートの底地",
    rightMoney: "0.00",
    renewalFee: "0.00",
  },
] as const;

const LOAN_LENDERS = [
  {
    loanAccountCode: "2510",
    interestAccountCode: "8210",
    payeeAddress: "東京都千代田区□□1-1-1",
    payeeName: "○○銀行 △△支店",
  },
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

export async function fetchRealEstateIncome(
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
): Promise<RealEstateIncomeSnapshot> {
  const balances = await accountBalances(sql, start, end, status);
  const balance = (code: string): Money => balances.get(code) ?? ZERO;

  const rentalLines = RENTAL_PROPERTIES.map((property) => {
    const rentAnnual = fixed(property.rentAnnual);
    const keyMoney = fixed(property.keyMoney);
    const rightMoney = fixed(property.rightMoney);
    const renewalFee = fixed(property.renewalFee);
    const nameChangeOther = fixed(property.nameChangeOther);
    const incomeSubtotal =
      rentAnnual + keyMoney + rightMoney + renewalFee + nameChangeOther;
    const ledgerIncome = balance(property.accountCode);
    if (incomeSubtotal !== ledgerIncome) {
      console.warn(
        `real-estate sample mismatch for ${property.accountCode}: fixture ${money(incomeSubtotal)} != ledger ${money(ledgerIncome)}`,
      );
    }
    return {
      account_code: property.accountCode,
      property_type: property.propertyType,
      usage: property.usage,
      location: property.location,
      tenant_address: property.tenantAddress,
      tenant_name: property.tenantName,
      contract_start_month: property.contractStartMonth,
      contract_end_month: property.contractEndMonth,
      rent_annual: money(rentAnnual),
      key_money: money(keyMoney),
      right_money: money(rightMoney),
      renewal_fee: money(renewalFee),
      name_change_other: money(nameChangeOther),
      deposit: property.deposit,
      income_subtotal: money(incomeSubtotal),
    };
  });
  const rentPaidLines = RENT_PAID_PAYEES.map((payee) => {
    const rent = balance(payee.accountCode);
    return {
      account_code: payee.accountCode,
      payee_address: payee.payeeAddress,
      payee_name: payee.payeeName,
      leased_property: payee.leasedProperty,
      right_money: payee.rightMoney,
      renewal_fee: payee.renewalFee,
      rent: money(rent),
      deductible_expense: money(rent),
    };
  });
  const loanLines = LOAN_LENDERS.map((lender) => {
    const interest = balance(lender.interestAccountCode);
    return {
      payee_address: lender.payeeAddress,
      payee_name: lender.payeeName,
      year_end_balance: money(balance(lender.loanAccountCode)),
      interest_paid: money(interest),
      deductible_interest: money(interest),
    };
  });

  const rentalAmount = (key: keyof (typeof rentalLines)[number]): Money =>
    sumMoney(rentalLines.map((line) => fixed(String(line[key]))));

  return {
    report: "real_estate_income",
    fiscal_year: fiscalYear,
    start_date: start,
    end_date: end,
    rental_income: {
      lines: rentalLines,
      rent_annual_total: money(rentalAmount("rent_annual")),
      key_right_renewal_total: money(
        rentalAmount("key_money") +
          rentalAmount("right_money") +
          rentalAmount("renewal_fee"),
      ),
      name_change_other_total: money(rentalAmount("name_change_other")),
      deposit_total: money(rentalAmount("deposit")),
      gross_income: money(rentalAmount("income_subtotal")),
    },
    rent_paid: {
      lines: rentPaidLines,
      rent_total: money(sumMoney(rentPaidLines.map((line) => fixed(line.rent)))),
      deductible_total: money(
        sumMoney(rentPaidLines.map((line) => fixed(line.deductible_expense))),
      ),
    },
    loan_interest: {
      lines: loanLines,
      year_end_balance_total: money(
        sumMoney(loanLines.map((line) => fixed(line.year_end_balance))),
      ),
      interest_total: money(sumMoney(loanLines.map((line) => fixed(line.interest_paid)))),
      deductible_total: money(
        sumMoney(loanLines.map((line) => fixed(line.deductible_interest))),
      ),
    },
  };
}
