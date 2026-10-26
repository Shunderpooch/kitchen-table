import { MEMBER_ENUM } from "~/lib/household";
import { z } from "zod";
import { createTRPCRouter, publicProcedure } from "~/server/api/trpc";
import { statementSources } from "~/server/db/schema";
import { eq, asc } from "drizzle-orm";

function toDate(value: unknown) {
  if (value === null || value === undefined) {
    return null;
  }

  if (value instanceof Date) {
    return value;
  }

  if (typeof value === "number") {
    return new Date(value * 1000);
  }

  if (typeof value === "string") {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  return null;
}

export const statementSourcesRouter = createTRPCRouter({
  // Get all statement sources with staleness info
  getAll: publicProcedure.query(async ({ ctx }) => {
    const sources = await ctx.db.query.statementSources.findMany({
      orderBy: [asc(statementSources.name)],
    });
    const now = new Date();
    const latestFiles = ctx.db.$client
      .prepare(
        `
          select
            e.card as source_name,
            sf.filename as latest_filename,
            sf.processed_at as latest_processed_at
          from expenses e
          inner join source_files sf on sf.id = e.source_file_id
          inner join (
            select
              e2.card as source_name,
              max(sf2.processed_at) as max_processed_at
            from expenses e2
            inner join source_files sf2 on sf2.id = e2.source_file_id
            where e2.card is not null
            group by e2.card
          ) latest
            on latest.source_name = e.card
           and latest.max_processed_at = sf.processed_at
          where e.card is not null
          group by e.card, sf.filename, sf.processed_at
        `,
      )
      .all() as Array<{
        source_name: string;
        latest_filename: string;
        latest_processed_at: number | string | null;
      }>;

    const latestFileBySource = new Map(
      latestFiles.map((row) => [
        row.source_name,
        {
          latestFilename: row.latest_filename,
          latestProcessedAt: toDate(row.latest_processed_at),
        },
      ]),
    );

    return sources.map((source) => {
      const latestFile = latestFileBySource.get(source.name);
      const effectiveLastImportDate = source.lastImportDate ?? latestFile?.latestProcessedAt ?? null;
      const daysSinceUpdate = effectiveLastImportDate
        ? Math.floor(
            (now.getTime() - new Date(effectiveLastImportDate).getTime()) /
              (1000 * 60 * 60 * 24)
          )
        : null;

      const isStale =
        daysSinceUpdate === null ||
        daysSinceUpdate > (source.expectedFrequencyDays ?? 30);

      return {
        ...source,
        lastImportDate: effectiveLastImportDate,
        daysSinceUpdate,
        isStale,
        latestFilename: latestFile?.latestFilename ?? null,
        latestProcessedAt: latestFile?.latestProcessedAt ?? null,
      };
    });
  }),

  // Get stale sources only
  getStale: publicProcedure.query(async ({ ctx }) => {
    const sources = await ctx.db.query.statementSources.findMany({
      orderBy: [asc(statementSources.name)],
    });

    const now = new Date();

    const latestFiles = ctx.db.$client
      .prepare(
        `
          select
            e.card as source_name,
            max(sf.processed_at) as latest_processed_at
          from expenses e
          inner join source_files sf on sf.id = e.source_file_id
          where e.card is not null
          group by e.card
        `,
      )
      .all() as Array<{
        source_name: string;
        latest_processed_at: number | string | null;
      }>;

    const latestFileBySource = new Map(
      latestFiles.map((row) => [row.source_name, toDate(row.latest_processed_at)]),
    );

    return sources
      .map((source) => {
        const effectiveLastImportDate = source.lastImportDate ?? latestFileBySource.get(source.name) ?? null;
        const daysSinceUpdate = effectiveLastImportDate
          ? Math.floor(
              (now.getTime() - new Date(effectiveLastImportDate).getTime()) /
                (1000 * 60 * 60 * 24)
            )
          : null;

      const isStale =
        daysSinceUpdate === null ||
        daysSinceUpdate > (source.expectedFrequencyDays ?? 30);

        return {
          ...source,
          lastImportDate: effectiveLastImportDate,
          daysSinceUpdate,
          isStale,
        };
      })
      .filter((source) => source.isStale);
  }),

  // Update last import date for a source
  markUpdated: publicProcedure
    .input(z.object({ id: z.number() }))
    .mutation(async ({ ctx, input }) => {
      const updated = await ctx.db
        .update(statementSources)
        .set({ lastImportDate: new Date() })
        .where(eq(statementSources.id, input.id))
        .returning();

      return updated[0];
    }),

  // Create a new statement source
  create: publicProcedure
    .input(
      z.object({
        name: z.string().min(1),
        sourceType: z.string().optional(),
        owner: z.enum([...MEMBER_ENUM, "Shared"]).optional(),
        expectedFrequencyDays: z.number().default(30),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const inserted = await ctx.db
        .insert(statementSources)
        .values({
          name: input.name,
          sourceType: input.sourceType,
          owner: input.owner,
          expectedFrequencyDays: input.expectedFrequencyDays,
        })
        .returning();

      return inserted[0];
    }),
});
