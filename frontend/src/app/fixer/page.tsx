"use client";

import { MEMBER_A, MEMBER_B } from "~/lib/household";

import { useDeferredValue, useEffect, useMemo, useState } from "react";
import { Navigation } from "~/app/_components/navigation";
import { ExpenseReceipts } from "~/app/_components/expense-receipts";
import type { FriendShareInput } from "~/lib/expense-sharing";
import { api } from "~/trpc/react";
import type { RouterOutputs } from "~/trpc/react";

function formatDateInput(value: Date) {
  return value.toISOString().slice(0, 10);
}

function formatMoneyPrecise(value: number) {
  return value.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

type ExpenseListItem = RouterOutputs["expenses"]["getAll"]["items"][number];
type ExpenseDetail = NonNullable<RouterOutputs["expenses"]["getById"]>;

type ExpenseFormState = {
  date: string;
  type: string;
  subtype: string;
  merchant: string;
  originalInputName: string;
  amount: string;
  split: string;
  spender: string;
  card: string;
  tag: string;
  logToWork: boolean;
  link: string;
  trackWithFriends: string;
  friendShares: FriendShareInput[];
  isDuplicate: boolean;
  needsValidation: boolean;
  categoryId: string;
  subtypeId: string;
};

const inputClassName =
  "w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none";
const checkboxClassName =
  "h-4 w-4 rounded border border-zinc-600 bg-zinc-950 text-violet-500 transition-colors focus:ring-2 focus:ring-violet-500/40 focus:ring-offset-0 focus:ring-offset-zinc-950";

function toFormState(expense: ExpenseDetail): ExpenseFormState {
  return {
    date: expense.date,
    type: expense.type ?? "",
    subtype: expense.subtype ?? "",
    merchant: expense.merchant ?? "",
    originalInputName: expense.originalInputName ?? "",
    amount: String(expense.amount ?? 0),
    split: String(expense.split ?? 1),
    spender:
      expense.spender === MEMBER_A || expense.spender === MEMBER_B
        ? expense.spender
        : "",
    card: expense.card ?? "",
    tag: expense.tag ?? "",
    logToWork: Boolean(expense.logToWork),
    link: expense.link ?? "",
    trackWithFriends: expense.trackWithFriends ?? "",
    friendShares: expense.friendShares ?? [],
    isDuplicate: Boolean(expense.isDuplicate),
    needsValidation: Boolean(expense.needsValidation),
    categoryId: expense.categoryId ? String(expense.categoryId) : "",
    subtypeId: expense.subtypeId ? String(expense.subtypeId) : "",
  };
}

export default function FixerPage() {
  const today = useMemo(() => new Date(), []);
  const [search, setSearch] = useState("");
  const [spenderFilter, setSpenderFilter] = useState("");
  const [sortBy, setSortBy] = useState<"uploaded_desc" | "expense_date_desc">("uploaded_desc");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState(formatDateInput(today));
  const [expandedExpenseId, setExpandedExpenseId] = useState<number | null>(null);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);

  const utils = api.useUtils();
  const categoriesQuery = api.categories.getAll.useQuery();
  const expensesQuery = api.expenses.getAll.useQuery({
    limit: 50,
    offset: 0,
    search: search.trim() || undefined,
    spender: spenderFilter || undefined,
    startDate: startDate || undefined,
    endDate: endDate || undefined,
    includeDuplicates: true,
    sortBy,
  });

  useEffect(() => {
    const visibleIds = new Set((expensesQuery.data?.items ?? []).map((expense) => expense.id));
    setSelectedIds((current) => current.filter((id) => visibleIds.has(id)));

    if (expandedExpenseId !== null && !visibleIds.has(expandedExpenseId)) {
      setExpandedExpenseId(null);
    }
  }, [expensesQuery.data, expandedExpenseId]);

  const bulkDeleteMutation = api.expenses.deleteExpenses.useMutation({
    onSuccess: async ({ deletedIds }) => {
      await Promise.all([
        utils.expenses.getAll.invalidate(),
        utils.expenses.getReviewInsights.invalidate(),
        utils.expenses.getStats.invalidate(),
        utils.expenses.getSummary.invalidate(),
        utils.expenses.getByCategory.invalidate(),
        utils.expenses.getByCard.invalidate(),
        utils.validation.getPending.invalidate(),
        utils.validation.getStats.invalidate(),
      ]);

      setSelectedIds((current) => current.filter((id) => !deletedIds.includes(id)));
      if (expandedExpenseId !== null && deletedIds.includes(expandedExpenseId)) {
        setExpandedExpenseId(null);
      }
    },
  });

  const items = expensesQuery.data?.items ?? [];
  const allVisibleSelected = items.length > 0 && items.every((item) => selectedIds.includes(item.id));

  return (
    <div className="min-h-screen bg-zinc-950">
      <Navigation />
      <main className="container mx-auto px-4 py-8">
        <div className="mb-8">
          <h1 className="text-2xl font-semibold text-zinc-100">Table Fixer</h1>
          <p className="mt-2 max-w-3xl text-zinc-400">
            Repair incorrect rows in place. Expand a line item to edit it, use quick actions for one-off cleanup, or select multiple rows and delete them together.
          </p>
        </div>

        <div className="mb-6 rounded-2xl border border-zinc-800 bg-zinc-900/50 p-5">
          <div className="grid gap-3 lg:grid-cols-[minmax(0,2fr)_180px_180px_180px_200px]">
            <label className="text-sm text-zinc-400">
              <span className="mb-2 block">Search merchant or category</span>
              <input
                type="text"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="amazon, groceries, venmo..."
                className={inputClassName}
              />
            </label>
            <label className="text-sm text-zinc-400">
              <span className="mb-2 block">Spender</span>
              <select
                value={spenderFilter}
                onChange={(event) => setSpenderFilter(event.target.value)}
                className={inputClassName}
              >
                <option value="">All</option>
                <option value={MEMBER_A}>{MEMBER_A}</option>
                <option value={MEMBER_B}>{MEMBER_B}</option>
              </select>
            </label>
            <label className="text-sm text-zinc-400">
              <span className="mb-2 block">Start date</span>
              <input
                type="date"
                value={startDate}
                onChange={(event) => setStartDate(event.target.value)}
                className={inputClassName}
              />
            </label>
            <label className="text-sm text-zinc-400">
              <span className="mb-2 block">End date</span>
              <input
                type="date"
                value={endDate}
                onChange={(event) => setEndDate(event.target.value)}
                className={inputClassName}
              />
            </label>
            <label className="text-sm text-zinc-400">
              <span className="mb-2 block">Sort rows</span>
              <select
                value={sortBy}
                onChange={(event) =>
                  setSortBy(event.target.value as "uploaded_desc" | "expense_date_desc")
                }
                className={inputClassName}
              >
                <option value="uploaded_desc">Most recently uploaded</option>
                <option value="expense_date_desc">Newest expense date</option>
              </select>
            </label>
          </div>
        </div>

        <div className="mb-4 rounded-2xl border border-zinc-800 bg-zinc-900/50 p-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex flex-wrap items-center gap-3 text-sm text-zinc-400">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={allVisibleSelected}
                  onChange={(event) =>
                    setSelectedIds(
                      event.target.checked ? items.map((item) => item.id) : [],
                    )
                  }
                  className={checkboxClassName}
                />
                <span>Select all shown</span>
              </label>
              <span>{items.length} rows shown</span>
              <span>{selectedIds.length} selected</span>
            </div>

            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => setSelectedIds([])}
                disabled={selectedIds.length === 0 || bulkDeleteMutation.isPending}
                className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 transition-colors hover:border-zinc-500 hover:text-zinc-100 disabled:opacity-50"
              >
                Clear selection
              </button>
              <button
                type="button"
                onClick={() => {
                  if (selectedIds.length === 0) {
                    return;
                  }

                  const confirmed = window.confirm(
                    `Delete ${selectedIds.length} selected row${selectedIds.length === 1 ? "" : "s"}? This cannot be undone.`,
                  );

                  if (!confirmed) {
                    return;
                  }

                  bulkDeleteMutation.mutate({ ids: selectedIds });
                }}
                disabled={selectedIds.length === 0 || bulkDeleteMutation.isPending}
                className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-2 text-sm font-medium text-rose-200 transition-colors hover:bg-rose-500/20 disabled:opacity-50"
              >
                {bulkDeleteMutation.isPending ? "Deleting..." : "Delete selected"}
              </button>
            </div>
          </div>
          {bulkDeleteMutation.error && (
            <p className="mt-3 text-sm text-rose-400">{bulkDeleteMutation.error.message}</p>
          )}
        </div>

        <div className="space-y-4">
          {expensesQuery.isLoading ? (
            [1, 2, 3, 4, 5].map((row) => (
              <div key={row} className="h-24 animate-pulse rounded-2xl bg-zinc-900/50" />
            ))
          ) : items.length ? (
            items.map((expense) => (
              <ExpenseFixerRow
                key={expense.id}
                expense={expense}
                isExpanded={expandedExpenseId === expense.id}
                isSelected={selectedIds.includes(expense.id)}
                onToggleExpanded={() =>
                  setExpandedExpenseId((current) =>
                    current === expense.id ? null : expense.id,
                  )
                }
                onToggleSelected={() =>
                  setSelectedIds((current) =>
                    current.includes(expense.id)
                      ? current.filter((id) => id !== expense.id)
                      : [...current, expense.id],
                  )
                }
                categories={categoriesQuery.data ?? []}
                onDeleted={async (id) => {
                  await Promise.all([
                    utils.expenses.getAll.invalidate(),
                    utils.expenses.getReviewInsights.invalidate(),
                    utils.expenses.getStats.invalidate(),
                    utils.expenses.getSummary.invalidate(),
                    utils.expenses.getByCategory.invalidate(),
                    utils.expenses.getByCard.invalidate(),
                    utils.validation.getPending.invalidate(),
                    utils.validation.getStats.invalidate(),
                  ]);

                  setSelectedIds((current) => current.filter((selectedId) => selectedId !== id));
                  setExpandedExpenseId((current) => (current === id ? null : current));
                }}
              />
            ))
          ) : (
            <div className="rounded-2xl border border-dashed border-zinc-800 p-10 text-center text-zinc-500">
              No rows match this filter.
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

function ExpenseFixerRow({
  expense,
  isExpanded,
  isSelected,
  onToggleExpanded,
  onToggleSelected,
  categories,
  onDeleted,
}: {
  expense: ExpenseListItem;
  isExpanded: boolean;
  isSelected: boolean;
  onToggleExpanded: () => void;
  onToggleSelected: () => void;
  categories: RouterOutputs["categories"]["getAll"];
  onDeleted: (id: number) => Promise<void>;
}) {
  const utils = api.useUtils();
  const detailQuery = api.expenses.getById.useQuery(
    { id: expense.id },
    { enabled: isExpanded },
  );
  const [form, setForm] = useState<ExpenseFormState | null>(null);
  const deferredMerchantQuery = useDeferredValue(form?.merchant.trim() ?? "");
  const merchantSuggestionsQuery = api.validation.getMerchantSuggestions.useQuery(
    { query: deferredMerchantQuery, limit: 6 },
    {
      enabled: isExpanded && deferredMerchantQuery.length >= 2,
    },
  );
  const createSubtypeMutation = api.categories.createSubtype.useMutation({
    onSuccess: async () => {
      await utils.categories.getAll.invalidate();
    },
  });
  const [isAddingSubtype, setIsAddingSubtype] = useState(false);
  const [newSubtypeName, setNewSubtypeName] = useState("");
  const [subtypeError, setSubtypeError] = useState<string | null>(null);

  useEffect(() => {
    if (detailQuery.data) {
      setForm(toFormState(detailQuery.data));
    }
  }, [detailQuery.data]);

  useEffect(() => {
    setIsAddingSubtype(false);
    setNewSubtypeName("");
    setSubtypeError(null);
  }, [form?.categoryId]);

  const selectedCategory = categories.find(
    (category) => String(category.id) === form?.categoryId,
  );

  const updateExpenseMutation = api.expenses.updateExpense.useMutation({
    onSuccess: async (updated) => {
      await Promise.all([
        utils.expenses.getAll.invalidate(),
        updated ? utils.expenses.getById.invalidate({ id: updated.id }) : Promise.resolve(),
        utils.expenses.getReviewInsights.invalidate(),
        utils.expenses.getStats.invalidate(),
        utils.expenses.getSummary.invalidate(),
        utils.expenses.getByCategory.invalidate(),
        utils.expenses.getByCard.invalidate(),
        utils.validation.getPending.invalidate(),
        utils.validation.getStats.invalidate(),
      ]);
    },
  });

  const deleteExpenseMutation = api.expenses.deleteExpense.useMutation({
    onSuccess: async ({ id }) => {
      await onDeleted(id);
    },
  });

  const canSave =
    form &&
    form.date &&
    form.type.trim() &&
    Number.isFinite(Number(form.amount)) &&
    Number(form.amount) >= 0 &&
    Number.isFinite(Number(form.split)) &&
    Number(form.split) >= 0 &&
    Number(form.split) <= 1;

  return (
    <div
      className={`overflow-hidden rounded-2xl border transition-all duration-300 ${
        isExpanded
          ? "border-violet-500/50 bg-zinc-900/70 shadow-[0_0_0_1px_rgba(139,92,246,0.15)]"
          : "border-zinc-800 bg-zinc-900/50"
      }`}
    >
      <div className="px-4 py-4 sm:px-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex min-w-0 items-start gap-3">
            <input
              type="checkbox"
              checked={isSelected}
              onChange={onToggleSelected}
              className={`${checkboxClassName} mt-1`}
            />
            <button
              type="button"
              onClick={onToggleExpanded}
              className="min-w-0 text-left"
            >
              <p className="truncate font-medium text-zinc-100">
                {expense.merchant?.trim() ||
                  expense.originalInputName?.trim() ||
                  "Unknown merchant"}
              </p>
              <p className="mt-1 text-sm text-zinc-500">
                #{expense.id} | {new Date(`${expense.date}T00:00:00`).toLocaleDateString()} |{" "}
                {expense.type}
              </p>
              {expense.sourceProcessedAt && (
                <p className="mt-1 text-xs text-zinc-600">
                  Uploaded {expense.sourceProcessedAt.toLocaleString()}
                  {expense.sourceFilename ? ` from ${expense.sourceFilename}` : ""}
                </p>
              )}
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-3 lg:justify-end">
            <p className="font-medium text-zinc-100">
              {formatMoneyPrecise(expense.amount ?? 0)}
            </p>
            <button
              type="button"
              onClick={onToggleExpanded}
              className="rounded-lg border border-zinc-700 px-3 py-2 text-sm text-zinc-300 transition-colors hover:border-zinc-500 hover:text-zinc-100"
            >
              {isExpanded ? "Collapse" : "Expand"}
            </button>
            <button
              type="button"
              onClick={() => {
                const confirmed = window.confirm(
                  `Delete expense row #${expense.id}? This cannot be undone.`,
                );

                if (!confirmed) {
                  return;
                }

                deleteExpenseMutation.mutate({ id: expense.id });
              }}
              disabled={deleteExpenseMutation.isPending || updateExpenseMutation.isPending}
              className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm font-medium text-rose-200 transition-colors hover:bg-rose-500/20 disabled:opacity-50"
            >
              {deleteExpenseMutation.isPending ? "Deleting..." : "Delete"}
            </button>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          {expense.isDuplicate && (
            <span className="rounded-full bg-amber-500/10 px-2 py-1 text-amber-300">
              duplicate
            </span>
          )}
          {expense.needsValidation && (
            <span className="rounded-full bg-rose-500/10 px-2 py-1 text-rose-300">
              needs validation
            </span>
          )}
          {expense.spender && (
            <span className="rounded-full bg-zinc-800 px-2 py-1 text-zinc-300">
              {expense.spender}
            </span>
          )}
          {expense.card && (
            <span className="rounded-full bg-zinc-800 px-2 py-1 text-zinc-300">
              {expense.card}
            </span>
          )}
        </div>
      </div>

      <div
        className={`grid transition-all duration-300 ease-out ${
          isExpanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
        }`}
      >
        <div className="overflow-hidden">
          <div className="border-t border-zinc-800 px-4 py-5 sm:px-5">
            {!isExpanded ? null : detailQuery.isLoading || !form ? (
              <div className="h-40 animate-pulse rounded-xl bg-zinc-950/70" />
            ) : (
              <div className="space-y-6">
                <div className="grid gap-3 rounded-xl border border-zinc-800 bg-zinc-950/70 p-4 md:grid-cols-2 xl:grid-cols-4">
                  <InfoRow
                    label="Source file"
                    value={detailQuery.data?.sourceFile?.filename ?? "Unknown"}
                  />
                  <InfoRow
                    label="Created"
                    value={detailQuery.data?.createdAt?.toLocaleString() ?? "Unknown"}
                  />
                  <InfoRow
                    label={`${MEMBER_A} spend`}
                    value={formatMoneyPrecise(detailQuery.data?.memberASpend ?? 0)}
                  />
                  <InfoRow
                    label={`${MEMBER_B} spend`}
                    value={formatMoneyPrecise(detailQuery.data?.memberBSpend ?? 0)}
                  />
                </div>

                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                  <Field label="Date">
                    <input
                      type="date"
                      value={form.date}
                      onChange={(event) =>
                        setForm((current) => current && { ...current, date: event.target.value })
                      }
                      className={inputClassName}
                    />
                  </Field>
                  <Field label="Amount">
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      value={form.amount}
                      onChange={(event) =>
                        setForm((current) => current && { ...current, amount: event.target.value })
                      }
                      className={inputClassName}
                    />
                  </Field>
                  <Field label="Split">
                    <input
                      type="number"
                      min="0"
                      max="1"
                      step="0.01"
                      value={form.split}
                      onChange={(event) =>
                        setForm((current) => current && { ...current, split: event.target.value })
                      }
                      className={inputClassName}
                    />
                  </Field>
                  <Field label="Canonical merchant">
                    <div className="space-y-3">
                      <input
                        type="text"
                        value={form.merchant}
                        onChange={(event) =>
                          setForm(
                            (current) => current && { ...current, merchant: event.target.value },
                          )
                        }
                        className={inputClassName}
                      />
                      {merchantSuggestionsQuery.data &&
                        merchantSuggestionsQuery.data.length > 0 && (
                          <div className="rounded-xl border border-zinc-800 bg-zinc-950/70 p-3">
                            <p className="text-xs uppercase tracking-wide text-zinc-500">
                              Similar canonical merchants
                            </p>
                            <div className="mt-2 flex flex-wrap gap-2">
                              {merchantSuggestionsQuery.data.map((suggestion) => (
                                <button
                                  key={suggestion.merchant}
                                  type="button"
                                  onClick={() =>
                                    setForm(
                                      (current) =>
                                        current && {
                                          ...current,
                                          merchant: suggestion.merchant,
                                        },
                                    )
                                  }
                                  className="rounded-full border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-left text-xs text-zinc-200 transition-colors hover:border-violet-500 hover:text-white"
                                >
                                  {suggestion.merchant} ({suggestion.count} uses ·{" "}
                                  {Math.round(suggestion.score * 100)}%)
                                </button>
                              ))}
                            </div>
                          </div>
                        )}
                    </div>
                  </Field>
                  <Field label="Original input name">
                    <input
                      type="text"
                      value={form.originalInputName}
                      onChange={(event) =>
                        setForm((current) =>
                          current && { ...current, originalInputName: event.target.value }
                        )
                      }
                      className={inputClassName}
                    />
                  </Field>
                  <Field label="Spender">
                    <select
                      value={form.spender}
                      onChange={(event) =>
                        setForm((current) =>
                          current && {
                            ...current,
                            spender: event.target.value as string,
                          }
                        )
                      }
                      className={inputClassName}
                    >
                      <option value="">Unknown</option>
                      <option value={MEMBER_A}>{MEMBER_A}</option>
                      <option value={MEMBER_B}>{MEMBER_B}</option>
                    </select>
                  </Field>
                  <Field label="Category">
                    <select
                      value={form.categoryId}
                      onChange={(event) =>
                        setForm((current) =>
                          current && {
                            ...current,
                            categoryId: event.target.value,
                            subtypeId: "",
                            type:
                              categories.find((category) => String(category.id) === event.target.value)
                                ?.name ?? current.type,
                            subtype: "",
                          }
                        )
                      }
                      className={inputClassName}
                    >
                      <option value="">No linked category</option>
                      {categories.map((category) => (
                        <option key={category.id} value={category.id}>
                          {category.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Subtype">
                    <div className="space-y-3">
                      <select
                        value={form.subtypeId}
                        onChange={(event) =>
                          setForm((current) =>
                            current && {
                              ...current,
                              subtypeId: event.target.value,
                              subtype:
                                selectedCategory?.subtypes.find(
                                  (subtype: RouterOutputs["categories"]["getAll"][number]["subtypes"][number]) =>
                                    String(subtype.id) === event.target.value,
                                )?.name ?? "",
                            }
                          )
                        }
                        className={inputClassName}
                      >
                        <option value="">No linked subtype</option>
                        {(selectedCategory?.subtypes ?? []).map((subtype: RouterOutputs["categories"]["getAll"][number]["subtypes"][number]) => (
                          <option key={subtype.id} value={subtype.id}>
                            {subtype.name}
                          </option>
                        ))}
                      </select>
                      {selectedCategory ? (
                        <>
                          <div className="flex items-center gap-3 text-sm">
                            <button
                              type="button"
                              onClick={() => {
                                setIsAddingSubtype((current) => !current);
                                setSubtypeError(null);
                              }}
                              className="text-violet-400 transition-colors hover:text-violet-300"
                            >
                              {isAddingSubtype ? "Cancel new subtype" : "+ New subtype"}
                            </button>
                            {subtypeError ? <span className="text-rose-400">{subtypeError}</span> : null}
                          </div>
                          {isAddingSubtype ? (
                            <div className="flex gap-2">
                              <input
                                type="text"
                                value={newSubtypeName}
                                onChange={(event) => setNewSubtypeName(event.target.value)}
                                placeholder={`Subtype for ${selectedCategory.name}`}
                                className={inputClassName}
                              />
                              <button
                                type="button"
                                disabled={createSubtypeMutation.isPending}
                                onClick={async () => {
                                  const categoryId = Number(form.categoryId);
                                  if (!Number.isFinite(categoryId) || categoryId <= 0) {
                                    setSubtypeError("Pick a category first.");
                                    return;
                                  }

                                  const trimmed = newSubtypeName.trim();
                                  if (!trimmed) {
                                    setSubtypeError("Enter a subtype name.");
                                    return;
                                  }

                                  try {
                                    const created = await createSubtypeMutation.mutateAsync({
                                      categoryId,
                                      name: trimmed,
                                    });
                                    if (!created) {
                                      throw new Error("Could not create subtype.");
                                    }
                                    setForm(
                                      (current) =>
                                        current && {
                                          ...current,
                                          subtypeId: String(created.id),
                                          subtype: created.name,
                                        },
                                    );
                                    setNewSubtypeName("");
                                    setIsAddingSubtype(false);
                                  } catch {
                                    setSubtypeError("Could not create subtype.");
                                  }
                                }}
                                className="rounded-lg border border-violet-500/40 bg-violet-500/10 px-4 py-2 text-zinc-100 transition-colors hover:bg-violet-500/20 disabled:opacity-50"
                              >
                                {createSubtypeMutation.isPending ? "Creating..." : "Create"}
                              </button>
                            </div>
                          ) : null}
                        </>
                      ) : (
                        <p className="text-xs text-zinc-500">Pick a category before adding a subtype.</p>
                      )}
                    </div>
                  </Field>
                  <Field label="Type text">
                    <input
                      type="text"
                      value={form.type}
                      onChange={(event) =>
                        setForm((current) => current && { ...current, type: event.target.value })
                      }
                      className={inputClassName}
                    />
                  </Field>
                  <Field label="Subtype text">
                    <input
                      type="text"
                      value={form.subtype}
                      onChange={(event) =>
                        setForm((current) => current && { ...current, subtype: event.target.value })
                      }
                      className={inputClassName}
                    />
                  </Field>
                  <Field label="Card">
                    <input
                      type="text"
                      value={form.card}
                      onChange={(event) =>
                        setForm((current) => current && { ...current, card: event.target.value })
                      }
                      className={inputClassName}
                    />
                  </Field>
                  <Field label="Tag">
                    <input
                      type="text"
                      value={form.tag}
                      onChange={(event) =>
                        setForm((current) => current && { ...current, tag: event.target.value })
                      }
                      className={inputClassName}
                    />
                  </Field>
                  <Field label="Link">
                    <input
                      type="text"
                      value={form.link}
                      onChange={(event) =>
                        setForm((current) => current && { ...current, link: event.target.value })
                      }
                      className={inputClassName}
                    />
                  </Field>
                  <Field label="Track with friends">
                    <input
                      type="text"
                      value={form.trackWithFriends}
                      onChange={(event) =>
                        setForm((current) =>
                          current && { ...current, trackWithFriends: event.target.value }
                        )
                      }
                      className={inputClassName}
                    />
                  </Field>
                </div>

                <ExpenseReceipts
                  expenseId={expense.id}
                  onLinked={(fileName) =>
                    // Linking fills an empty link in the database; keep the form in step so saving does not clear it.
                    setForm((current) => current && (current.link ? current : { ...current, link: fileName }))
                  }
                />

                <div className="grid gap-3 lg:grid-cols-3">
                  <ToggleCard
                    title="Log to work"
                    checked={form.logToWork}
                    onChange={(checked) =>
                      setForm((current) => current && { ...current, logToWork: checked })
                    }
                  />
                  <ToggleCard
                    title="Duplicate flag"
                    checked={form.isDuplicate}
                    onChange={(checked) =>
                      setForm((current) => current && { ...current, isDuplicate: checked })
                    }
                  />
                  <ToggleCard
                    title="Needs validation"
                    checked={form.needsValidation}
                    onChange={(checked) =>
                      setForm((current) => current && { ...current, needsValidation: checked })
                    }
                  />
                </div>

                <div className="flex flex-wrap items-center gap-3">
                  <button
                    type="button"
                    onClick={() => detailQuery.data && setForm(toFormState(detailQuery.data))}
                    className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 transition-colors hover:border-zinc-500 hover:text-zinc-100"
                  >
                    Reset
                  </button>
                  <button
                    type="button"
                    disabled={
                      !canSave ||
                      updateExpenseMutation.isPending ||
                      deleteExpenseMutation.isPending
                    }
                    onClick={() => {
                      if (!form) {
                        return;
                      }

                      updateExpenseMutation.mutate({
                        id: expense.id,
                        date: form.date,
                        type: form.type.trim(),
                        subtype: form.subtype.trim() || null,
                        merchant: form.merchant.trim() || null,
                        originalInputName: form.originalInputName.trim() || null,
                        amount: Number(form.amount),
                        split: Number(form.split),
                        spender: form.spender || null,
                        card: form.card.trim() || null,
                        tag: form.tag.trim() || null,
                        logToWork: form.logToWork,
                        link: form.link.trim() || null,
                        trackWithFriends: form.trackWithFriends.trim() || null,
                        friendShares: form.friendShares,
                        isDuplicate: form.isDuplicate,
                        needsValidation: form.needsValidation,
                        categoryId: form.categoryId ? Number(form.categoryId) : null,
                        subtypeId: form.subtypeId ? Number(form.subtypeId) : null,
                      });
                    }}
                    className="rounded-lg bg-violet-600 px-5 py-2.5 font-medium text-white transition-colors hover:bg-violet-700 disabled:opacity-50"
                  >
                    {updateExpenseMutation.isPending ? "Saving..." : "Save fixes"}
                  </button>
                  {updateExpenseMutation.isSuccess && (
                    <p className="text-sm text-emerald-400">Saved.</p>
                  )}
                  {updateExpenseMutation.error && (
                    <p className="text-sm text-rose-400">
                      {updateExpenseMutation.error.message}
                    </p>
                  )}
                  {deleteExpenseMutation.error && (
                    <p className="text-sm text-rose-400">
                      {deleteExpenseMutation.error.message}
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block text-sm text-zinc-400">
      <span className="mb-2 block">{label}</span>
      {children}
    </label>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-zinc-500">{label}</p>
      <p className="mt-1 text-sm text-zinc-200">{value}</p>
    </div>
  );
}

function ToggleCard({
  title,
  checked,
  onChange,
}: {
  title: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex items-center justify-between rounded-xl border border-zinc-800 bg-zinc-950/70 px-4 py-3 text-zinc-100">
      <span>{title}</span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className={checkboxClassName}
      />
    </label>
  );
}
