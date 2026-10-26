import { z } from "zod";
import { and, asc, desc, eq, inArray, isNull, sql } from "drizzle-orm";
import { createTRPCRouter, publicProcedure } from "~/server/api/trpc";
import { labelFindings, scannedDocuments } from "~/server/db/schema";
import { runScans, runScansJson } from "~/server/scans";

type DB = typeof import("~/server/db").db;

function auditAvailable(db: DB) {
  const row = db.get<{ n: number }>(
    sql`SELECT count(*) AS n FROM sqlite_master WHERE type = 'table' AND name = 'label_findings'`,
  );
  return (row?.n ?? 0) > 0;
}

export interface FixPlan {
  applied: boolean;
  renames: { from: string; to: string; checks: string[] }[];
  skipped: { from: string; problem: string }[];
  log: string | null;
}

export const labelAuditRouter = createTRPCRouter({
  summary: publicProcedure.query(({ ctx }) => {
    if (!auditAvailable(ctx.db)) {
      return [];
    }
    return ctx.db.all<{ check: string; total: number; open: number; fix: number; fixed: number; keep: number }>(sql`
      SELECT "check" AS "check", count(*) AS total,
             coalesce(sum(user_decision IS NULL OR user_decision = 'later'), 0) AS open,
             coalesce(sum(user_decision = 'fix'), 0) AS fix,
             coalesce(sum(user_decision = 'fixed'), 0) AS fixed,
             coalesce(sum(user_decision = 'keep'), 0) AS keep
      FROM label_findings GROUP BY "check" ORDER BY "check"`);
  }),

  list: publicProcedure
    .input(
      z.object({
        check: z.string().optional(),
        decision: z.enum(["open", "fix", "fixed", "keep", "all"]).default("open"),
        limit: z.number().int().min(1).max(500).default(100),
        offset: z.number().int().min(0).default(0),
      }),
    )
    .query(async ({ ctx, input }) => {
      if (!auditAvailable(ctx.db)) {
        return [];
      }
      const conditions = [
        input.check ? eq(labelFindings.check, input.check) : undefined,
        input.decision === "open"
          ? sql`(${labelFindings.userDecision} IS NULL OR ${labelFindings.userDecision} = 'later')`
          : input.decision === "all"
            ? undefined
            : eq(labelFindings.userDecision, input.decision),
      ].filter(Boolean);
      return ctx.db
        .select({
          finding: labelFindings,
          documentId: scannedDocuments.id,
        })
        .from(labelFindings)
        .leftJoin(scannedDocuments, eq(scannedDocuments.path, labelFindings.path))
        .where(conditions.length ? and(...conditions) : undefined)
        .orderBy(asc(labelFindings.check), desc(labelFindings.strength), asc(labelFindings.path))
        .limit(input.limit)
        .offset(input.offset);
    }),

  decide: publicProcedure
    .input(
      z.object({
        ids: z.array(z.number().int()).min(1).max(500),
        decision: z.enum(["fix", "keep", "later"]),
        value: z.string().trim().max(500).optional(),
        note: z.string().trim().max(1000).optional(),
      }),
    )
    .mutation(async ({ ctx, input }) => {
      const changes: Partial<typeof labelFindings.$inferInsert> = { userDecision: input.decision };
      if (input.value) changes.suggestion = input.value;
      if (input.note) changes.reviewerNote = input.note;
      await ctx.db.update(labelFindings).set(changes).where(inArray(labelFindings.id, input.ids));
      return { updated: input.ids.length };
    }),

  undecided: publicProcedure.query(async ({ ctx }) => {
    if (!auditAvailable(ctx.db)) return 0;
    const rows = await ctx.db.select({ id: labelFindings.id }).from(labelFindings).where(isNull(labelFindings.userDecision));
    return rows.length;
  }),

  /** What `fix-labels` would rename now (nothing is changed). */
  fixPlan: publicProcedure.query(() => runScansJson<FixPlan>(["fix-labels", "--json"], 300_000)),

  applyFixes: publicProcedure.mutation(() => runScansJson<FixPlan>(["fix-labels", "--apply", "--json"], 600_000)),

  /** Re-scan names, folders, and text for new findings (keeps earlier decisions). */
  rerun: publicProcedure.mutation(async () => {
    const { stdout, code, stderr } = await runScans(["audit-labels"], 900_000);
    if (code !== 0) {
      throw new Error(stderr.trim().split(/\r?\n/).pop() ?? "Audit failed");
    }
    return { output: stdout.trim() };
  }),
});
