"use client";

import { MEMBER_A, MEMBER_B } from "~/lib/household";

import { useDeferredValue, useEffect, useState } from "react";
import { Navigation } from "~/app/_components/navigation";
import {
  calculateHouseholdRemainder,
  deriveSplitFromExactAmounts,
  sumFriendShares,
  summarizeTrackedFriends,
  type FriendShareInput,
  type SplitMode,
} from "~/lib/expense-sharing";
import { api } from "~/trpc/react";
import type { RouterOutputs } from "~/trpc/react";

type PendingItem = RouterOutputs["validation"]["getPending"][number];
type ApprovedItem = RouterOutputs["validation"]["getRecentApproved"][number];
type CategoryOption = RouterOutputs["categories"]["getAll"][number];
type FriendOption = RouterOutputs["friends"]["getAll"][number];

type ValidationExpense =
  | PendingItem["expense"]
  | ApprovedItem;
type SuggestionCategoryLike = { id: number } | null | undefined;
type SuggestionSubtypeLike = { id: number } | null | undefined;
type ExpenseCategoryLike = { name: string; subtypes?: CategoryOption["subtypes"] } | null | undefined;

function formatMoney(value: number | null | undefined) {
  return (value ?? 0).toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function roundMoney(value: number) {
  return Math.round(value * 100) / 100;
}

function clampPercent(value: number) {
  if (!Number.isFinite(value)) {
    return 0;
  }

  return Math.min(Math.max(value, 0), 100);
}

function formatEditableNumber(value: number) {
  if (!Number.isFinite(value)) {
    return "";
  }

  return `${roundMoney(value)}`;
}

function formatDate(value: string | Date) {
  const date = value instanceof Date ? value : new Date(`${value}T00:00:00`);
  return date.toLocaleDateString();
}

function formatDateTime(value: string | Date | null | undefined) {
  if (!value) {
    return null;
  }

  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }

  return date.toLocaleString([], {
    month: "numeric",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function getMerchantMappingLabel(expense: ValidationExpense, merchantName: string) {
  const original = expense.originalInputName?.trim();
  const canonical = merchantName.trim();
  if (!original || !canonical) {
    return null;
  }

  if (original.toLowerCase() === canonical.toLowerCase()) {
    return null;
  }

  return `${original} -> ${canonical}`;
}

function getInitialAllocation(expense: ValidationExpense) {
  const totalAmount = roundMoney(Number(expense.amount ?? 0));
  const split = Number.isFinite(expense.split ?? 1) ? Math.min(Math.max(expense.split ?? 1, 0), 1) : 1;

  if (typeof expense.memberASpend === "number" || typeof expense.memberBSpend === "number") {
    return {
      memberAAmount: roundMoney(expense.memberASpend ?? 0),
      memberBAmount: roundMoney(expense.memberBSpend ?? 0),
    };
  }

  if (expense.spender === MEMBER_A) {
    return {
      memberAAmount: roundMoney(totalAmount * split),
      memberBAmount: roundMoney(totalAmount - totalAmount * split),
    };
  }

  if (expense.spender === MEMBER_B) {
    return {
      memberAAmount: roundMoney(totalAmount - totalAmount * split),
      memberBAmount: roundMoney(totalAmount * split),
    };
  }

  return {
    memberAAmount: roundMoney(totalAmount / 2),
    memberBAmount: roundMoney(totalAmount / 2),
  };
}

function signedAmount(value: number, totalAmount: number) {
  return totalAmount < 0 ? -Math.abs(value) : Math.abs(value);
}

export default function ValidationPage() {
  const utils = api.useUtils();
  const pendingQuery = api.validation.getPending.useQuery({ limit: 24 });
  const approvedQuery = api.validation.getRecentApproved.useQuery({ limit: 10 });
  const statsQuery = api.validation.getStats.useQuery();
  const categoriesQuery = api.categories.getAll.useQuery();
  const friendsQuery = api.friends.getAll.useQuery();
  const [expandedCardId, setExpandedCardId] = useState<string | null>(null);

  const createCategoryMutation = api.categories.create.useMutation({
    onSuccess: async () => {
      await utils.categories.getAll.invalidate();
    },
  });
  const createSubtypeMutation = api.categories.createSubtype.useMutation({
    onSuccess: async () => {
      await utils.categories.getAll.invalidate();
    },
  });
  const createFriendMutation = api.friends.create.useMutation({
    onSuccess: async () => {
      await utils.friends.getAll.invalidate();
    },
  });

  const validateMutation = api.validation.validate.useMutation({
    onSuccess: async () => {
      await Promise.all([
        utils.validation.getPending.invalidate(),
        utils.validation.getRecentApproved.invalidate(),
        utils.validation.getStats.invalidate(),
        utils.expenses.getReviewInsights.invalidate(),
        utils.expenses.getRecent.invalidate(),
        utils.expenses.getRecentUnapproved.invalidate(),
        utils.expenses.getStats.invalidate(),
        utils.expenses.getSummary.invalidate(),
        utils.expenses.getByCategory.invalidate(),
        utils.expenses.getByCard.invalidate(),
      ]);
    },
  });
  const deleteExpenseMutation = api.expenses.deleteExpense.useMutation({
    onSuccess: async () => {
      await Promise.all([
        utils.validation.getPending.invalidate(),
        utils.validation.getRecentApproved.invalidate(),
        utils.validation.getStats.invalidate(),
        utils.expenses.getRecent.invalidate(),
        utils.expenses.getRecentUnapproved.invalidate(),
        utils.expenses.getStats.invalidate(),
        utils.expenses.getSummary.invalidate(),
        utils.expenses.getByCategory.invalidate(),
        utils.expenses.getByCard.invalidate(),
        utils.expenses.getReviewInsights.invalidate(),
      ]);
    },
  });

  const pendingItems = pendingQuery.data ?? [];
  const approvedItems = approvedQuery.data ?? [];
  const categories = categoriesQuery.data ?? [];
  const friends = friendsQuery.data ?? [];

  useEffect(() => {
    const firstPending = pendingItems[0];
    if (!expandedCardId && firstPending) {
      setExpandedCardId(`pending-${firstPending.expenseId}`);
    }
  }, [expandedCardId, pendingItems]);

  return (
    <div className="min-h-screen bg-zinc-950">
      <Navigation />
      <main className="container mx-auto px-4 py-8">
        <div className="mb-8">
          <h1 className="text-2xl font-semibold text-zinc-100">Approval Queue</h1>
          <p className="mt-2 max-w-3xl text-zinc-400">
            Uploaded transactions stay staged until you approve them. Reporting only uses approved rows.
          </p>
        </div>

        <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_320px]">
          <div className="space-y-8">
            <section className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-medium text-zinc-100">Awaiting Approval</h2>
                  <p className="text-sm text-zinc-500">
                    Every staged transaction can set category, subtype, merchant, split, tag, work logging, and friends tracking before it enters reports.
                  </p>
                </div>
                <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-3 py-1 text-sm text-amber-200">
                  {pendingItems.length} shown
                </span>
              </div>

              {pendingQuery.isLoading ? (
                [1, 2, 3].map((item) => (
                  <div key={item} className="h-44 animate-pulse rounded-2xl bg-zinc-900/50" />
                ))
              ) : pendingItems.length > 0 ? (
                pendingItems.map((item) => {
                  const cardId = `pending-${item.expenseId}`;
                  const suggestedCategory = item.suggestedCategory as SuggestionCategoryLike;
                  const suggestedSubtype = item.suggestedSubtype as SuggestionSubtypeLike;
                  return (
                    <ValidationCard
                      key={cardId}
                      expense={item.expense}
                      categories={categories}
                      friends={friends}
                      reason={item.reason}
                      suggestedCategoryId={suggestedCategory?.id ?? item.expense.categoryId ?? null}
                      suggestedSubtypeId={suggestedSubtype?.id ?? item.expense.subtypeId ?? null}
                      rememberLabelSource={item.expense.originalInputName ?? item.expense.merchant}
                      expanded={expandedCardId === cardId}
                      compact={false}
                      isSaving={validateMutation.isPending || deleteExpenseMutation.isPending}
                      isCreatingCategory={createCategoryMutation.isPending}
                      isCreatingSubtype={createSubtypeMutation.isPending}
                      isCreatingFriend={createFriendMutation.isPending}
                      onExpand={() => setExpandedCardId(cardId)}
                      onCollapse={() => setExpandedCardId(null)}
                      onCreateCategory={async (name) => {
                        const created = await createCategoryMutation.mutateAsync({ name });
                        if (!created) {
                          throw new Error("Could not create category.");
                        }
                        return created.id;
                      }}
                      onCreateSubtype={async (categoryId, name) => {
                        const created = await createSubtypeMutation.mutateAsync({ categoryId, name });
                        if (!created) {
                          throw new Error("Could not create subtype.");
                        }
                        return created.id;
                      }}
                      onCreateFriend={async (name) => {
                        const created = await createFriendMutation.mutateAsync({ name });
                        if (!created) {
                          throw new Error("Could not create friend.");
                        }
                        return created.id;
                      }}
                      onApprove={(input) =>
                        validateMutation.mutate({
                          expenseId: item.expenseId,
                          ...input,
                        })
                      }
                      onDelete={() => deleteExpenseMutation.mutate({ id: item.expenseId })}
                    />
                  );
                })
              ) : (
                <div className="rounded-2xl border border-dashed border-zinc-800 p-10 text-center text-zinc-500">
                  Nothing is staged right now.
                </div>
              )}
            </section>

            <section className="space-y-4">
              <div>
                <h2 className="text-lg font-medium text-zinc-100">Recently Approved</h2>
                <p className="text-sm text-zinc-500">
                  These cards stay compact by default so the queue does not overwhelm you, but each one can be expanded if you need to revise it.
                </p>
              </div>

              {approvedItems.map((expense) => {
                const cardId = `approved-${expense.id}`;
                return (
                  <ValidationCard
                    key={cardId}
                    expense={expense}
                    categories={categories}
                    friends={friends}
                    reason="approved"
                    suggestedCategoryId={expense.categoryId ?? null}
                    suggestedSubtypeId={expense.subtypeId ?? null}
                    rememberLabelSource={expense.originalInputName ?? expense.merchant}
                    expanded={expandedCardId === cardId}
                    compact
                    isSaving={validateMutation.isPending || deleteExpenseMutation.isPending}
                    isCreatingCategory={createCategoryMutation.isPending}
                    isCreatingSubtype={createSubtypeMutation.isPending}
                    isCreatingFriend={createFriendMutation.isPending}
                    onExpand={() => setExpandedCardId(cardId)}
                    onCollapse={() => setExpandedCardId(null)}
                    onCreateCategory={async (name) => {
                      const created = await createCategoryMutation.mutateAsync({ name });
                      if (!created) {
                        throw new Error("Could not create category.");
                      }
                      return created.id;
                    }}
                    onCreateSubtype={async (categoryId, name) => {
                      const created = await createSubtypeMutation.mutateAsync({ categoryId, name });
                      if (!created) {
                        throw new Error("Could not create subtype.");
                      }
                      return created.id;
                    }}
                    onCreateFriend={async (name) => {
                      const created = await createFriendMutation.mutateAsync({ name });
                      if (!created) {
                        throw new Error("Could not create friend.");
                      }
                      return created.id;
                    }}
                    onApprove={(input) =>
                      validateMutation.mutate({
                        expenseId: expense.id,
                        ...input,
                      })
                    }
                    onDelete={() => deleteExpenseMutation.mutate({ id: expense.id })}
                  />
                );
              })}
            </section>
          </div>

          <aside className="space-y-6">
            <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6">
              <h2 className="text-lg font-medium text-zinc-100">Approval Stats</h2>
              {statsQuery.isLoading ? (
                <div className="mt-4 space-y-3">
                  <div className="h-4 animate-pulse rounded bg-zinc-800" />
                  <div className="h-4 animate-pulse rounded bg-zinc-800" />
                  <div className="h-4 animate-pulse rounded bg-zinc-800" />
                </div>
              ) : (
                <div className="mt-4 space-y-4 text-sm">
                  <StatRow label="Staged" value={statsQuery.data?.pending ?? 0} tone="amber" />
                  <StatRow label="Approved Today" value={statsQuery.data?.validatedToday ?? 0} tone="emerald" />
                  <StatRow label="Approved Total" value={statsQuery.data?.totalValidated ?? 0} tone="zinc" />
                  <StatRow label="Mappings Learned" value={statsQuery.data?.merchantsLearned ?? 0} tone="violet" />
                </div>
              )}
            </div>

            <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6">
              <h2 className="text-lg font-medium text-zinc-100">Workflow</h2>
              <div className="mt-4 space-y-3 text-sm text-zinc-400">
                <p>1. Upload statements into staging.</p>
                <p>2. Approve each transaction with the right category and attributes.</p>
                <p>3. Only approved transactions flow into dashboards, review, and export.</p>
                <p>4. If you toggle mapping memory, future similar inputs inherit category, subtype, and canonical merchant.</p>
              </div>
            </div>
          </aside>
        </div>
      </main>
    </div>
  );
}

function ValidationCard({
  expense,
  categories,
  friends,
  reason,
  suggestedCategoryId,
  suggestedSubtypeId,
  rememberLabelSource,
  expanded,
  compact,
  isSaving,
  isCreatingCategory,
  isCreatingSubtype,
  isCreatingFriend,
  onExpand,
  onCollapse,
  onCreateCategory,
  onCreateSubtype,
  onCreateFriend,
  onApprove,
  onDelete,
}: {
  expense: ValidationExpense;
  categories: CategoryOption[];
  friends: FriendOption[];
  reason: string | null;
  suggestedCategoryId: number | null;
  suggestedSubtypeId: number | null;
  rememberLabelSource: string | null | undefined;
  expanded: boolean;
  compact: boolean;
  isSaving: boolean;
  isCreatingCategory: boolean;
  isCreatingSubtype: boolean;
  isCreatingFriend: boolean;
  onExpand: () => void;
  onCollapse: () => void;
  onCreateCategory: (name: string) => Promise<number>;
  onCreateSubtype: (categoryId: number, name: string) => Promise<number>;
  onCreateFriend: (name: string) => Promise<number>;
  onApprove: (input: {
    categoryId: number;
    subtypeId: number | null;
    rememberMapping: boolean;
    merchant: string | null;
    split: number;
    tag: string | null;
    logToWork: boolean | null;
    trackWithFriends: string | null;
    friendShares: FriendShareInput[];
  }) => void;
  onDelete: () => void;
}) {
  const initialAllocation = getInitialAllocation(expense);
  const [selectedCategoryId, setSelectedCategoryId] = useState<number | null>(suggestedCategoryId);
  const [selectedSubtypeId, setSelectedSubtypeId] = useState<number | null>(suggestedSubtypeId);
  const [merchantName, setMerchantName] = useState(expense.merchant ?? "");
  const [tag, setTag] = useState(expense.tag ?? "");
  const [friendShares, setFriendShares] = useState<FriendShareInput[]>(
    expense.friendShares?.map((share) => ({ friendId: share.friendId, amount: share.amount })) ?? [],
  );
  const [splitMode, setSplitMode] = useState<SplitMode>(
    (expense.friendShares?.length ?? 0) > 0 ? "exact" : "percentage",
  );
  const [memberAAmount, setMemberAAmount] = useState<number>(initialAllocation.memberAAmount);
  const [memberBAmount, setMemberBAmount] = useState<number>(initialAllocation.memberBAmount);
  const [logToWork, setLogToWork] = useState<boolean>(expense.logToWork ?? false);
  const [rememberMapping, setRememberMapping] = useState(true);
  const [isTagFocused, setIsTagFocused] = useState(false);
  const [isAddingCategory, setIsAddingCategory] = useState(false);
  const [newCategoryName, setNewCategoryName] = useState("");
  const [categoryError, setCategoryError] = useState<string | null>(null);
  const [isAddingSubtype, setIsAddingSubtype] = useState(false);
  const [newSubtypeName, setNewSubtypeName] = useState("");
  const [subtypeError, setSubtypeError] = useState<string | null>(null);
  const [isAddingFriend, setIsAddingFriend] = useState(false);
  const [newFriendName, setNewFriendName] = useState("");
  const [friendError, setFriendError] = useState<string | null>(null);
  const [editingAllocationField, setEditingAllocationField] = useState<string | null>(null);
  const [allocationDrafts, setAllocationDrafts] = useState<Record<string, string>>({});

  const deferredMerchantName = useDeferredValue(merchantName.trim());
  const deferredTag = useDeferredValue(tag.trim());
  const [debouncedTagQuery, setDebouncedTagQuery] = useState(deferredTag);

  useEffect(() => {
    setSelectedCategoryId(suggestedCategoryId);
  }, [suggestedCategoryId]);

  useEffect(() => {
    setSelectedSubtypeId(suggestedSubtypeId);
  }, [suggestedSubtypeId]);

  useEffect(() => {
    setIsAddingSubtype(false);
    setNewSubtypeName("");
    setSubtypeError(null);
  }, [selectedCategoryId]);

  useEffect(() => {
    setEditingAllocationField(null);
    setAllocationDrafts({});
  }, [expense.id, splitMode]);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setDebouncedTagQuery(deferredTag);
    }, 250);

    return () => window.clearTimeout(timeout);
  }, [deferredTag]);

  const merchantSuggestionsQuery = api.validation.getMerchantSuggestions.useQuery(
    { query: deferredMerchantName, limit: 5 },
    { enabled: expanded && deferredMerchantName.length >= 2 },
  );
  const tagSuggestionsQuery = api.validation.getTagSuggestions.useQuery(
    {
      expenseId: expense.id,
      query: debouncedTagQuery,
      limit: 6,
      windowDays: 45,
    },
    {
      enabled: expanded && (isTagFocused || debouncedTagQuery.length > 0),
    },
  );
  const categorySuggestionsQuery = api.validation.getCategorySuggestions.useQuery(
    {
      expenseId: expense.id,
      limit: 5,
      windowDays: 30,
    },
    { enabled: expanded },
  );

  const merchantSuggestions = merchantSuggestionsQuery.data ?? [];
  const tagSuggestions = tagSuggestionsQuery.data ?? [];
  const categorySuggestions = categorySuggestionsQuery.data ?? [];
  const selectedCategory = categories.find((category) => category.id === selectedCategoryId);
  const expenseCategory = expense.categoryRel as ExpenseCategoryLike;
  const friendsById = new Map(friends.map((friend) => [friend.id, { name: friend.name }]));
  const totalAmount = roundMoney(Number(expense.amount ?? 0));
  const absoluteTotalAmount = Math.abs(totalAmount);
  const friendShareTotal = sumFriendShares(friendShares);
  const householdAmount = calculateHouseholdRemainder(totalAmount, friendShares);
  const memberADisplayAmount = Math.abs(memberAAmount);
  const memberBDisplayAmount = Math.abs(memberBAmount);
  const allocatedTotal = roundMoney(memberADisplayAmount + memberBDisplayAmount + friendShareTotal);
  const allocationDelta = roundMoney(absoluteTotalAmount - allocatedTotal);
  const memberAPercent = absoluteTotalAmount > 0 ? clampPercent((memberADisplayAmount / absoluteTotalAmount) * 100) : 0;
  const memberBPercent = absoluteTotalAmount > 0 ? clampPercent((memberBDisplayAmount / absoluteTotalAmount) * 100) : 0;
  const derivedSplit = deriveSplitFromExactAmounts({
    householdAmount,
    spender: expense.spender,
    memberAAmount,
    memberBAmount,
  });
  const trackedFriendsLabel = summarizeTrackedFriends(friendShares, friendsById);
  const hasExactTagMatch = tagSuggestions.some(
    (suggestion) => suggestion.tag.trim().toLowerCase() === debouncedTagQuery.toLowerCase(),
  );
  const merchantMappingLabel = getMerchantMappingLabel(expense, merchantName);
  const approvedAtLabel = compact ? formatDateTime("approvedAt" in expense ? expense.approvedAt : null) : null;

  const categoryName =
    selectedCategory?.name ??
    expenseCategory?.name ??
    expense.type ??
    "Uncategorized";
  const allocationIsBalanced = Math.abs(allocationDelta) <= 0.01;
  const compactSplitSummary = [
    `${MEMBER_A} ${formatMoney(expense.memberASpend ?? memberAAmount)}`,
    `${MEMBER_B} ${formatMoney(expense.memberBSpend ?? memberBAmount)}`,
    trackedFriendsLabel ? `Friends ${formatMoney(signedAmount(friendShareTotal, totalAmount))}` : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" · ");

  function getAllocationInputValue(fieldKey: string, numericValue: number) {
    if (editingAllocationField === fieldKey) {
      return allocationDrafts[fieldKey] ?? "";
    }

    return formatEditableNumber(numericValue);
  }

  function setAllocationDraft(fieldKey: string, value: string) {
    setAllocationDrafts((current) => ({
      ...current,
      [fieldKey]: value,
    }));
  }

  function clearAllocationDraft(fieldKey: string) {
    setAllocationDrafts((current) => {
      if (!(fieldKey in current)) {
        return current;
      }

      const next = { ...current };
      delete next[fieldKey];
      return next;
    });
  }

  function updateMemberAAllocation(rawValue: number) {
    const safeValue = Math.abs(Number.isFinite(rawValue) ? rawValue : 0);
    if (splitMode === "percentage") {
      const nextMemberAAmount = signedAmount(
        roundMoney((absoluteTotalAmount * clampPercent(safeValue)) / 100),
        totalAmount,
      );
      setMemberAAmount(nextMemberAAmount);
      if (friendShares.length === 0) {
        setMemberBAmount(
          signedAmount(roundMoney(absoluteTotalAmount - Math.abs(nextMemberAAmount)), totalAmount),
        );
      }
      return;
    }

    setMemberAAmount(signedAmount(roundMoney(safeValue), totalAmount));
  }

  function updateMemberBAllocation(rawValue: number) {
    const safeValue = Math.abs(Number.isFinite(rawValue) ? rawValue : 0);
    if (splitMode === "percentage") {
      const nextMemberBAmount = signedAmount(
        roundMoney((absoluteTotalAmount * clampPercent(safeValue)) / 100),
        totalAmount,
      );
      setMemberBAmount(nextMemberBAmount);
      if (friendShares.length === 0) {
        setMemberAAmount(
          signedAmount(roundMoney(absoluteTotalAmount - Math.abs(nextMemberBAmount)), totalAmount),
        );
      }
      return;
    }

    setMemberBAmount(signedAmount(roundMoney(safeValue), totalAmount));
  }

  function updateFriendAllocation(index: number, rawValue: number) {
    const safeValue = Math.abs(Number.isFinite(rawValue) ? rawValue : 0);
    const nextAmount =
      splitMode === "percentage"
        ? roundMoney((absoluteTotalAmount * clampPercent(safeValue)) / 100)
        : roundMoney(safeValue);

    setFriendShares((current) =>
      current.map((item, itemIndex) =>
        itemIndex === index ? { ...item, amount: nextAmount } : item,
      ),
    );
  }

  function handleAllocationInputChange(
    fieldKey: string,
    value: string,
  ) {
    setAllocationDraft(fieldKey, value);
  }

  function handleAllocationInputBlur(
    fieldKey: string,
    onParsedValue: (parsedValue: number) => void,
  ) {
    const draftValue = allocationDrafts[fieldKey];
    if (draftValue !== undefined) {
      if (draftValue.trim() === "") {
        onParsedValue(0);
      } else {
        const parsedValue = Number(draftValue);
        if (Number.isFinite(parsedValue)) {
          onParsedValue(parsedValue);
        }
      }
    }

    if (editingAllocationField === fieldKey) {
      setEditingAllocationField(null);
    }

    clearAllocationDraft(fieldKey);
  }

  return (
    <div
      className={`rounded-2xl border transition-all ${
        expanded
          ? "border-violet-500/50 bg-zinc-900/70"
          : compact
            ? "border-zinc-800 bg-zinc-900/40"
            : "border-zinc-800 bg-zinc-900/50"
      }`}
    >
      <div className={`${compact && !expanded ? "px-4 py-3" : "px-5 py-4"}`}>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <button type="button" onClick={expanded ? onCollapse : onExpand} className="min-w-0 text-left">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="truncate font-medium text-zinc-100">
                {expense.originalInputName || merchantName || "Unknown merchant"}
              </h3>
              <span
                className={`rounded-full px-2 py-0.5 text-xs ${
                  compact ? "bg-emerald-500/10 text-emerald-300" : "bg-amber-500/10 text-amber-300"
                }`}
              >
                {compact ? "approved" : "staged"}
              </span>
            </div>
            <p className="mt-1 text-sm text-zinc-500">
              {compact && approvedAtLabel
                ? `Approved ${approvedAtLabel} · ${expense.card || "Unknown card"} · ${categoryName}`
                : `${formatDate(expense.date)} · ${expense.card || "Unknown card"} · ${categoryName}`}
            </p>
            {compact && approvedAtLabel ? (
              <p className="mt-1 text-xs text-zinc-600">Transaction date {formatDate(expense.date)}</p>
            ) : null}
            {merchantMappingLabel ? (
              <p className="mt-1 text-xs text-zinc-400">{merchantMappingLabel}</p>
            ) : null}
            {compact && !expanded ? (
              <p className="mt-1 text-xs text-zinc-600">
                {expense.tag ? `Tag: ${expense.tag} · ` : ""}
                {compactSplitSummary}
              </p>
            ) : null}
          </button>

          <div className="flex items-center gap-3">
            <p className="font-medium text-zinc-100">{formatMoney(expense.amount)}</p>
            <button
              type="button"
              onClick={expanded ? onCollapse : onExpand}
              className="rounded-lg border border-zinc-700 px-3 py-2 text-sm text-zinc-300 transition-colors hover:border-zinc-500 hover:text-zinc-100"
            >
              {expanded ? "Collapse" : compact ? "Expand" : "Review"}
            </button>
          </div>
        </div>
      </div>

      {expanded ? (
        <div className="border-t border-zinc-800 px-5 py-5">
          <div className="mb-4 flex flex-wrap gap-2 text-xs">
            {reason ? (
              <span className="rounded-full bg-zinc-800 px-2 py-1 text-zinc-300">{reason}</span>
            ) : null}
            {expense.originalInputName && expense.originalInputName !== expense.merchant ? (
              <span className="rounded-full bg-zinc-800 px-2 py-1 text-zinc-300">
                imported as {expense.originalInputName}
              </span>
            ) : null}
            {expense.spender ? (
              <span className="rounded-full bg-zinc-800 px-2 py-1 text-zinc-300">
                paid by {expense.spender}
              </span>
            ) : null}
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label className="mb-2 block text-sm text-zinc-400">Category</label>
              <select
                value={selectedCategoryId ?? ""}
                onChange={(event) => {
                  setSelectedCategoryId(event.target.value ? Number(event.target.value) : null);
                  setSelectedSubtypeId(null);
                }}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
              >
                <option value="">Select a category...</option>
                {categories.map((category) => (
                  <option key={category.id} value={category.id}>
                    {category.name}
                  </option>
                ))}
              </select>
              {categorySuggestions.length > 0 ? (
                <div className="mt-2 flex flex-wrap gap-2">
                  {categorySuggestions.map((suggestion) => (
                    <button
                      key={suggestion.categoryId}
                      type="button"
                      onClick={() => {
                        setSelectedCategoryId(suggestion.categoryId);
                        setSelectedSubtypeId(null);
                      }}
                      className={`rounded-full border px-3 py-1 text-xs transition-colors ${
                        selectedCategoryId === suggestion.categoryId
                          ? "border-violet-500 bg-violet-500/10 text-zinc-100"
                          : "border-zinc-700 bg-zinc-900 text-zinc-300 hover:border-violet-500"
                      }`}
                    >
                      {suggestion.categoryName}
                      <span className="ml-2 text-zinc-500">{suggestion.count} nearby</span>
                    </button>
                  ))}
                </div>
              ) : null}
              <div className="mt-2 flex items-center gap-3 text-sm">
                <button
                  type="button"
                  onClick={() => {
                    setIsAddingCategory((current) => !current);
                    setCategoryError(null);
                  }}
                  className="text-violet-400 transition-colors hover:text-violet-300"
                >
                  {isAddingCategory ? "Cancel new category" : "+ New category"}
                </button>
                {categoryError ? <span className="text-rose-400">{categoryError}</span> : null}
              </div>
              {isAddingCategory ? (
                <div className="mt-3 flex gap-2">
                  <input
                    type="text"
                    value={newCategoryName}
                    onChange={(event) => setNewCategoryName(event.target.value)}
                    placeholder="Category name"
                    className="flex-1 rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
                  />
                  <button
                    type="button"
                    disabled={isCreatingCategory}
                    onClick={async () => {
                      const trimmed = newCategoryName.trim();
                      if (!trimmed) {
                        setCategoryError("Enter a category name.");
                        return;
                      }

                      try {
                        const categoryId = await onCreateCategory(trimmed);
                        setSelectedCategoryId(categoryId);
                        setSelectedSubtypeId(null);
                        setNewCategoryName("");
                        setIsAddingCategory(false);
                      } catch {
                        setCategoryError("Could not create category.");
                      }
                    }}
                    className="rounded-lg border border-violet-500/40 bg-violet-500/10 px-4 py-2 text-zinc-100 transition-colors hover:bg-violet-500/20 disabled:opacity-50"
                  >
                    {isCreatingCategory ? "Creating..." : "Create"}
                  </button>
                </div>
              ) : null}
            </div>

            <div>
              <label className="mb-2 block text-sm text-zinc-400">Subtype</label>
              <select
                value={selectedSubtypeId ?? ""}
                onChange={(event) =>
                  setSelectedSubtypeId(event.target.value ? Number(event.target.value) : null)
                }
                className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
              >
                <option value="">No subtype</option>
                {(selectedCategory?.subtypes ?? []).map((subtype: CategoryOption["subtypes"][number]) => (
                  <option key={subtype.id} value={subtype.id}>
                    {subtype.name}
                  </option>
                ))}
              </select>
              {selectedCategory ? (
                <>
                  <div className="mt-2 flex items-center gap-3 text-sm">
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
                    <div className="mt-3 flex gap-2">
                      <input
                        type="text"
                        value={newSubtypeName}
                        onChange={(event) => setNewSubtypeName(event.target.value)}
                        placeholder={`Subtype for ${selectedCategory.name}`}
                        className="flex-1 rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
                      />
                      <button
                        type="button"
                        disabled={isCreatingSubtype}
                        onClick={async () => {
                          if (!selectedCategoryId) {
                            setSubtypeError("Pick a category first.");
                            return;
                          }

                          const trimmed = newSubtypeName.trim();
                          if (!trimmed) {
                            setSubtypeError("Enter a subtype name.");
                            return;
                          }

                          try {
                            const subtypeId = await onCreateSubtype(selectedCategoryId, trimmed);
                            setSelectedSubtypeId(subtypeId);
                            setNewSubtypeName("");
                            setIsAddingSubtype(false);
                          } catch {
                            setSubtypeError("Could not create subtype.");
                          }
                        }}
                        className="rounded-lg border border-violet-500/40 bg-violet-500/10 px-4 py-2 text-zinc-100 transition-colors hover:bg-violet-500/20 disabled:opacity-50"
                      >
                        {isCreatingSubtype ? "Creating..." : "Create"}
                      </button>
                    </div>
                  ) : null}
                </>
              ) : (
                <p className="mt-2 text-xs text-zinc-500">Pick a category before adding a subtype.</p>
              )}
            </div>

            <div className="md:col-span-2">
              <label className="mb-2 block text-sm text-zinc-400">Canonical Merchant Name</label>
              <input
                type="text"
                value={merchantName}
                onChange={(event) => setMerchantName(event.target.value)}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
              />
              {merchantSuggestions.length > 0 ? (
                <div className="mt-2 overflow-hidden rounded-lg border border-zinc-800 bg-zinc-950">
                  {merchantSuggestions.map((suggestion) => (
                    <button
                      key={suggestion.merchant}
                      type="button"
                      onClick={() => setMerchantName(suggestion.merchant)}
                      className="flex w-full items-center justify-between px-3 py-2 text-left text-sm text-zinc-300 transition-colors hover:bg-zinc-900 hover:text-zinc-100"
                    >
                      <span>{suggestion.merchant}</span>
                      <span className="text-xs text-zinc-500">
                        {suggestion.count} uses · {Math.round(suggestion.score * 100)}%
                      </span>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          </div>

          <div className="mt-5 grid gap-4 md:grid-cols-2">
            <div className="md:col-span-2 space-y-3">
              <label className="mb-2 block text-sm text-zinc-400">Split across household and friends</label>
              <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-4">
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => setSplitMode("percentage")}
                    className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                      splitMode === "percentage"
                        ? "bg-violet-500/20 text-zinc-100"
                        : "bg-zinc-900 text-zinc-400 hover:text-zinc-200"
                    }`}
                  >
                    Percentages
                  </button>
                  <button
                    type="button"
                    onClick={() => setSplitMode("exact")}
                    className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                      splitMode === "exact"
                        ? "bg-violet-500/20 text-zinc-100"
                        : "bg-zinc-900 text-zinc-400 hover:text-zinc-200"
                    }`}
                  >
                    Exact amounts
                  </button>
                </div>

                <div className="mt-3 grid gap-2 text-xs text-zinc-500 sm:grid-cols-2">
                  <div className="flex justify-between">
                    <span>Total transaction</span>
                    <span>{formatMoney(totalAmount)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Allocated so far</span>
                    <span>{formatMoney(signedAmount(allocatedTotal, totalAmount))}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Friends total</span>
                    <span>{formatMoney(signedAmount(friendShareTotal, totalAmount))}</span>
                  </div>
                  <div className={`flex justify-between ${allocationIsBalanced ? "text-emerald-300" : "text-amber-300"}`}>
                    <span>{allocationIsBalanced ? "Balanced" : "Remaining to allocate"}</span>
                    <span>{formatMoney(Math.abs(allocationDelta))}</span>
                  </div>
                </div>

                <div className="mt-4 space-y-3">
                  <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_160px]">
                    <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-sm text-zinc-200">
                      {MEMBER_A}
                    </div>
                    <input
                      type="text"
                      inputMode="decimal"
                      value={getAllocationInputValue(
                        "memberA",
                        splitMode === "percentage" ? memberAPercent : memberADisplayAmount,
                      )}
                      onFocus={() => setEditingAllocationField("memberA")}
                      onChange={(event) => handleAllocationInputChange("memberA", event.target.value)}
                      onBlur={() => handleAllocationInputBlur("memberA", updateMemberAAllocation)}
                      className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
                    />
                  </div>
                  <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_160px]">
                    <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-sm text-zinc-200">
                      {MEMBER_B}
                    </div>
                    <input
                      type="text"
                      inputMode="decimal"
                      value={getAllocationInputValue(
                        "memberB",
                        splitMode === "percentage" ? memberBPercent : memberBDisplayAmount,
                      )}
                      onFocus={() => setEditingAllocationField("memberB")}
                      onChange={(event) => handleAllocationInputChange("memberB", event.target.value)}
                      onBlur={() => handleAllocationInputBlur("memberB", updateMemberBAllocation)}
                      className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
                    />
                  </div>
                  {friendShares.map((share, index) => (
                    <div
                      key={`${share.friendId}-${index}`}
                      className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_160px_auto]"
                    >
                      <select
                        value={share.friendId}
                        onChange={(event) => {
                          const nextFriendId = Number(event.target.value);
                          setFriendShares((current) =>
                            current.map((item, itemIndex) =>
                              itemIndex === index ? { ...item, friendId: nextFriendId } : item,
                            ),
                          );
                        }}
                        className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
                      >
                        {friends.map((friend) => (
                          <option key={friend.id} value={friend.id}>
                            {friend.name}
                          </option>
                        ))}
                      </select>
                      <input
                        type="text"
                        inputMode="decimal"
                        value={
                          getAllocationInputValue(
                            `friend-${index}`,
                            splitMode === "percentage"
                              ? roundMoney(absoluteTotalAmount > 0 ? (share.amount / absoluteTotalAmount) * 100 : 0)
                              : share.amount,
                          )
                        }
                        onFocus={() => setEditingAllocationField(`friend-${index}`)}
                        onChange={(event) => handleAllocationInputChange(`friend-${index}`, event.target.value)}
                        onBlur={() =>
                          handleAllocationInputBlur(`friend-${index}`, (parsedValue) =>
                            updateFriendAllocation(index, parsedValue),
                          )
                        }
                        className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
                      />
                      <button
                        type="button"
                        onClick={() =>
                          setFriendShares((current) => current.filter((_, itemIndex) => itemIndex !== index))
                        }
                        className="rounded-lg border border-zinc-700 px-3 py-2 text-sm text-zinc-300 transition-colors hover:border-rose-500 hover:text-rose-200"
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                </div>

                <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
                  <button
                    type="button"
                    disabled={friends.length === 0}
                    onClick={() =>
                      setFriendShares((current) => [...current, { friendId: friends[0]?.id ?? 0, amount: 0 }])
                    }
                    className="text-violet-400 transition-colors hover:text-violet-300 disabled:opacity-50"
                  >
                    + Add friend share
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setIsAddingFriend((current) => !current);
                      setFriendError(null);
                    }}
                    className="text-violet-400 transition-colors hover:text-violet-300"
                  >
                    {isAddingFriend ? "Cancel new friend" : "+ Add friend"}
                  </button>
                  {friendError ? <span className="text-rose-400">{friendError}</span> : null}
                </div>

                {isAddingFriend ? (
                  <div className="mt-3 flex gap-2">
                    <input
                      type="text"
                      value={newFriendName}
                      onChange={(event) => setNewFriendName(event.target.value)}
                      placeholder="Friend name"
                      className="flex-1 rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
                    />
                    <button
                      type="button"
                      disabled={isCreatingFriend}
                      onClick={async () => {
                        const trimmed = newFriendName.trim();
                        if (!trimmed) {
                          setFriendError("Enter a friend name.");
                          return;
                        }

                        try {
                          const friendId = await onCreateFriend(trimmed);
                          setFriendShares((current) => [...current, { friendId, amount: 0 }]);
                          setNewFriendName("");
                          setIsAddingFriend(false);
                        } catch {
                          setFriendError("Could not create friend.");
                        }
                      }}
                      className="rounded-lg border border-violet-500/40 bg-violet-500/10 px-4 py-2 text-zinc-100 transition-colors hover:bg-violet-500/20 disabled:opacity-50"
                    >
                      {isCreatingFriend ? "Creating..." : "Create"}
                    </button>
                  </div>
                ) : null}

                <div className="mt-4 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-3 text-xs text-zinc-400">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <span>{trackedFriendsLabel ? `Tracking with: ${trackedFriendsLabel}` : "No friend shares set."}</span>
                    <span>Household remainder {formatMoney(householdAmount)}</span>
                  </div>
                  <div className="mt-2">
                    {splitMode === "percentage"
                      ? "Percentage mode applies to the full transaction, including friends."
                      : "Exact mode lets you assign dollar amounts directly."}
                  </div>
                </div>
              </div>
            </div>

            <div className="md:col-span-2">
              <label className="mb-2 block text-sm text-zinc-400">Tag</label>
              <input
                type="text"
                value={tag}
                onChange={(event) => setTag(event.target.value)}
                onFocus={() => setIsTagFocused(true)}
                onBlur={() => setIsTagFocused(false)}
                placeholder="vacation, project, conference..."
                className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
              />
              {(tagSuggestions.length > 0 || debouncedTagQuery.length > 0) ? (
                <div className="mt-2 flex flex-wrap gap-2">
                  {debouncedTagQuery.length > 0 && !hasExactTagMatch ? (
                    <button
                      type="button"
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => setTag(debouncedTagQuery)}
                      className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-3 py-1 text-xs text-emerald-200 transition-colors hover:bg-emerald-500/20"
                    >
                      + Add tag <span className="ml-1 font-medium">{debouncedTagQuery}</span>
                    </button>
                  ) : null}
                  {tagSuggestions.map((suggestion) => (
                    <button
                      key={suggestion.tag}
                      type="button"
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => setTag(suggestion.tag)}
                      className="rounded-full border border-zinc-700 bg-zinc-900 px-3 py-1 text-xs text-zinc-300 transition-colors hover:border-violet-500 hover:text-zinc-100"
                    >
                      {suggestion.tag}
                      <span className="ml-2 text-zinc-500">{suggestion.count} nearby</span>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>

            <div className="flex items-center gap-6 rounded-xl border border-zinc-800 bg-zinc-950/60 px-4 py-3">
              <label className="flex items-center gap-2 text-sm text-zinc-300">
                <input
                  type="checkbox"
                  checked={logToWork}
                  onChange={(event) => setLogToWork(event.target.checked)}
                  className="h-4 w-4 rounded border-zinc-700 bg-zinc-800 text-violet-600"
                />
                Log to work
              </label>
              <label className="flex items-center gap-2 text-sm text-zinc-300">
                <input
                  type="checkbox"
                  checked={rememberMapping}
                  onChange={(event) => setRememberMapping(event.target.checked)}
                  className="h-4 w-4 rounded border-zinc-700 bg-zinc-800 text-violet-600"
                />
                Remember mapping for {rememberLabelSource ?? "this merchant"}
              </label>
            </div>
          </div>

          <div className="mt-5 flex flex-wrap items-center gap-3">
            {!allocationIsBalanced ? (
              <p className="text-sm text-amber-300">
                Allocate the remaining {formatMoney(Math.abs(allocationDelta))} before saving.
              </p>
            ) : null}
            <button
              type="button"
              disabled={!selectedCategoryId || isSaving || !allocationIsBalanced}
              onClick={() => {
                if (!selectedCategoryId) {
                  return;
                }

                onApprove({
                  categoryId: selectedCategoryId,
                  subtypeId: selectedSubtypeId,
                  rememberMapping,
                  merchant: merchantName.trim() || null,
                  split: derivedSplit,
                  tag: tag.trim() || null,
                  logToWork,
                  trackWithFriends: trackedFriendsLabel,
                  friendShares,
                });
              }}
              className="rounded-lg bg-violet-600 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-violet-700 disabled:opacity-50"
            >
              {isSaving ? "Saving..." : compact ? "Update approved transaction" : "Approve transaction"}
            </button>
            <button
              type="button"
              onClick={onCollapse}
              className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 transition-colors hover:border-zinc-500 hover:text-zinc-100"
            >
              Collapse
            </button>
            <button
              type="button"
              onClick={() => {
                const confirmed = window.confirm(
                  `Delete staged expense #${expense.id}? This cannot be undone.`,
                );
                if (confirmed) {
                  onDelete();
                }
              }}
              className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-2 text-sm text-rose-200 transition-colors hover:bg-rose-500/20"
            >
              Delete expense
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function StatRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "amber" | "emerald" | "violet" | "zinc";
}) {
  const toneClassName =
    tone === "amber"
      ? "text-amber-300"
      : tone === "emerald"
        ? "text-emerald-300"
        : tone === "violet"
          ? "text-violet-300"
          : "text-zinc-100";

  return (
    <div className="flex items-center justify-between">
      <span className="text-zinc-400">{label}</span>
      <span className={`font-medium ${toneClassName}`}>{value}</span>
    </div>
  );
}
