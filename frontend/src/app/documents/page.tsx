"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Navigation } from "~/app/_components/navigation";
import { DocumentViewer } from "~/app/_components/document-viewer";
import { api } from "~/trpc/react";
import type { RouterOutputs } from "~/trpc/react";

const TABS = [
  { key: "search", label: "Search" },
  { key: "matches", label: "Receipt matches" },
  { key: "audit", label: "Label audit" },
] as const;
type Tab = (typeof TABS)[number]["key"];

export default function DocumentsPage() {
  return (
    <div className="min-h-screen bg-zinc-950">
      <Navigation />
      <main className="container mx-auto px-4 py-8">
        <Suspense>
          <DocumentsContent />
        </Suspense>
      </main>
    </div>
  );
}

function DocumentsContent() {
  const params = useSearchParams();
  const router = useRouter();
  const tab = (params.get("tab") as Tab | null) ?? "search";
  const openId = params.get("id") ? Number(params.get("id")) : null;
  const status = api.documents.status.useQuery();

  const setParams = (next: Record<string, string | null>) => {
    const merged = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(next)) {
      if (value === null) merged.delete(key);
      else merged.set(key, value);
    }
    router.replace(`/documents?${merged.toString()}`);
  };

  return (
    <>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-zinc-100">Documents</h1>
          <p className="text-zinc-400">
            Every filed receipt, manual, and car document in OneDrive, searchable by its text
          </p>
        </div>
        {status.data?.available && (
          <div className="flex gap-4 text-sm text-zinc-400">
            <span>{status.data.documents.toLocaleString()} documents</span>
            <span>{status.data.pages.toLocaleString()} pages</span>
            <span>{status.data.linked.toLocaleString()} linked to expenses</span>
            <span className="text-amber-400">{status.data.suggested.toLocaleString()} matches to review</span>
          </div>
        )}
      </div>

      {status.data && !status.data.available && (
        <div className="mb-6 rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-200">
          No document text yet. Run <code>python -m backend.src.main scans index-text</code> once to read
          every filed document; after that, filing scans keeps it up to date.
        </div>
      )}

      <div className="mb-6 flex gap-1 border-b border-zinc-800">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setParams({ tab: t.key })}
            className={`px-4 py-2 text-sm font-medium ${
              tab === t.key ? "border-b-2 border-zinc-100 text-zinc-100" : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div>
          {tab === "search" && <SearchPanel openId={openId} onOpen={(id) => setParams({ id: String(id) })} />}
          {tab === "matches" && <MatchesPanel onOpen={(id) => setParams({ id: String(id) })} />}
          {tab === "audit" && <AuditPanel onOpen={(id) => setParams({ id: String(id) })} />}
        </div>
        <div className="lg:sticky lg:top-20 lg:self-start">
          {openId ? (
            <DocumentViewer id={openId} onClose={() => setParams({ id: null })} />
          ) : (
            <div className="rounded-lg border border-dashed border-zinc-800 p-10 text-center text-sm text-zinc-500">
              Pick a document to see it here
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function SearchPanel({ openId, onOpen }: { openId: number | null; onOpen: (id: number) => void }) {
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setQuery(input), 300);
    return () => clearTimeout(timer);
  }, [input]);
  const results = api.documents.search.useQuery({ query, kind: kind || undefined, limit: 100 });

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder='Search text, e.g. brake pads, "oat milk", civic OR rav4'
          className="flex-1 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500"
        />
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          className="rounded-lg border border-zinc-800 bg-zinc-900 px-2 text-sm text-zinc-200"
        >
          <option value="">All kinds</option>
          <option value="receipt">Receipts</option>
          <option value="car">Car documents</option>
          <option value="manual">Manuals</option>
          <option value="instructions">Instructions</option>
        </select>
      </div>
      {results.data?.error && <p className="text-sm text-red-400">Search problem: {results.data.error}</p>}
      <ul className="divide-y divide-zinc-800 rounded-lg border border-zinc-800">
        {(results.data?.hits ?? []).map((hit) => (
          <li key={hit.id}>
            <button
              onClick={() => onOpen(hit.id)}
              className={`block w-full px-4 py-3 text-left hover:bg-zinc-900 ${openId === hit.id ? "bg-zinc-900" : ""}`}
            >
              <div className="flex items-baseline justify-between gap-3">
                <span className="truncate text-sm font-medium text-zinc-100">{hit.path.split("/").pop()}</span>
                <span className="shrink-0 text-xs text-zinc-500">
                  {hit.docDate ?? ""} {hit.linkedExpenses > 0 && <span className="text-emerald-400">linked</span>}
                </span>
              </div>
              <div className="truncate text-xs text-zinc-500">{hit.path.split("/").slice(0, -1).join(" / ")}</div>
              {hit.snippet && (
                <div className="mt-1 text-xs text-zinc-400">
                  {hit.page ? `p${hit.page}: ` : ""}
                  <Highlighted text={hit.snippet} />
                </div>
              )}
            </button>
          </li>
        ))}
        {results.data && results.data.hits.length === 0 && (
          <li className="px-4 py-6 text-center text-sm text-zinc-500">No documents found</li>
        )}
      </ul>
    </div>
  );
}

function Highlighted({ text }: { text: string }) {
  return (
    <>
      {text.split(/(\[[^\]]*\])/).map((part, i) =>
        part.startsWith("[") ? (
          <mark key={i} className="rounded bg-amber-400/20 px-0.5 text-amber-200">
            {part.slice(1, -1)}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

function MatchesPanel({ onOpen }: { onOpen: (id: number) => void }) {
  const utils = api.useUtils();
  const [status, setStatus] = useState<"suggested" | "confirmed" | "rejected">("suggested");
  const matches = api.receipts.matches.useQuery({ status, limit: 100 });
  const invalidate = async () => {
    await Promise.all([utils.receipts.matches.invalidate(), utils.documents.status.invalidate()]);
  };
  const decide = api.receipts.decide.useMutation({ onSuccess: invalidate });
  const confirmAbove = api.receipts.confirmAbove.useMutation({ onSuccess: invalidate });
  const rematch = api.receipts.rematch.useMutation({ onSuccess: invalidate });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {(["suggested", "confirmed", "rejected"] as const).map((s) => (
          <button
            key={s}
            onClick={() => setStatus(s)}
            className={`rounded-full px-3 py-1 text-xs ${
              status === s ? "bg-zinc-100 text-zinc-900" : "bg-zinc-800 text-zinc-300 hover:bg-zinc-700"
            }`}
          >
            {s}
          </button>
        ))}
        <span className="ml-auto text-xs text-zinc-500">{matches.data?.total ?? 0} total</span>
      </div>
      {status === "suggested" && (
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => confirmAbove.mutate({ minScore: 7 })}
            disabled={confirmAbove.isPending}
            className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            Confirm all strong matches (score 7+)
          </button>
          <button
            onClick={() => rematch.mutate()}
            disabled={rematch.isPending}
            className="rounded-lg bg-zinc-800 px-3 py-1.5 text-sm text-zinc-200 hover:bg-zinc-700 disabled:opacity-50"
          >
            {rematch.isPending ? "Matching…" : "Find new matches"}
          </button>
          {confirmAbove.data && <span className="text-sm text-emerald-400">Confirmed {confirmAbove.data.confirmed}</span>}
        </div>
      )}
      <ul className="space-y-2">
        {(matches.data?.rows ?? []).map((m) => (
          <li key={m.id} className="rounded-lg border border-zinc-800 p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 text-sm">
                <div className="text-zinc-100">
                  {m.date} · {m.merchant ?? m.originalInputName} ·{" "}
                  <span className="font-medium">${m.amount.toFixed(2)}</span>
                </div>
                <div className="text-xs text-zinc-500">
                  {m.card} {m.spender ? `· ${m.spender}` : ""}
                </div>
                <button onClick={() => onOpen(m.documentId)} className="mt-1 truncate text-left text-xs text-sky-400 hover:underline">
                  {m.path.split("/").pop()}
                </button>
                <div className="mt-1 text-xs text-zinc-400">{m.reasons}</div>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1">
                <span className="text-xs text-zinc-500">score {m.score}</span>
                {m.status !== "confirmed" && (
                  <button
                    onClick={() => decide.mutate({ ids: [m.id], status: "confirmed" })}
                    className="rounded bg-emerald-600/80 px-2 py-0.5 text-xs text-white hover:bg-emerald-500"
                  >
                    Confirm
                  </button>
                )}
                {m.status !== "rejected" && (
                  <button
                    onClick={() => decide.mutate({ ids: [m.id], status: "rejected" })}
                    className="rounded bg-zinc-800 px-2 py-0.5 text-xs text-zinc-300 hover:bg-zinc-700"
                  >
                    Not this receipt
                  </button>
                )}
              </div>
            </div>
          </li>
        ))}
        {matches.data && matches.data.rows.length === 0 && (
          <li className="px-4 py-6 text-center text-sm text-zinc-500">Nothing here</li>
        )}
      </ul>
    </div>
  );
}

const CHECK_LABELS: Record<string, string> = {
  name_format: "Name format",
  implausible_date: "Impossible date",
  undated_receipt: "No date in name",
  merchant_variant: "Merchant spelling",
  category_outlier: "Unusual folder",
  duplicate_content: "Duplicate file",
  text_date: "Date vs. receipt",
  text_time: "Time vs. receipt",
  text_merchant: "Merchant vs. receipt",
};

function AuditPanel({ onOpen }: { onOpen: (id: number) => void }) {
  const utils = api.useUtils();
  const [check, setCheck] = useState<string>("");
  const [decision, setDecision] = useState<"open" | "fix" | "fixed" | "keep" | "all">("open");
  const [showPlan, setShowPlan] = useState(false);
  const summary = api.labelAudit.summary.useQuery();
  const findings = api.labelAudit.list.useQuery({ check: check || undefined, decision, limit: 200 });
  const plan = api.labelAudit.fixPlan.useQuery(undefined, { enabled: showPlan });
  const invalidate = async () => {
    await Promise.all([
      utils.labelAudit.summary.invalidate(),
      utils.labelAudit.list.invalidate(),
      utils.labelAudit.fixPlan.invalidate(),
    ]);
  };
  const decide = api.labelAudit.decide.useMutation({ onSuccess: invalidate });
  const applyFixes = api.labelAudit.applyFixes.useMutation({
    onSuccess: async () => {
      setShowPlan(false);
      await Promise.all([invalidate(), utils.documents.invalidate()]);
    },
  });
  const rerun = api.labelAudit.rerun.useMutation({ onSuccess: invalidate });
  const pendingFixes = (summary.data ?? []).reduce((sum, row) => sum + row.fix, 0);

  return (
    <div className="space-y-4">
      <p className="text-sm text-zinc-400">
        Possible mistakes in how documents are named or filed. Mark each one <b>fix</b> (with the corrected value) or{" "}
        <b>keep</b>; nothing is renamed until you apply the fixes, and every rename can be undone.
      </p>
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => setCheck("")}
          className={`rounded-full px-3 py-1 text-xs ${check === "" ? "bg-zinc-100 text-zinc-900" : "bg-zinc-800 text-zinc-300"}`}
        >
          All
        </button>
        {(summary.data ?? []).map((row) => (
          <button
            key={row.check}
            onClick={() => setCheck(row.check)}
            className={`rounded-full px-3 py-1 text-xs ${
              check === row.check ? "bg-zinc-100 text-zinc-900" : "bg-zinc-800 text-zinc-300"
            }`}
          >
            {CHECK_LABELS[row.check] ?? row.check} {row.open > 0 && <span className="text-amber-500">({row.open})</span>}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {(["open", "fix", "fixed", "keep", "all"] as const).map((d) => (
          <button
            key={d}
            onClick={() => setDecision(d)}
            className={`rounded px-2 py-1 ${decision === d ? "bg-zinc-700 text-zinc-100" : "text-zinc-400 hover:text-zinc-200"}`}
          >
            {d}
          </button>
        ))}
        <button
          onClick={() => rerun.mutate()}
          disabled={rerun.isPending}
          className="ml-auto rounded bg-zinc-800 px-2 py-1 text-zinc-300 hover:bg-zinc-700 disabled:opacity-50"
        >
          {rerun.isPending ? "Checking…" : "Check for new problems"}
        </button>
        <button
          onClick={() => setShowPlan(true)}
          disabled={pendingFixes === 0}
          className="rounded bg-sky-700 px-2 py-1 text-white hover:bg-sky-600 disabled:opacity-40"
        >
          Review {pendingFixes} fixes
        </button>
      </div>

      {showPlan && (
        <div className="rounded-lg border border-sky-700/50 bg-sky-950/30 p-3 text-sm">
          {plan.isLoading && <p className="text-zinc-400">Working out the renames…</p>}
          {plan.data && (
            <>
              <p className="mb-2 text-zinc-200">
                {plan.data.renames.length} files will be renamed or moved
                {plan.data.skipped.length > 0 && `, ${plan.data.skipped.length} cannot be`}:
              </p>
              <ul className="mb-3 max-h-64 space-y-1 overflow-auto text-xs">
                {plan.data.renames.map((r) => (
                  <li key={r.from} className="text-zinc-300">
                    <span className="text-zinc-500">{r.from.split("/").pop()}</span> →{" "}
                    {r.to.replace(/^.*\/(receipts|cars|_duplicates)\//, "$1/")}
                  </li>
                ))}
                {plan.data.skipped.map((s) => (
                  <li key={s.from} className="text-amber-400">
                    {s.from.split("/").pop()}: {s.problem}
                  </li>
                ))}
              </ul>
              <div className="flex gap-2">
                <button
                  onClick={() => applyFixes.mutate()}
                  disabled={applyFixes.isPending || plan.data.renames.length === 0}
                  className="rounded bg-sky-600 px-3 py-1 text-white hover:bg-sky-500 disabled:opacity-50"
                >
                  {applyFixes.isPending ? "Renaming…" : "Apply these fixes"}
                </button>
                <button onClick={() => setShowPlan(false)} className="rounded bg-zinc-800 px-3 py-1 text-zinc-300">
                  Cancel
                </button>
              </div>
            </>
          )}
        </div>
      )}
      {applyFixes.data && (
        <p className="text-sm text-emerald-400">
          Renamed {applyFixes.data.renames.length}. Undo log: {applyFixes.data.log}
        </p>
      )}

      <ul className="space-y-2">
        {(findings.data ?? []).map(({ finding, documentId }) => (
          <FindingCard
            key={finding.id}
            finding={finding}
            documentId={documentId}
            onOpen={onOpen}
            onDecide={(decision, value) => decide.mutate({ ids: [finding.id], decision, value })}
          />
        ))}
        {findings.data && findings.data.length === 0 && (
          <li className="px-4 py-6 text-center text-sm text-zinc-500">Nothing here</li>
        )}
      </ul>
    </div>
  );
}

type Finding = RouterOutputs["labelAudit"]["list"][number]["finding"];

function FindingCard({
  finding,
  documentId,
  onOpen,
  onDecide,
}: {
  finding: Finding;
  documentId: number | null;
  onOpen: (id: number) => void;
  onDecide: (decision: "fix" | "keep" | "later", value?: string) => void;
}) {
  const [value, setValue] = useState(finding.suggestion ?? "");
  const decided = finding.userDecision;
  return (
    <li className="rounded-lg border border-zinc-800 p-3 text-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs text-zinc-500">{CHECK_LABELS[finding.check] ?? finding.check}</div>
          {documentId ? (
            <button onClick={() => onOpen(documentId)} className="truncate text-left text-sky-400 hover:underline">
              {finding.path.split("/").pop()}
            </button>
          ) : (
            <div className="truncate text-zinc-200">{finding.path.split("/").pop()}</div>
          )}
          <div className="truncate text-xs text-zinc-500">{finding.path.split("/").slice(0, -1).join(" / ")}</div>
          <div className="mt-1 text-xs text-zinc-300">{finding.evidence}</div>
          {finding.reviewerNote && (
            <div className="mt-1 text-xs text-zinc-500">
              {finding.reviewedBy ? `${finding.reviewedBy}: ` : ""}
              {finding.reviewerNote}
            </div>
          )}
        </div>
        <span
          className={`shrink-0 rounded px-2 py-0.5 text-xs ${
            decided === "fixed"
              ? "bg-emerald-900 text-emerald-300"
              : decided === "fix"
                ? "bg-sky-900 text-sky-300"
                : decided === "keep"
                  ? "bg-zinc-800 text-zinc-400"
                  : "bg-amber-900/60 text-amber-300"
          }`}
        >
          {decided ?? "open"}
        </span>
      </div>
      {decided !== "fixed" && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="corrected value"
            className="min-w-48 flex-1 rounded border border-zinc-800 bg-zinc-900 px-2 py-1 text-xs text-zinc-100"
          />
          <button
            onClick={() => onDecide("fix", value || undefined)}
            className="rounded bg-sky-700 px-2 py-1 text-xs text-white hover:bg-sky-600"
          >
            Fix
          </button>
          <button onClick={() => onDecide("keep")} className="rounded bg-zinc-800 px-2 py-1 text-xs text-zinc-300">
            Keep
          </button>
          <button onClick={() => onDecide("later")} className="rounded px-2 py-1 text-xs text-zinc-500 hover:text-zinc-300">
            Later
          </button>
        </div>
      )}
    </li>
  );
}
