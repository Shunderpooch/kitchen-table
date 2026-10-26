import { z } from "zod";
import { createTRPCRouter, publicProcedure } from "~/server/api/trpc";
import { categories, subtypes } from "~/server/db/schema";
import { eq, asc, and } from "drizzle-orm";

export const categoriesRouter = createTRPCRouter({
  // Get all categories with subtypes
  getAll: publicProcedure.query(async ({ ctx }) => {
    return ctx.db.query.categories.findMany({
      orderBy: [asc(categories.name)],
      with: {
        subtypes: true,
      },
    });
  }),

  // Get category by ID
  getById: publicProcedure
    .input(z.object({ id: z.number() }))
    .query(async ({ ctx, input }) => {
      return ctx.db.query.categories.findFirst({
        where: eq(categories.id, input.id),
        with: {
          subtypes: true,
        },
      });
    }),

  // Get subtypes for a category
  getSubtypes: publicProcedure
    .input(z.object({ categoryId: z.number() }))
    .query(async ({ ctx, input }) => {
      return ctx.db.query.subtypes.findMany({
        where: eq(subtypes.categoryId, input.categoryId),
        orderBy: [asc(subtypes.name)],
      });
    }),

  // Create a new category
  create: publicProcedure
    .input(
      z.object({
        name: z.string().trim().min(1).max(255),
        color: z.string().optional(),
        parentId: z.number().optional(),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const existing = await ctx.db.query.categories.findFirst({
        where: eq(categories.name, input.name),
      });

      if (existing) {
        return existing;
      }

      const result = await ctx.db
        .insert(categories)
        .values(input)
        .returning();
      const created = Array.isArray(result) ? result[0] : undefined;

      if (!created) {
        throw new Error("Could not create category.");
      }

      return created;
    }),

  // Create a new subtype
  createSubtype: publicProcedure
    .input(
      z.object({
        categoryId: z.number(),
        name: z.string().trim().min(1).max(255),
        keywords: z.string().optional(),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const existing = await ctx.db.query.subtypes.findFirst({
        where: and(
          eq(subtypes.categoryId, input.categoryId),
          eq(subtypes.name, input.name),
        ),
      });

      if (existing) {
        return existing;
      }

      const result = await ctx.db
        .insert(subtypes)
        .values(input)
        .returning();
      const created = Array.isArray(result) ? result[0] : undefined;

      if (!created) {
        throw new Error("Could not create subtype.");
      }

      return created;
    }),
});
