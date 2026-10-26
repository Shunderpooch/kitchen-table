import { expensesRouter } from "~/server/api/routers/expenses";
import { validationRouter } from "~/server/api/routers/validation";
import { categoriesRouter } from "~/server/api/routers/categories";
import { friendsRouter } from "~/server/api/routers/friends";
import { statementSourcesRouter } from "~/server/api/routers/statementSources";
import { documentsRouter } from "~/server/api/routers/documents";
import { receiptsRouter } from "~/server/api/routers/receipts";
import { labelAuditRouter } from "~/server/api/routers/labelAudit";
import { scansRouter } from "~/server/api/routers/scans";
import { createCallerFactory, createTRPCRouter } from "~/server/api/trpc";

/**
 * This is the primary router for your server.
 *
 * All routers added in /api/routers should be manually added here.
 */
export const appRouter = createTRPCRouter({
  expenses: expensesRouter,
  validation: validationRouter,
  categories: categoriesRouter,
  friends: friendsRouter,
  statementSources: statementSourcesRouter,
  documents: documentsRouter,
  receipts: receiptsRouter,
  labelAudit: labelAuditRouter,
  scans: scansRouter,
});

// export type definition of API
export type AppRouter = typeof appRouter;

/**
 * Create a server-side caller for the tRPC API.
 * @example
 * const trpc = createCaller(createContext);
 * const res = await trpc.expenses.getAll();
 */
export const createCaller = createCallerFactory(appRouter);
