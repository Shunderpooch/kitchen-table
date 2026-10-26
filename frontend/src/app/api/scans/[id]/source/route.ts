import { NextResponse } from "next/server";
import { createReadStream, existsSync } from "fs";
import { readFile } from "fs/promises";
import path from "path";
import { Readable } from "stream";
import { batchDir } from "~/server/scans";

const TYPES: Record<string, string> = {
  ".pdf": "application/pdf",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png": "image/png",
  ".tif": "image/tiff",
  ".tiff": "image/tiff",
};

// Serves a raw scan from a batch's inbox for review: ?file=<path as written in the manifest>.
// Only files the batch's manifest refers to are served.
export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const file = new URL(request.url).searchParams.get("file") ?? "";
  let manifest: string;
  try {
    manifest = await readFile(path.join(batchDir(id), "manifest.csv"), "utf-8");
  } catch {
    return NextResponse.json({ error: "No such batch" }, { status: 404 });
  }
  // Page references look like "C:\inbox\Document_0001.pdf#1 | C:\inbox\Document_0002.pdf#1".
  const referenced = new Set(
    manifest
      .split(/[|\r\n",]/)
      .map((part) => part.trim().replace(/#\d+$/, ""))
      .filter((part) => path.isAbsolute(part)),
  );
  if (!file || !path.isAbsolute(file) || !referenced.has(file)) {
    return NextResponse.json({ error: "Not part of this batch" }, { status: 403 });
  }
  const type = TYPES[path.extname(file).toLowerCase()];
  if (!type || !existsSync(file)) {
    return NextResponse.json({ error: "Not available" }, { status: 404 });
  }
  return new Response(Readable.toWeb(createReadStream(file)) as ReadableStream, {
    headers: { "Content-Type": type, "Cache-Control": "private, max-age=60" },
  });
}
