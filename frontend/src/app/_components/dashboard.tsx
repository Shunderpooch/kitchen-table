"use client";

import { MEMBER_A, MEMBER_B } from "~/lib/household";

import Link from "next/link";
import { useMemo } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "~/trpc/react";

function formatDateInput(value: Date) {
  return value.toISOString().slice(0, 10);
}

function shiftWeeks(value: Date, weeks: number) {
  const next = new Date(value);
  next.setDate(next.getDate() + weeks * 7);
  return next;
}

function formatMoney(value: number) {
  return value.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });
}

function formatMoneyPrecise(value: number) {
  return value.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function Dashboard() {
  const today = useMemo(() => new Date(), []);
  const endDate = formatDateInput(today);
  const startDate = formatDateInput(shiftWeeks(today, -12));

  const { data: validationStats } = api.validation.getStats.useQuery();
  const reviewQuery = api.expenses.getReviewInsights.useQuery({
    startDate,
    endDate,
    categories: [],
    granularity: "week",
  });
  const allTimeSettlementQuery = api.expenses.getReviewInsights.useQuery({
    endDate,
    categories: [],
    granularity: "month",
  });

  const pendingCount = validationStats?.pending ?? 0;
  const reviewData = reviewQuery.data;
  const settlementData = allTimeSettlementQuery.data?.settlement;

  return (
    <div className="space-y-8">
      <DashboardHeader startDate={startDate} endDate={endDate} />
      <QuickStatsGrid
        pendingCount={pendingCount}
        summary={reviewData?.summary}
        settlement={settlementData}
      />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
        <SpendingOverviewCard trend={reviewData?.trend ?? []} isLoading={reviewQuery.isLoading} />
        <SettlementOverviewCard
          settlement={settlementData}
          isLoading={allTimeSettlementQuery.isLoading}
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <CategoryOverviewCard
          categories={reviewData?.categories ?? []}
          isLoading={reviewQuery.isLoading}
        />
        <RecentExpensesCard
          expenses={reviewData?.recentExpenses ?? []}
          isLoading={reviewQuery.isLoading}
        />
      </div>
    </div>
  );
}

function DashboardHeader({
  startDate,
  endDate,
}: {
  startDate: string;
  endDate: string;
}) {
  const startLabel = new Date(`${startDate}T00:00:00`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
  const endLabel = new Date(`${endDate}T00:00:00`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });

  return (
    <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
      <div>
        <h1 className="text-2xl font-semibold text-zinc-100">Dashboard</h1>
        <p className="text-zinc-400">
          Last 12 weeks of real spending, based on expense date from the imported records.
        </p>
      </div>
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 px-4 py-3 text-sm text-zinc-300">
        {startLabel} to {endLabel}
      </div>
    </div>
  );
}

function QuickStatsGrid({
  pendingCount,
  summary,
  settlement,
}: {
  pendingCount: number;
  summary?:
    | {
        totalAmount: number;
        memberASpend: number;
        memberBSpend: number;
        transactionCount: number;
        avgTransaction: number;
      }
    | undefined;
  settlement?:
    | {
        direction: "b_owes_a" | "a_owes_b" | "settled";
        absoluteAmount: number;
      }
    | undefined;
}) {
  const netDueLabel =
    settlement?.direction === "b_owes_a"
      ? `${MEMBER_B} owes ${MEMBER_A}`
      : settlement?.direction === "a_owes_b"
        ? `${MEMBER_A} owes ${MEMBER_B}`
        : "Net due";

  const netDueValue =
    settlement?.direction === "settled"
      ? "Settled"
      : formatMoney(settlement?.absoluteAmount ?? 0);

  const stats = [
    {
      label: "12 Week Spend",
      value: formatMoney(summary?.totalAmount ?? 0),
      detail: `${summary?.transactionCount ?? 0} validated expenses`,
    },
    {
      label: `${MEMBER_A} Share`,
      value: formatMoney(summary?.memberASpend ?? 0),
      detail: "Expense split allocation",
    },
    {
      label: `${MEMBER_B} Share`,
      value: formatMoney(summary?.memberBSpend ?? 0),
      detail: "Expense split allocation",
    },
    {
      label: netDueLabel,
      value: netDueValue,
      detail:
        settlement?.direction === "settled"
          ? "No all-time outstanding balance"
          : "All-time after logged payments",
    },
    {
      label: "Pending Validation",
      value: pendingCount.toString(),
      detail: "Items still needing review",
    },
  ];

  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
      {stats.map((stat) => (
        <div
          key={stat.label}
          className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-5"
        >
          <p className="text-sm text-zinc-400">{stat.label}</p>
          <p className="mt-2 text-3xl font-semibold text-zinc-100">{stat.value}</p>
          <p className="mt-2 text-sm text-zinc-500">{stat.detail}</p>
        </div>
      ))}
    </div>
  );
}

function SpendingOverviewCard({
  trend,
  isLoading,
}: {
  trend: Array<{ label: string; total: number; memberA: number; memberB: number }>;
  isLoading: boolean;
}) {
  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6">
      <div className="mb-5 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-medium text-zinc-100">Spending trend</h2>
          <p className="text-sm text-zinc-500">
            Weekly spacing is preserved, including zero-activity weeks.
          </p>
        </div>
        <Link
          href="/review"
          className="text-sm text-violet-400 transition-colors hover:text-violet-300"
        >
          Open Review & Reflect
        </Link>
      </div>

      {isLoading ? (
        <div className="h-80 animate-pulse rounded-xl bg-zinc-950/70" />
      ) : trend.length === 0 ? (
        <div className="flex h-80 items-center justify-center rounded-xl border border-dashed border-zinc-800 text-zinc-500">
          No spending data in the last 12 weeks.
        </div>
      ) : (
        <div className="h-80 rounded-xl border border-zinc-800 bg-zinc-950/70 p-3">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={trend} margin={{ top: 16, right: 20, left: 0, bottom: 8 }}>
              <defs>
                <linearGradient id="dashboardTotalFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#8b5cf6" stopOpacity={0.45} />
                  <stop offset="100%" stopColor="#8b5cf6" stopOpacity={0.03} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
              <XAxis dataKey="label" stroke="#71717a" tickLine={false} axisLine={false} />
              <YAxis
                stroke="#71717a"
                tickLine={false}
                axisLine={false}
                tickFormatter={(value) => formatMoney(Number(value))}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: "#09090b",
                  border: "1px solid #27272a",
                  borderRadius: "12px",
                  color: "#f4f4f5",
                }}
                labelStyle={{ color: "#a1a1aa" }}
                itemStyle={{ color: "#f4f4f5" }}
                formatter={(value, name) => [
                  formatMoneyPrecise(Number(value ?? 0)),
                  name === "total" ? "Total" : name === "memberA" || name === MEMBER_A ? MEMBER_A : MEMBER_B,
                ]}
              />
              <Area
                type="monotone"
                dataKey="total"
                stroke="#8b5cf6"
                fill="url(#dashboardTotalFill)"
                strokeWidth={3}
              />
              <Area type="monotone" dataKey="memberA" name={MEMBER_A} stroke="#14b8a6" fillOpacity={0} strokeWidth={2} />
              <Area type="monotone" dataKey="memberB" name={MEMBER_B} stroke="#ec4899" fillOpacity={0} strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

function SettlementOverviewCard({
  settlement,
  isLoading,
}: {
  settlement?:
    | {
        expenseBalance: number;
        paymentAdjustment: number;
        netDueToA: number;
        direction: "b_owes_a" | "a_owes_b" | "settled";
        absoluteAmount: number;
      }
    | undefined;
  isLoading: boolean;
}) {
  const headline =
    settlement?.direction === "b_owes_a"
      ? `${MEMBER_B} owes ${MEMBER_A} ${formatMoneyPrecise(settlement.absoluteAmount)}`
      : settlement?.direction === "a_owes_b"
        ? `${MEMBER_A} owes ${MEMBER_B} ${formatMoneyPrecise(settlement.absoluteAmount)}`
        : `${MEMBER_A} and ${MEMBER_B} are settled`;

  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6">
      <div className="mb-5">
        <h2 className="text-lg font-medium text-zinc-100">Net due</h2>
        <p className="text-sm text-zinc-500">
          Based on all recorded expenses and all logged payments through today.
        </p>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          <div className="h-16 animate-pulse rounded-xl bg-zinc-950/70" />
          <div className="h-24 animate-pulse rounded-xl bg-zinc-950/70" />
        </div>
      ) : (
        <>
          <div className="rounded-xl border border-zinc-800 bg-zinc-950/70 px-4 py-4">
            <p className="text-xs uppercase tracking-wide text-zinc-500">Current balance</p>
            <p className="mt-2 text-2xl font-semibold text-zinc-100">{headline}</p>
          </div>

          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            <MetricTile
              label="From expenses"
              value={formatMoneyPrecise(settlement?.expenseBalance ?? 0)}
            />
            <MetricTile
              label="Payments"
              value={formatMoneyPrecise(settlement?.paymentAdjustment ?? 0)}
            />
            <MetricTile
              label="Outstanding"
              value={formatMoneyPrecise(settlement?.netDueToA ?? 0)}
            />
          </div>

          <Link
            href="/review"
            className="mt-5 inline-flex text-sm text-violet-400 transition-colors hover:text-violet-300"
          >
            Open payment log in Review & Reflect
          </Link>
        </>
      )}
    </div>
  );
}

function MetricTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-zinc-950/70 p-4">
      <p className="text-sm text-zinc-500">{label}</p>
      <p className="mt-1 text-lg font-semibold text-zinc-100">{value}</p>
    </div>
  );
}

function CategoryOverviewCard({
  categories,
  isLoading,
}: {
  categories: Array<{ name: string; amount: number; count: number }>;
  isLoading: boolean;
}) {
  const chartData = categories.slice(0, 6);

  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6">
      <div className="mb-5 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-medium text-zinc-100">Top categories</h2>
          <p className="text-sm text-zinc-500">
            Overview only. Use Review & Reflect to segment or drill into subtypes.
          </p>
        </div>
        <Link
          href="/review"
          className="text-sm text-violet-400 transition-colors hover:text-violet-300"
        >
          Drill down
        </Link>
      </div>

      {isLoading ? (
        <div className="h-80 animate-pulse rounded-xl bg-zinc-950/70" />
      ) : chartData.length === 0 ? (
        <div className="flex h-80 items-center justify-center rounded-xl border border-dashed border-zinc-800 text-zinc-500">
          No category data in this range.
        </div>
      ) : (
        <div className="h-80 rounded-xl border border-zinc-800 bg-zinc-950/70 p-3">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={chartData}
              layout="vertical"
              margin={{ top: 8, right: 16, left: 24, bottom: 8 }}
            >
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" horizontal={false} />
              <XAxis
                type="number"
                stroke="#71717a"
                tickLine={false}
                axisLine={false}
                tickFormatter={(value) => formatMoney(Number(value))}
              />
              <YAxis
                type="category"
                dataKey="name"
                stroke="#71717a"
                tickLine={false}
                axisLine={false}
                width={120}
              />
              <Tooltip
                cursor={{ fill: "#18181b" }}
                contentStyle={{
                  backgroundColor: "#09090b",
                  border: "1px solid #27272a",
                  borderRadius: "12px",
                  color: "#f4f4f5",
                }}
                labelStyle={{ color: "#a1a1aa" }}
                itemStyle={{ color: "#f4f4f5" }}
                formatter={(value) => formatMoneyPrecise(Number(value ?? 0))}
              />
              <Bar dataKey="amount" radius={[0, 8, 8, 0]} fill="#8b5cf6" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

function RecentExpensesCard({
  expenses,
  isLoading,
}: {
  expenses: Array<{
    id: number;
    date: string;
    merchant: string | null;
    type: string | null;
    amount: number | null;
  }>;
  isLoading: boolean;
}) {
  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6">
      <div className="mb-5 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-medium text-zinc-100">Recent expenses</h2>
          <p className="text-sm text-zinc-500">
            Latest validated expenses inside the same 12-week overview window.
          </p>
        </div>
        <Link
          href="/review"
          className="text-sm text-violet-400 transition-colors hover:text-violet-300"
        >
          View table
        </Link>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3, 4, 5].map((row) => (
            <div key={row} className="h-16 animate-pulse rounded-xl bg-zinc-950/70" />
          ))}
        </div>
      ) : expenses.length === 0 ? (
        <div className="flex h-80 items-center justify-center rounded-xl border border-dashed border-zinc-800 text-zinc-500">
          No recent expenses in this range.
        </div>
      ) : (
        <div className="space-y-3">
          {expenses.slice(0, 6).map((expense) => (
            <div
              key={expense.id}
              className="flex items-center justify-between rounded-xl border border-zinc-800 bg-zinc-950/70 px-4 py-3"
            >
              <div className="min-w-0">
                <p className="truncate font-medium text-zinc-100">
                  {expense.merchant?.trim() || "Unknown merchant"}
                </p>
                <p className="text-sm text-zinc-500">
                  {new Date(`${expense.date}T00:00:00`).toLocaleDateString()} |{" "}
                  {expense.type?.trim() || "Uncategorized"}
                </p>
              </div>
              <p className="ml-4 whitespace-nowrap font-medium text-zinc-100">
                {formatMoneyPrecise(expense.amount ?? 0)}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
