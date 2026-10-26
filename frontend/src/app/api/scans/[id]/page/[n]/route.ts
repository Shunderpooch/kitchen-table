import { NextResponse } from "next/server";
import { existsSync } from "fs";
import { readFile } from "fs/promises";
import path from "path";
import { batchDir } from "~/server/scans";

// A rendered page of a scan batch: /api/scans/<batch>/page/<n> (preview), ?thumb=1 (thumbnail).
export async function GET(request: Request, { params }: { params: Promise<{ id: string; n: string }> }) {
  const { id, n } = await params;
  if (!/^\d{1,4}$/.test(n)) {
    return NextResponse.json({ error: "Bad page" }, { status: 400 });
  }
  let dir: string;
  try {
    dir = batchDir(id);
  } catch {
    return NextResponse.json({ error: "No such batch" }, { status: 404 });
  }
  const thumb = new URL(request.url).searchParams.has("thumb");
  const file = path.join(dir, "pages", `${n.padStart(4, "0")}${thumb ? "_t" : ""}.jpg`);
  if (!existsSync(file)) {
    return NextResponse.json({ error: "Not rendered" }, { status: 404 });
  }
  return new Response(new Uint8Array(await readFile(file)), {
    headers: { "Content-Type": "image/jpeg", "Cache-Control": "private, max-age=3600" },
  });
}
