"use client";

import { useEffect, useMemo, useState } from "react";
import { Navigation } from "~/app/_components/navigation";
import { DocumentViewer } from "~/app/_components/document-viewer";
import { api, type RouterOutputs } from "~/trpc/react";
import { MEMBER_B } from "~/lib/household";
import type { ManifestRow } from "~/server/api/routers/scans";

const input = "rounded border border-zinc-800 bg-zinc-900 px-2 py-1 text-xs text-zinc-100";
const button = "rounded px-3 py-1.5 text-sm disabled:opacity-40";

export default function InboxPage() {
  const [tab, setTab] = useState<"scans" | "expenses">("scans");
  const inbox = api.scans.inbox.useQuery(undefined, { refetchInterval: 15_000 });
  const staged = api.receipts.staged.useQuery({ limit: 200 });
  const toReview = inbox.data?.batches.filter((b) => b.status !== "filed").length ?? 0;

  return (
    <div className="min-h-screen bg-zinc-950">
      <Navigation />
      <main className="mx-auto max-w-[1800px] px-4 py-6">
        <InboxHeader />
        <div className="mb-4 flex gap-2 border-b border-zinc-800">
          {(
            [
              ["scans", `Scans to review (${toReview})`],
              ["expenses", `Expenses to approve (${staged.data?.total ?? "…"})`],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`-mb-px border-b-2 px-3 py-2 text-sm ${
                tab === key ? "border-sky-500 text-zinc-100" : "border-transparent text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        {tab === "scans" ? <ScansTab /> : <ExpensesTab />}
      </main>
    </div>
  );
}

function InboxHeader() {
  const utils = api.useUtils();
  const inbox = api.scans.inbox.useQuery(undefined, { refetchInterval: 15_000 });
  const check = api.scans.checkInbox.useMutation({ onSuccess: () => utils.scans.inbox.invalidate() });
  const pause = api.scans.setPaused.useMutation({ onSuccess: () => utils.scans.inbox.invalidate() });
  const data = inbox.data;
  return (
    <div className="mb-4 flex flex-wrap items-end gap-4">
      <div>
        <h1 className="text-2xl font-semibold text-zinc-100">Inbox</h1>
        <p className="text-sm text-zinc-400">
          Scan from any computer into <code className="text-zinc-300">{data?.inbox ?? "OneDrive\\Scans\\Inbox"}</code>{" "}
          (a subfolder named after a person, like <code>Inbox\{MEMBER_B}</code>, files them for that person). Nothing is
          filed until you approve it here.
        </p>
      </div>
      <div className="ml-auto flex items-center gap-2 text-sm">
        {data && (
          <span className={data.waiting.length ? "text-amber-300" : "text-zinc-500"}>
            {data.paused
              ? "Paused"
              : data.waiting.length
                ? `${data.waiting.length} scan${data.waiting.length === 1 ? "" : "s"} arriving`
                : "Inbox empty"}
          </span>
        )}
        <button
          onClick={() => check.mutate()}
          disabled={check.isPending || data?.paused}
          className={`${button} bg-zinc-800 text-zinc-200 hover:bg-zinc-700`}
        >
          Check now
        </button>
        <button
          onClick={() => pause.mutate({ paused: !data?.paused })}
          className={`${button} bg-zinc-800 text-zinc-200 hover:bg-zinc-700`}
          title="Paused: scans wait in the inbox and nothing runs in the background"
        >
          {data?.paused ? "Resume" : "Pause"}
        </button>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- scans */

function ScansTab() {
  const inbox = api.scans.inbox.useQuery(undefined, { refetchInterval: 15_000 });
  const [selected, setSelected] = useState<string | null>(null);
  const batches = inbox.data?.batches ?? [];
  const open = batches.filter((b) => b.status !== "filed");
  const filed = batches.filter((b) => b.status === "filed").slice(0, 8);
  useEffect(() => {
    if (!selected && open[0]) setSelected(open[0].id);
  }, [open, selected]);

  return (
    <div className="grid gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
      <div className="space-y-4">
        <BatchList title="To review" batches={open} selected={selected} onSelect={setSelected} empty="Nothing to review" />
        {filed.length > 0 && (
          <BatchList title="Filed" batches={filed} selected={selected} onSelect={setSelected} empty="" />
        )}
      </div>
      <div>
        {selected ? (
          <BatchReview key={selected} id={selected} />
        ) : (
          <div className="rounded-lg border border-dashed border-zinc-800 p-10 text-center text-sm text-zinc-500">
            Scans saved to the inbox show up here a couple of minutes after they finish syncing.
          </div>
        )}
      </div>
    </div>
  );
}

type BatchSummary = RouterOutputs["scans"]["inbox"]["batches"][number];

function BatchList({
  title,
  batches,
  selected,
  onSelect,
  empty,
}: {
  title: string;
  batches: BatchSummary[];
  selected: string | null;
  onSelect: (id: string) => void;
  empty: string;
}) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-zinc-500">{title}</div>
      <ul className="divide-y divide-zinc-800 rounded-lg border border-zinc-800">
        {batches.map((b) => (
          <li key={b.id}>
            <button
              onClick={() => onSelect(b.id)}
              className={`w-full px-3 py-2 text-left text-sm ${selected === b.id ? "bg-zinc-800/80" : "hover:bg-zinc-900"}`}
            >
              <div className="text-zinc-200">{batchLabel(b.id)}</div>
              <div
                className={`text-xs ${
                  b.status === "failed" ? "text-red-400" : b.status === "planning" ? "text-amber-300" : "text-zinc-500"
                }`}
              >
                {b.status === "planning" ? "reading…" : b.status}
                {b.person && b.person !== "self" ? ` · ${b.person}` : ""}
                {b.origin !== "inbox" ? " · from Scans page" : ""}
              </div>
            </button>
          </li>
        ))}
        {batches.length === 0 && <li className="px-3 py-3 text-xs text-zinc-500">{empty}</li>}
      </ul>
    </div>
  );
}

function batchLabel(id: string) {
  const m = id.match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})/);
  return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}` : id;
}

const FIELDS = ["action", "kind", "person", "category", "car", "date", "time", "merchant", "merge_into"] as const;
type Field = (typeof FIELDS)[number];
type Edits = Record<string, Partial<Record<Field, string>>>;

interface Doc {
  head: ManifestRow;
  items: ManifestRow[];
  pages: number[];
}

function BatchReview({ id }: { id: string }) {
  const utils = api.useUtils();
  const inbox = api.scans.inbox.useQuery();
  const summary = inbox.data?.batches.find((b) => b.id === id);
  const ready = summary?.status === "ready" || summary?.status === "filed";
  const batch = api.scans.batch.useQuery(
    { id },
    { enabled: ready, refetchInterval: (q) => (q.state.data?.filed && !q.state.data.settled ? 5000 : false) },
  );
  const conventions = api.scans.conventions.useQuery();
  const [edits, setEdits] = useState<Edits>({});
  const [focus, setFocus] = useState<string | null>(null);
  const save = api.scans.updateManifest.useMutation();
  const file = api.scans.fileBatch.useMutation({
    onSuccess: async (result) => {
      if (!result.dry_run) {
        await Promise.all([utils.scans.inbox.invalidate(), utils.scans.batch.invalidate({ id }), utils.documents.invalidate()]);
      }
    },
  });
  const render = api.scans.renderPages.useMutation({ onSuccess: () => utils.scans.inbox.invalidate() });
  const replan = api.scans.replan.useMutation({ onSuccess: () => utils.scans.inbox.invalidate() });

  const rows = useMemo(
    () => [...(batch.data?.rows ?? [])].sort((a, b) => a.item_id.localeCompare(b.item_id)),
    [batch.data],
  );
  const value = (row: ManifestRow, field: Field) => edits[row.item_id]?.[field] ?? row[field];
  const pagesByItem = useMemo(() => {
    const map = new Map<string, number[]>();
    for (const page of batch.data?.pages ?? []) map.set(page.item_id, [...(map.get(page.item_id) ?? []), page.n]);
    return map;
  }, [batch.data]);
  const docs: Doc[] = [];
  for (const row of rows) {
    const current = docs[docs.length - 1];
    if (value(row, "merge_into") && current) {
      current.items.push(row);
      current.pages.push(...(pagesByItem.get(row.item_id) ?? []));
    } else {
      docs.push({ head: row, items: [row], pages: [...(pagesByItem.get(row.item_id) ?? [])] });
    }
  }
  const setField = (itemId: string, field: Field, v: string) =>
    setEdits((prev) => ({ ...prev, [itemId]: { ...prev[itemId], [field]: v } }));
  const setMany = (changes: [string, Field, string][]) =>
    setEdits((prev) => {
      const next = { ...prev };
      for (const [itemId, field, v] of changes) next[itemId] = { ...next[itemId], [field]: v };
      return next;
    });

  // Re-cut documents are named again from their own pages.
  const [described, setDescribed] = useState<Record<string, { total?: string; reasons?: string; text_preview?: string }>>({});
  const describe = api.scans.describe.useMutation({
    onSuccess: (result) => {
      setMany(
        Object.entries(result).flatMap(([itemId, fields]) =>
          (["kind", "category", "car", "date", "time", "merchant"] as const).map(
            (field): [string, Field, string] => [itemId, field, fields[field] ?? ""],
          ),
        ),
      );
      setDescribed((prev) => ({ ...prev, ...result }));
    },
  });
  const ids = (items: ManifestRow[]) => items.map((item) => item.item_id);

  // Cut or join between two scanned items. Every part points at its document's first item.
  const joinWithPrevious = (docIndex: number) => {
    const previous = docs[docIndex - 1];
    const doc = docs[docIndex];
    if (!previous || !doc) return;
    setMany(doc.items.map((item) => [item.item_id, "merge_into", previous.head.item_id]));
    describe.mutate({ id, groups: [[...ids(previous.items), ...ids(doc.items)]] });
  };
  const cutBefore = (doc: Doc, itemIndex: number) => {
    const newHead = doc.items[itemIndex];
    if (!newHead) return;
    setMany([
      [newHead.item_id, "merge_into", ""],
      [newHead.item_id, "action", "review"],
      ...doc.items.slice(itemIndex + 1).map((item): [string, Field, string] => [item.item_id, "merge_into", newHead.item_id]),
    ]);
    describe.mutate({ id, groups: [ids(doc.items.slice(0, itemIndex)), ids(doc.items.slice(itemIndex))] });
  };

  if (!summary) return null;
  if (summary.status === "planning") {
    return (
      <div className="rounded-lg border border-zinc-800 p-4">
        <p className="mb-2 text-sm text-amber-300">Reading the scans and cutting them into documents…</p>
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs text-zinc-500">{summary.log || "starting"}</pre>
      </div>
    );
  }
  if (summary.status === "failed") {
    return (
      <div className="rounded-lg border border-red-900 p-4">
        <p className="mb-2 text-sm text-red-300">Reading this batch failed. The scans are kept in Scans\Originals.</p>
        <pre className="mb-3 max-h-64 overflow-auto whitespace-pre-wrap text-xs text-zinc-400">{summary.log}</pre>
        {summary.origin === "inbox" && (
          <button onClick={() => replan.mutate({ id })} className={`${button} bg-zinc-800 text-zinc-200`}>
            Try again
          </button>
        )}
      </div>
    );
  }
  if (!batch.data) return <p className="text-sm text-zinc-500">Loading…</p>;
  if (!batch.data.pages) {
    return (
      <div className="rounded-lg border border-zinc-800 p-4 text-sm text-zinc-400">
        This batch has no page previews yet.{" "}
        <button onClick={() => render.mutate({ id })} disabled={render.isPending} className="text-sky-400 hover:underline">
          Make them
        </button>{" "}
        (takes a moment, then reload).
      </div>
    );
  }

  const filed = batch.data.filed;
  const counts = {
    approved: docs.filter((d) => value(d.head, "action") === "file").length,
    look: docs.filter((d) => value(d.head, "action") === "review").length,
    skipped: docs.filter((d) => value(d.head, "action") === "skip").length,
  };
  const focused = docs.find((d) => d.head.item_id === focus) ?? docs[0];
  const editList = () =>
    Object.entries(edits).map(([item_id, changes]) => ({
      item_id,
      ...changes,
      action: changes.action as "file" | "review" | "skip" | undefined,
    }));
  const fileAll = async (dryRun: boolean) => {
    if (Object.keys(edits).length) {
      await save.mutateAsync({ id, edits: editList() });
      setEdits({});
      await utils.scans.batch.invalidate({ id });
    }
    file.mutate({ id, dryRun });
  };

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_32rem]">
      <div>
        {!filed && (
          <div className="sticky top-0 z-10 mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950/95 p-2 backdrop-blur">
            <span className="text-sm text-zinc-300">
              {docs.length} documents · <span className="text-emerald-400">{counts.approved} approved</span> ·{" "}
              <span className="text-amber-300">{counts.look} to look at</span> · {counts.skipped} skipped
            </span>
            <button
              onClick={() => setMany(docs.filter((d) => value(d.head, "action") === "review").map((d) => [d.head.item_id, "action", "file"]))}
              disabled={counts.look === 0}
              className={`${button} ml-auto bg-zinc-800 text-zinc-200 hover:bg-zinc-700`}
            >
              Approve the rest
            </button>
            <button
              onClick={() => void fileAll(true)}
              disabled={counts.look > 0 || save.isPending || file.isPending}
              title={counts.look > 0 ? "Approve or skip every document first" : "See where every document goes"}
              className={`${button} bg-sky-700 text-white hover:bg-sky-600`}
            >
              Check placement
            </button>
          </div>
        )}
        {(save.error ?? file.error) && <p className="mb-2 text-sm text-red-400">{(save.error ?? file.error)?.message}</p>}
        {batch.data.problems && <p className="mb-2 text-sm text-amber-400">{batch.data.problems}</p>}
        {file.data && !filed && (
          <div className="mb-3 rounded-lg border border-sky-700/50 bg-sky-950/30 p-3 text-sm">
            <p className="mb-2 text-zinc-200">
              {file.data.filed.length} documents will be filed ({file.data.skipped.length} skipped):
            </p>
            <ul className="mb-2 max-h-48 overflow-auto text-xs text-zinc-300">
              {file.data.filed.map((f) => (
                <li key={f.item_id}>
                  → {f.target}
                  {f.pages > 1 ? ` (${f.pages} pages)` : ""}
                </li>
              ))}
            </ul>
            <button
              onClick={() => file.mutate({ id, dryRun: false })}
              disabled={file.isPending || file.data.filed.length === 0 || Object.keys(edits).length > 0}
              className={`${button} bg-sky-600 text-white hover:bg-sky-500`}
            >
              {file.isPending ? "Filing…" : `File ${file.data.filed.length} documents into OneDrive`}
            </button>
          </div>
        )}
        {filed && (
          <div className="mb-3 rounded-lg border border-emerald-800/60 bg-emerald-950/20 p-3 text-sm text-emerald-300">
            Filed.{" "}
            {batch.data.settled
              ? "Receipts are matched to statement rows, and expenses were made for the rest: see Expenses to approve."
              : "Matching the receipts to your expenses in the background…"}
          </div>
        )}
        <ol className="space-y-2">
          {docs.map((doc, index) => (
            <li key={doc.head.item_id}>
              {index > 0 && !filed && (
                <button
                  onClick={() => joinWithPrevious(index)}
                  className="mb-2 w-full rounded border border-dashed border-zinc-800 py-0.5 text-[11px] text-zinc-600 hover:border-sky-700 hover:text-sky-400"
                  title="These pages belong to the document above"
                >
                  ↑ join with the document above
                </button>
              )}
              <DocumentCard
                batchId={id}
                doc={doc}
                value={(field) => value(doc.head, field)}
                setField={(field, v) => setField(doc.head.item_id, field, v)}
                onCut={(itemIndex) => cutBefore(doc, itemIndex)}
                pagesByItem={pagesByItem}
                target={
                  doc.items.some((item) => Object.keys(edits[item.item_id] ?? {}).some((f) => f !== "action"))
                    ? undefined // changed since the last save: shown again after "Check placement"
                    : (batch.data.targets[doc.head.item_id] ?? `${doc.head.destination}/${doc.head.filename}`)
                }
                info={described[doc.head.item_id]}
                focused={focused?.head.item_id === doc.head.item_id}
                onFocus={() => setFocus(doc.head.item_id)}
                readOnly={filed}
                categories={conventions.data?.categories ?? []}
                people={conventions.data?.people ?? []}
                kinds={conventions.data?.kinds ?? []}
                cars={conventions.data?.cars ?? []}
              />
            </li>
          ))}
        </ol>
      </div>
      <aside className="hidden xl:block">
        <div className="sticky top-2 max-h-[calc(100vh-1rem)] space-y-2 overflow-auto rounded-lg border border-zinc-800 bg-zinc-900/40 p-2">
          {focused ? (
            <>
              <div className="px-1 text-xs text-zinc-400">
                {value(focused.head, "date")} {value(focused.head, "merchant")} · {focused.pages.length} page
                {focused.pages.length === 1 ? "" : "s"}
              </div>
              {focused.pages.map((n) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img key={n} src={`/api/scans/${id}/page/${n}`} alt={`page ${n}`} className="w-full rounded bg-white" />
              ))}
            </>
          ) : (
            <p className="p-4 text-sm text-zinc-500">Pick a document to see its pages</p>
          )}
        </div>
      </aside>
    </div>
  );
}

function DocumentCard({
  batchId,
  doc,
  value,
  setField,
  onCut,
  pagesByItem,
  target,
  info,
  focused,
  onFocus,
  readOnly,
  categories,
  people,
  kinds,
  cars,
}: {
  batchId: string;
  doc: Doc;
  value: (field: Field) => string;
  setField: (field: Field, v: string) => void;
  onCut: (itemIndex: number) => void;
  pagesByItem: Map<string, number[]>;
  target?: string;
  info?: { total?: string; reasons?: string; text_preview?: string };
  focused: boolean;
  onFocus: () => void;
  readOnly: boolean;
  categories: string[];
  people: { key: string; name: string }[];
  kinds: string[];
  cars: string[];
}) {
  const action = value("action");
  const kind = value("kind") || "receipt";
  const planned = doc.head.action;
  const border =
    action === "skip" ? "border-zinc-800 opacity-50" : action === "file" ? "border-emerald-900/70" : "border-amber-700/70";
  return (
    <div
      onClick={onFocus}
      className={`rounded-lg border p-3 ${border} ${focused ? "ring-1 ring-sky-600" : ""} ${readOnly ? "" : "cursor-pointer"}`}
    >
      <div className="flex gap-3">
        <div className="flex shrink-0 items-start gap-1 overflow-x-auto">
          {doc.items.map((item, itemIndex) => (
            <div key={item.item_id} className="flex items-start gap-1">
              {itemIndex > 0 && !readOnly && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onCut(itemIndex);
                  }}
                  className="mt-10 rounded px-0.5 text-sm text-zinc-600 hover:bg-red-950 hover:text-red-400"
                  title="Cut here: a new document starts with the next page"
                >
                  ✂
                </button>
              )}
              {(pagesByItem.get(item.item_id) ?? []).map((n) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  key={n}
                  src={`/api/scans/${batchId}/page/${n}?thumb=1`}
                  alt={`page ${n}`}
                  className="h-28 w-auto rounded border border-zinc-800 bg-white"
                />
              ))}
            </div>
          ))}
        </div>
        <div className="min-w-0 flex-1 space-y-1.5" onClick={(e) => e.stopPropagation()}>
          <div className="flex flex-wrap items-center gap-2">
            {readOnly ? (
              <span className="text-xs text-zinc-400">{action === "skip" ? "skipped" : "filed"}</span>
            ) : (
              <>
                <label className="flex items-center gap-1 text-xs text-zinc-300">
                  <input
                    type="checkbox"
                    checked={action === "file"}
                    onChange={(e) => setField("action", e.target.checked ? "file" : "review")}
                  />
                  approve
                </label>
                <button
                  onClick={() => setField("action", action === "skip" ? "review" : "skip")}
                  className="text-xs text-zinc-500 hover:text-zinc-300"
                  title="Not a document to keep (blank page, duplicate)"
                >
                  {action === "skip" ? "unskip" : "skip"}
                </button>
              </>
            )}
            <fieldset disabled={readOnly} className="flex flex-wrap items-center gap-2">
              <select value={kind} onChange={(e) => setField("kind", e.target.value)} className={input}>
                {kinds.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
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
                className={`${input} w-40`}
              />
              {(kind === "receipt" || kind === "unknown") && (
                <select value={value("category")} onChange={(e) => setField("category", e.target.value)} className={input}>
                  <option value="">(folder)</option>
                  {categories.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              )}
              {kind === "car" && (
                <select value={value("car")} onChange={(e) => setField("car", e.target.value)} className={input}>
                  <option value="">(car)</option>
                  {cars.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              )}
              <select value={value("person") || "self"} onChange={(e) => setField("person", e.target.value)} className={input}>
                <option value="self">mine</option>
                {people.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.name}
                  </option>
                ))}
              </select>
            </fieldset>
          </div>
          <div className="truncate text-xs text-zinc-300">
            {target ? `→ ${target}` : <span className="text-zinc-500">changed: Check placement shows where it goes</span>}
            {(info?.total ?? doc.head.total) && (
              <span className="ml-2 text-zinc-500">total ${info?.total ?? doc.head.total}</span>
            )}
          </div>
          <div className="truncate text-xs text-zinc-500">{info?.text_preview ?? doc.head.text_preview}</div>
          {(planned === "review" || info) && (info?.reasons ?? doc.head.reasons) && (
            <div className="text-[11px] text-amber-400/80">
              {info ? "Read again after the cut: " : "Why to look: "}
              {info?.reasons ?? doc.head.reasons}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- expenses */

type StagedRow = RouterOutputs["receipts"]["staged"]["rows"][number];

function ExpensesTab() {
  const utils = api.useUtils();
  const staged = api.receipts.staged.useQuery({ limit: 200 });
  const categories = api.categories.getAll.useQuery();
  const [filter, setFilter] = useState<"all" | "look" | "ready">("all");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [focus, setFocus] = useState<number | null>(null);
  const refresh = () => Promise.all([utils.receipts.staged.invalidate(), utils.validation.invalidate()]);
  const approve = api.receipts.approveStaged.useMutation({
    onSuccess: async () => {
      setSelected(new Set());
      await refresh();
    },
  });
  const remove = api.expenses.deleteExpenses.useMutation({
    onSuccess: async () => {
      setSelected(new Set());
      await refresh();
    },
  });
  const update = api.receipts.updateStaged.useMutation({ onSuccess: refresh });

  const rows = (staged.data?.rows ?? []).filter((r) =>
    filter === "all" ? true : filter === "look" ? r.look.length > 0 : r.look.length === 0,
  );
  const focused = staged.data?.rows.find((r) => r.id === focus) ?? rows[0];
  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_34rem]">
      <div>
        <div className="sticky top-0 z-10 mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950/95 p-2 backdrop-blur">
          {(["all", "look", "ready"] as const).map((key) => (
            <button
              key={key}
              onClick={() => setFilter(key)}
              className={`rounded px-2 py-1 text-xs ${filter === key ? "bg-zinc-700 text-zinc-100" : "text-zinc-400 hover:text-zinc-200"}`}
            >
              {key === "all" ? "All" : key === "look" ? "Needs a look" : "Ready"}
            </button>
          ))}
          <span className="text-xs text-zinc-500">
            showing {rows.length} of {staged.data?.total ?? 0} (newest first)
          </span>
          <button
            onClick={() => setSelected(new Set(rows.filter((r) => r.look.length === 0).map((r) => r.id)))}
            className={`${button} ml-auto bg-zinc-800 text-zinc-200 hover:bg-zinc-700`}
          >
            Select ready
          </button>
          <button
            onClick={() => approve.mutate({ ids: [...selected] })}
            disabled={selected.size === 0 || approve.isPending}
            className={`${button} bg-emerald-700 text-white hover:bg-emerald-600`}
          >
            Approve {selected.size || ""}
          </button>
          <button
            onClick={() => {
              if (confirm(`Delete ${selected.size} expenses? Their receipts stay filed and are not made into expenses again.`))
                remove.mutate({ ids: [...selected] });
            }}
            disabled={selected.size === 0 || remove.isPending}
            className={`${button} bg-zinc-800 text-zinc-300 hover:bg-red-900`}
            title="Not an expense (a quote, a duplicate)"
          >
            Not an expense
          </button>
        </div>
        {approve.data && approve.data.missingCategory.length > 0 && (
          <p className="mb-2 text-sm text-amber-400">
            {approve.data.missingCategory.length} not approved: choose a category first.
          </p>
        )}
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-zinc-500">
            <tr>
              <th className="w-6 py-1">
                <input
                  type="checkbox"
                  checked={rows.length > 0 && rows.every((r) => selected.has(r.id))}
                  onChange={(e) => setSelected(e.target.checked ? new Set(rows.map((r) => r.id)) : new Set())}
                />
              </th>
              <th className="pr-3">Date</th>
              <th>Merchant</th>
              <th className="text-right">Amount</th>
              <th>Category</th>
              <th>Card</th>
              <th>Who</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <StagedExpenseRow
                key={row.id}
                row={row}
                selected={selected.has(row.id)}
                focused={focused?.id === row.id}
                onToggle={() => toggle(row.id)}
                onFocus={() => setFocus(row.id)}
                categories={categories.data ?? []}
                onUpdate={(changes) => update.mutate({ id: row.id, ...changes })}
              />
            ))}
          </tbody>
        </table>
        {staged.data && staged.data.total === 0 && (
          <p className="p-6 text-center text-sm text-zinc-500">No expenses from receipts waiting for approval.</p>
        )}
      </div>
      <aside className="hidden xl:block">
        <div className="sticky top-2">
          {focused ? <DocumentViewer id={focused.documentId} /> : <p className="text-sm text-zinc-500">Pick an expense</p>}
        </div>
      </aside>
    </div>
  );
}

type Category = RouterOutputs["categories"]["getAll"][number];

function StagedExpenseRow({
  row,
  selected,
  focused,
  onToggle,
  onFocus,
  categories,
  onUpdate,
}: {
  row: StagedRow;
  selected: boolean;
  focused: boolean;
  onToggle: () => void;
  onFocus: () => void;
  categories: Category[];
  onUpdate: (changes: { amount?: number; date?: string; categoryId?: number; subtypeId?: number | null }) => void;
}) {
  const [amount, setAmount] = useState(row.amount.toFixed(2));
  useEffect(() => setAmount(row.amount.toFixed(2)), [row.amount]);
  const category = categories.find((c) => c.id === row.categoryId);
  return (
    <tr onClick={onFocus} className={`cursor-pointer border-t border-zinc-800/70 ${focused ? "bg-zinc-800/50" : "hover:bg-zinc-900"}`}>
      <td className="py-1.5" onClick={(e) => e.stopPropagation()}>
        <input type="checkbox" checked={selected} onChange={onToggle} />
      </td>
      <td className="whitespace-nowrap pr-3 text-zinc-300">{row.date}</td>
      <td className="max-w-[14rem] truncate pr-2 text-zinc-200" title={row.path}>
        {row.merchant}
      </td>
      <td className="pr-2 text-right" onClick={(e) => e.stopPropagation()}>
        <input
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          onBlur={() => {
            const parsed = Number(amount);
            if (parsed > 0 && parsed !== row.amount) onUpdate({ amount: Math.round(parsed * 100) / 100 });
          }}
          className={`${input} w-20 text-right`}
        />
      </td>
      <td onClick={(e) => e.stopPropagation()}>
        <div className="flex gap-1">
          <select
            value={row.categoryId ?? ""}
            onChange={(e) => onUpdate({ categoryId: Number(e.target.value) })}
            className={`${input} max-w-[11rem]`}
          >
            <option value="" disabled>
              (category)
            </option>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          {category && category.subtypes.length > 0 && (
            <select
              value={row.subtypeId ?? ""}
              onChange={(e) => onUpdate({ subtypeId: e.target.value ? Number(e.target.value) : null })}
              className={`${input} max-w-[9rem]`}
            >
              <option value="">—</option>
              {category.subtypes.map((s: { id: number; name: string }) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          )}
        </div>
      </td>
      <td className="whitespace-nowrap text-xs text-zinc-400">{row.card ?? "—"}</td>
      <td className="whitespace-nowrap text-xs text-zinc-400">{row.spender ?? row.tag ?? "—"}</td>
      <td className="text-[11px] text-amber-400/90">{row.look.join(" · ")}</td>
    </tr>
  );
}
