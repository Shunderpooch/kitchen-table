"use client";

import Link from "next/link";
import { useState } from "react";
import { api } from "~/trpc/react";

/** Receipts for one expense: confirmed and suggested matches, and nearby receipts to link by hand. */
export function ExpenseReceipts({
  expenseId,
  onLinked,
}: {
  expenseId: number;
  onLinked?: (fileName: string) => void;
}) {
  const utils = api.useUtils();
  const [picking, setPicking] = useState(false);
  const links = api.receipts.forExpense.useQuery({ expenseId });
  const candidates = api.receipts.candidates.useQuery({ expenseId }, { enabled: picking });
  const refresh = async () => {
    await Promise.all([
      utils.receipts.forExpense.invalidate({ expenseId }),
      utils.receipts.summaryForExpenses.invalidate(),
      utils.documents.status.invalidate(),
    ]);
  };
  const decide = api.receipts.decide.useMutation({ onSuccess: refresh });
  const link = api.receipts.link.useMutation({
    onSuccess: async () => {
      setPicking(false);
      await refresh();
    },
  });

  const rows = (links.data ?? []).filter((row) => row.status !== "rejected");
  return (
    <div className="rounded-lg border border-zinc-800 p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-medium text-zinc-200">Receipt</span>
        <button
          type="button"
          onClick={() => setPicking((v) => !v)}
          className="text-xs text-zinc-400 hover:text-zinc-200"
        >
          {picking ? "Cancel" : "Link a receipt"}
        </button>
      </div>
      {rows.length === 0 && !picking && <p className="text-xs text-zinc-500">No receipt linked or suggested</p>}
      <ul className="space-y-1">
        {rows.map((row) => (
          <li key={row.id} className="flex items-center justify-between gap-2 text-xs">
            <Link href={`/documents?id=${row.documentId}`} className="min-w-0 truncate text-sky-400 hover:underline">
              {row.path.split("/").pop()}
            </Link>
            {row.status === "confirmed" ? (
              <span className="shrink-0 text-emerald-400">linked</span>
            ) : (
              <span className="flex shrink-0 items-center gap-1">
                <span className="text-zinc-500" title={row.reasons ?? ""}>
                  suggested
                </span>
                <button
                  type="button"
                  onClick={() => {
                    decide.mutate({ ids: [row.id], status: "confirmed" });
                    onLinked?.(row.path.split("/").pop() ?? "");
                  }}
                  className="rounded bg-emerald-600/80 px-2 py-0.5 text-white"
                >
                  Confirm
                </button>
                <button
                  type="button"
                  onClick={() => decide.mutate({ ids: [row.id], status: "rejected" })}
                  className="rounded bg-zinc-800 px-2 py-0.5 text-zinc-300"
                >
                  No
                </button>
              </span>
            )}
          </li>
        ))}
      </ul>
      {picking && (
        <ul className="mt-2 max-h-56 space-y-1 overflow-auto border-t border-zinc-800 pt-2">
          {(candidates.data ?? []).map((doc) => (
            <li key={doc.id} className="flex items-center justify-between gap-2 text-xs">
              <Link href={`/documents?id=${doc.id}`} className="min-w-0 truncate text-zinc-300 hover:underline">
                {doc.path.split("/").pop()}
                {doc.ocrTotal != null && <span className="text-zinc-500"> · ${doc.ocrTotal.toFixed(2)}</span>}
              </Link>
              <button
                type="button"
                onClick={() => {
                  link.mutate({ expenseId, documentId: doc.id });
                  onLinked?.(doc.path.split("/").pop() ?? "");
                }}
                className="shrink-0 rounded bg-zinc-800 px-2 py-0.5 text-zinc-200 hover:bg-zinc-700"
              >
                Link
              </button>
            </li>
          ))}
          {candidates.data?.length === 0 && <li className="text-xs text-zinc-500">No receipts dated within a week</li>}
        </ul>
      )}
    </div>
  );
}
