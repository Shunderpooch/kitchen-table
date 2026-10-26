import { z } from "zod";
import { asc, eq } from "drizzle-orm";
import { createTRPCRouter, publicProcedure } from "~/server/api/trpc";
import { friends } from "~/server/db/schema";

export const friendsRouter = createTRPCRouter({
  getAll: publicProcedure.query(async ({ ctx }) => {
    return ctx.db.query.friends.findMany({
      orderBy: [asc(friends.name)],
    });
  }),

  create: publicProcedure
    .input(
      z.object({
        name: z.string().trim().min(1).max(255),
      }),
    )
    .mutation(async ({ ctx, input }) => {
      const existing = await ctx.db.query.friends.findFirst({
        where: eq(friends.name, input.name),
      });

      if (existing) {
        return existing;
      }

      const result = await ctx.db.insert(friends).values(input).returning();
      const created = Array.isArray(result) ? result[0] : undefined;

      if (!created) {
        throw new Error("Could not create friend.");
      }

      return created;
    }),
});
