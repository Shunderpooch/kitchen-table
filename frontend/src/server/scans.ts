import "server-only";

import { spawn } from "child_process";
import { createWriteStream, existsSync } from "fs";
import { mkdir, readdir, readFile, stat } from "fs/promises";
import path from "path";

// The Python backend shares the database; `scans` commands do the OCR,
// naming, filing, and renaming. The app runs them the same way upload does.
export const REPO_ROOT = path.resolve(process.cwd(), "..");
const BACKEND_DIR = path.join(REPO_ROOT, "backend");
export const SCANS_DIR = path.join(REPO_ROOT, "data", "scans");
const PYTHON_BIN = process.env.PYTHON_BIN ?? "python";

export function oneDriveRoot() {
  const root = process.env.ONEDRIVE_ROOT ?? process.env.OneDrive;
  if (!root) {
    throw new Error("Set ONEDRIVE_ROOT (or the OneDrive environment variable) to the OneDrive folder.");
  }
  return path.resolve(root);
}

/** Absolute path of a document path stored relative to OneDrive; refuses anything outside it. */
export function resolveInOneDrive(relativePath: string) {
  const root = oneDriveRoot();
  const full = path.resolve(root, relativePath);
  if (full !== root && !full.startsWith(root + path.sep)) {
    throw new Error("Path is outside OneDrive");
  }
  return full;
}

function pythonEnv() {
  return { ...process.env, PYTHONIOENCODING: "utf-8", NO_COLOR: "1" };
}

export function runScans(
  args: string[],
  timeoutMs = 120_000,
): Promise<{ stdout: string; stderr: string; code: number }> {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON_BIN, ["-m", "src.main", "scans", ...args], {
      cwd: BACKEND_DIR,
      env: pythonEnv(),
      shell: false,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (data) => (stdout += data.toString()));
    child.stderr.on("data", (data) => (stderr += data.toString()));
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`scans ${args[0]} timed out`));
    }, timeoutMs);
    child.on("error", (err) => {
      clearTimeout(timer);
      reject(err);
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({ stdout, stderr, code: code ?? 1 });
    });
  });
}

/** JSON printed on the last line of a scans command. */
export async function runScansJson<T>(args: string[], timeoutMs?: number): Promise<T> {
  const { stdout, stderr, code } = await runScans(args, timeoutMs);
  const lastLine = stdout.trim().split(/\r?\n/).pop() ?? "";
  if (code !== 0) {
    throw new Error(stderr.trim().split(/\r?\n/).pop() ?? `scans ${args[0]} failed`);
  }
  return JSON.parse(lastLine) as T;
}

/**
 * Start a long command (OCR of a scan batch, text indexing) in the background.
 * Output goes to `<dir>/<name>.log`; `<name>.done` holds the exit code when finished.
 */
export async function startScansJob(args: string[], dir: string, name: string) {
  await mkdir(dir, { recursive: true });
  const log = createWriteStream(path.join(dir, `${name}.log`), { flags: "a" });
  const child = spawn(PYTHON_BIN, ["-m", "src.main", "scans", ...args], {
    cwd: BACKEND_DIR,
    env: pythonEnv(),
    shell: false,
  });
  child.stdout.pipe(log);
  child.stderr.pipe(log);
  child.on("close", (code) => {
    void import("fs/promises").then((fs) => fs.writeFile(path.join(dir, `${name}.done`), String(code ?? 1)));
  });
  return child.pid;
}

export type BatchStatus = "planning" | "ready" | "failed" | "filed";

export interface Batch {
  id: string;
  manifest: string;
  status: BatchStatus;
  createdAt: Date;
  log: string;
  applyLogs: string[];
  /** "inbox" when the batch came from the scan inbox. */
  origin: string | null;
  person: string | null;
  pagesReady: boolean;
}

/** OneDrive/Scans/Inbox, where ScanSmart on every computer saves. */
export function scanInbox() {
  return path.join(oneDriveRoot(), "Scans", "Inbox"); // the `scans inbox-tick` default
}

export const PAUSE_FILE = path.join(SCANS_DIR, "PAUSED");

export function batchDir(id: string) {
  if (!/^[0-9_]+$/.test(id)) {
    throw new Error("Bad batch id");
  }
  return path.join(SCANS_DIR, id);
}

export async function listBatches(): Promise<Batch[]> {
  if (!existsSync(SCANS_DIR)) {
    return [];
  }
  const batches: Batch[] = [];
  for (const entry of await readdir(SCANS_DIR, { withFileTypes: true })) {
    if (!entry.isDirectory() || !/^[0-9_]+$/.test(entry.name)) {
      continue;
    }
    const dir = path.join(SCANS_DIR, entry.name);
    const manifest = path.join(dir, "manifest.csv");
    const done = path.join(dir, "plan.done");
    const logPath = path.join(dir, "plan.log");
    const log = existsSync(logPath) ? await readFile(logPath, "utf-8") : "";
    let status: BatchStatus = "planning";
    if (existsSync(done)) {
      status = (await readFile(done, "utf-8")).trim() === "0" && existsSync(manifest) ? "ready" : "failed";
    } else if (existsSync(manifest) && !existsSync(logPath)) {
      status = "ready"; // planned from the command line
    }
    if (existsSync(path.join(dir, "filed.json"))) {
      status = "filed";
    }
    const files = await readdir(dir);
    let info: { origin?: string; person?: string } = {};
    try {
      info = JSON.parse(await readFile(path.join(dir, "batch.json"), "utf-8")) as typeof info;
    } catch {
      // Planned on the Scans page or the command line.
    }
    batches.push({
      id: entry.name,
      manifest,
      status,
      createdAt: (await stat(dir)).birthtime,
      log: log.slice(-4000),
      applyLogs: files.filter((f) => f.startsWith("apply_log_")).sort(),
      origin: info.origin ?? null,
      person: info.person ?? null,
      pagesReady: files.includes("pages.json"),
    });
  }
  return batches.sort((a, b) => b.id.localeCompare(a.id));
}
