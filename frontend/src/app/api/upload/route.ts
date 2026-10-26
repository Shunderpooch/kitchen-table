import { MEMBER_A, MEMBER_B, memberSlot } from "~/lib/household";
import { NextRequest, NextResponse } from "next/server";
import { writeFile, mkdir } from "fs/promises";
import { existsSync } from "fs";
import path from "path";
import { spawn } from "child_process";
import { db } from "~/server/db";
import { statementSources } from "~/server/db/schema";
import { eq } from "drizzle-orm";

// Get the uploads directory (relative to project root)
const UPLOADS_DIR = path.resolve(process.cwd(), "..", "uploads");
const BACKEND_DIR = path.resolve(process.cwd(), "..", "backend");

function runPythonProcess(
  filepath: string,
  card?: string,
  spender?: string,
): Promise<{ stdout: string; stderr: string }> {
  const args = ["-m", "src.main", "process", filepath];
  if (card) {
    args.push("--card", card);
  }
  if (spender) {
    args.push("--spender", spender);
  }
  return runPython(args);
}

function runPython(args: string[]): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    const python = spawn(process.env.PYTHON_BIN ?? "python", args, {
      cwd: BACKEND_DIR,
      shell: false,
    });

    let stdout = "";
    let stderr = "";

    python.stdout.on("data", (data) => {
      stdout += data.toString();
    });

    python.stderr.on("data", (data) => {
      stderr += data.toString();
    });

    python.on("close", (code) => {
      if (code === 0) {
        resolve({ stdout, stderr });
      } else {
        const error = new Error(`Process exited with code ${code}`) as Error & { stdout: string; stderr: string };
        error.stdout = stdout;
        error.stderr = stderr;
        reject(error);
      }
    });

    python.on("error", (err) => {
      reject(err);
    });

    // Timeout after 2 minutes
    setTimeout(() => {
      python.kill();
      reject(new Error("Process timed out"));
    }, 120000);
  });
}

/** Sources are named "<member> <card>": the first word says whose card it is. */
function inferSourceOwner(sourceName: string) {
  return memberSlot(sourceName.trim().split(/\s+/)[0]) === "a"
    ? MEMBER_A
    : memberSlot(sourceName.trim().split(/\s+/)[0]) === "b"
      ? MEMBER_B
      : null;
}

async function markStatementSourceUpdated(sourceName: string) {
  const existing = await db.query.statementSources.findFirst({
    where: eq(statementSources.name, sourceName),
  });

  if (existing) {
    await db
      .update(statementSources)
      .set({ lastImportDate: new Date() })
      .where(eq(statementSources.id, existing.id));
    return;
  }

  await db.insert(statementSources).values({
    name: sourceName,
    sourceType: "manual",
    owner: inferSourceOwner(sourceName),
    expectedFrequencyDays: 30,
    lastImportDate: new Date(),
  });
}

function getDetectedSourceName(stdout: string, fallback?: string | null) {
  const detectedMatch = stdout.match(/Using card name:\s*(.+)/i);
  const detected = detectedMatch?.[1]?.trim();
  if (detected) {
    return detected;
  }

  return fallback?.trim() || null;
}

export async function POST(request: NextRequest) {
  try {
    const formData = await request.formData();
    const files = formData.getAll("files") as File[];
    const card = formData.get("card") as string | null;
    const spender = formData.get("spender") as string | null;
    const resolvedCard =
      card === "BofA" && spender
        ? `${spender} Bofa Visa`
        : card;

    if (files.length === 0) {
      return NextResponse.json(
        { error: "No files provided" },
        { status: 400 }
      );
    }

    // Ensure uploads directory exists
    if (!existsSync(UPLOADS_DIR)) {
      await mkdir(UPLOADS_DIR, { recursive: true });
    }

    const savedFiles: string[] = [];
    const savedPaths: string[] = [];

    // Save each file to the uploads directory
    for (const file of files) {
      const bytes = await file.arrayBuffer();
      const buffer = Buffer.from(bytes);

      // Create a unique filename to avoid conflicts
      const timestamp = Date.now();
      const filename = `${timestamp}_${file.name}`;
      const filepath = path.join(UPLOADS_DIR, filename);

      await writeFile(filepath, buffer);
      savedFiles.push(filename);
      savedPaths.push(filepath);
    }

    // Aggregate results from processing all files
    const processResult = {
      success: true,
      total: 0,
      categorized: 0,
      needsValidation: 0,
      duplicates: 0,
      paymentsOrCreditsSkipped: 0,
      errors: [] as string[],
    };

    // Process each file individually
    for (const filepath of savedPaths) {
      try {
        // Run the Python processing command for this file
        const { stdout, stderr } = await runPythonProcess(
          filepath,
          resolvedCard ?? undefined,
          spender ?? undefined,
        );

        console.log("Python stdout:", stdout);
        if (stderr) console.log("Python stderr:", stderr);

        // Parse the output to extract stats
        const totalMatch = stdout.match(/Processed (\d+) expenses/);
        const categorizedMatch = stdout.match(/Auto-categorized: (\d+)/);
        const validationMatch = stdout.match(/Needs validation: (\d+)/);
        const duplicatesMatch = stdout.match(/Duplicates found: (\d+)/);
        const paymentsOrCreditsMatch = stdout.match(/Payments\/credits skipped: (\d+)/);
        const unsupportedTypeMatch = stdout.match(/Unsupported file type:\s*(\.[a-z0-9]+)/i);
        const explicitFailureMatch = stdout.match(/\[red\](.+?)\[\/red\]/i);

        if (totalMatch) processResult.total += parseInt(totalMatch[1] ?? "0");
        if (categorizedMatch) processResult.categorized += parseInt(categorizedMatch[1] ?? "0");
        if (validationMatch) processResult.needsValidation += parseInt(validationMatch[1] ?? "0");
        if (duplicatesMatch) processResult.duplicates += parseInt(duplicatesMatch[1] ?? "0");
        if (paymentsOrCreditsMatch) {
          processResult.paymentsOrCreditsSkipped += parseInt(paymentsOrCreditsMatch[1] ?? "0");
        }
        if (unsupportedTypeMatch || explicitFailureMatch) {
          processResult.success = false;
          processResult.errors.push(
            `${path.basename(filepath)}: ${
              unsupportedTypeMatch
                ? `Unsupported file type ${unsupportedTypeMatch[1]}`
                : "Processing failed"
            }`,
          );
          continue;
        }

        if (stderr && stderr.trim()) {
          processResult.errors.push(stderr.trim());
        }

        const detectedSourceName = getDetectedSourceName(stdout, resolvedCard);
        if (detectedSourceName) {
          await markStatementSourceUpdated(detectedSourceName);
        }
      } catch (execError: unknown) {
        const error = execError as Error & { stdout?: string; stderr?: string };
        console.error("Python processing error:", error);
        console.error("stdout:", error.stdout);
        console.error("stderr:", error.stderr);
        processResult.success = false;
        processResult.errors.push(
          `${path.basename(filepath)}: ${error.message || "Failed to process"}`
        );

        // Still try to parse any output we got
        if (error.stdout) {
          const totalMatch = error.stdout.match(/Processed (\d+) expenses/);
          if (totalMatch) processResult.total += parseInt(totalMatch[1] ?? "0");
        }
      }
    }

    // New statement rows: link them to filed receipts. A staged expense made
    // from a receipt gives way to the statement row that matches it.
    let receiptMatching: string | null = null;
    if (processResult.total > 0) {
      try {
        const { stdout } = await runPython(["-m", "src.main", "scans", "match-receipts", "--confirm-above", "6"]);
        receiptMatching = stdout.match(/Linked by filename[\s\S]*/)?.[0]?.replace(/\s+/g, " ").trim() ?? null;
      } catch (matchError) {
        console.error("Receipt matching error:", matchError);
        processResult.errors.push("Statements imported, but matching them to receipts failed");
      }
    }

    return NextResponse.json({
      files: savedFiles,
      ...processResult,
      receiptMatching,
    });
  } catch (error) {
    console.error("Upload error:", error);
    return NextResponse.json(
      { error: "Failed to upload files" },
      { status: 500 }
    );
  }
}
