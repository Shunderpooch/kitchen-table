import { drizzle } from "drizzle-orm/better-sqlite3";
import Database from "better-sqlite3";
import * as schema from "./db/schema";
import { env } from "~/env";
import path from "path";

const dbPath = env.DATABASE_URL.replace("file:", "");
const absolutePath = path.resolve(process.cwd(), dbPath);

const sqlite = new Database(absolutePath);
// Databases from before household configuration kept each person's share in
// "<firstname>_spend": rename to member_a_spend / member_b_spend by the order
// of HOUSEHOLD_MEMBERS (the backend does the same, whichever runs first).
{
  const columns = new Set(
    (sqlite.prepare("PRAGMA table_info(expenses)").all() as { name: string }[]).map((c) => c.name),
  );
  if (columns.size > 0 && !columns.has("member_a_spend")) {
    const names = (process.env.HOUSEHOLD_MEMBERS ?? "").split(",").map((n) => n.trim()).filter(Boolean);
    names.slice(0, 2).forEach((name, index) => {
      const legacy = `${name.toLowerCase()}_spend`;
      if (columns.has(legacy)) {
        try {
          sqlite.exec(`ALTER TABLE expenses RENAME COLUMN "${legacy}" TO member_${index === 0 ? "a" : "b"}_spend`);
        } catch {
          // Another process (a build worker, the backend) renamed it first.
        }
      }
    });
  }
}
for (const statement of [
  "ALTER TABLE expenses ADD COLUMN is_approved INTEGER DEFAULT 0",
  "ALTER TABLE expenses ADD COLUMN approved_at INTEGER",
  "ALTER TABLE merchant_mappings ADD COLUMN canonical_merchant TEXT",
  "ALTER TABLE statement_sources ADD COLUMN owner TEXT",
]) {
  try {
    sqlite.exec(statement);
  } catch {
    // Column already exists in the live DB.
  }
}
sqlite.exec(
  "CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)",
);
sqlite.exec(`
  CREATE TABLE IF NOT EXISTS friends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at INTEGER DEFAULT (unixepoch())
  )
`);
sqlite.exec(`
  CREATE TABLE IF NOT EXISTS expense_friend_shares (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_id INTEGER NOT NULL REFERENCES expenses(id),
    friend_id INTEGER NOT NULL REFERENCES friends(id),
    amount REAL NOT NULL
  )
`);
// Receipts whose own expense was deleted: the scans tools do not make them again.
sqlite.exec(`
  CREATE TABLE IF NOT EXISTS dismissed_receipts (
    document_id INTEGER PRIMARY KEY,
    reason TEXT,
    dismissed_at INTEGER DEFAULT (unixepoch())
  )
`);
// People who share some expenses (FRIENDS in frontend/.env, comma-separated).
for (const friendName of (process.env.FRIENDS ?? "").split(",").map((n) => n.trim()).filter(Boolean)) {
  sqlite
    .prepare("INSERT OR IGNORE INTO friends (name) VALUES (?)")
    .run(friendName);
}
const approvalBackfill = sqlite
  .prepare("SELECT value FROM app_meta WHERE key = ?")
  .get("approval_backfill_v1");
if (!approvalBackfill) {
  sqlite.exec("UPDATE expenses SET is_approved = 1, approved_at = NULL");
  sqlite
    .prepare("INSERT INTO app_meta (key, value) VALUES (?, ?)")
    .run("approval_backfill_v1", new Date().toISOString());
}
sqlite.exec("UPDATE expenses SET is_approved = 0, approved_at = NULL WHERE needs_validation = 1");
export const db = drizzle(sqlite, { schema });

export type DB = typeof db;
