import { MEMBER_A, MEMBER_B } from "~/lib/household";
import { NextRequest, NextResponse } from "next/server";
import { and, asc, eq, gte, lte } from "drizzle-orm";
import { db } from "~/server/db";
import { expenses } from "~/server/db/schema";

function parseDateParam(value: string | null) {
  if (!value) {
    return null;
  }

  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;
}

function formatHouseholdDate(value: string) {
  const [year, month, day] = value.split("-");
  if (!year || !month || !day) {
    return value;
  }

  return `${Number(month)}/${Number(day)}/${year}`;
}

function formatCurrency(value: number | null) {
  const normalized = Number(value ?? 0);
  return `$${normalized.toFixed(2)}`;
}

function formatSplit(value: number | null) {
  if (value === null || value === undefined) {
    return "1";
  }

  return `${value}`;
}

function formatBoolean(value: boolean | null) {
  return value ? "TRUE" : "";
}

function escapeCsv(value: string) {
  if (/[",\n]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`;
  }

  return value;
}

export async function GET(request: NextRequest) {
  const url = new URL(request.url);
  const startDate = parseDateParam(url.searchParams.get("startDate"));
  const endDate = parseDateParam(url.searchParams.get("endDate"));
  const includeDuplicates = url.searchParams.get("includeDuplicates") === "true";
  const workOnly = url.searchParams.get("logToWork") === "true";

  const conditions = [];
  if (!includeDuplicates) {
    conditions.push(eq(expenses.isDuplicate, false));
  }
  if (workOnly) {
    conditions.push(eq(expenses.logToWork, true));
  }
  if (startDate) {
    conditions.push(gte(expenses.date, startDate));
  }
  if (endDate) {
    conditions.push(lte(expenses.date, endDate));
  }

  const rows = await db.query.expenses.findMany({
    where: conditions.length > 0 ? and(...conditions) : undefined,
    orderBy: [asc(expenses.date), asc(expenses.id)],
  });

  const header = [
    "Date",
    "Type",
    "Subtype",
    "Merchant",
    "Amount",
    "Split",
    `${MEMBER_B} Spend`,
    "Spender",
    `${MEMBER_A} Spend`,
    "Card",
    "Log_to_work",
    "Tag",
    "Link",
    "Track with Friends",
  ];

  const lines = [
    header.join(","),
    ...rows.map((row) =>
      [
        formatHouseholdDate(row.date),
        row.type ?? "",
        row.subtype ?? "",
        row.merchant ?? "",
        formatCurrency(row.amount),
        formatSplit(row.split),
        formatCurrency(row.memberBSpend),
        row.spender ?? "",
        formatCurrency(row.memberASpend),
        row.card ?? "",
        formatBoolean(row.logToWork),
        row.tag ?? "",
        row.link ?? "",
        row.trackWithFriends ?? "",
      ]
        .map((value) => escapeCsv(String(value)))
        .join(",")
    ),
  ];

  const suffixStart = startDate ?? "beginning";
  const suffixEnd = endDate ?? "latest";
  const filename = workOnly
    ? `household-work-expenses-${suffixStart}-to-${suffixEnd}.csv`
    : `household-expenses-${suffixStart}-to-${suffixEnd}.csv`;

  return new NextResponse(lines.join("\n"), {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="${filename}"`,
    },
  });
}
