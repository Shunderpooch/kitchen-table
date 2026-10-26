import { MEMBER_ENUM } from "~/lib/household";
import { z } from "zod";
import { and, desc, eq, gte, inArray, sql } from "drizzle-orm";
import { createTRPCRouter, publicProcedure } from "~/server/api/trpc";
import {
  categories,
  expenseDocuments,
  expenses,
  scannedDocuments,
  subtypes,
  validationQueue,
} from "~/server/db/schema";
import { documentsAvailable } from "~/server/api/routers/documents";
import { calculateSpendAllocation } from "~/lib/expense-sharing";
import { runScans } from "~/server/scans";

type DB = typeof import("~/server/db").db;

interface StagedRow {
  id: number;
  date: string;
  amount: number;
  merchant: string | null;
  type: string | null;
  subtype: string | null;
  categoryId: number | null;
  subtypeId: number | null;
  card: string | null;
  spender: string | null;
  tag: string | null;
  split: number | null;
  reasons: string | null;
  documentId: number;
  path: string;
  docTime: string | null;
}

/** Why a staged receipt expense should not be approved without a look. */
function lookReasons(row: StagedRow) {
  const reasons: string[] = [];
  const notes = row.reasons ?? "";
  if (!row.categoryId) reasons.push("no category");
  if (notes.includes("largest number")) reasons.push("amount guessed");
  if (notes.includes("large amount")) reasons.push("large amount");
  if (notes.includes("tip written")) reasons.push("tip may be missing");
  if (!row.spender) reasons.push("someone else's receipt");
  return reasons;
}

function fileName(documentPath: string) {
  return documentPath.split("/").pop() ?? documentPath;
}

/**
 * Confirming a receipt also fills the expense's `link` with the receipt's
 * filename when it is empty, the same way receipts were linked by hand.
 */
async function fillLinks(db: DB, linkIds: number[]) {
  if (linkIds.length === 0) {
    return;
  }
  const rows = await db
    .select({ expenseId: expenseDocuments.expenseId, path: scannedDocuments.path, link: expenses.link })
    .from(expenseDocuments)
    .innerJoin(scannedDocuments, eq(scannedDocuments.id, expenseDocuments.documentId))
    .innerJoin(expenses, eq(expenses.id, expenseDocuments.expenseId))
    .where(inArray(expenseDocuments.id, linkIds));
  for (const row of rows) {
    if (!row.link) {
      await db.update(expenses).set({ link: fileName(row.path) }).where(eq(expenses.id, row.expenseId));
    }
  }
}

const linkColumns = {
  id: expenseDocuments.id,
  status: expenseDocuments.status,
  method: expenseDocuments.method,
  score: expenseDocuments.score,
  reasons: expenseDocuments.reasons,
  expenseId: expenses.id,
  date: expenses.date,
  merchant: expenses.merchant,
  originalInputName: expenses.originalInputName,
  amount: expenses.amount,
  card: expenses.card,
  spender: expenses.spender,
  documentId: scannedDocuments.id,
  path: scannedDocuments.path,
  docDate: scannedDocuments.docDate,
  docTime: scannedDocuments.docTime,
  docMerchant: scannedDocuments.merchant,
  ocrTotal: scannedDocuments.ocrTotal,
};

export const receiptsRouter = createTRPCRouter({
  matches: publicProcedure
    .input(
      z.object({
        status: z.enum(["suggested", "confirmed", "rejected"]).default("suggested"),
        minScore: z.number().default(0),
        limit: z.number().int().min(1).max(500).default(100),
        offset: z.number().int().min(0).default(0),
      }),
    )
    .query(async ({ ctx, input }) => {
      if (!documentsAvailable(ctx.db)) {
        return { rows: [], total: 0 };
      }
      const where = and(eq(expenseDocuments.status, input.status), gte(expenseDocuments.score, input.minScore));
      const rows = await ctx.db
        .select(linkColumns)
        .from(expenseDocuments)
        .innerJoin(expenses, eq(expenses.id, expenseDocuments.expenseId))
        .innerJoin(scannedDocuments, eq(scannedDocuments.id, expenseDocuments.documentId))
        .where(where)
        .orderBy(desc(expenseDocuments.score), desc(expenses.date))
        .limit(input.limit)
        .offset(input.offset);
      const total = ctx.db.get<{ n: number }>(
        sql`SELECT count(*) AS n FROM expense_documents WHERE status = ${input.status} AND score >= ${input.minScore}`,
      );
      return { rows, total: total?.n ?? 0 };
    }),

  forExpense: publicProcedure.input(z.object({ expenseId: z.number().int() })).query(async ({ ctx, input }) => {
    if (!documentsAvailable(ctx.db)) {
      return [];
    }
    return ctx.db
      .select(linkColumns)
      .from(expenseDocuments)
      .innerJoin(expenses, eq(expenses.id, expenseDocuments.expenseId))
      .innerJoin(scannedDocuments, eq(scannedDocuments.id, expenseDocuments.documentId))
      .where(eq(expenseDocuments.expenseId, input.expenseId))
      .orderBy(desc(expenseDocuments.score));
  }),

  /** Receipt status for many expenses at once (for tables): confirmed doc id or suggestion count. */
  summaryForExpenses: publicProcedure
    .input(z.object({ expenseIds: z.array(z.number().int()).max(1000) }))
    .query(async ({ ctx, input }) => {
      if (!documentsAvailable(ctx.db) || input.expenseIds.length === 0) {
        return {} as Record<number, { confirmed: number | null; suggested: number }>;
      }
      const rows = await ctx.db
        .select({
          expenseId: expenseDocuments.expenseId,
          documentId: expenseDocuments.documentId,
          status: expenseDocuments.status,
        })
        .from(expenseDocuments)
        .where(inArray(expenseDocuments.expenseId, input.expenseIds));
      const summary: Record<number, { confirmed: number | null; suggested: number }> = {};
      for (const row of rows) {
        const entry = (summary[row.expenseId] ??= { confirmed: null, suggested: 0 });
        if (row.status === "confirmed") entry.confirmed = row.documentId;
        if (row.status === "suggested") entry.suggested += 1;
      }
      return summary;
    }),

  decide: publicProcedure
    .input(z.object({ ids: z.array(z.number().int()).min(1).max(1000), status: z.enum(["confirmed", "rejected"]) }))
    .mutation(async ({ ctx, input }) => {
      await ctx.db
        .update(expenseDocuments)
        .set({ status: input.status })
        .where(inArray(expenseDocuments.id, input.ids));
      if (input.status === "confirmed") {
        await fillLinks(ctx.db, input.ids);
      }
      return { updated: input.ids.length };
    }),

  /** Confirm every suggestion at or above a score (the strongest matches). */
  confirmAbove: publicProcedure.input(z.object({ minScore: z.number().min(5) })).mutation(async ({ ctx, input }) => {
    const ids = (
      await ctx.db
        .select({ id: expenseDocuments.id })
        .from(expenseDocuments)
        .where(and(eq(expenseDocuments.status, "suggested"), gte(expenseDocuments.score, input.minScore)))
    ).map((row) => row.id);
    if (ids.length) {
      await ctx.db.update(expenseDocuments).set({ status: "confirmed" }).where(inArray(expenseDocuments.id, ids));
      await fillLinks(ctx.db, ids);
    }
    return { confirmed: ids.length };
  }),

  link: publicProcedure
    .input(z.object({ expenseId: z.number().int(), documentId: z.number().int() }))
    .mutation(async ({ ctx, input }) => {
      const existing = await ctx.db.query.expenseDocuments.findFirst({
        where: and(eq(expenseDocuments.expenseId, input.expenseId), eq(expenseDocuments.documentId, input.documentId)),
      });
      let id = existing?.id;
      if (existing) {
        await ctx.db.update(expenseDocuments).set({ status: "confirmed" }).where(eq(expenseDocuments.id, existing.id));
      } else {
        const created = await ctx.db
          .insert(expenseDocuments)
          .values({ ...input, score: 10, reasons: "linked by hand", method: "manual", status: "confirmed" })
          .returning();
        id = created[0]?.id;
      }
      if (id) await fillLinks(ctx.db, [id]);
      return { id };
    }),

  /** Receipts dated near an expense, for linking one by hand. */
  candidates: publicProcedure.input(z.object({ expenseId: z.number().int() })).query(async ({ ctx, input }) => {
    const expense = await ctx.db.query.expenses.findFirst({ where: eq(expenses.id, input.expenseId) });
    if (!expense || !documentsAvailable(ctx.db)) {
      return [];
    }
    return ctx.db.all<{
      id: number;
      path: string;
      docDate: string;
      docTime: string | null;
      merchant: string | null;
      ocrTotal: number | null;
    }>(sql`
      SELECT id, path, doc_date AS docDate, doc_time AS docTime, merchant, ocr_total AS ocrTotal
      FROM scanned_documents
      WHERE doc_date BETWEEN date(${expense.date}, '-7 days') AND date(${expense.date}, '+7 days')
      ORDER BY abs(julianday(doc_date) - julianday(${expense.date})), path
      LIMIT 40`);
  }),

  /**
   * Expenses made from receipts that wait for approval, with their receipt.
   * `look`: what deserves a closer look before approving in bulk.
   */
  staged: publicProcedure
    .input(z.object({ limit: z.number().int().min(1).max(500).default(200), offset: z.number().int().min(0).default(0) }))
    .query(({ ctx, input }) => {
      if (!documentsAvailable(ctx.db)) {
        return { rows: [], total: 0 };
      }
      const rows = ctx.db.all<StagedRow>(sql`
        SELECT e.id, e.date, e.amount, e.merchant, e.type, e.subtype, e.category_id AS categoryId,
               e.subtype_id AS subtypeId, e.card, e.spender, e.tag, e.split,
               l.reasons, d.id AS documentId, d.path, d.doc_time AS docTime
        FROM expenses e
        JOIN expense_documents l ON l.expense_id = e.id AND l.method = 'receipt'
        JOIN scanned_documents d ON d.id = l.document_id
        WHERE coalesce(e.is_approved, 0) = 0 AND e.content_hash LIKE 'receipt:%'
        GROUP BY e.id
        ORDER BY e.date DESC, e.id DESC
        LIMIT ${input.limit} OFFSET ${input.offset}`);
      const total = ctx.db.get<{ n: number }>(sql`
        SELECT count(*) AS n FROM expenses WHERE coalesce(is_approved, 0) = 0 AND content_hash LIKE 'receipt:%'`);
      return { rows: rows.map((row) => ({ ...row, look: lookReasons(row) })), total: total?.n ?? 0 };
    }),

  updateStaged: publicProcedure
    .input(
      z.object({
        id: z.number().int(),
        date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
        amount: z.number().positive().optional(),
        merchant: z.string().trim().min(1).max(200).optional(),
        categoryId: z.number().int().optional(),
        subtypeId: z.number().int().nullable().optional(),
        card: z.string().trim().max(100).nullable().optional(),
        spender: z.enum(MEMBER_ENUM).nullable().optional(),
        tag: z.string().trim().max(255).nullable().optional(),
      }),
    )
    .mutation(async ({ ctx, input }) => {
      const { id, categoryId, subtypeId, ...fields } = input;
      const set: Partial<typeof expenses.$inferInsert> = { ...fields };
      if (categoryId !== undefined) {
        const category = await ctx.db.query.categories.findFirst({ where: eq(categories.id, categoryId) });
        if (!category) throw new Error(`Category ${categoryId} not found`);
        set.categoryId = categoryId;
        set.type = category.name;
        set.subtypeId = null;
        set.subtype = null;
      }
      if (subtypeId) {
        const subtype = await ctx.db.query.subtypes.findFirst({ where: eq(subtypes.id, subtypeId) });
        if (!subtype) throw new Error(`Subcategory ${subtypeId} not found`);
        set.subtypeId = subtypeId;
        set.subtype = subtype.name;
      } else if (subtypeId === null) {
        set.subtypeId = null;
        set.subtype = null;
      }
      await ctx.db.update(expenses).set(set).where(eq(expenses.id, id));
      return { id };
    }),

  /** Approve staged receipt expenses as they are (each needs a category). */
  approveStaged: publicProcedure
    .input(z.object({ ids: z.array(z.number().int()).min(1).max(500) }))
    .mutation(async ({ ctx, input }) => {
      const rows = await ctx.db.query.expenses.findMany({ where: inArray(expenses.id, input.ids) });
      const approved: number[] = [];
      const missingCategory: number[] = [];
      for (const row of rows) {
        if (!row.categoryId) {
          missingCategory.push(row.id);
          continue;
        }
        const allocation = calculateSpendAllocation(Number(row.amount), row.split ?? 1, row.spender, []);
        await ctx.db
          .update(expenses)
          .set({
            memberASpend: allocation.memberASpend,
            memberBSpend: allocation.memberBSpend,
            needsValidation: false,
            isApproved: true,
            approvedAt: new Date(),
          })
          .where(eq(expenses.id, row.id));
        await ctx.db
          .update(validationQueue)
          .set({ status: "validated", validatedAt: new Date() })
          .where(eq(validationQueue.expenseId, row.id));
        approved.push(row.id);
      }
      return { approved: approved.length, missingCategory };
    }),

  rematch: publicProcedure.mutation(async () => {
    const { stdout, code, stderr } = await runScans(["match-receipts", "--confirm-above", "6"], 600_000);
    if (code !== 0) {
      throw new Error(stderr.trim().split(/\r?\n/).pop() ?? "Matching failed");
    }
    return { output: stdout.trim() };
  }),
});
