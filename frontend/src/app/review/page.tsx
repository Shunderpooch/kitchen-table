"use client";

import { MEMBER_A, MEMBER_B } from "~/lib/household";

import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { Navigation } from "~/app/_components/navigation";
import { api } from "~/trpc/react";
import type { RouterOutputs } from "~/trpc/react";

type ReviewData = RouterOutputs["expenses"]["getReviewInsights"];
type WorkData = RouterOutputs["expenses"]["getWorkInsights"];
type TagData = RouterOutputs["expenses"]["getTagInsights"];
type RecentExpense = ReviewData["recentExpenses"][number];

const chartPalette = [
  "#8b5cf6",
  "#14b8a6",
  "#f59e0b",
  "#ec4899",
  "#3b82f6",
  "#22c55e",
  "#f97316",
  "#a855f7",
];

const expenseColumnHelper = createColumnHelper<RecentExpense>();

const expenseColumns = [
  expenseColumnHelper.accessor("date", {
    header: "Date",
    cell: (info) =>
      new Date(`${info.getValue()}T00:00:00`).toLocaleDateString(),
  }),
  expenseColumnHelper.accessor((row) => row.merchant ?? "Unknown", {
    id: "merchant",
    header: "Merchant",
    cell: (info) => (
      <span className="font-medium text-zinc-100">{info.getValue()}</span>
    ),
  }),
  expenseColumnHelper.accessor("type", {
    header: "Category",
  }),
  expenseColumnHelper.accessor((row) => row.subtype ?? "Other", {
    id: "subtype",
    header: "Subtype",
  }),
  expenseColumnHelper.accessor((row) => row.card ?? "Unknown", {
    id: "card",
    header: "Card",
  }),
  expenseColumnHelper.accessor((row) => row.amount ?? 0, {
    id: "amount",
    header: "Amount",
    cell: (info) => (
      <span className="font-medium text-zinc-100">
        {formatMoneyPrecise(info.getValue())}
      </span>
    ),
  }),
];

function formatDateInput(value: Date) {
  return value.toISOString().slice(0, 10);
}

function shiftMonths(value: Date, months: number) {
  const next = new Date(value);
  next.setMonth(next.getMonth() + months);
  return next;
}

function chooseGranularity(startDate: string, endDate: string) {
  if (!startDate || !endDate) {
    return "month" as const;
  }

  const start = new Date(`${startDate}T00:00:00`);
  const end = new Date(`${endDate}T00:00:00`);
  const diffDays = Math.max(
    1,
    Math.round((end.getTime() - start.getTime()) / (1000 * 60 * 60 * 24)),
  );

  if (diffDays <= 45) {
    return "day" as const;
  }

  if (diffDays <= 400) {
    return "week" as const;
  }

  return "month" as const;
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

export default function ReviewPage() {
  const today = useMemo(() => new Date(), []);
  const [activeTab, setActiveTab] = useState<"spending" | "work" | "tags">("spending");
  const [timeRange, setTimeRange] = useState<"month" | "quarter" | "year" | "all">("month");
  const [endDate, setEndDate] = useState(formatDateInput(today));
  const [startDate, setStartDate] = useState(formatDateInput(shiftMonths(today, -1)));
  const [selectedCategories, setSelectedCategories] = useState<string[]>([]);
  const [pendingCategory, setPendingCategory] = useState("");
  const [selectedTag, setSelectedTag] = useState<string>("");
  const [groupBy, setGroupBy] = useState<"auto" | "day" | "week" | "month">("auto");
  const [paymentDate, setPaymentDate] = useState(formatDateInput(today));
  const [paymentFrom, setPaymentFrom] = useState<string>(MEMBER_A);
  const [paymentTo, setPaymentTo] = useState<string>(MEMBER_B);
  const [paymentAmount, setPaymentAmount] = useState("");
  const [paymentNote, setPaymentNote] = useState("");

  const granularity =
    groupBy === "auto" ? chooseGranularity(startDate, endDate) : groupBy;

  const utils = api.useUtils();
  const reviewQuery = api.expenses.getReviewInsights.useQuery({
    startDate: startDate || undefined,
    endDate: endDate || undefined,
    categories: selectedCategories,
    granularity,
  });
  const workQuery = api.expenses.getWorkInsights.useQuery(
    {
      startDate: startDate || undefined,
      endDate: endDate || undefined,
      granularity,
    },
    { enabled: activeTab === "work" },
  );
  const tagQuery = api.expenses.getTagInsights.useQuery(
    {
      startDate: startDate || undefined,
      endDate: endDate || undefined,
      granularity,
      selectedTag: selectedTag || undefined,
    },
    { enabled: activeTab === "tags" },
  );
  const addPaymentMutation = api.expenses.addPayment.useMutation({
    onSuccess: async () => {
      setPaymentAmount("");
      setPaymentNote("");
      await utils.expenses.getReviewInsights.invalidate();
    },
  });
  const deletePaymentMutation = api.expenses.deletePayment.useMutation({
    onSuccess: async () => {
      await utils.expenses.getReviewInsights.invalidate();
    },
  });

  const data = reviewQuery.data;
  const workData = workQuery.data;
  const tagData = tagQuery.data;

  const applyRange = (range: "month" | "quarter" | "year" | "all") => {
    setTimeRange(range);

    if (range === "all") {
      setStartDate("");
      setEndDate("");
      return;
    }

    if (range === "month") {
      setStartDate(formatDateInput(shiftMonths(today, -1)));
      setEndDate(formatDateInput(today));
      return;
    }

    if (range === "quarter") {
      setStartDate(formatDateInput(shiftMonths(today, -3)));
      setEndDate(formatDateInput(today));
      return;
    }

    setStartDate(formatDateInput(shiftMonths(today, -12)));
    setEndDate(formatDateInput(today));
  };

  const addCategoryFilter = () => {
    if (!pendingCategory || selectedCategories.includes(pendingCategory)) {
      return;
    }

    setSelectedCategories((current) => [...current, pendingCategory]);
    setPendingCategory("");
  };

  const clearCategory = (category: string) => {
    setSelectedCategories((current) => current.filter((item) => item !== category));
  };

  const focusCategory = (category: string) => {
    setSelectedCategories([category]);
    setPendingCategory("");
  };

  return (
    <div className="min-h-screen bg-zinc-950">
      <Navigation />
      <main className="container mx-auto px-4 py-8">
        <div className="mb-8 flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-zinc-100">
              Review & Reflect
            </h1>
            <p className="max-w-3xl text-zinc-400">
              BI-style spending analysis driven by expense date, with broad trend context first and deeper category or subtype detail only when you ask for it.
            </p>
          </div>

          <div className="flex flex-col gap-3 xl:items-end">
            <div className="flex flex-wrap gap-2">
              {(["month", "quarter", "year", "all"] as const).map((range) => (
                <button
                  key={range}
                  type="button"
                  onClick={() => applyRange(range)}
                  className={`rounded-full border px-4 py-2 text-sm transition-colors ${
                    timeRange === range
                      ? "border-violet-500 bg-violet-500/10 text-zinc-100"
                      : "border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-zinc-500 hover:text-zinc-100"
                  }`}
                >
                  {range === "all" ? "All time" : `Last ${range}`}
                </button>
              ))}
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-sm text-zinc-400">
                <span className="mb-2 block">Start date</span>
                <input
                  type="date"
                  value={startDate}
                  onChange={(event) => {
                    setStartDate(event.target.value);
                    setTimeRange("all");
                  }}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
                />
              </label>
              <label className="text-sm text-zinc-400">
                <span className="mb-2 block">End date</span>
                <input
                  type="date"
                  value={endDate}
                  onChange={(event) => {
                    setEndDate(event.target.value);
                    setTimeRange("all");
                  }}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
                />
              </label>
            </div>
          </div>
        </div>

        <div className="mb-8 flex flex-wrap gap-2">
          {[
            {
              id: "spending" as const,
              label: "Spending",
              description: "Household trends, categories, settlement, and recent expenses.",
            },
            {
              id: "work" as const,
              label: "Work",
              description: "Expenses logged for reimbursement or work-related review.",
            },
            {
              id: "tags" as const,
              label: "Tags",
              description: "Event and context tags as a lighter discovery lens.",
            },
          ].map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={`rounded-2xl border px-4 py-3 text-left transition-colors ${
                activeTab === tab.id
                  ? "border-violet-500 bg-violet-500/10 text-zinc-100"
                  : "border-zinc-800 bg-zinc-900/50 text-zinc-400 hover:border-zinc-600 hover:text-zinc-100"
              }`}
            >
              <div className="font-medium">{tab.label}</div>
              <div className="mt-1 text-xs text-inherit/80">{tab.description}</div>
            </button>
          ))}
        </div>

        {activeTab === "spending" ? (
        <div className="mb-8 rounded-2xl border border-zinc-800 bg-zinc-900/50 p-5">
          <div className="mb-3 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <h2 className="text-lg font-medium text-zinc-100">Category lens</h2>
              <p className="text-sm text-zinc-500">
                Segment the view by any categories you care about. Click one category below to drill into subtype detail.
              </p>
            </div>
            <div className="flex flex-col gap-2 sm:flex-row">
              <select
                value={groupBy}
                onChange={(event) =>
                  setGroupBy(event.target.value as "auto" | "day" | "week" | "month")
                }
                className="min-w-40 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 focus:border-violet-500 focus:outline-none"
              >
                <option value="auto">Auto grouping</option>
                <option value="day">Group by day</option>
                <option value="week">Group by week</option>
                <option value="month">Group by month</option>
              </select>
              <select
                value={pendingCategory}
                onChange={(event) => setPendingCategory(event.target.value)}
                className="min-w-56 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 focus:border-violet-500 focus:outline-none"
              >
                <option value="">Add a category filter...</option>
                {(data?.availableCategories ?? [])
                  .filter((category) => !selectedCategories.includes(category))
                  .map((category) => (
                    <option key={category} value={category}>
                      {category}
                    </option>
                  ))}
              </select>
              <button
                type="button"
                onClick={addCategoryFilter}
                disabled={!pendingCategory}
                className="rounded-lg border border-violet-500/40 bg-violet-500/10 px-4 py-2 text-sm text-zinc-100 transition-colors hover:bg-violet-500/20 disabled:opacity-40"
              >
                Add filter
              </button>
            </div>
          </div>

          {selectedCategories.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {selectedCategories.map((category) => (
                <button
                  key={category}
                  type="button"
                  onClick={() => clearCategory(category)}
                  className="rounded-full border border-zinc-700 bg-zinc-950 px-3 py-1 text-sm text-zinc-300 transition-colors hover:border-rose-400 hover:text-zinc-100"
                >
                  {category} ×
                </button>
              ))}
              <button
                type="button"
                onClick={() => setSelectedCategories([])}
                className="rounded-full border border-zinc-800 px-3 py-1 text-sm text-zinc-500 transition-colors hover:border-zinc-600 hover:text-zinc-300"
              >
                Clear all
              </button>
            </div>
          ) : (
            <p className="text-sm text-zinc-500">Showing all validated categories in the selected date range.</p>
          )}
        </div>
        ) : null}

        {activeTab === "spending" ? (
          reviewQuery.isLoading ? (
            <LoadingState />
          ) : !data ? (
            <EmptyState />
          ) : (
            <>
              <div className="mb-8">
                <SettlementPanel
                  settlement={data.settlement}
                  payments={data.payments}
                  selectedCategories={selectedCategories}
                  paymentDate={paymentDate}
                  paymentFrom={paymentFrom}
                  paymentTo={paymentTo}
                  paymentAmount={paymentAmount}
                  paymentNote={paymentNote}
                  onPaymentDateChange={setPaymentDate}
                  onPaymentFromChange={(value) => {
                    setPaymentFrom(value);
                    setPaymentTo(value === MEMBER_A ? MEMBER_B : MEMBER_A);
                  }}
                  onPaymentToChange={setPaymentTo}
                  onPaymentAmountChange={setPaymentAmount}
                  onPaymentNoteChange={setPaymentNote}
                  onSubmitPayment={() => {
                    const amount = Number(paymentAmount);
                    if (!paymentDate || !Number.isFinite(amount) || amount <= 0) {
                      return;
                    }

                    addPaymentMutation.mutate({
                      date: paymentDate,
                      fromPerson: paymentFrom,
                      toPerson: paymentTo,
                      amount,
                      note: paymentNote.trim() || null,
                    });
                  }}
                  onDeletePayment={(id) => deletePaymentMutation.mutate({ id })}
                  isSavingPayment={addPaymentMutation.isPending}
                  isDeletingPayment={deletePaymentMutation.isPending}
                />
              </div>

              <div className="mb-8 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <SummaryCard
                  title="Total spending"
                  value={formatMoney(data.summary.totalAmount)}
                  subtitle={`${data.summary.transactionCount} transactions`}
                />
                <SummaryCard
                  title={`${MEMBER_A} share`}
                  value={formatMoney(data.summary.memberASpend)}
                  subtitle={`${Math.round(
                    data.summary.totalAmount > 0
                      ? (data.summary.memberASpend / data.summary.totalAmount) * 100
                      : 0,
                  )}% of selected spend`}
                />
                <SummaryCard
                  title={`${MEMBER_B} share`}
                  value={formatMoney(data.summary.memberBSpend)}
                  subtitle={`${Math.round(
                    data.summary.totalAmount > 0
                      ? (data.summary.memberBSpend / data.summary.totalAmount) * 100
                      : 0,
                  )}% of selected spend`}
                />
                <SummaryCard
                  title="Average transaction"
                  value={formatMoney(data.summary.avgTransaction)}
                  subtitle="Based on expense date"
                />
              </div>

              <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(360px,1fr)]">
                <TrendPanel
                  trend={data.trend}
                  granularity={granularity}
                  selectedCategories={selectedCategories}
                />
                <BreakdownPanel
                  categories={data.categories}
                  subtypes={data.subtypes}
                  selectedCategories={selectedCategories}
                  onFocusCategory={focusCategory}
                  onClearFocus={() => setSelectedCategories([])}
                />
              </div>

              <div className="mt-8 grid gap-6 xl:grid-cols-2">
                <SharePanel
                  title={
                    selectedCategories.length === 1
                      ? `${MEMBER_A} vs ${MEMBER_B} in ${selectedCategories[0]}`
                      : `${MEMBER_A} vs ${MEMBER_B} by category`
                  }
                  rows={selectedCategories.length === 1 ? data.subtypes : data.categories}
                />
                <CardPanel cards={data.cards} />
              </div>

              <div className="mt-8">
                <RecentExpensesPanel expenses={data.recentExpenses} />
              </div>
            </>
          )
        ) : activeTab === "work" ? (
          workQuery.isLoading ? (
            <LoadingState />
          ) : !workData ? (
            <EmptyState />
          ) : (
            <WorkTabContent
              data={workData}
              granularity={granularity}
              startDate={startDate}
              endDate={endDate}
            />
          )
        ) : tagQuery.isLoading ? (
          <LoadingState />
        ) : !tagData ? (
          <EmptyState />
        ) : (
          <TagsTabContent
            data={tagData}
            granularity={granularity}
            selectedTag={selectedTag}
            onSelectTag={setSelectedTag}
            onClearTag={() => setSelectedTag("")}
          />
        )}
      </main>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {[1, 2, 3, 4].map((index) => (
          <div key={index} className="h-28 animate-pulse rounded-2xl bg-zinc-900" />
        ))}
      </div>
      <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(360px,1fr)]">
        <div className="h-[420px] animate-pulse rounded-2xl bg-zinc-900" />
        <div className="h-[420px] animate-pulse rounded-2xl bg-zinc-900" />
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-10 text-center text-zinc-500">
      No validated spending data matches the current filters.
    </div>
  );
}

function SummaryCard({
  title,
  value,
  subtitle,
}: {
  title: string;
  value: string;
  subtitle: string;
}) {
  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-5">
      <p className="text-sm text-zinc-400">{title}</p>
      <p className="mt-2 text-3xl font-semibold text-zinc-100">{value}</p>
      <p className="mt-2 text-sm text-zinc-500">{subtitle}</p>
    </div>
  );
}

function TrendPanel({
  trend,
  granularity,
  selectedCategories,
}: {
  trend: ReviewData["trend"];
  granularity: "day" | "week" | "month";
  selectedCategories: string[];
}) {
  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6">
      <div className="mb-5 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-lg font-medium text-zinc-100">Spending over time</h2>
          <p className="text-sm text-zinc-500">
            Grouped by {granularity}. This always uses the expense date on each row.
          </p>
        </div>
        <p className="text-sm text-zinc-400">
          {selectedCategories.length > 0
            ? `Filtered to ${selectedCategories.join(", ")}`
            : "All validated categories"}
        </p>
      </div>

      {trend.length === 0 ? (
        <div className="flex h-80 items-center justify-center rounded-xl border border-dashed border-zinc-800 text-zinc-500">
          No trend points in this range.
        </div>
      ) : (
        <div className="h-80 rounded-xl border border-zinc-800 bg-zinc-950/70 p-3">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={trend} margin={{ top: 16, right: 20, left: 0, bottom: 8 }}>
              <defs>
                <linearGradient id="totalSpendFill" x1="0" y1="0" x2="0" y2="1">
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
              <Legend />
              <Area
                type="monotone"
                dataKey="total"
                stroke="#8b5cf6"
                fill="url(#totalSpendFill)"
                strokeWidth={2.5}
                name="total"
              />
              <Area
                type="monotone"
                dataKey="memberA"
                stroke="#14b8a6"
                fill="transparent"
                strokeWidth={2}
                name={MEMBER_A}
              />
              <Area
                type="monotone"
                dataKey="memberB"
                stroke="#ec4899"
                fill="transparent"
                strokeWidth={2}
                name={MEMBER_B}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

function BreakdownPanel({
  categories,
  subtypes,
  selectedCategories,
  onFocusCategory,
  onClearFocus,
}: {
  categories: ReviewData["categories"];
  subtypes: ReviewData["subtypes"];
  selectedCategories: string[];
  onFocusCategory: (category: string) => void;
  onClearFocus: () => void;
}) {
  const isSubtypeView = selectedCategories.length === 1;
  const rows = isSubtypeView ? subtypes : categories;
  const chartData = rows.slice(0, 8).map((row, index) => ({
    ...row,
    fill: chartPalette[index % chartPalette.length],
  }));

  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6">
      <div className="mb-5 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-medium text-zinc-100">
            {isSubtypeView ? "Subtype breakdown" : "Category breakdown"}
          </h2>
          <p className="text-sm text-zinc-500">
            {isSubtypeView
              ? `Focused on ${selectedCategories[0]}. Use Back to return to the higher-level category view.`
              : "Click a category bar or row to drill into subtype detail."}
          </p>
        </div>
        {isSubtypeView && (
          <button
            type="button"
            onClick={onClearFocus}
            className="rounded-full border border-zinc-700 px-3 py-1 text-sm text-zinc-300 transition-colors hover:border-zinc-500 hover:text-zinc-100"
          >
            Back
          </button>
        )}
      </div>

      {chartData.length === 0 ? (
        <p className="text-sm text-zinc-500">No breakdown data for this view.</p>
      ) : (
        <>
          <div className="h-72 rounded-xl border border-zinc-800 bg-zinc-950/70 p-3">
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
                <Bar
                  dataKey="amount"
                  radius={[0, 8, 8, 0]}
                  onClick={(entry: { name?: string }) => {
                    if (!isSubtypeView && typeof entry.name === "string") {
                      onFocusCategory(entry.name);
                    }
                  }}
                >
                  {chartData.map((entry) => (
                    <Cell key={entry.name} fill={entry.fill} cursor={isSubtypeView ? "default" : "pointer"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="mt-4 space-y-2">
            {rows.slice(0, 8).map((row, index) => (
              <button
                key={row.name}
                type="button"
                onClick={() => {
                  if (!isSubtypeView) {
                    onFocusCategory(row.name);
                  }
                }}
                className={`flex w-full items-center justify-between rounded-xl border px-4 py-3 text-left transition-colors ${
                  isSubtypeView
                    ? "border-zinc-800 bg-zinc-950/60"
                    : "border-zinc-800 bg-zinc-950/60 hover:border-violet-500/60"
                }`}
              >
                <div className="flex items-center gap-3">
                  <span
                    className="h-3 w-3 rounded-full"
                    style={{ backgroundColor: chartPalette[index % chartPalette.length] }}
                  />
                  <div>
                    <p className="font-medium text-zinc-100">{row.name}</p>
                    <p className="text-xs text-zinc-500">{row.count} transactions</p>
                  </div>
                </div>
                <p className="font-medium text-zinc-100">{formatMoney(row.amount)}</p>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function SharePanel({
  title,
  rows,
}: {
  title: string;
  rows: Array<{ name: string; memberA: number; memberB: number }>;
}) {
  const chartData = rows.slice(0, 8);

  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6">
      <h2 className="mb-5 text-lg font-medium text-zinc-100">{title}</h2>
      {chartData.length === 0 ? (
        <p className="text-sm text-zinc-500">No share data available.</p>
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
                  name === "memberA" || name === MEMBER_A ? MEMBER_A : MEMBER_B,
                ]}
              />
              <Legend />
              <Bar dataKey="memberA" name={MEMBER_A} stackId="share" fill="#8b5cf6" radius={[0, 0, 0, 0]} />
              <Bar dataKey="memberB" name={MEMBER_B} stackId="share" fill="#ec4899" radius={[0, 8, 8, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

function CardPanel({ cards }: { cards: ReviewData["cards"] }) {
  const chartData = cards.slice(0, 8).map((card, index) => ({
    ...card,
    fill: chartPalette[index % chartPalette.length],
  }));

  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6">
      <h2 className="mb-5 text-lg font-medium text-zinc-100">By card</h2>
      {chartData.length === 0 ? (
        <p className="text-sm text-zinc-500">No card data available.</p>
      ) : (
        <div className="h-80 rounded-xl border border-zinc-800 bg-zinc-950/70 p-3">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 12, right: 16, left: 0, bottom: 12 }}>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="card" stroke="#71717a" tickLine={false} axisLine={false} interval={0} angle={-20} textAnchor="end" height={72} />
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
                formatter={(value) => formatMoneyPrecise(Number(value ?? 0))}
              />
              <Bar dataKey="amount" radius={[8, 8, 0, 0]}>
                {chartData.map((card) => (
                  <Cell key={card.card} fill={card.fill} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

function WorkTabContent({
  data,
  granularity,
  startDate,
  endDate,
}: {
  data: WorkData;
  granularity: "day" | "week" | "month";
  startDate: string;
  endDate: string;
}) {
  const workExportHref = `/api/export/household?logToWork=true${
    startDate ? `&startDate=${encodeURIComponent(startDate)}` : ""
  }${endDate ? `&endDate=${encodeURIComponent(endDate)}` : ""}`;

  return (
    <>
      <div className="mb-8 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h2 className="text-lg font-medium text-zinc-100">Work review</h2>
          <p className="text-sm text-zinc-500">
            This tab is intentionally operational: recent reimbursable expenses first, then concentration and trend views.
          </p>
        </div>
        <a
          href={workExportHref}
          className="inline-flex items-center justify-center rounded-lg border border-violet-500/40 bg-violet-500/10 px-4 py-2 text-sm text-zinc-100 transition-colors hover:bg-violet-500/20"
        >
          Export work expenses
        </a>
      </div>

      <div className="mb-8 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          title="Work spend"
          value={formatMoney(data.summary.totalAmount)}
          subtitle={`${data.summary.transactionCount} logged expenses`}
        />
        <SummaryCard
          title="Average work expense"
          value={formatMoney(data.summary.avgTransaction)}
          subtitle="Based on expense date"
        />
        <SummaryCard
          title="Tagged work spend"
          value={formatMoney(data.tags.reduce((total, row) => total + row.amount, 0))}
          subtitle={`${data.tags.length} active work tags`}
        />
        <SummaryCard
          title="Top merchant"
          value={data.merchants[0]?.merchant ?? "None"}
          subtitle={data.merchants[0] ? formatMoney(data.merchants[0].amount) : "No work expenses in range"}
        />
      </div>

      <div className="mb-8">
        <RecentExpensesPanel expenses={data.recentExpenses as unknown as ReviewData["recentExpenses"]} />
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(360px,1fr)]">
        <TrendPanel trend={data.trend} granularity={granularity} selectedCategories={["Work only"]} />
        <SimpleRankPanel
          title="Work by merchant"
          description="Use this to find reimbursement concentration quickly."
          rows={data.merchants.map((row) => ({ name: row.merchant, amount: row.amount, count: row.count }))}
        />
      </div>

      <div className="mt-8 grid gap-6 xl:grid-cols-2">
        <SimpleRankPanel
          title="Work by category"
          description="Still grouped by your canonical category labels."
          rows={data.categories}
        />
        <SimpleRankPanel
          title="Work by tag"
          description="Tags stay secondary here, but they are still visible for reimbursement context."
          rows={data.tags.map((row) => ({ name: row.tag, amount: row.amount, count: row.count }))}
        />
      </div>
    </>
  );
}

function TagsTabContent({
  data,
  granularity,
  selectedTag,
  onSelectTag,
  onClearTag,
}: {
  data: TagData;
  granularity: "day" | "week" | "month";
  selectedTag: string;
  onSelectTag: (value: string) => void;
  onClearTag: () => void;
}) {
  return (
    <>
      <div className="mb-8 rounded-2xl border border-zinc-800 bg-zinc-900/50 p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-lg font-medium text-zinc-100">Tag lens</h2>
            <p className="text-sm text-zinc-500">
              Tags are useful as a lighter discovery layer. Keep them secondary to categories and use them to inspect trips, projects, conferences, or seasons.
            </p>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <select
              value={selectedTag}
              onChange={(event) => onSelectTag(event.target.value)}
              className="min-w-56 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 focus:border-violet-500 focus:outline-none"
            >
              <option value="">All tags</option>
              {data.availableTags.map((row) => (
                <option key={row.tag} value={row.tag}>
                  {row.tag} ({row.count})
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={onClearTag}
              disabled={!selectedTag}
              className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 transition-colors hover:border-zinc-500 hover:text-zinc-100 disabled:opacity-40"
            >
              Clear tag
            </button>
          </div>
        </div>
      </div>

      <div className="mb-8 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          title={selectedTag ? "Selected tag spend" : "Tagged spend"}
          value={formatMoney(data.summary.totalAmount)}
          subtitle={`${data.summary.transactionCount} tagged expenses`}
        />
        <SummaryCard
          title="Average tagged expense"
          value={formatMoney(data.summary.avgTransaction)}
          subtitle="Based on expense date"
        />
        <SummaryCard
          title="Available tags"
          value={String(data.availableTags.length)}
          subtitle="Only tags with validated spend in range"
        />
        <SummaryCard
          title="Focused tag"
          value={selectedTag || "All tags"}
          subtitle={selectedTag ? "Drilled into one tag" : "Cross-tag overview"}
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(360px,1fr)]">
        <TrendPanel
          trend={data.trend}
          granularity={granularity}
          selectedCategories={selectedTag ? [selectedTag] : ["Tagged expenses"]}
        />
        <SimpleRankPanel
          title={selectedTag ? "Spend inside selected tag" : "Top tags"}
          description={
            selectedTag
              ? "Once you choose a tag, the right side shifts to the category mix inside that tag."
              : "This stays deliberately lighter than the category analysis in Spending."
          }
          rows={
            selectedTag
              ? data.categories
              : data.availableTags.map((row) => ({ name: row.tag, amount: row.amount, count: row.count }))
          }
        />
      </div>

      <div className="mt-8">
        <RecentExpensesPanel expenses={data.recentExpenses as unknown as ReviewData["recentExpenses"]} />
      </div>
    </>
  );
}

function SimpleRankPanel({
  title,
  description,
  rows,
}: {
  title: string;
  description: string;
  rows: Array<{ name: string; amount: number; count: number }>;
}) {
  const chartData = rows.slice(0, 8).map((row, index) => ({
    ...row,
    fill: chartPalette[index % chartPalette.length],
  }));

  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6">
      <div className="mb-5">
        <h2 className="text-lg font-medium text-zinc-100">{title}</h2>
        <p className="text-sm text-zinc-500">{description}</p>
      </div>

      {chartData.length === 0 ? (
        <p className="text-sm text-zinc-500">No data for this view.</p>
      ) : (
        <>
          <div className="h-72 rounded-xl border border-zinc-800 bg-zinc-950/70 p-3">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} layout="vertical" margin={{ top: 8, right: 16, left: 24, bottom: 8 }}>
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
                <Bar dataKey="amount" radius={[0, 8, 8, 0]}>
                  {chartData.map((entry) => (
                    <Cell key={entry.name} fill={entry.fill} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="mt-4 space-y-2">
            {rows.slice(0, 8).map((row, index) => (
              <div
                key={row.name}
                className="flex items-center justify-between rounded-xl border border-zinc-800 bg-zinc-950/60 px-4 py-3"
              >
                <div className="flex items-center gap-3">
                  <span
                    className="h-3 w-3 rounded-full"
                    style={{ backgroundColor: chartPalette[index % chartPalette.length] }}
                  />
                  <div>
                    <p className="font-medium text-zinc-100">{row.name}</p>
                    <p className="text-xs text-zinc-500">{row.count} transactions</p>
                  </div>
                </div>
                <p className="font-medium text-zinc-100">{formatMoney(row.amount)}</p>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function RecentExpensesPanel({
  expenses,
}: {
  expenses: ReviewData["recentExpenses"];
}) {
  const [sorting, setSorting] = useState<SortingState>([
    { id: "date", desc: true },
  ]);

  const table = useReactTable({
    data: expenses,
    columns: expenseColumns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6">
      <div className="mb-4 flex items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-medium text-zinc-100">Recent matching expenses</h2>
          <p className="text-sm text-zinc-500">
            Sortable table of the latest 20 validated expenses in the current lens.
          </p>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px]">
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id} className="border-b border-zinc-800 text-left text-sm text-zinc-400">
                {headerGroup.headers.map((header) => (
                  <th key={header.id} className="pb-3 font-medium">
                    {header.isPlaceholder ? null : (
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        className="flex items-center gap-2 transition-colors hover:text-zinc-100"
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {{
                          asc: "↑",
                          desc: "↓",
                        }[header.column.getIsSorted() as string] ?? null}
                      </button>
                    )}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody className="text-sm">
            {table.getRowModel().rows.map((row) => (
              <tr key={row.id} className="border-b border-zinc-800/60">
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="py-3 text-zinc-300">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SettlementPanel({
  settlement,
  payments,
  selectedCategories,
  paymentDate,
  paymentFrom,
  paymentTo,
  paymentAmount,
  paymentNote,
  onPaymentDateChange,
  onPaymentFromChange,
  onPaymentToChange,
  onPaymentAmountChange,
  onPaymentNoteChange,
  onSubmitPayment,
  onDeletePayment,
  isSavingPayment,
  isDeletingPayment,
}: {
  settlement: ReviewData["settlement"];
  payments: ReviewData["payments"];
  selectedCategories: string[];
  paymentDate: string;
  paymentFrom: string;
  paymentTo: string;
  paymentAmount: string;
  paymentNote: string;
  onPaymentDateChange: (value: string) => void;
  onPaymentFromChange: (value: string) => void;
  onPaymentToChange: (value: string) => void;
  onPaymentAmountChange: (value: string) => void;
  onPaymentNoteChange: (value: string) => void;
  onSubmitPayment: () => void;
  onDeletePayment: (id: number) => void;
  isSavingPayment: boolean;
  isDeletingPayment: boolean;
}) {
  const directionText =
    settlement.direction === "b_owes_a"
      ? `${MEMBER_B} owes ${MEMBER_A} ${formatMoneyPrecise(settlement.absoluteAmount)}`
      : settlement.direction === "a_owes_b"
        ? `${MEMBER_A} owes ${MEMBER_B} ${formatMoneyPrecise(settlement.absoluteAmount)}`
        : `${MEMBER_A} and ${MEMBER_B} are settled`;

  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6">
      <div className="mb-5 flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h2 className="text-lg font-medium text-zinc-100">Settlement</h2>
          <p className="text-sm text-zinc-500">
            Net due is calculated from expense split vs spender, then adjusted by logged payments in the current date range.
          </p>
          {selectedCategories.length > 0 && (
            <p className="mt-2 text-xs text-amber-400">
              Expense balance respects the current category lens. Logged payments are date-range scoped and not category-tagged.
            </p>
          )}
        </div>
        <div className="rounded-xl border border-zinc-800 bg-zinc-950/70 px-4 py-3">
          <p className="text-xs uppercase tracking-wide text-zinc-500">Net due</p>
          <p className="mt-1 text-2xl font-semibold text-zinc-100">{directionText}</p>
        </div>
      </div>

      <div className="mb-6 grid gap-4 md:grid-cols-3">
        <div className="rounded-xl bg-zinc-950/70 p-4">
          <p className="text-sm text-zinc-500">From expenses</p>
          <p className="mt-1 text-xl font-semibold text-zinc-100">
            {formatMoneyPrecise(settlement.expenseBalance)}
          </p>
        </div>
        <div className="rounded-xl bg-zinc-950/70 p-4">
          <p className="text-sm text-zinc-500">Payment adjustment</p>
          <p className="mt-1 text-xl font-semibold text-zinc-100">
            {formatMoneyPrecise(settlement.paymentAdjustment)}
          </p>
        </div>
        <div className="rounded-xl bg-zinc-950/70 p-4">
          <p className="text-sm text-zinc-500">Outstanding</p>
          <p className="mt-1 text-xl font-semibold text-zinc-100">
            {formatMoneyPrecise(settlement.netDueToA)}
          </p>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <div className="rounded-xl border border-zinc-800 bg-zinc-950/70 p-4">
          <h3 className="mb-4 font-medium text-zinc-100">Log payment</h3>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-sm text-zinc-400">
              <span className="mb-2 block">Date</span>
              <input
                type="date"
                value={paymentDate}
                onChange={(event) => onPaymentDateChange(event.target.value)}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
              />
            </label>
            <label className="text-sm text-zinc-400">
              <span className="mb-2 block">Amount</span>
              <input
                type="number"
                min="0"
                step="0.01"
                value={paymentAmount}
                onChange={(event) => onPaymentAmountChange(event.target.value)}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
                placeholder="0.00"
              />
            </label>
            <label className="text-sm text-zinc-400">
              <span className="mb-2 block">From</span>
              <select
                value={paymentFrom}
                onChange={(event) => onPaymentFromChange(event.target.value as string)}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
              >
                <option value={MEMBER_A}>{MEMBER_A}</option>
                <option value={MEMBER_B}>{MEMBER_B}</option>
              </select>
            </label>
            <label className="text-sm text-zinc-400">
              <span className="mb-2 block">To</span>
              <select
                value={paymentTo}
                onChange={(event) => onPaymentToChange(event.target.value as string)}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
              >
                <option value={MEMBER_A}>{MEMBER_A}</option>
                <option value={MEMBER_B}>{MEMBER_B}</option>
              </select>
            </label>
          </div>
          <label className="mt-3 block text-sm text-zinc-400">
            <span className="mb-2 block">Note</span>
            <input
              type="text"
              value={paymentNote}
              onChange={(event) => onPaymentNoteChange(event.target.value)}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
              placeholder="Venmo, cash, transfer..."
            />
          </label>
          <button
            type="button"
            onClick={onSubmitPayment}
            disabled={
              isSavingPayment ||
              !paymentDate ||
              !paymentAmount ||
              Number(paymentAmount) <= 0 ||
              paymentFrom === paymentTo
            }
            className="mt-4 w-full rounded-lg bg-violet-600 px-4 py-3 font-medium text-white transition-colors hover:bg-violet-700 disabled:opacity-50"
          >
            {isSavingPayment ? "Saving payment..." : "Log payment"}
          </button>
        </div>

        <div className="rounded-xl border border-zinc-800 bg-zinc-950/70 p-4">
          <h3 className="mb-4 font-medium text-zinc-100">Recent payments in range</h3>
          <div className="space-y-3">
            {payments.length === 0 ? (
              <p className="text-sm text-zinc-500">No payments logged in this date range.</p>
            ) : (
              payments.map((payment) => (
                <div
                  key={payment.id}
                  className="flex items-start justify-between gap-3 rounded-xl border border-zinc-800 bg-zinc-900/70 px-4 py-3"
                >
                  <div>
                    <p className="font-medium text-zinc-100">
                      {payment.fromPerson} → {payment.toPerson} · {formatMoneyPrecise(payment.amount)}
                    </p>
                    <p className="text-sm text-zinc-500">
                      {new Date(`${payment.date}T00:00:00`).toLocaleDateString()}
                      {payment.note ? ` · ${payment.note}` : ""}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => onDeletePayment(payment.id)}
                    disabled={isDeletingPayment}
                    className="text-sm text-zinc-500 transition-colors hover:text-rose-400 disabled:opacity-50"
                  >
                    Delete
                  </button>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
