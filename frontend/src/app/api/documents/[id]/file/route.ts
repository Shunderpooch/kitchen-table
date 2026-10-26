import { NextResponse } from "next/server";
import { createReadStream, existsSync, statSync } from "fs";
import { Readable } from "stream";
import { eq } from "drizzle-orm";
import { db } from "~/server/db";
import { scannedDocuments } from "~/server/db/schema";
import { resolveInOneDrive } from "~/server/scans";

// Streams a filed document from OneDrive for the in-app viewer.
// Only paths recorded in scanned_documents are served.
export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const document = await db.query.scannedDocuments.findFirst({
    where: eq(scannedDocuments.id, Number(id)),
  });
  if (!document) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }
  let fullPath: string;
  try {
    fullPath = resolveInOneDrive(document.path);
  } catch {
    return NextResponse.json({ error: "Not available" }, { status: 400 });
  }
  if (!existsSync(fullPath)) {
    return NextResponse.json({ error: "File is no longer at this path; re-index documents." }, { status: 404 });
  }
  const stream = Readable.toWeb(createReadStream(fullPath)) as ReadableStream;
  return new Response(stream, {
    headers: {
      "Content-Type": "application/pdf",
      "Content-Length": String(statSync(fullPath).size),
      "Content-Disposition": `inline; filename="${encodeURIComponent(document.path.split("/").pop() ?? "document.pdf")}"`,
      "Cache-Control": "private, max-age=60",
    },
  });
}
