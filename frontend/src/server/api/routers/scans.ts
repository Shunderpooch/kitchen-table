import { z } from "zod";
import { existsSync } from "fs";
import { mkdir, readdir, readFile, rm, writeFile } from "fs/promises";
import path from "path";
import { createTRPCRouter, publicProcedure } from "~/server/api/trpc";
import {
  batchDir,
  listBatches,
  PAUSE_FILE,
  runScansJson,
  SCANS_DIR,
  scanInbox,
  startScansJob,
} from "~/server/scans";

export interface ManifestRow {
  item_id: string;
  action: string;
  kind: string;
  person: string;
  category: string;
  car: string;
  date: string;
  time: string;
  merchant: string;
  total: string;
  filename: string;
  filename_override: string;
  destination: string;
  merge_into: string;
  reasons: string;
  pages: string;
  text_preview: string;
}

interface ApplyResult {
  error?: string;
  dry_run: boolean;
  filed: { item_id: string; target: string; pages: number }[];
  review: string[];
  skipped: string[];
  merged: string[];
  index_errors: string[];
  log: string | null;
}

const editSchema = z
  .object({
    item_id: z.string(),
    action: z.enum(["file", "review", "skip"]).optional(),
    kind: z.string().max(40).optional(),
    person: z.string().max(100).optional(),
    category: z.string().max(100).optional(),
    car: z.string().max(200).optional(),
    date: z.string().regex(/^(\d{4}-\d{2}-\d{2})?$/).optional(),
    time: z.string().regex(/^(\d{2}[-:]\d{2})?$/).optional(),
    merchant: z.string().max(200).optional(),
    filename_override: z.string().max(200).optional(),
    merge_into: z.string().max(10).optional(),
  })
  .strict();

function manifestPath(id: string) {
  const manifest = path.join(batchDir(id), "manifest.csv");
  if (!existsSync(manifest)) {
    throw new Error("This batch has no manifest yet.");
  }
  return manifest;
}

export const scansRouter = createTRPCRouter({
  conventions: publicProcedure.query(() =>
    runScansJson<{
      onedrive: string;
      categories: string[];
      people: { key: string; name: string }[];
      cars: string[];
      kinds: string[];
    }>(["conventions", "--json"]),
  ),

  defaults: publicProcedure.query(() => ({
    inbox: process.env.SCANS_INBOX ?? "",
  })),

  batches: publicProcedure.query(() => listBatches()),

  /** OCR and cut a ScanSmart inbox folder into a reviewable batch (runs in the background). */
  plan: publicProcedure
    .input(
      z.object({
        inbox: z.string().trim().min(1),
        person: z.string().default("self"),
        kind: z.string().optional(),
        duplex: z.boolean().default(false),
        separatorSheets: z.boolean().default(false),
        onePerPage: z.boolean().default(false),
      }),
    )
    .mutation(async ({ input }) => {
      if (!existsSync(input.inbox)) {
        throw new Error(`Folder not found: ${input.inbox}`);
      }
      const now = new Date();
      const pad = (n: number) => String(n).padStart(2, "0");
      const id = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(
        now.getMinutes(),
      )}${pad(now.getSeconds())}`;
      const dir = batchDir(id);
      const args = ["plan", input.inbox, "--out", path.join(dir, "manifest.csv"), "--person", input.person];
      if (input.kind) args.push("--kind", input.kind);
      if (input.duplex) args.push("--duplex");
      if (input.separatorSheets) args.push("--separator-sheets");
      if (input.onePerPage) args.push("--one-per-page");
      await startScansJob(args, dir, "plan");
      return { id };
    }),

  manifest: publicProcedure.input(z.object({ id: z.string() })).query(({ input }) =>
    runScansJson<{ rows: ManifestRow[]; targets: Record<string, string>; problems: string | null }>([
      "manifest-json",
      manifestPath(input.id),
    ]),
  ),

  updateManifest: publicProcedure
    .input(z.object({ id: z.string(), edits: z.array(editSchema).min(1).max(500) }))
    .mutation(({ input }) =>
      runScansJson<{ saved: number }>(["manifest-set", manifestPath(input.id), JSON.stringify(input.edits)]),
    ),

  /**
   * File the batch into OneDrive (dryRun first to see every target). Filing
   * a whole batch marks it filed and starts the background step: receipts
   * matched to statement rows, expenses made for the rest.
   */
  fileBatch: publicProcedure
    .input(z.object({ id: z.string(), dryRun: z.boolean() }))
    .mutation(async ({ input }) => {
      const args = ["apply", manifestPath(input.id), "--json"];
      if (input.dryRun) args.push("--dry-run");
      const result = await runScansJson<ApplyResult>(args, 600_000);
      if (result.error) {
        throw new Error(result.error);
      }
      if (!input.dryRun && result.filed.length > 0 && result.review.length === 0) {
        const dir = batchDir(input.id);
        await writeFile(
          path.join(dir, "filed.json"),
          JSON.stringify({ filedAt: new Date().toISOString(), filed: result.filed, log: result.log }, null, 2),
        );
        await startScansJob(["settle"], dir, "settle");
      }
      return result;
    }),

  /** Scan inbox: where to save scans, what is waiting, and the review batches. */
  inbox: publicProcedure.query(async () => {
    const inbox = scanInbox();
    const waiting: string[] = [];
    if (existsSync(inbox)) {
      for (const entry of await readdir(inbox, { withFileTypes: true })) {
        if (entry.isFile() && /\.(pdf|jpe?g|png|tiff?|bmp)$/i.test(entry.name)) waiting.push(entry.name);
        if (entry.isDirectory()) {
          for (const name of await readdir(path.join(inbox, entry.name))) {
            if (/\.(pdf|jpe?g|png|tiff?|bmp)$/i.test(name)) waiting.push(`${entry.name}/${name}`);
          }
        }
      }
    }
    const batches = (await listBatches()).filter((b) => b.status !== "filed" || b.origin === "inbox");
    return { inbox, waiting, paused: existsSync(PAUSE_FILE), batches };
  }),

  setPaused: publicProcedure.input(z.object({ paused: z.boolean() })).mutation(async ({ input }) => {
    await mkdir(path.dirname(PAUSE_FILE), { recursive: true });
    if (input.paused) {
      await writeFile(PAUSE_FILE, new Date().toISOString());
    } else if (existsSync(PAUSE_FILE)) {
      await rm(PAUSE_FILE);
    }
    return { paused: input.paused };
  }),

  /** Check the inbox now instead of waiting for the scheduled run. */
  checkInbox: publicProcedure.mutation(async () => {
    await startScansJob(["inbox-tick", "--settle-seconds", "20"], SCANS_DIR, "inbox");
    return { started: true };
  }),

  /** A batch with its page previews, for the review dashboard. */
  batch: publicProcedure.input(z.object({ id: z.string() })).query(async ({ input }) => {
    const dir = batchDir(input.id);
    const pagesFile = path.join(dir, "pages.json");
    const manifest = await runScansJson<{ rows: ManifestRow[]; targets: Record<string, string>; problems: string | null }>(
      ["manifest-json", manifestPath(input.id)],
    );
    const pages = existsSync(pagesFile)
      ? (JSON.parse(await readFile(pagesFile, "utf-8")) as BatchPage[])
      : null;
    const filed = existsSync(path.join(dir, "filed.json"));
    const settled = existsSync(path.join(dir, "settle.done"));
    return { ...manifest, pages, filed, settled };
  }),

  /** Name, date, and folder of documents re-cut in review, read from their own pages. */
  describe: publicProcedure
    .input(z.object({ id: z.string(), groups: z.array(z.array(z.string().max(10)).min(1)).min(1).max(200) }))
    .mutation(({ input }) =>
      runScansJson<Record<string, Partial<Record<"kind" | "category" | "car" | "date" | "time" | "merchant" | "total" | "reasons" | "text_preview", string>>>>(
        ["describe", manifestPath(input.id), JSON.stringify(input.groups)],
      ),
    ),

  /** Page previews for a batch planned before previews existed. */
  renderPages: publicProcedure.input(z.object({ id: z.string() })).mutation(async ({ input }) => {
    await startScansJob(["render-pages", manifestPath(input.id)], batchDir(input.id), "render");
    return { started: true };
  }),

  /** Cut an inbox batch again from its originals (after a failed run). */
  replan: publicProcedure.input(z.object({ id: z.string() })).mutation(async ({ input }) => {
    const dir = batchDir(input.id);
    if (!existsSync(path.join(dir, "batch.json"))) {
      throw new Error("Only inbox batches can be cut again from their originals.");
    }
    for (const name of ["plan.done", "plan.log"]) {
      if (existsSync(path.join(dir, name))) await rm(path.join(dir, name));
    }
    await startScansJob(["replan", dir], dir, "plan");
    return { started: true };
  }),
});

export interface BatchPage {
  n: number;
  item_id: string;
  ref: string;
  width: number;
  height: number;
}
