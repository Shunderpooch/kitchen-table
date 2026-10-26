"use client";

import { useEffect, useMemo, useState } from "react";
import { Navigation } from "~/app/_components/navigation";
import { api } from "~/trpc/react";
import type { ManifestRow } from "~/server/api/routers/scans";

export default function ScansPage() {
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <div className="min-h-screen bg-zinc-950">
      <Navigation />
      <main className="container mx-auto px-4 py-8">
        <div className="mb-8">
          <h1 className="text-2xl font-semibold text-zinc-100">Scans</h1>
          <p className="text-zinc-400">
            Turn a ScanSmart stack into named, filed documents in OneDrive. Nothing is filed until you approve it.
          </p>
        </div>
        <div className="grid gap-8 lg:grid-cols-[22rem_minmax(0,1fr)]">
          <div className="space-y-6">
            <PlanForm onPlanned={setSelected} />
            <BatchList selected={selected} onSelect={setSelected} />
          </div>
          <div>{selected ? <BatchReview id={selected} /> : <EmptyState />}</div>
        </div>
      </main>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-lg border border-dashed border-zinc-800 p-10 text-center text-sm text-zinc-500">
      Start a batch from your scanner inbox, or pick an earlier batch to review it.
    </div>
  );
}

function PlanForm({ onPlanned }: { onPlanned: (id: string) => void }) {
  const utils = api.useUtils();
  const defaults = api.scans.defaults.useQuery();
  const conventions = api.scans.conventions.useQuery();
  const [inbox, setInbox] = useState("");
  const [person, setPerson] = useState("self");
  const [kind, setKind] = useState("");
  const [duplex, setDuplex] = useState(false);
  const [separatorSheets, setSeparatorSheets] = useState(false);
  const [onePerPage, setOnePerPage] = useState(false);
  useEffect(() => {
    if (defaults.data?.inbox && !inbox) setInbox(defaults.data.inbox);
  }, [defaults.data, inbox]);
  const plan = api.scans.plan.useMutation({
    onSuccess: async ({ id }) => {
      await utils.scans.batches.invalidate();
      onPlanned(id);
    },
  });

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        plan.mutate({ inbox, person, kind: kind || undefined, duplex, separatorSheets, onePerPage });
      }}
      className="space-y-3 rounded-lg border border-zinc-800 p-4"
    >
      <div className="text-sm font-medium text-zinc-100">New batch</div>
      <label className="block text-xs text-zinc-400">
        Scanner inbox folder
        <input
          value={inbox}
          onChange={(e) => setInbox(e.target.value)}
          placeholder="C:\Users\...\Pictures\ScanSmart Inbox"
          className="mt-1 w-full rounded border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-sm text-zinc-100"
        />
      </label>
      <div className="grid grid-cols-2 gap-2">
        <label className="block text-xs text-zinc-400">
          Whose
          <select
            value={person}
            onChange={(e) => setPerson(e.target.value)}
            className="mt-1 w-full rounded border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-sm text-zinc-100"
          >
            <option value="self">Mine</option>
            {(conventions.data?.people ?? []).map((p) => (
              <option key={p.key} value={p.key}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-xs text-zinc-400">
          Kind
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value)}
            className="mt-1 w-full rounded border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-sm text-zinc-100"
          >
            <option value="">Detect</option>
            {(conventions.data?.kinds ?? []).map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>
      </div>
      <Check label="Double-sided (every sheet has a front and back page)" value={duplex} onChange={setDuplex} />
      <Check label="Blank separator sheets between documents" value={separatorSheets} onChange={setSeparatorSheets} />
      <Check label="One document per page (no joining)" value={onePerPage} onChange={setOnePerPage} />
      <button
        type="submit"
        disabled={!inbox || plan.isPending}
        className="w-full rounded-lg bg-zinc-100 px-3 py-2 text-sm font-medium text-zinc-900 hover:bg-white disabled:opacity-50"
      >
        {plan.isPending ? "Starting…" : "Read and plan this stack"}
      </button>
      {plan.error && <p className="text-xs text-red-400">{plan.error.message}</p>}
      <p className="text-xs text-zinc-500">Reading takes a few seconds per page; you can leave this page meanwhile.</p>
    </form>
  );
}

function Check({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 text-xs text-zinc-300">
      <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

function BatchList({ selected, onSelect }: { selected: string | null; onSelect: (id: string) => void }) {
  const batches = api.scans.batches.useQuery(undefined, {
    refetchInterval: (query) => (query.state.data?.some((b) => b.status === "planning") ? 3000 : false),
  });
  return (
    <div className="rounded-lg border border-zinc-800">
      <div className="border-b border-zinc-800 px-4 py-2 text-sm font-medium text-zinc-100">Batches</div>
      <ul className="max-h-96 divide-y divide-zinc-800 overflow-auto">
        {(batches.data ?? []).map((batch) => (
          <li key={batch.id}>
            <button
              onClick={() => onSelect(batch.id)}
              className={`flex w-full items-center justify-between px-4 py-2 text-left text-sm hover:bg-zinc-900 ${
                selected === batch.id ? "bg-zinc-900" : ""
              }`}
            >
              <span className="text-zinc-200">{batch.id.replace(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2}).*/, "$1-$2-$3 $4:$5")}</span>
              <span
                className={`text-xs ${
                  batch.status === "ready" ? "text-emerald-400" : batch.status === "failed" ? "text-red-400" : "text-amber-400"
                }`}
              >
                {batch.applyLogs.length > 0 ? "filed" : batch.status}
              </span>
            </button>
          </li>
        ))}
        {batches.data?.length === 0 && <li className="px-4 py-4 text-sm text-zinc-500">No batches yet</li>}
      </ul>
    </div>
  );
}

const EDITABLE = ["action", "person", "category", "date", "time", "merchant", "merge_into"] as const;
type Editable = (typeof EDITABLE)[number];

function BatchReview({ id }: { id: string }) {
  const utils = api.useUtils();
  const batches = api.scans.batches.useQuery();
  const batch = batches.data?.find((b) => b.id === id);
  const ready = batch?.status === "ready";
  const manifest = api.scans.manifest.useQuery({ id }, { enabled: ready });
  const conventions = api.scans.conventions.useQuery();
  const [edits, setEdits] = useState<Record<string, Partial<Record<Editable, string>>>>({});
  useEffect(() => setEdits({}), [id]);
  const save = api.scans.updateManifest.useMutation({
    onSuccess: async () => {
      setEdits({});
      await utils.scans.manifest.invalidate({ id });
    },
  });
  const apply = api.scans.fileBatch.useMutation({
    onSuccess: async (result) => {
      if (!result.dry_run) await Promise.all([utils.scans.batches.invalidate(), utils.documents.invalidate()]);
    },
  });

  const rows = manifest.data?.rows ?? [];
  const value = (row: ManifestRow, field: Editable) => edits[row.item_id]?.[field] ?? row[field];
  const documents = useMemo(() => rows.filter((r) => !value(r, "merge_into")), [rows, edits]); // eslint-disable-line react-hooks/exhaustive-deps
  const partsOf = (itemId: string) => rows.filter((r) => value(r, "merge_into") === itemId);
  const setField = (itemId: string, field: Editable, v: string) =>
    setEdits((prev) => ({ ...prev, [itemId]: { ...prev[itemId], [field]: v } }));
  const dirty = Object.keys(edits).length > 0;

  if (!batch) return null;
  if (batch.status === "planning") {
    return (
      <div className="rounded-lg border border-zinc-800 p-4">
        <p className="mb-2 text-sm text-amber-300">Reading the stack…</p>
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap text-xs text-zinc-500">{batch.log || "starting"}</pre>
      </div>
    );
  }
  if (batch.status === "failed") {
    return (
      <div className="rounded-lg border border-red-900 p-4">
        <p className="mb-2 text-sm text-red-300">Planning failed</p>
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap text-xs text-zinc-400">{batch.log}</pre>
      </div>
    );
  }

  const toFile = documents.filter((r) => value(r, "action") === "file").length;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="text-sm text-zinc-300">
          {documents.length} documents from {rows.length} pages · {toFile} to file ·{" "}
          {documents.filter((r) => value(r, "action") === "review").length} need review
        </div>
        <div className="ml-auto flex gap-2">
          <button
            onClick={() =>
              save.mutate({
                id,
                edits: Object.entries(edits).map(([item_id, changes]) => ({
                  item_id,
                  ...changes,
                  action: changes.action as "file" | "review" | "skip" | undefined,
                })),
              })
            }
            disabled={!dirty || save.isPending}
            className="rounded bg-zinc-800 px-3 py-1.5 text-sm text-zinc-200 hover:bg-zinc-700 disabled:opacity-40"
          >
            {save.isPending ? "Saving…" : "Save changes"}
          </button>
          <button
            onClick={() => apply.mutate({ id, dryRun: true })}
            disabled={dirty || apply.isPending}
            className="rounded bg-sky-700 px-3 py-1.5 text-sm text-white hover:bg-sky-600 disabled:opacity-40"
          >
            Preview filing
          </button>
        </div>
      </div>
      {save.error && <p className="text-sm text-red-400">{save.error.message}</p>}
      {manifest.data?.problems && <p className="text-sm text-amber-400">{manifest.data.problems}</p>}

      {apply.data && (
        <div className="rounded-lg border border-sky-700/50 bg-sky-950/30 p-3 text-sm">
          <p className="mb-2 text-zinc-200">
            {apply.data.dry_run ? "Would file" : "Filed"} {apply.data.filed.length}; {apply.data.review.length} still in
            review; {apply.data.skipped.length} skipped.
          </p>
          <ul className="mb-2 max-h-48 overflow-auto text-xs text-zinc-300">
            {apply.data.filed.map((f) => (
              <li key={f.item_id}>
                {f.item_id} → {f.target} {f.pages > 1 ? `(${f.pages} pages)` : ""}
              </li>
            ))}
          </ul>
          {apply.data.dry_run ? (
            <button
              onClick={() => apply.mutate({ id, dryRun: false })}
              disabled={apply.isPending || apply.data.filed.length === 0}
              className="rounded bg-sky-600 px-3 py-1 text-white hover:bg-sky-500 disabled:opacity-50"
            >
              {apply.isPending ? "Filing…" : `File ${apply.data.filed.length} documents into OneDrive`}
            </button>
          ) : (
            <p className="text-xs text-emerald-400">Filed. Undo log: {apply.data.log}</p>
          )}
        </div>
      )}
      {apply.error && <p className="text-sm text-red-400">{apply.error.message}</p>}

      <ul className="space-y-2">
        {documents.map((row) => (
          <DocumentRow
            key={row.item_id}
            batchId={id}
            row={row}
            parts={partsOf(row.item_id)}
            value={(field) => value(row, field)}
            setField={(field, v) => setField(row.item_id, field, v)}
            onSplit={(itemId) => setField(itemId, "merge_into", "")}
            target={manifest.data?.targets[row.item_id]}
            categories={conventions.data?.categories ?? []}
            people={conventions.data?.people ?? []}
          />
        ))}
      </ul>
    </div>
  );
}

function sourceLinks(batchId: string, pages: string) {
  return pages
    .split("|")
    .map((p) => p.trim())
    .filter(Boolean)
    .map((ref) => {
      const file = ref.replace(/#\d+$/, "");
      const page = ref.match(/#(\d+)$/)?.[1];
      return { ref, href: `/api/scans/${batchId}/source?file=${encodeURIComponent(file)}${page ? `#page=${page}` : ""}` };
    });
}

function DocumentRow({
  batchId,
  row,
  parts,
  value,
  setField,
  onSplit,
  target,
  categories,
  people,
}: {
  batchId: string;
  row: ManifestRow;
  parts: ManifestRow[];
  value: (field: Editable) => string;
  setField: (field: Editable, v: string) => void;
  onSplit: (itemId: string) => void;
  target?: string;
  categories: string[];
  people: { key: string; name: string }[];
}) {
  const action = value("action");
  const input = "rounded border border-zinc-800 bg-zinc-900 px-2 py-1 text-xs text-zinc-100";
  return (
    <li
      className={`rounded-lg border p-3 ${
        action === "file" ? "border-emerald-900/60" : action === "skip" ? "border-zinc-800 opacity-60" : "border-amber-900/60"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-zinc-500">#{row.item_id}</span>
        <select value={action} onChange={(e) => setField("action", e.target.value)} className={input}>
          <option value="file">file</option>
          <option value="review">review</option>
          <option value="skip">skip</option>
        </select>
        <input type="date" value={value("date")} onChange={(e) => setField("date", e.target.value)} className={input} />
        <input
          value={value("time")}
          onChange={(e) => setField("time", e.target.value)}
          placeholder="HH-MM"
          className={`${input} w-16`}
        />
        <input
          value={value("merchant")}
          onChange={(e) => setField("merchant", e.target.value)}
          placeholder="merchant"
          className={`${input} w-44`}
        />
        <select value={value("category")} onChange={(e) => setField("category", e.target.value)} className={input}>
          <option value="">(category)</option>
          {categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <select value={value("person") || "self"} onChange={(e) => setField("person", e.target.value)} className={input}>
          <option value="self">mine</option>
          {people.map((p) => (
            <option key={p.key} value={p.key}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      <div className="mt-1 text-xs text-zinc-400">
        {target ? <span className="text-zinc-300">→ {target}</span> : <span>{row.destination}/{row.filename}</span>}
        {row.total && <span className="ml-2">total ${row.total}</span>}
      </div>
      <div className="mt-1 text-xs text-zinc-500">{row.text_preview}</div>
      {row.reasons && <div className="mt-1 text-[11px] text-zinc-600">{row.reasons}</div>}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        {sourceLinks(batchId, row.pages).map((link) => (
          <a key={link.ref} href={link.href} target="_blank" rel="noreferrer" className="text-sky-400 hover:underline">
            page
          </a>
        ))}
        {parts.map((part) => (
          <span key={part.item_id} className="flex items-center gap-1 rounded bg-zinc-800 px-1.5 py-0.5 text-zinc-300">
            + #{part.item_id}
            {sourceLinks(batchId, part.pages).map((link) => (
              <a key={link.ref} href={link.href} target="_blank" rel="noreferrer" className="text-sky-400">
                view
              </a>
            ))}
            <button onClick={() => onSplit(part.item_id)} className="text-zinc-500 hover:text-red-400" title="Split off">
              ×
            </button>
          </span>
        ))}
        <span className="ml-auto flex items-center gap-1 text-zinc-500">
          join into
          <input
            value={value("merge_into")}
            onChange={(e) => setField("merge_into", e.target.value)}
            placeholder="#"
            className={`${input} w-14`}
          />
        </span>
      </div>
    </li>
  );
}
