"use client";

import { MEMBER_A, MEMBER_B } from "~/lib/household";

import { useMemo, useState } from "react";
import { Navigation } from "~/app/_components/navigation";

function formatDateInput(value: Date) {
  return value.toISOString().slice(0, 10);
}

function startOfMonth(value: Date) {
  return new Date(value.getFullYear(), value.getMonth(), 1);
}

function startOfYear(value: Date) {
  return new Date(value.getFullYear(), 0, 1);
}

export default function ExportPage() {
  const today = useMemo(() => new Date(), []);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState(formatDateInput(today));
  const [includeDuplicates, setIncludeDuplicates] = useState(false);

  const downloadUrl = useMemo(() => {
    const params = new URLSearchParams();
    if (startDate) {
      params.set("startDate", startDate);
    }
    if (endDate) {
      params.set("endDate", endDate);
    }
    if (includeDuplicates) {
      params.set("includeDuplicates", "true");
    }

    return `/api/export/household?${params.toString()}`;
  }, [endDate, includeDuplicates, startDate]);

  const applyPreset = (preset: "month" | "year" | "all") => {
    if (preset === "all") {
      setStartDate("");
      setEndDate("");
      return;
    }

    if (preset === "month") {
      setStartDate(formatDateInput(startOfMonth(today)));
      setEndDate(formatDateInput(today));
      return;
    }

    setStartDate(formatDateInput(startOfYear(today)));
    setEndDate(formatDateInput(today));
  };

  return (
    <div className="min-h-screen bg-zinc-950">
      <Navigation />
      <main className="container mx-auto px-4 py-8">
        <div className="mx-auto max-w-3xl">
          <div className="mb-8">
            <h1 className="text-2xl font-semibold text-zinc-100">Export</h1>
            <p className="text-zinc-400">
              Export the database in the household expenses sheet format.
            </p>
          </div>

          <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6">
            <div className="mb-6 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => applyPreset("month")}
                className="rounded-full border border-zinc-700 bg-zinc-900 px-4 py-2 text-sm text-zinc-300 transition-colors hover:border-violet-500 hover:text-zinc-100"
              >
                This month
              </button>
              <button
                type="button"
                onClick={() => applyPreset("year")}
                className="rounded-full border border-zinc-700 bg-zinc-900 px-4 py-2 text-sm text-zinc-300 transition-colors hover:border-violet-500 hover:text-zinc-100"
              >
                This year
              </button>
              <button
                type="button"
                onClick={() => applyPreset("all")}
                className="rounded-full border border-zinc-700 bg-zinc-900 px-4 py-2 text-sm text-zinc-300 transition-colors hover:border-violet-500 hover:text-zinc-100"
              >
                All time
              </button>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-2 block text-sm text-zinc-400">
                  Start date
                </label>
                <input
                  type="date"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
                />
              </div>
              <div>
                <label className="mb-2 block text-sm text-zinc-400">
                  End date
                </label>
                <input
                  type="date"
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
                />
              </div>
            </div>

            <label className="mt-4 flex items-center gap-3 text-sm text-zinc-400">
              <input
                type="checkbox"
                checked={includeDuplicates}
                onChange={(e) => setIncludeDuplicates(e.target.checked)}
                className="h-4 w-4 rounded border-zinc-700 bg-zinc-800 text-violet-600 focus:ring-violet-500"
              />
              Include rows marked as duplicates
            </label>

            <div className="mt-6 rounded-xl border border-zinc-800 bg-zinc-950/70 p-4 text-sm text-zinc-400">
              <p className="text-zinc-300">Export shape</p>
              <p className="mt-1">
                Date, Type, Subtype, Merchant, Amount, Split, {MEMBER_B} Spend, Spender, {MEMBER_A} Spend,
                Card, Log_to_work, Tag, Link, Track with Friends
              </p>
            </div>

            <a
              href={downloadUrl}
              className="mt-6 inline-flex w-full items-center justify-center rounded-lg bg-violet-600 px-4 py-3 font-medium text-white transition-colors hover:bg-violet-700"
            >
              Download household-format CSV
            </a>
          </div>
        </div>
      </main>
    </div>
  );
}
