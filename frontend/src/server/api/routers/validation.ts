import { z } from "zod";
import { createTRPCRouter, publicProcedure } from "~/server/api/trpc";
import { validationQueue, expenses, merchantMappings, categories, subtypes, friends, expenseFriendShares } from "~/server/db/schema";
import { eq, and, gte, count, isNotNull, lte, ne, desc, inArray } from "drizzle-orm";
import {
  calculateSpendAllocation,
  normalizeFriendShares,
  summarizeTrackedFriends,
} from "~/lib/expense-sharing";

function normalizeMerchantName(value: string) {
  return value.trim().toLowerCase();
}

function bigrams(value: string) {
  const normalized = normalizeMerchantName(value).replace(/[^a-z0-9]+/g, " ");
  const compact = normalized.replace(/\s+/g, " ").trim();
  if (compact.length < 2) {
    return new Set(compact ? [compact] : []);
  }

  const grams = new Set<string>();
  for (let i = 0; i < compact.length - 1; i++) {
    grams.add(compact.slice(i, i + 2));
  }
  return grams;
}

function similarityScore(left: string, right: string) {
  const normalizedLeft = normalizeMerchantName(left);
  const normalizedRight = normalizeMerchantName(right);
  if (!normalizedLeft || !normalizedRight) {
    return 0;
  }

  if (normalizedLeft === normalizedRight) {
    return 1;
  }

  if (normalizedLeft.includes(normalizedRight) || normalizedRight.includes(normalizedLeft)) {
    return 0.92;
  }

  const leftBigrams = bigrams(normalizedLeft);
  const rightBigrams = bigrams(normalizedRight);
  if (leftBigrams.size === 0 || rightBigrams.size === 0) {
    return 0;
  }

  let overlap = 0;
  for (const gram of leftBigrams) {
    if (rightBigrams.has(gram)) {
      overlap += 1;
    }
  }

  return (2 * overlap) / (leftBigrams.size + rightBigrams.size);
}

function formatDateOnly(value: Date) {
  return value.toISOString().slice(0, 10);
}

function addDays(value: Date, days: number) {
  const next = new Date(value);
  next.setDate(next.getDate() + days);
  return next;
}

async function listExpenseFriendShares(ctx: any, expenseIds: number[]) {
  if (expenseIds.length === 0) {
    return new Map<number, Array<{ friendId: number; friendName: string; amount: number }>>();
  }

  const rows = await ctx.db
    .select({
      expenseId: expenseFriendShares.expenseId,
      friendId: expenseFriendShares.friendId,
      friendName: friends.name,
      amount: expenseFriendShares.amount,
    })
    .from(expenseFriendShares)
    .innerJoin(friends, eq(expenseFriendShares.friendId, friends.id))
    .where(inArray(expenseFriendShares.expenseId, expenseIds));

  const grouped = new Map<number, Array<{ friendId: number; friendName: string; amount: number }>>();
  for (const row of rows) {
    const current = grouped.get(row.expenseId) ?? [];
    current.push({
      friendId: row.friendId,
      friendName: row.friendName,
      amount: row.amount,
    });
    grouped.set(row.expenseId, current);
  }

  return grouped;
}

export const validationRouter = createTRPCRouter({
  getPending: publicProcedure
    .input(z.object({ limit: z.number().default(20) }))
    .query(async ({ ctx, input }) => {
      const unapprovedExpenses = await ctx.db.query.expenses.findMany({
        where: and(eq(expenses.isApproved, false), eq(expenses.isDuplicate, false)),
        limit: input.limit,
        orderBy: [desc(expenses.date), desc(expenses.id)],
        with: {
          categoryRel: true,
          subtypeRel: true,
          validationItem: true,
        },
      });
      const shareMap = await listExpenseFriendShares(
        ctx,
        unapprovedExpenses.map((expense) => expense.id),
      );

      return unapprovedExpenses.map((expense) => ({
        id: (expense.validationItem as { id: number; reason: string | null } | null | undefined)?.id ?? expense.id,
        expenseId: expense.id,
        reason: expense.needsValidation
          ? ((expense.validationItem as { id: number; reason: string | null } | null | undefined)?.reason ??
            "needs_review")
          : "staged_for_approval",
        expense: {
          ...expense,
          friendShares: shareMap.get(expense.id) ?? [],
        },
        suggestedCategory: expense.categoryRel,
        suggestedSubtype: expense.subtypeRel,
      }));
    }),

  getRecentApproved: publicProcedure
    .input(z.object({ limit: z.number().min(1).max(30).default(10) }))
    .query(async ({ ctx, input }) => {
      const recentApprovedIds = ctx.db.$client
        .prepare(
          `
            select id, approved_at
            from expenses
            where is_approved = 1
              and is_duplicate = 0
              and approved_at is not null
            order by
              case
                when typeof(approved_at) = 'integer' then approved_at
                else unixepoch(approved_at)
              end desc,
              id desc
            limit ?
          `,
        )
        .all(input.limit) as Array<{ id: number; approved_at: string | number | null }>;

      if (recentApprovedIds.length === 0) {
        return [];
      }

      const orderIndex = new Map(
        recentApprovedIds.map((row, index) => [row.id, index]),
      );
      const approvedAtById = new Map(
        recentApprovedIds.map((row) => [
          row.id,
          typeof row.approved_at === "number"
            ? new Date(row.approved_at * 1000).toISOString()
            : row.approved_at,
        ]),
      );

      const approvedExpenses = await ctx.db.query.expenses.findMany({
        where: and(
          eq(expenses.isApproved, true),
          eq(expenses.isDuplicate, false),
          isNotNull(expenses.approvedAt),
        ),
        with: {
          categoryRel: true,
          subtypeRel: true,
        },
      });
      const shareMap = await listExpenseFriendShares(
        ctx,
        approvedExpenses.map((expense) => expense.id),
      );

      return approvedExpenses
        .filter((expense) => orderIndex.has(expense.id))
        .map((expense) => ({
          ...expense,
          approvedAt: approvedAtById.get(expense.id) ?? expense.approvedAt,
          friendShares: shareMap.get(expense.id) ?? [],
        }))
        .sort(
          (left, right) =>
            (orderIndex.get(left.id) ?? Number.MAX_SAFE_INTEGER) -
            (orderIndex.get(right.id) ?? Number.MAX_SAFE_INTEGER),
        );
    }),

  getStats: publicProcedure.query(async ({ ctx }) => {
    const pendingResult = await ctx.db
      .select({ count: count() })
      .from(expenses)
      .where(and(eq(expenses.isApproved, false), eq(expenses.isDuplicate, false)));

    const today = new Date();
    today.setHours(0, 0, 0, 0);

    const validatedTodayResult = await ctx.db
      .select({ count: count() })
      .from(expenses)
      .where(
        and(
          eq(expenses.isApproved, true),
          eq(expenses.isDuplicate, false),
          isNotNull(expenses.approvedAt),
          gte(expenses.approvedAt, today),
        ),
      );

    const totalValidatedResult = await ctx.db
      .select({ count: count() })
      .from(expenses)
      .where(and(eq(expenses.isApproved, true), eq(expenses.isDuplicate, false)));

    const merchantsLearnedResult = await ctx.db
      .select({ count: count() })
      .from(merchantMappings);

    return {
      pending: pendingResult[0]?.count ?? 0,
      validatedToday: validatedTodayResult[0]?.count ?? 0,
      totalValidated: totalValidatedResult[0]?.count ?? 0,
      merchantsLearned: merchantsLearnedResult[0]?.count ?? 0,
    };
  }),

  getMerchantSuggestions: publicProcedure
    .input(
      z.object({
        query: z.string().trim().min(1),
        limit: z.number().min(1).max(10).default(5),
      }),
    )
    .query(async ({ ctx, input }) => {
      const merchantRows = await ctx.db
        .select({ merchant: expenses.merchant })
        .from(expenses)
        .where(and(isNotNull(expenses.merchant), eq(expenses.isApproved, true)));

      const grouped = new Map<string, number>();
      for (const row of merchantRows) {
        const merchant = row.merchant?.trim();
        if (!merchant) {
          continue;
        }

        grouped.set(merchant, (grouped.get(merchant) ?? 0) + 1);
      }

      return Array.from(grouped.entries())
        .map(([merchant, count]) => ({
          merchant,
          count,
          score: similarityScore(input.query, merchant),
        }))
        .filter((item) => item.score >= 0.3)
        .sort(
          (left, right) =>
            right.score - left.score ||
            right.count - left.count ||
            left.merchant.localeCompare(right.merchant),
        )
        .slice(0, input.limit);
    }),

  getTagSuggestions: publicProcedure
    .input(
      z.object({
        expenseId: z.number(),
        query: z.string().trim().default(""),
        limit: z.number().min(1).max(10).default(5),
        windowDays: z.number().min(1).max(90).default(30),
      }),
    )
    .query(async ({ ctx, input }) => {
      const expense = await ctx.db.query.expenses.findFirst({
        where: eq(expenses.id, input.expenseId),
      });

      if (!expense) {
        throw new Error(`Expense ${input.expenseId} not found`);
      }

      const expenseDate = new Date(expense.date);
      if (Number.isNaN(expenseDate.getTime())) {
        return [];
      }

      const startDate = formatDateOnly(addDays(expenseDate, -input.windowDays));
      const endDate = formatDateOnly(addDays(expenseDate, input.windowDays));
      const normalizedQuery = input.query.trim();

      const tagRows = await ctx.db
        .select({
          tag: expenses.tag,
          date: expenses.date,
        })
        .from(expenses)
        .where(
          and(
            isNotNull(expenses.tag),
            eq(expenses.isApproved, true),
            gte(expenses.date, startDate),
            lte(expenses.date, endDate),
            ne(expenses.id, input.expenseId),
          ),
        );

      const historicalTagRows = normalizedQuery
        ? await ctx.db
            .select({
              tag: expenses.tag,
              date: expenses.date,
            })
            .from(expenses)
            .where(
              and(
                isNotNull(expenses.tag),
                eq(expenses.isApproved, true),
                ne(expenses.id, input.expenseId),
              ),
            )
        : [];

      const grouped = new Map<
        string,
        { count: number; latestDate: string; similarity: number; nearbyCount: number }
      >();

      for (const row of tagRows) {
        const tag = row.tag?.trim();
        if (!tag) {
          continue;
        }

        const existing = grouped.get(tag);
        const similarity = normalizedQuery ? similarityScore(normalizedQuery, tag) : 0;

        if (!existing) {
          grouped.set(tag, {
            count: 1,
            latestDate: row.date,
            similarity,
            nearbyCount: 1,
          });
          continue;
        }

        existing.count += 1;
        existing.nearbyCount += 1;
        existing.latestDate = row.date > existing.latestDate ? row.date : existing.latestDate;
        existing.similarity = Math.max(existing.similarity, similarity);
      }

      for (const row of historicalTagRows) {
        const tag = row.tag?.trim();
        if (!tag) {
          continue;
        }

        const similarity = similarityScore(normalizedQuery, tag);
        if (similarity < 0.3) {
          continue;
        }

        const existing = grouped.get(tag);
        if (!existing) {
          grouped.set(tag, {
            count: 1,
            latestDate: row.date,
            similarity,
            nearbyCount: 0,
          });
          continue;
        }

        existing.count += 1;
        existing.latestDate = row.date > existing.latestDate ? row.date : existing.latestDate;
        existing.similarity = Math.max(existing.similarity, similarity);
      }

      return Array.from(grouped.entries())
        .map(([tag, stats]) => ({
          tag,
          count: stats.count,
          latestDate: stats.latestDate,
          score: normalizedQuery
            ? stats.similarity +
              Math.min(stats.count, 5) * 0.02 +
              Math.min(stats.nearbyCount, 3) * 0.06
            : stats.count,
          nearbyCount: stats.nearbyCount,
        }))
        .filter((item) => !normalizedQuery || item.score >= 0.3)
        .sort((left, right) => {
          if (right.score !== left.score) {
            return right.score - left.score;
          }

          if (right.nearbyCount !== left.nearbyCount) {
            return right.nearbyCount - left.nearbyCount;
          }

          if (right.count !== left.count) {
            return right.count - left.count;
          }

          return right.latestDate.localeCompare(left.latestDate);
        })
        .slice(0, input.limit);
    }),

  getCategorySuggestions: publicProcedure
    .input(
      z.object({
        expenseId: z.number(),
        limit: z.number().min(1).max(10).default(5),
        windowDays: z.number().min(1).max(90).default(30),
      }),
    )
    .query(async ({ ctx, input }) => {
      const expense = await ctx.db.query.expenses.findFirst({
        where: eq(expenses.id, input.expenseId),
      });

      if (!expense) {
        throw new Error(`Expense ${input.expenseId} not found`);
      }

      const expenseDate = new Date(expense.date);
      if (Number.isNaN(expenseDate.getTime())) {
        return [];
      }

      const startDate = formatDateOnly(addDays(expenseDate, -input.windowDays));
      const endDate = formatDateOnly(addDays(expenseDate, input.windowDays));

      const categoryRows = await ctx.db
        .select({
          categoryId: expenses.categoryId,
          categoryName: categories.name,
          date: expenses.date,
        })
        .from(expenses)
        .innerJoin(categories, eq(expenses.categoryId, categories.id))
        .where(
          and(
            isNotNull(expenses.categoryId),
            eq(expenses.isApproved, true),
            gte(expenses.date, startDate),
            lte(expenses.date, endDate),
            ne(expenses.id, input.expenseId),
          ),
        );

      const grouped = new Map<
        number,
        { categoryId: number; categoryName: string; count: number; latestDate: string }
      >();

      for (const row of categoryRows) {
        if (!row.categoryId) {
          continue;
        }

        const existing = grouped.get(row.categoryId);
        if (!existing) {
          grouped.set(row.categoryId, {
            categoryId: row.categoryId,
            categoryName: row.categoryName,
            count: 1,
            latestDate: row.date,
          });
          continue;
        }

        existing.count += 1;
        existing.latestDate = row.date > existing.latestDate ? row.date : existing.latestDate;
      }

      return Array.from(grouped.values())
        .sort((left, right) => {
          if (right.count !== left.count) {
            return right.count - left.count;
          }

          return right.latestDate.localeCompare(left.latestDate);
        })
        .slice(0, input.limit);
    }),

  validate: publicProcedure
    .input(
      z.object({
        expenseId: z.number(),
        categoryId: z.number(),
        subtypeId: z.number().nullable(),
        rememberMapping: z.boolean().default(true),
        merchant: z.string().trim().max(500).nullable(),
        split: z.number().min(0).max(1).default(1),
        tag: z.string().trim().max(255).nullable(),
        logToWork: z.boolean().nullable(),
        trackWithFriends: z.string().trim().max(255).nullable(),
        friendShares: z
          .array(
            z.object({
              friendId: z.number(),
              amount: z.number().nonnegative(),
            }),
          )
          .default([]),
      }),
    )
    .mutation(async ({ ctx, input }) => {
      const {
        expenseId,
        categoryId,
        subtypeId,
        rememberMapping,
        merchant,
        split,
        tag,
        logToWork,
        trackWithFriends,
        friendShares,
      } = input;

      const existingExpense = await ctx.db.query.expenses.findFirst({
        where: eq(expenses.id, expenseId),
      });

      if (!existingExpense) {
        throw new Error(`Expense ${expenseId} not found`);
      }

      const categoryRecord = await ctx.db.query.categories.findFirst({
        where: eq(categories.id, categoryId),
      });

      if (!categoryRecord) {
        throw new Error(`Category ${categoryId} not found`);
      }

      let resolvedSubtypeId = subtypeId;
      let resolvedSubtypeName: string | null = null;

      if (subtypeId) {
        const subtypeRecord = await ctx.db.query.subtypes.findFirst({
          where: eq(subtypes.id, subtypeId),
        });

        if (!subtypeRecord) {
          throw new Error(`Subtype ${subtypeId} not found`);
        }

        resolvedSubtypeId = subtypeRecord.id;
        resolvedSubtypeName = subtypeRecord.name;
      }

      const normalizedFriendShares = normalizeFriendShares(friendShares);
      const relatedFriends = normalizedFriendShares.length
        ? await ctx.db
            .select({ id: friends.id, name: friends.name })
            .from(friends)
            .where(
              inArray(
                friends.id,
                normalizedFriendShares.map((share) => share.friendId),
              ),
            )
        : [];
      const friendsById = new Map(
        relatedFriends.map((friend) => [friend.id, { name: friend.name }]),
      );
      const allocation = calculateSpendAllocation(
        Number(existingExpense.amount),
        split,
        existingExpense.spender,
        normalizedFriendShares,
      );
      const derivedTrackWithFriends =
        summarizeTrackedFriends(normalizedFriendShares, friendsById) ??
        (trackWithFriends || null);

      const updatedExpenses = await ctx.db
        .update(expenses)
        .set({
          type: categoryRecord.name,
          subtype: resolvedSubtypeName,
          categoryId,
          subtypeId: resolvedSubtypeId,
          merchant: merchant || null,
          split,
          tag: tag || null,
          logToWork,
          trackWithFriends: derivedTrackWithFriends,
          memberASpend: allocation.memberASpend,
          memberBSpend: allocation.memberBSpend,
          needsValidation: false,
          isApproved: true,
          approvedAt: new Date(),
        })
        .where(eq(expenses.id, expenseId))
        .returning();

      await ctx.db
        .delete(expenseFriendShares)
        .where(eq(expenseFriendShares.expenseId, expenseId));
      if (normalizedFriendShares.length > 0) {
        await ctx.db.insert(expenseFriendShares).values(
          normalizedFriendShares.map((share) => ({
            expenseId,
            friendId: share.friendId,
            amount: share.amount,
          })),
        );
      }

      const expense = updatedExpenses[0];

      await ctx.db
        .update(validationQueue)
        .set({
          status: "validated",
          validatedAt: new Date(),
        })
        .where(eq(validationQueue.expenseId, expenseId));

      const mappingSource = existingExpense.originalInputName ?? existingExpense.merchant;
      // Expenses made from receipts are named after the file; no statement row would match that.
      const fromReceipt = existingExpense.originalInputName?.startsWith("Receipt: ") ?? false;
      if (rememberMapping && mappingSource && !fromReceipt) {
        const existing = await ctx.db.query.merchantMappings.findFirst({
          where: eq(merchantMappings.merchantPattern, mappingSource.toLowerCase()),
        });

        if (!existing) {
          await ctx.db.insert(merchantMappings).values({
            merchantPattern: mappingSource.toLowerCase(),
            categoryId,
            subtypeId,
            canonicalMerchant: merchant || null,
            requiresValidation: false,
          });
        } else {
          await ctx.db
            .update(merchantMappings)
            .set({
              categoryId,
              subtypeId,
              canonicalMerchant: merchant || null,
              requiresValidation: false,
            })
            .where(eq(merchantMappings.id, existing.id));
        }
      }

      return expense;
    }),

  skip: publicProcedure
    .input(z.object({ expenseId: z.number() }))
    .mutation(async ({ ctx, input }) => {
      await ctx.db
        .update(validationQueue)
        .set({ status: "skipped" })
        .where(eq(validationQueue.expenseId, input.expenseId));

      return { success: true };
    }),
});
