"use client";

import { useState } from "react";
import { api } from "~/trpc/react";

/** A filed document: the PDF itself, where it is filed, its expenses, and its text. */
export function DocumentViewer({ id, onClose }: { id: number; onClose?: () => void }) {
  const utils = api.useUtils();
  const [showText, setShowText] = useState(false);
  const detail = api.documents.get.useQuery({ id });
  const decide = api.receipts.decide.useMutation({
    onSuccess: async () => {
      await Promise.all([utils.documents.get.invalidate({ id }), utils.receipts.invalidate(), utils.documents.status.invalidate()]);
    },
  });

  if (detail.isLoading) {
    return <div className="rounded-lg border border-zinc-800 p-6 text-sm text-zinc-500">Loading…</div>;
  }
  if (!detail.data) {
    return <div className="rounded-lg border border-zinc-800 p-6 text-sm text-zinc-500">Document not found</div>;
  }
  const { document, pages, links } = detail.data;
  const name = document.path.split("/").pop();

  return (
    <div className="space-y-3 rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate font-medium text-zinc-100">{name}</div>
          <div className="truncate text-xs text-zinc-500">{document.path.split("/").slice(0, -1).join(" / ")}</div>
          <div className="mt-1 flex flex-wrap gap-3 text-xs text-zinc-400">
            {document.docDate && (
              <span>
                {document.docDate} {document.docTime ?? ""}
              </span>
            )}
            {document.ocrTotal != null && <span>total read ${document.ocrTotal.toFixed(2)}</span>}
            <span>
              {document.pageCount} page{document.pageCount === 1 ? "" : "s"}
            </span>
            {document.person && <span>for {document.person}</span>}
          </div>
        </div>
        <div className="flex shrink-0 gap-2">
          <a
            href={`/api/documents/${document.id}/file`}
            target="_blank"
            rel="noreferrer"
            className="rounded bg-zinc-800 px-2 py-1 text-xs text-zinc-300 hover:bg-zinc-700"
          >
            Open
          </a>
          {onClose && (
            <button onClick={onClose} className="rounded bg-zinc-800 px-2 py-1 text-xs text-zinc-300 hover:bg-zinc-700">
              Close
            </button>
          )}
        </div>
      </div>

      <iframe
        title={name}
        src={`/api/documents/${document.id}/file#view=FitH`}
        className="h-[60vh] w-full rounded border border-zinc-800 bg-white"
      />

      <div>
        <div className="mb-1 text-xs font-medium uppercase tracking-wide text-zinc-500">Expenses</div>
        {links.length === 0 && <p className="text-xs text-zinc-500">Not linked to an expense yet</p>}
        <ul className="space-y-1">
          {links.map((link) => (
            <li key={link.linkId} className="flex items-center justify-between gap-2 text-sm">
              <span className="min-w-0 truncate text-zinc-300">
                {link.date} · {link.merchant} · ${link.amount.toFixed(2)}{" "}
                <span className="text-xs text-zinc-500">
                  {link.card} · {link.status}
                </span>
              </span>
              {link.status === "suggested" && (
                <span className="flex shrink-0 gap-1">
                  <button
                    onClick={() => decide.mutate({ ids: [link.linkId], status: "confirmed" })}
                    className="rounded bg-emerald-600/80 px-2 py-0.5 text-xs text-white"
                  >
                    Confirm
                  </button>
                  <button
                    onClick={() => decide.mutate({ ids: [link.linkId], status: "rejected" })}
                    className="rounded bg-zinc-800 px-2 py-0.5 text-xs text-zinc-300"
                  >
                    No
                  </button>
                </span>
              )}
            </li>
          ))}
        </ul>
      </div>

      <div>
        <button onClick={() => setShowText((v) => !v)} className="text-xs text-zinc-400 hover:text-zinc-200">
          {showText ? "Hide" : "Show"} text read from the document
        </button>
        {showText && (
          <div className="mt-2 max-h-72 space-y-3 overflow-auto rounded border border-zinc-800 p-2">
            {pages.map((page) => (
              <div key={page.pageNumber}>
                <div className="text-[11px] text-zinc-500">
                  page {page.pageNumber} · {page.source}
                </div>
                <pre className="whitespace-pre-wrap text-xs text-zinc-300">{page.text || "(no text)"}</pre>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
