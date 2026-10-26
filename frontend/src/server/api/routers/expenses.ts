import { MEMBER_A, MEMBER_B, MEMBER_ENUM } from "~/lib/household";
import { z } from "zod";
import { createTRPCRouter, publicProcedure } from "~/server/api/trpc";
import type { DB } from "~/server/db";
import {
  expenses,
  payments,
  categories,
  subtypes,
  validationQueue,
  sourceFiles,
  friends,
  expenseFriendShares,
} from "~/server/db/schema";
import { eq, and, gte, lte, like, or, desc, sql, count, sum, inArray, isNotNull } from "drizzle-orm";
import {
  calculateSpendAllocation,
  normalizeFriendShares,
  summarizeTrackedFriends,
} from "~/lib/expense-sharing";

const dateStringSchema = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);

/**
 * Delete an expense and the rows that reference it (foreign keys are enforced).
 * A receipt whose own expense is deleted is remembered as dismissed, so the
 * scans `receipts-to-expenses` step does not make it again.
 */
async function removeExpense(db: DB, id: number) {
  db.run(sql`INSERT OR IGNORE INTO dismissed_receipts (document_id, reason)
    SELECT document_id, 'its expense was deleted' FROM expense_documents
    WHERE expense_id = ${id} AND method = 'receipt'`);
  db.run(sql`DELETE FROM expense_documents WHERE expense_id = ${id}`);
  await db.delete(expenseFriendShares).where(eq(expenseFriendShares.expenseId, id));
  await db.delete(validationQueue).where(eq(validationQueue.expenseId, id));
  await db.delete(expenses).where(eq(expenses.id, id));
}

function formatDateOnly(value: Date) {
  return value.toISOString().slice(0, 10);
}

function startOfWeek(value: Date) {
  const next = new Date(value);
  const day = next.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  next.setDate(next.getDate() + diff);
  next.setHours(0, 0, 0, 0);
  return next;
}

function startOfMonth(value: Date) {
  const next = new Date(value);
  next.setDate(1);
  next.setHours(0, 0, 0, 0);
  return next;
}

function buildExpenseConditions(input: {
  startDate?: string;
  endDate?: string;
  includeDuplicates?: boolean;
  includeNeedsValidation?: boolean;
  includeUnapproved?: boolean;
}) {
  const conditions: any[] = [];

  if (!input.includeDuplicates) {
    conditions.push(eq(expenses.isDuplicate, false));
  }

  if (!input.includeNeedsValidation) {
    conditions.push(eq(expenses.needsValidation, false));
  }

  if (!input.includeUnapproved) {
    conditions.push(eq(expenses.isApproved, true));
  }

  if (input.startDate) {
    conditions.push(gte(expenses.date, input.startDate));
  }

  if (input.endDate) {
    conditions.push(lte(expenses.date, input.endDate));
  }

  return conditions;
}

function normalizeCategory(type: string | null) {
  return type?.trim() || "Uncategorized";
}

function normalizeSubtype(subtype: string | null) {
  return subtype?.trim() || "Other";
}

function toBucket(dateString: string, granularity: "day" | "week" | "month") {
  const date = new Date(`${dateString}T00:00:00`);
  if (Number.isNaN(date.getTime())) {
    return dateString;
  }

  if (granularity === "month") {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
  }

  if (granularity === "week") {
    return formatDateOnly(startOfWeek(date));
  }

  return dateString;
}

function toLabel(bucket: string, granularity: "day" | "week" | "month") {
  if (granularity === "month") {
    const [year, month] = bucket.split("-");
    return new Date(Number(year), Number(month) - 1, 1).toLocaleDateString("en-US", {
      month: "short",
      year: "2-digit",
    });
  }

  if (granularity === "week") {
    return new Date(`${bucket}T00:00:00`).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
    });
  }

  return new Date(`${bucket}T00:00:00`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

function nextBucketDate(value: Date, granularity: "day" | "week" | "month") {
  const next = new Date(value);

  if (granularity === "month") {
    next.setMonth(next.getMonth() + 1);
    return startOfMonth(next);
  }

  if (granularity === "week") {
    next.setDate(next.getDate() + 7);
    return startOfWeek(next);
  }

  next.setDate(next.getDate() + 1);
  next.setHours(0, 0, 0, 0);
  return next;
}

function normalizeBucketStart(value: Date, granularity: "day" | "week" | "month") {
  if (granularity === "month") {
    return startOfMonth(value);
  }

  if (granularity === "week") {
    return startOfWeek(value);
  }

  const next = new Date(value);
  next.setHours(0, 0, 0, 0);
  return next;
}

function ensurePaymentsTable(ctx: { db: { $client: { exec: (sql: string) => unknown } } }) {
  ctx.db.$client.exec(`
    CREATE TABLE IF NOT EXISTS payments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      date TEXT NOT NULL,
      from_person TEXT NOT NULL,
      to_person TEXT NOT NULL,
      amount REAL NOT NULL,
      note TEXT,
      created_at INTEGER DEFAULT (unixepoch())
    )
  `);
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

type InsightGranularity = "day" | "week" | "month";

type NormalizedInsightRow = {
  id: number;
  date: string;
  type: string | null;
  subtype: string | null;
  merchant: string | null;
  amount: number | null;
  memberASpend: number | null;
  memberBSpend: number | null;
  spender: string | null;
  card: string | null;
  tag: string | null;
  logToWork: boolean | null;
};

async function loadNormalizedInsightRows(
  ctx: any,
  input: {
    startDate?: string;
    endDate?: string;
    extraConditions?: any[];
  },
) {
  const baseConditions = buildExpenseConditions({
    startDate: input.startDate,
    endDate: input.endDate,
    includeUnapproved: false,
  });

  const rows = await ctx.db.query.expenses.findMany({
    where: and(...baseConditions, ...(input.extraConditions ?? [])),
    orderBy: [desc(expenses.date)],
    columns: {
      id: true,
      date: true,
      type: true,
      subtype: true,
      merchant: true,
      amount: true,
      memberASpend: true,
      memberBSpend: true,
      spender: true,
      card: true,
      tag: true,
      logToWork: true,
    },
    with: {
      categoryRel: {
        columns: {
          name: true,
        },
      },
      subtypeRel: {
        columns: {
          name: true,
        },
      },
    },
  });

  const normalizedRows: NormalizedInsightRow[] = rows.map((row: any) => ({
    id: row.id,
    date: row.date,
    type: row.categoryRel?.name ?? row.type,
    subtype: row.subtypeRel?.name ?? row.subtype,
    merchant: row.merchant,
    amount: row.amount,
    memberASpend: row.memberASpend,
    memberBSpend: row.memberBSpend,
    spender: row.spender,
    card: row.card,
    tag: row.tag,
    logToWork: row.logToWork,
  }));

  return normalizedRows;
}

function buildTrendFromRows(rows: NormalizedInsightRow[], granularity: InsightGranularity) {
  const trendMap = new Map<
    string,
    { bucket: string; label: string; total: number; memberA: number; memberB: number }
  >();

  rows
    .slice()
    .sort((left, right) => left.date.localeCompare(right.date))
    .forEach((row) => {
      const bucket = toBucket(row.date, granularity);
      const current = trendMap.get(bucket) ?? {
        bucket,
        label: toLabel(bucket, granularity),
        total: 0,
        memberA: 0,
        memberB: 0,
      };

      current.total += row.amount ?? 0;
      current.memberA += row.memberASpend ?? 0;
      current.memberB += row.memberBSpend ?? 0;
      trendMap.set(bucket, current);
    });

  const boundaryStart =
    rows.length > 0
      ? rows.slice().sort((left, right) => left.date.localeCompare(right.date))[0]?.date
      : undefined;
  const boundaryEnd =
    rows.length > 0
      ? rows.slice().sort((left, right) => right.date.localeCompare(left.date))[0]?.date
      : undefined;

  if (boundaryStart && boundaryEnd) {
    let cursor = normalizeBucketStart(new Date(`${boundaryStart}T00:00:00`), granularity);
    const endCursor = normalizeBucketStart(new Date(`${boundaryEnd}T00:00:00`), granularity);

    while (cursor.getTime() <= endCursor.getTime()) {
      const bucket = toBucket(formatDateOnly(cursor), granularity);
      if (!trendMap.has(bucket)) {
        trendMap.set(bucket, {
          bucket,
          label: toLabel(bucket, granularity),
          total: 0,
          memberA: 0,
          memberB: 0,
        });
      }

      cursor = nextBucketDate(cursor, granularity);
    }
  }

  return Array.from(trendMap.values()).sort((left, right) =>
    left.bucket.localeCompare(right.bucket),
  );
}

export const expensesRouter = createTRPCRouter({
  getStats: publicProcedure.query(async ({ ctx }) => {
    const totalResult = await ctx.db
      .select({
        total: sum(expenses.amount),
        count: count(),
      })
      .from(expenses)
      .where(and(eq(expenses.isDuplicate, false), eq(expenses.isApproved, true)));

    const memberAResult = await ctx.db
      .select({ total: sum(expenses.memberASpend) })
      .from(expenses)
      .where(and(eq(expenses.isDuplicate, false), eq(expenses.isApproved, true)));

    const memberBResult = await ctx.db
      .select({ total: sum(expenses.memberBSpend) })
      .from(expenses)
      .where(and(eq(expenses.isDuplicate, false), eq(expenses.isApproved, true)));

    return {
      totalAmount: Number(totalResult[0]?.total ?? 0),
      totalCount: totalResult[0]?.count ?? 0,
      memberASpend: Number(memberAResult[0]?.total ?? 0),
      memberBSpend: Number(memberBResult[0]?.total ?? 0),
    };
  }),

  getAll: publicProcedure
    .input(
      z.object({
        limit: z.number().min(1).max(100).default(50),
        offset: z.number().default(0),
        category: z.string().optional(),
        spender: z.string().optional(),
        card: z.string().optional(),
        startDate: dateStringSchema.optional(),
        endDate: dateStringSchema.optional(),
        search: z.string().optional(),
        includeDuplicates: z.boolean().default(true),
        includeUnapproved: z.boolean().default(true),
        sortBy: z.enum(["expense_date_desc", "uploaded_desc"]).default("expense_date_desc"),
      })
    )
    .query(async ({ ctx, input }) => {
      const {
        limit,
        offset,
        category,
        spender,
        card,
        startDate,
        endDate,
        search,
        includeDuplicates,
        includeUnapproved,
        sortBy,
      } = input;

      const conditions = buildExpenseConditions({
        startDate,
        endDate,
        includeNeedsValidation: true,
        includeDuplicates,
        includeUnapproved,
      });
      if (category) conditions.push(eq(expenses.type, category));
      if (spender) conditions.push(eq(expenses.spender, spender));
      if (card) conditions.push(eq(expenses.card, card));
      if (search) {
        conditions.push(
          or(
            like(expenses.merchant, `%${search}%`),
            like(expenses.type, `%${search}%`),
          ),
        );
      }

      const uploadSortAt = sql<number>`COALESCE(${sourceFiles.processedAt}, ${expenses.createdAt})`;
      const items = await ctx.db
        .select({
          id: expenses.id,
          date: expenses.date,
          type: expenses.type,
          subtype: expenses.subtype,
          merchant: expenses.merchant,
          originalInputName: expenses.originalInputName,
          amount: expenses.amount,
          split: expenses.split,
          memberBSpend: expenses.memberBSpend,
          memberASpend: expenses.memberASpend,
          spender: expenses.spender,
          card: expenses.card,
          logToWork: expenses.logToWork,
          tag: expenses.tag,
          link: expenses.link,
          trackWithFriends: expenses.trackWithFriends,
          sourceFileId: expenses.sourceFileId,
          categoryId: expenses.categoryId,
          subtypeId: expenses.subtypeId,
          isDuplicate: expenses.isDuplicate,
          needsValidation: expenses.needsValidation,
          isApproved: expenses.isApproved,
          approvedAt: expenses.approvedAt,
          contentHash: expenses.contentHash,
          createdAt: expenses.createdAt,
          sourceProcessedAt: sourceFiles.processedAt,
          sourceFilename: sourceFiles.filename,
        })
        .from(expenses)
        .leftJoin(sourceFiles, eq(expenses.sourceFileId, sourceFiles.id))
        .where(conditions.length > 0 ? and(...conditions) : undefined)
        .orderBy(
          ...(sortBy === "uploaded_desc"
            ? [desc(uploadSortAt), desc(expenses.id)]
            : [desc(expenses.date), desc(expenses.id)]),
        )
        .limit(limit)
        .offset(offset);

      return { items };
    }),

  getById: publicProcedure
    .input(z.object({ id: z.number() }))
    .query(async ({ ctx, input }) => {
      const expense = await ctx.db.query.expenses.findFirst({
        where: eq(expenses.id, input.id),
        with: {
          categoryRel: true,
          subtypeRel: true,
          sourceFile: true,
        },
      });

      if (!expense) {
        return expense;
      }

      const shareMap = await listExpenseFriendShares(ctx, [expense.id]);
      return {
        ...expense,
        friendShares: shareMap.get(expense.id) ?? [],
      };
    }),

  updateExpense: publicProcedure
    .input(
      z.object({
        id: z.number(),
        date: dateStringSchema,
        type: z.string().trim().min(1).max(255),
        subtype: z.string().trim().max(255).nullable(),
        merchant: z.string().trim().max(500).nullable(),
        originalInputName: z.string().trim().max(500).nullable(),
        amount: z.number().nonnegative(),
        split: z.number().min(0).max(1),
        spender: z.enum(MEMBER_ENUM).nullable(),
        card: z.string().trim().max(255).nullable(),
        tag: z.string().trim().max(255).nullable(),
        logToWork: z.boolean().nullable(),
        link: z.string().trim().max(1000).nullable(),
        trackWithFriends: z.string().trim().max(255).nullable(),
        friendShares: z
          .array(
            z.object({
              friendId: z.number(),
              amount: z.number().nonnegative(),
            }),
          )
          .default([]),
        isDuplicate: z.boolean(),
        needsValidation: z.boolean(),
        isApproved: z.boolean().optional(),
        categoryId: z.number().nullable(),
        subtypeId: z.number().nullable(),
      }),
    )
    .mutation(async ({ ctx, input }) => {
      const existingExpense = await ctx.db.query.expenses.findFirst({
        where: eq(expenses.id, input.id),
      });

      if (!existingExpense) {
        throw new Error(`Expense ${input.id} not found`);
      }

      let resolvedCategoryId = input.categoryId;
      let resolvedSubtypeId = input.subtypeId;
      let resolvedType = input.type;
      let resolvedSubtype = input.subtype;

      if (input.subtypeId) {
        const subtypeRecord = await ctx.db.query.subtypes.findFirst({
          where: eq(subtypes.id, input.subtypeId),
          with: {
            category: true,
          },
        });

        if (!subtypeRecord) {
          throw new Error(`Subtype ${input.subtypeId} not found`);
        }

        resolvedSubtypeId = subtypeRecord.id;
        resolvedSubtype = subtypeRecord.name;
        resolvedCategoryId = subtypeRecord.categoryId;
        resolvedType = subtypeRecord.category.name;
      } else if (input.categoryId) {
        const categoryRecord = await ctx.db.query.categories.findFirst({
          where: eq(categories.id, input.categoryId),
        });

        if (!categoryRecord) {
          throw new Error(`Category ${input.categoryId} not found`);
        }

        resolvedCategoryId = categoryRecord.id;
        resolvedType = categoryRecord.name;
      }

      const normalizedFriendShares = normalizeFriendShares(input.friendShares);
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
        input.amount,
        input.split,
        input.spender,
        normalizedFriendShares,
      );
      const derivedTrackWithFriends =
        summarizeTrackedFriends(normalizedFriendShares, friendsById) ??
        (input.trackWithFriends || null);

      const updatedRows = await ctx.db
        .update(expenses)
        .set({
          date: input.date,
          type: resolvedType,
          subtype: resolvedSubtype || null,
          merchant: input.merchant || null,
          originalInputName: input.originalInputName || null,
          amount: input.amount,
          split: input.split,
          spender: input.spender,
          card: input.card || null,
          tag: input.tag || null,
          logToWork: input.logToWork,
          link: input.link || null,
          trackWithFriends: derivedTrackWithFriends,
          isDuplicate: input.isDuplicate,
          needsValidation: input.needsValidation,
          isApproved: input.isApproved ?? existingExpense.isApproved,
          categoryId: resolvedCategoryId,
          subtypeId: resolvedSubtypeId,
          memberASpend: allocation.memberASpend,
          memberBSpend: allocation.memberBSpend,
        })
        .where(eq(expenses.id, input.id))
        .returning();

      await ctx.db
        .delete(expenseFriendShares)
        .where(eq(expenseFriendShares.expenseId, input.id));
      if (normalizedFriendShares.length > 0) {
        await ctx.db.insert(expenseFriendShares).values(
          normalizedFriendShares.map((share) => ({
            expenseId: input.id,
            friendId: share.friendId,
            amount: share.amount,
          })),
        );
      }

      return updatedRows[0];
    }),

  deleteExpense: publicProcedure
    .input(z.object({ id: z.number() }))
    .mutation(async ({ ctx, input }) => {
      const existingExpense = await ctx.db.query.expenses.findFirst({
        where: eq(expenses.id, input.id),
      });

      if (!existingExpense) {
        throw new Error(`Expense ${input.id} not found`);
      }

      await removeExpense(ctx.db, input.id);

      return { success: true, id: input.id };
    }),

  deleteExpenses: publicProcedure
    .input(z.object({ ids: z.array(z.number()).min(1).max(200) }))
    .mutation(async ({ ctx, input }) => {
      const deletedIds: number[] = [];

      for (const id of input.ids) {
        const existingExpense = await ctx.db.query.expenses.findFirst({
          where: eq(expenses.id, id),
        });

        if (!existingExpense) {
          continue;
        }

        await removeExpense(ctx.db, id);
        deletedIds.push(id);
      }

      return {
        success: true,
        deletedIds,
      };
    }),

  getRecent: publicProcedure
    .input(z.object({ limit: z.number().default(10) }))
    .query(async ({ ctx, input }) => {
      return ctx.db.query.expenses.findMany({
        where: and(eq(expenses.isDuplicate, false), eq(expenses.isApproved, true)),
        orderBy: [desc(expenses.date)],
        limit: input.limit,
        with: {
          categoryRel: true,
        },
      });
    }),

  getRecentUnapproved: publicProcedure
    .input(z.object({ limit: z.number().min(1).max(30).default(12) }))
    .query(async ({ ctx, input }) => {
      return ctx.db.query.expenses.findMany({
        where: and(eq(expenses.isDuplicate, false), eq(expenses.isApproved, false)),
        orderBy: [desc(expenses.date)],
        limit: input.limit,
        with: {
          categoryRel: true,
        },
      });
    }),

  getSummary: publicProcedure
    .input(
      z.object({
        startDate: dateStringSchema.optional(),
        endDate: dateStringSchema.optional(),
      })
    )
    .query(async ({ ctx, input }) => {
      const conditions = buildExpenseConditions({
        startDate: input.startDate,
        endDate: input.endDate,
        includeUnapproved: false,
      });
      const whereClause = and(...conditions);

      const totalResult = await ctx.db
        .select({
          total: sum(expenses.amount),
          count: count(),
        })
        .from(expenses)
        .where(whereClause);

      const memberAResult = await ctx.db
        .select({ total: sum(expenses.memberASpend) })
        .from(expenses)
        .where(whereClause);

      const memberBResult = await ctx.db
        .select({ total: sum(expenses.memberBSpend) })
        .from(expenses)
        .where(whereClause);

      const pendingResult = await ctx.db
        .select({ count: count() })
        .from(expenses)
        .where(and(...conditions, eq(expenses.needsValidation, true)));

      return {
        totalAmount: Number(totalResult[0]?.total ?? 0),
        totalCount: totalResult[0]?.count ?? 0,
        memberASpend: Number(memberAResult[0]?.total ?? 0),
        memberBSpend: Number(memberBResult[0]?.total ?? 0),
        pendingValidation: pendingResult[0]?.count ?? 0,
      };
    }),

  getByCategory: publicProcedure
    .input(
      z.object({
        startDate: dateStringSchema.optional(),
        endDate: dateStringSchema.optional(),
      })
    )
    .query(async ({ ctx, input }) => {
      const conditions = buildExpenseConditions({
        startDate: input.startDate,
        endDate: input.endDate,
        includeUnapproved: false,
      });

      const result = await ctx.db
        .select({
          category: expenses.type,
          amount: sum(expenses.amount),
          count: count(),
        })
        .from(expenses)
        .where(and(...conditions))
        .groupBy(expenses.type)
        .orderBy(desc(sum(expenses.amount)));

      return result.map((row) => ({
        category: row.category,
        amount: Number(row.amount ?? 0),
        count: row.count,
      }));
    }),

  getByCard: publicProcedure
    .input(
      z.object({
        startDate: dateStringSchema.optional(),
        endDate: dateStringSchema.optional(),
      })
    )
    .query(async ({ ctx, input }) => {
      const conditions = buildExpenseConditions({
        startDate: input.startDate,
        endDate: input.endDate,
        includeUnapproved: false,
      });
      conditions.push(sql`${expenses.card} IS NOT NULL`);

      const result = await ctx.db
        .select({
          card: expenses.card,
          amount: sum(expenses.amount),
          count: count(),
        })
        .from(expenses)
        .where(and(...conditions))
        .groupBy(expenses.card)
        .orderBy(desc(sum(expenses.amount)));

      return result.map((row) => ({
        card: row.card ?? "Unknown",
        amount: Number(row.amount ?? 0),
        count: row.count,
      }));
    }),

  getMonthlyTrends: publicProcedure
    .input(z.object({ months: z.number().default(6) }))
    .query(async ({ ctx, input }) => {
      const startDate = new Date();
      startDate.setMonth(startDate.getMonth() - input.months);

      const expensesList = await ctx.db.query.expenses.findMany({
        where: and(
          eq(expenses.isDuplicate, false),
          eq(expenses.needsValidation, false),
          eq(expenses.isApproved, true),
          gte(expenses.date, formatDateOnly(startDate)),
        ),
        columns: {
          date: true,
          amount: true,
          memberASpend: true,
          memberBSpend: true,
        },
      });

      const monthlyData = new Map<
        string,
        { total: number; memberA: number; memberB: number }
      >();

      expensesList.forEach((expense) => {
        const bucket = toBucket(expense.date, "month");
        const current = monthlyData.get(bucket) ?? {
          total: 0,
          memberA: 0,
          memberB: 0,
        };
        monthlyData.set(bucket, {
          total: current.total + (expense.amount ?? 0),
          memberA: current.memberA + (expense.memberASpend ?? 0),
          memberB: current.memberB + (expense.memberBSpend ?? 0),
        });
      });

      return Array.from(monthlyData.entries())
        .map(([month, data]) => ({
          month,
          ...data,
        }))
        .sort((left, right) => left.month.localeCompare(right.month));
    }),

  getReviewInsights: publicProcedure
    .input(
      z.object({
        startDate: dateStringSchema.optional(),
        endDate: dateStringSchema.optional(),
        categories: z.array(z.string()).default([]),
        granularity: z.enum(["day", "week", "month"]).default("day"),
      })
    )
    .query(async ({ ctx, input }) => {
      ensurePaymentsTable(ctx);
      const baseConditions = buildExpenseConditions({
        startDate: input.startDate,
        endDate: input.endDate,
        includeUnapproved: false,
      });

      const rows = await ctx.db.query.expenses.findMany({
        where: and(...baseConditions),
        orderBy: [desc(expenses.date)],
        columns: {
          id: true,
          date: true,
          type: true,
          subtype: true,
          merchant: true,
          amount: true,
          memberASpend: true,
          memberBSpend: true,
          spender: true,
          card: true,
        },
        with: {
          categoryRel: {
            columns: {
              name: true,
            },
          },
          subtypeRel: {
            columns: {
              name: true,
            },
          },
        },
      });

      const normalizedRows = rows.map((row) => {
        const canonicalType = row.categoryRel?.name ?? row.type;
        const canonicalSubtype = row.subtypeRel?.name ?? row.subtype;

        return {
          ...row,
          type: canonicalType,
          subtype: canonicalSubtype,
        };
      });

      const availableCategories = Array.from(
        new Set(normalizedRows.map((row) => normalizeCategory(row.type))),
      ).sort((left, right) => left.localeCompare(right));

      const selectedRows =
        input.categories.length > 0
          ? normalizedRows.filter((row) => input.categories.includes(normalizeCategory(row.type)))
          : normalizedRows;

      const summary = selectedRows.reduce(
        (acc, row) => {
          acc.totalAmount += row.amount ?? 0;
          acc.memberASpend += row.memberASpend ?? 0;
          acc.memberBSpend += row.memberBSpend ?? 0;
          acc.transactionCount += 1;
          return acc;
        },
        {
          totalAmount: 0,
          memberASpend: 0,
          memberBSpend: 0,
          transactionCount: 0,
        },
      );

      const trendMap = new Map<
        string,
        { bucket: string; label: string; total: number; memberA: number; memberB: number }
      >();

      selectedRows
        .slice()
        .sort((left, right) => left.date.localeCompare(right.date))
        .forEach((row) => {
          const bucket = toBucket(row.date, input.granularity);
          const current = trendMap.get(bucket) ?? {
            bucket,
            label: toLabel(bucket, input.granularity),
            total: 0,
            memberA: 0,
            memberB: 0,
          };

          current.total += row.amount ?? 0;
          current.memberA += row.memberASpend ?? 0;
          current.memberB += row.memberBSpend ?? 0;
          trendMap.set(bucket, current);
        });

      const trendBoundaryRows = selectedRows.length > 0 ? selectedRows : normalizedRows;
      const boundaryStart =
        input.startDate ??
        (trendBoundaryRows.length > 0
          ? trendBoundaryRows
              .slice()
              .sort((left, right) => left.date.localeCompare(right.date))[0]?.date
          : undefined);
      const boundaryEnd =
        input.endDate ??
        (trendBoundaryRows.length > 0
          ? trendBoundaryRows
              .slice()
              .sort((left, right) => right.date.localeCompare(left.date))[0]?.date
          : undefined);

      if (boundaryStart && boundaryEnd) {
        let cursor = normalizeBucketStart(
          new Date(`${boundaryStart}T00:00:00`),
          input.granularity,
        );
        const endCursor = normalizeBucketStart(
          new Date(`${boundaryEnd}T00:00:00`),
          input.granularity,
        );

        while (cursor.getTime() <= endCursor.getTime()) {
          const bucket = toBucket(formatDateOnly(cursor), input.granularity);
          if (!trendMap.has(bucket)) {
            trendMap.set(bucket, {
              bucket,
              label: toLabel(bucket, input.granularity),
              total: 0,
              memberA: 0,
              memberB: 0,
            });
          }

          cursor = nextBucketDate(cursor, input.granularity);
        }
      }

      const categoryMap = new Map<
        string,
        { name: string; amount: number; count: number; memberA: number; memberB: number }
      >();

      selectedRows.forEach((row) => {
        const category = normalizeCategory(row.type);
        const current = categoryMap.get(category) ?? {
          name: category,
          amount: 0,
          count: 0,
          memberA: 0,
          memberB: 0,
        };

        current.amount += row.amount ?? 0;
        current.count += 1;
        current.memberA += row.memberASpend ?? 0;
        current.memberB += row.memberBSpend ?? 0;
        categoryMap.set(category, current);
      });

      const selectedSingleCategory =
        input.categories.length === 1 ? input.categories[0] : null;

      const subtypeMap = new Map<
        string,
        { name: string; amount: number; count: number; memberA: number; memberB: number }
      >();

      if (selectedSingleCategory) {
        selectedRows.forEach((row) => {
          const subtype = normalizeSubtype(row.subtype);
          const current = subtypeMap.get(subtype) ?? {
            name: subtype,
            amount: 0,
            count: 0,
            memberA: 0,
            memberB: 0,
          };

          current.amount += row.amount ?? 0;
          current.count += 1;
          current.memberA += row.memberASpend ?? 0;
          current.memberB += row.memberBSpend ?? 0;
          subtypeMap.set(subtype, current);
        });
      }

      const cardMap = new Map<string, { card: string; amount: number; count: number }>();
      selectedRows.forEach((row) => {
        const card = row.card?.trim() || "Unknown";
        const current = cardMap.get(card) ?? {
          card,
          amount: 0,
          count: 0,
        };
        current.amount += row.amount ?? 0;
        current.count += 1;
        cardMap.set(card, current);
      });

      const paymentConditions = [];
      if (input.startDate) {
        paymentConditions.push(gte(payments.date, input.startDate));
      }
      if (input.endDate) {
        paymentConditions.push(lte(payments.date, input.endDate));
      }

      const paymentRows = await ctx.db.query.payments.findMany({
        where: paymentConditions.length > 0 ? and(...paymentConditions) : undefined,
        orderBy: [desc(payments.date), desc(payments.id)],
        limit: 20,
      });

      const expenseBalance = selectedRows.reduce((balance, row) => {
        const normalizedSpender = row.spender?.trim().toLowerCase();
        if (normalizedSpender === MEMBER_A.toLowerCase()) {
          return balance + (row.memberBSpend ?? 0);
        }

        if (normalizedSpender === MEMBER_B.toLowerCase()) {
          return balance - (row.memberASpend ?? 0);
        }

        return balance;
      }, 0);

      const paymentAdjustment = paymentRows.reduce((balance, payment) => {
        const from = payment.fromPerson.trim().toLowerCase();
        const to = payment.toPerson.trim().toLowerCase();

        if (from === MEMBER_A.toLowerCase() && to === MEMBER_B.toLowerCase()) {
          return balance + payment.amount;
        }

        if (from === MEMBER_B.toLowerCase() && to === MEMBER_A.toLowerCase()) {
          return balance - payment.amount;
        }

        return balance;
      }, 0);

      const netDueToA = expenseBalance + paymentAdjustment;

      return {
        availableCategories,
        summary: {
          ...summary,
          avgTransaction:
            summary.transactionCount > 0
              ? summary.totalAmount / summary.transactionCount
              : 0,
        },
        trend: Array.from(trendMap.values()).sort((left, right) =>
          left.bucket.localeCompare(right.bucket),
        ),
        categories: Array.from(categoryMap.values())
          .sort((left, right) => right.amount - left.amount)
          .slice(0, 10),
        subtypes: Array.from(subtypeMap.values()).sort(
          (left, right) => right.amount - left.amount,
        ),
        cards: Array.from(cardMap.values())
          .sort((left, right) => right.amount - left.amount)
          .slice(0, 8),
        recentExpenses: selectedRows.slice(0, 20),
        settlement: {
          expenseBalance,
          paymentAdjustment,
          netDueToA,
          direction:
            netDueToA > 0.009
              ? ("b_owes_a" as const)
              : netDueToA < -0.009
                ? ("a_owes_b" as const)
                : ("settled" as const),
          absoluteAmount: Math.abs(netDueToA),
        },
        payments: paymentRows,
      };
    }),

  getWorkInsights: publicProcedure
    .input(
      z.object({
        startDate: dateStringSchema.optional(),
        endDate: dateStringSchema.optional(),
        granularity: z.enum(["day", "week", "month"]).default("week"),
      }),
    )
    .query(async ({ ctx, input }) => {
      const rows = await loadNormalizedInsightRows(ctx, {
        startDate: input.startDate,
        endDate: input.endDate,
        extraConditions: [eq(expenses.logToWork, true)],
      });

      const summary = rows.reduce(
        (acc: { totalAmount: number; transactionCount: number }, row: NormalizedInsightRow) => {
          acc.totalAmount += row.amount ?? 0;
          acc.transactionCount += 1;
          return acc;
        },
        {
          totalAmount: 0,
          transactionCount: 0,
        },
      );

      const merchantMap = new Map<string, { merchant: string; amount: number; count: number }>();
      const categoryMap = new Map<string, { name: string; amount: number; count: number }>();
      const tagMap = new Map<string, { tag: string; amount: number; count: number }>();

      rows.forEach((row: NormalizedInsightRow) => {
        const merchant = row.merchant?.trim() || "Unknown";
        const currentMerchant = merchantMap.get(merchant) ?? { merchant, amount: 0, count: 0 };
        currentMerchant.amount += row.amount ?? 0;
        currentMerchant.count += 1;
        merchantMap.set(merchant, currentMerchant);

        const category = normalizeCategory(row.type);
        const currentCategory = categoryMap.get(category) ?? { name: category, amount: 0, count: 0 };
        currentCategory.amount += row.amount ?? 0;
        currentCategory.count += 1;
        categoryMap.set(category, currentCategory);

        if (row.tag?.trim()) {
          const tag = row.tag.trim();
          const currentTag = tagMap.get(tag) ?? { tag, amount: 0, count: 0 };
          currentTag.amount += row.amount ?? 0;
          currentTag.count += 1;
          tagMap.set(tag, currentTag);
        }
      });

      return {
        summary: {
          ...summary,
          avgTransaction:
            summary.transactionCount > 0
              ? summary.totalAmount / summary.transactionCount
              : 0,
        },
        trend: buildTrendFromRows(rows, input.granularity),
        merchants: Array.from(merchantMap.values()).sort((left, right) => right.amount - left.amount).slice(0, 10),
        categories: Array.from(categoryMap.values()).sort((left, right) => right.amount - left.amount).slice(0, 10),
        tags: Array.from(tagMap.values()).sort((left, right) => right.amount - left.amount).slice(0, 10),
        recentExpenses: rows.slice(0, 20),
      };
    }),

  getTagInsights: publicProcedure
    .input(
      z.object({
        startDate: dateStringSchema.optional(),
        endDate: dateStringSchema.optional(),
        granularity: z.enum(["day", "week", "month"]).default("week"),
        selectedTag: z.string().trim().nullable().optional(),
      }),
    )
    .query(async ({ ctx, input }) => {
      const rows = await loadNormalizedInsightRows(ctx, {
        startDate: input.startDate,
        endDate: input.endDate,
        extraConditions: [isNotNull(expenses.tag)],
      });

      const tagMap = new Map<string, { tag: string; amount: number; count: number }>();
      rows.forEach((row: NormalizedInsightRow) => {
        const tag = row.tag?.trim();
        if (!tag) {
          return;
        }

        const currentTag = tagMap.get(tag) ?? { tag, amount: 0, count: 0 };
        currentTag.amount += row.amount ?? 0;
        currentTag.count += 1;
        tagMap.set(tag, currentTag);
      });

      const availableTags = Array.from(tagMap.values()).sort((left, right) =>
        right.amount - left.amount || right.count - left.count || left.tag.localeCompare(right.tag),
      );

      const selectedRows = input.selectedTag
        ? rows.filter((row: NormalizedInsightRow) => row.tag?.trim() === input.selectedTag)
        : rows;

      const summary = selectedRows.reduce(
        (acc: { totalAmount: number; transactionCount: number }, row: NormalizedInsightRow) => {
          acc.totalAmount += row.amount ?? 0;
          acc.transactionCount += 1;
          return acc;
        },
        {
          totalAmount: 0,
          transactionCount: 0,
        },
      );

      const categoryMap = new Map<string, { name: string; amount: number; count: number }>();
      selectedRows.forEach((row: NormalizedInsightRow) => {
        const category = normalizeCategory(row.type);
        const currentCategory = categoryMap.get(category) ?? { name: category, amount: 0, count: 0 };
        currentCategory.amount += row.amount ?? 0;
        currentCategory.count += 1;
        categoryMap.set(category, currentCategory);
      });

      return {
        availableTags,
        selectedTag: input.selectedTag ?? null,
        summary: {
          ...summary,
          avgTransaction:
            summary.transactionCount > 0
              ? summary.totalAmount / summary.transactionCount
              : 0,
        },
        trend: buildTrendFromRows(selectedRows, input.granularity),
        categories: Array.from(categoryMap.values()).sort((left, right) => right.amount - left.amount).slice(0, 10),
        recentExpenses: selectedRows.slice(0, 20),
      };
    }),

  addPayment: publicProcedure
    .input(
      z.object({
        date: dateStringSchema,
        fromPerson: z.enum(MEMBER_ENUM),
        toPerson: z.enum(MEMBER_ENUM),
        amount: z.number().positive(),
        note: z.string().trim().max(500).nullable().optional(),
      })
    )
    .mutation(async ({ ctx, input }) => {
      ensurePaymentsTable(ctx);

      if (input.fromPerson === input.toPerson) {
        throw new Error(`Payment must be between ${MEMBER_A} and ${MEMBER_B}.`);
      }

      const created = await ctx.db
        .insert(payments)
        .values({
          date: input.date,
          fromPerson: input.fromPerson,
          toPerson: input.toPerson,
          amount: input.amount,
          note: input.note ?? null,
        })
        .returning();

      return created[0];
    }),

  deletePayment: publicProcedure
    .input(z.object({ id: z.number() }))
    .mutation(async ({ ctx, input }) => {
      ensurePaymentsTable(ctx);

      await ctx.db.delete(payments).where(eq(payments.id, input.id));

      return { success: true };
    }),
});
