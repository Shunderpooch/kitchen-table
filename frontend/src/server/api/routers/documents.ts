import { z } from "zod";
import { asc, eq, sql } from "drizzle-orm";
import { createTRPCRouter, publicProcedure } from "~/server/api/trpc";
import { expenseDocuments, expenses, scannedDocuments, scannedPages } from "~/server/db/schema";
import { startScansJob, SCANS_DIR } from "~/server/scans";
import path from "path";

type DB = typeof import("~/server/db").db;

export function documentsAvailable(db: DB) {
  const row = db.get<{ n: number }>(
    sql`SELECT count(*) AS n FROM sqlite_master WHERE type = 'table' AND name = 'scanned_documents'`,
  );
  return (row?.n ?? 0) > 0;
}

/**
 * Plain words become an all-words search (each word quoted, so punctuation
 * cannot break FTS5); queries already using FTS5 syntax pass through.
 */
function ftsQuery(query: string) {
  if (/["*]|\b(AND|OR|NOT|NEAR)\b/.test(query)) {
    return query;
  }
  const words = query.match(/[\p{L}\p{N}][\p{L}\p{N}'&.-]*/gu) ?? [];
  return words.map((word) => `"${word.replaceAll('"', "")}"`).join(" ");
}

export interface DocumentHit {
  id: number;
  path: string;
  kind: string | null;
  person: string | null;
  docDate: string | null;
  docTime: string | null;
  merchant: string | null;
  pageCount: number | null;
  ocrTotal: number | null;
  page: number | null;
  snippet: string | null;
  linkedExpenses: number;
}

export const documentsRouter = createTRPCRouter({
  status: publicProcedure.query(({ ctx }) => {
    if (!documentsAvailable(ctx.db)) {
      return { available: false, documents: 0, pages: 0, linked: 0, suggested: 0 };
    }
    const counts = ctx.db.get<{ documents: number; pages: number }>(
      sql`SELECT (SELECT count(*) FROM scanned_documents) AS documents,
                 (SELECT count(*) FROM scanned_pages) AS pages`,
    );
    const links = ctx.db.get<{ linked: number; suggested: number }>(
      sql`SELECT coalesce(sum(status = 'confirmed'), 0) AS linked,
                 coalesce(sum(status = 'suggested'), 0) AS suggested
          FROM expense_documents`,
    );
    return { available: true, ...counts!, ...(links ?? { linked: 0, suggested: 0 }) };
  }),

  search: publicProcedure
    .input(
      z.object({
        query: z.string().trim().max(200).default(""),
        kind: z.string().optional(),
        person: z.string().optional(),
        limit: z.number().int().min(1).max(200).default(50),
        offset: z.number().int().min(0).default(0),
      }),
    )
    .query(({ ctx, input }) => {
      if (!documentsAvailable(ctx.db)) {
        return { hits: [] as DocumentHit[], error: null };
      }
      const filters = [
        input.kind ? sql`AND d.kind = ${input.kind}` : sql``,
        input.person === "self"
          ? sql`AND d.person IS NULL`
          : input.person
            ? sql`AND d.person = ${input.person}`
            : sql``,
      ];
      const linkedCount = sql`(SELECT count(*) FROM expense_documents l
                               WHERE l.document_id = d.id AND l.status = 'confirmed')`;
      try {
        if (!input.query) {
          const hits = ctx.db.all<DocumentHit>(sql`
            SELECT d.id, d.path, d.kind, d.person, d.doc_date AS docDate, d.doc_time AS docTime,
                   d.merchant, d.page_count AS pageCount, d.ocr_total AS ocrTotal,
                   NULL AS page, NULL AS snippet, ${linkedCount} AS linkedExpenses
            FROM scanned_documents d
            WHERE 1 = 1 ${filters[0]} ${filters[1]}
            ORDER BY d.doc_date DESC NULLS LAST, d.path
            LIMIT ${input.limit} OFFSET ${input.offset}`);
          return { hits, error: null };
        }
        // Best page per document, ranked by FTS5 relevance.
        const hits = ctx.db.all<DocumentHit>(sql`
          SELECT d.id, d.path, d.kind, d.person, d.doc_date AS docDate, d.doc_time AS docTime,
                 d.merchant, d.page_count AS pageCount, d.ocr_total AS ocrTotal,
                 m.page_number AS page, m.snip AS snippet, ${linkedCount} AS linkedExpenses
          FROM (
            SELECT s.*,
                   row_number() OVER (PARTITION BY s.document_id ORDER BY s.score) AS rn,
                   min(s.score) OVER (PARTITION BY s.document_id) AS best
            FROM (
              SELECT p.document_id, p.page_number,
                     snippet(scanned_pages_fts, 0, '[', ']', ' … ', 14) AS snip,
                     bm25(scanned_pages_fts) AS score
              FROM scanned_pages_fts JOIN scanned_pages p ON p.id = scanned_pages_fts.rowid
              WHERE scanned_pages_fts MATCH ${ftsQuery(input.query)}
            ) s
          ) m
          JOIN scanned_documents d ON d.id = m.document_id
          WHERE m.rn = 1 ${filters[0]} ${filters[1]}
          ORDER BY m.best
          LIMIT ${input.limit} OFFSET ${input.offset}`);
        return { hits, error: null };
      } catch (error) {
        return { hits: [] as DocumentHit[], error: (error as Error).message };
      }
    }),

  get: publicProcedure.input(z.object({ id: z.number().int() })).query(async ({ ctx, input }) => {
    const document = await ctx.db.query.scannedDocuments.findFirst({
      where: eq(scannedDocuments.id, input.id),
    });
    if (!document) {
      return null;
    }
    const pages = await ctx.db
      .select({ pageNumber: scannedPages.pageNumber, text: scannedPages.text, source: scannedPages.source })
      .from(scannedPages)
      .where(eq(scannedPages.documentId, input.id))
      .orderBy(asc(scannedPages.pageNumber));
    const links = await ctx.db
      .select({
        linkId: expenseDocuments.id,
        status: expenseDocuments.status,
        method: expenseDocuments.method,
        score: expenseDocuments.score,
        reasons: expenseDocuments.reasons,
        expenseId: expenses.id,
        date: expenses.date,
        merchant: expenses.merchant,
        amount: expenses.amount,
        card: expenses.card,
        spender: expenses.spender,
      })
      .from(expenseDocuments)
      .innerJoin(expenses, eq(expenses.id, expenseDocuments.expenseId))
      .where(eq(expenseDocuments.documentId, input.id));
    return { document, pages, links };
  }),

  /** Re-read new or changed files in OneDrive (runs in the background). */
  reindex: publicProcedure.mutation(async () => {
    const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
    const pid = await startScansJob(["index-text"], path.join(SCANS_DIR, "jobs"), `index_${stamp}`);
    return { started: true, pid };
  }),
});
