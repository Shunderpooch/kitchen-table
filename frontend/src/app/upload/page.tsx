"use client";

import { MEMBER_A, MEMBER_B } from "~/lib/household";

import { useState, useCallback } from "react";
import Link from "next/link";
import { Navigation } from "~/app/_components/navigation";
import { api } from "~/trpc/react";

interface ProcessingResult {
  success: boolean;
  total: number;
  categorized: number;
  needsValidation: number;
  duplicates: number;
  paymentsOrCreditsSkipped: number;
  errors: string[];
  files: string[];
}

const SPENDER_OPTIONS = [
  { value: "", label: "Leave blank / use file data" },
  { value: MEMBER_A, label: MEMBER_A },
  { value: MEMBER_B, label: MEMBER_B },
];

const SOURCE_OWNER_OPTIONS = [
  { value: MEMBER_A, label: MEMBER_A },
  { value: MEMBER_B, label: MEMBER_B },
  { value: "Shared", label: "Shared" },
] as const;

type SourceOwner = (typeof SOURCE_OWNER_OPTIONS)[number]["value"];

export default function UploadPage() {
  const [processingResult, setProcessingResult] =
    useState<ProcessingResult | null>(null);

  return (
    <div className="min-h-screen bg-zinc-950">
      <Navigation />
      <main className="container mx-auto px-4 py-8">
        <div className="mb-8">
          <h1 className="text-2xl font-semibold text-zinc-100">
            Upload & Process
          </h1>
          <p className="text-zinc-400">
            Import expense files from your credit cards and bank accounts
          </p>
        </div>

        <div className="grid gap-8 lg:grid-cols-2">
          <div className="space-y-6">
            <FileUploader onProcessComplete={setProcessingResult} />
            <StatementSourcesStatus />
          </div>

          <div className="space-y-6">
            <ProcessingResults result={processingResult} />
          </div>
        </div>
      </main>
    </div>
  );
}

function FileUploader({
  onProcessComplete,
}: {
  onProcessComplete: (result: ProcessingResult) => void;
}) {
  const utils = api.useUtils();
  const statementSourcesQuery = api.statementSources.getAll.useQuery();
  const createSourceMutation = api.statementSources.create.useMutation({
    onSuccess: async (created) => {
      await utils.statementSources.getAll.invalidate();
      if (created) setSelectedCard(created.name);
      setIsAddingSource(false);
      setNewSourceName("");
      setNewSourceType("credit_card");
      setNewSourceOwner(MEMBER_A);
      setSourceError(null);
    },
  });
  const [isDragging, setIsDragging] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedCard, setSelectedCard] = useState<string>("");
  const [selectedSpender, setSelectedSpender] = useState<string>("");
  const [isAddingSource, setIsAddingSource] = useState(false);
  const [newSourceName, setNewSourceName] = useState("");
  const [newSourceType, setNewSourceType] = useState<"credit_card" | "bank_account">("credit_card");
  const [newSourceOwner, setNewSourceOwner] = useState<SourceOwner>(MEMBER_A);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const groupedSources = (statementSourcesQuery.data ?? []).reduce<
    Record<string, NonNullable<typeof statementSourcesQuery.data>>
  >((groups, source) => {
    const ownerLabel = source.owner ?? "Unassigned";
    groups[ownerLabel] ??= [];
    groups[ownerLabel].push(source);
    return groups;
  }, {});

  const handleDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    setIsDragging(false);

    const droppedFiles = Array.from(event.dataTransfer.files).filter(
      (file) =>
        file.name.endsWith(".csv") ||
        file.name.endsWith(".xlsx") ||
        file.name.endsWith(".xls") ||
        file.name.endsWith(".pdf"),
    );

    setFiles((previous) => [...previous, ...droppedFiles]);
    setError(null);
  }, []);

  const handleFileSelect = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      if (event.target.files) {
        const selectedFiles = Array.from(event.target.files);
        const supportedFiles = selectedFiles.filter(
          (file) =>
            file.name.endsWith(".csv") ||
            file.name.endsWith(".xlsx") ||
            file.name.endsWith(".xls") ||
            file.name.endsWith(".pdf"),
        );
        setFiles((previous) => [...previous, ...supportedFiles]);
        setError(null);
      }
    },
    [],
  );

  const handleProcess = async () => {
    setIsProcessing(true);
    setError(null);

    try {
      const formData = new FormData();
      files.forEach((file) => {
        formData.append("files", file);
      });

      if (selectedCard) {
        formData.append("card", selectedCard);
      }

      if (selectedSpender) {
        formData.append("spender", selectedSpender);
      }

      const response = await fetch("/api/upload", {
        method: "POST",
        body: formData,
      });

      const result = (await response.json()) as ProcessingResult & {
        error?: string;
      };

      if (!response.ok) {
        throw new Error(result.error || "Upload failed");
      }

      onProcessComplete(result);
      await Promise.all([
        utils.statementSources.getAll.invalidate(),
        utils.validation.getStats.invalidate(),
      ]);
      setFiles([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to process files");
    } finally {
      setIsProcessing(false);
    }
  };

  const removeFile = (index: number) => {
    setFiles((previous) => previous.filter((_, itemIndex) => itemIndex !== index));
  };

  const handleCreateSource = async () => {
    const trimmedName = newSourceName.trim();
    if (!trimmedName) {
      setSourceError("Enter a source name.");
      return;
    }

    const existingNames = new Set(
      (statementSourcesQuery.data ?? []).map((source) => source.name.toLowerCase()),
    );
    if (existingNames.has(trimmedName.toLowerCase())) {
      setSourceError("That source already exists.");
      return;
    }

    try {
      await createSourceMutation.mutateAsync({
        name: trimmedName,
        sourceType: newSourceType,
        owner: newSourceOwner,
        expectedFrequencyDays: 30,
      });
    } catch (mutationError) {
      setSourceError(
        mutationError instanceof Error ? mutationError.message : "Failed to create source.",
      );
    }
  };

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6">
      <h2 className="mb-4 text-lg font-medium text-zinc-100">Upload Files</h2>

      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`relative flex min-h-48 flex-col items-center justify-center rounded-lg border-2 border-dashed transition-colors ${
          isDragging
            ? "border-violet-500 bg-violet-500/10"
            : "border-zinc-700 bg-zinc-800/30 hover:border-zinc-600"
        }`}
      >
        <input
          type="file"
          accept=".csv,.xlsx,.xls,.pdf"
          multiple
          onChange={handleFileSelect}
          className="absolute inset-0 cursor-pointer opacity-0"
        />
        <div className="text-center">
          <div className="mb-2 text-4xl">[ ]</div>
          <p className="text-zinc-100">
            Drag and drop files here or{" "}
            <span className="text-violet-400">browse</span>
          </p>
          <p className="mt-1 text-sm text-zinc-500">
            CSV, Excel, and supported PDF statements
          </p>
        </div>
      </div>

      {error && (
        <div className="mt-4 rounded-lg bg-red-500/10 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {files.length > 0 && (
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div>
            <label className="mb-2 block text-sm text-zinc-400">
              Which card is this statement from?
            </label>
            <select
              value={selectedCard}
              onChange={(event) => setSelectedCard(event.target.value)}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
            >
              <option value="">Auto-detect from filename</option>
              {Object.entries(groupedSources).map(([owner, ownerSources]) => (
                <optgroup key={owner} label={owner}>
                  {ownerSources.map((source) => (
                    <option key={source.id} value={source.name}>
                      {source.name}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
            <div className="mt-2 flex items-center gap-3 text-sm">
              <button
                type="button"
                onClick={() => {
                  setIsAddingSource((current) => !current);
                  setSourceError(null);
                }}
                className="text-violet-400 transition-colors hover:text-violet-300"
              >
                {isAddingSource ? "Cancel new source" : "+ Add new card or account source"}
              </button>
              {sourceError ? <span className="text-rose-400">{sourceError}</span> : null}
            </div>
            {isAddingSource ? (
              <div className="mt-3 rounded-lg border border-zinc-800 bg-zinc-950/70 p-4">
                <p className="mb-3 text-sm text-zinc-400">
                  Create a real statement source once, then use it in future uploads.
                </p>
                <div className="grid gap-3 md:grid-cols-[minmax(0,2fr)_150px_140px_auto]">
                  <input
                    type="text"
                    value={newSourceName}
                    onChange={(event) => setNewSourceName(event.target.value)}
                    placeholder={`${MEMBER_A} Visa`}
                    className="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
                  />
                  <select
                    value={newSourceType}
                    onChange={(event) =>
                      setNewSourceType(event.target.value as "credit_card" | "bank_account")
                    }
                    className="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
                  >
                    <option value="credit_card">Credit card</option>
                    <option value="bank_account">Bank account</option>
                  </select>
                  <select
                    value={newSourceOwner}
                    onChange={(event) =>
                      setNewSourceOwner(event.target.value as SourceOwner)
                    }
                    className="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
                  >
                    {SOURCE_OWNER_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={handleCreateSource}
                    disabled={createSourceMutation.isPending}
                    className="rounded-lg border border-violet-500/40 bg-violet-500/10 px-4 py-2 text-sm text-zinc-100 transition-colors hover:bg-violet-500/20 disabled:opacity-40"
                  >
                    {createSourceMutation.isPending ? "Creating..." : "Create source"}
                  </button>
                </div>
              </div>
            ) : null}
          </div>
          <div>
            <label className="mb-2 block text-sm text-zinc-400">
              Who spent on this statement?
            </label>
            <select
              value={selectedSpender}
              onChange={(event) => setSelectedSpender(event.target.value)}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 focus:border-violet-500 focus:outline-none"
            >
              {SPENDER_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      {files.length > 0 && (
        <div className="mt-4 space-y-2">
          {files.map((file, index) => (
            <div
              key={`${file.name}-${index}`}
              className="flex items-center justify-between rounded-lg bg-zinc-800 px-4 py-2"
            >
              <div className="flex items-center gap-3">
                <span className="text-lg">
                  {file.name.endsWith(".csv")
                    ? "CSV"
                    : file.name.endsWith(".pdf")
                      ? "PDF"
                      : "XLS"}
                </span>
                <div>
                  <p className="text-sm font-medium text-zinc-100">
                    {file.name}
                  </p>
                  <p className="text-xs text-zinc-500">
                    {(file.size / 1024).toFixed(1)} KB
                  </p>
                </div>
              </div>
              <button
                onClick={() => removeFile(index)}
                className="text-zinc-500 hover:text-zinc-300"
              >
                X
              </button>
            </div>
          ))}

          <button
            onClick={handleProcess}
            disabled={isProcessing}
            className="mt-4 w-full rounded-lg bg-violet-600 px-4 py-2 font-medium text-white transition-colors hover:bg-violet-700 disabled:opacity-50"
          >
            {isProcessing ? (
              <span className="flex items-center justify-center gap-2">
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                Processing...
              </span>
            ) : (
              `Process ${files.length} file${files.length > 1 ? "s" : ""}`
            )}
          </button>
        </div>
      )}
    </div>
  );
}

function StatementSourcesStatus() {
  const { data: sources, isLoading } = api.statementSources.getAll.useQuery();

  if (isLoading) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6">
        <div className="animate-pulse space-y-3">
          <div className="h-5 w-40 rounded bg-zinc-800" />
          <div className="h-12 rounded bg-zinc-800" />
          <div className="h-12 rounded bg-zinc-800" />
          <div className="h-12 rounded bg-zinc-800" />
        </div>
      </div>
    );
  }

  const staleCount = sources?.filter((source) => source.isStale).length ?? 0;
  const groupedSources = (sources ?? []).reduce<Record<string, NonNullable<typeof sources>>>(
    (groups, source) => {
      const ownerLabel = source.owner ?? "Unassigned";
      groups[ownerLabel] ??= [];
      groups[ownerLabel].push(source);
      return groups;
    },
    {},
  );

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-medium text-zinc-100">Statement Sources</h2>
        {staleCount > 0 && (
          <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-sm text-amber-400">
            {staleCount} stale
          </span>
        )}
      </div>

      {sources && sources.length > 0 ? (
        <div className="space-y-4">
          {Object.entries(groupedSources).map(([owner, ownerSources]) => (
            <div key={owner} className="space-y-2">
              <h3 className="text-sm font-medium uppercase tracking-wide text-zinc-500">
                {owner}
              </h3>
              {ownerSources.map((source) => (
                <div
                  key={source.id}
                  className={`flex items-center justify-between rounded-lg px-4 py-3 ${
                    source.isStale ? "bg-amber-500/5" : "bg-zinc-800/50"
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <span
                      className={`h-2 w-2 rounded-full ${
                        source.isStale ? "bg-amber-400" : "bg-emerald-400"
                      }`}
                    />
                    <div>
                      <p className="text-zinc-100">{source.name}</p>
                      <p className="text-xs text-zinc-500">
                        {source.latestFilename ?? "No uploaded file recorded yet"}
                      </p>
                    </div>
                  </div>
                  <div className="text-right text-sm text-zinc-500">
                    <p>
                      {source.lastImportDate
                        ? new Date(source.lastImportDate).toLocaleDateString()
                        : "Never"}
                    </p>
                    <p className="text-xs text-zinc-600">
                      {source.latestProcessedAt
                        ? new Date(source.latestProcessedAt).toLocaleTimeString([], {
                            hour: "numeric",
                            minute: "2-digit",
                          })
                        : ""}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
      ) : (
        <p className="text-center text-zinc-500">No statement sources yet</p>
      )}
    </div>
  );
}

function ProcessingResults({ result }: { result: ProcessingResult | null }) {
  const { data: validationStats } = api.validation.getStats.useQuery();
  const pendingCount = validationStats?.pending ?? 0;

  if (!result) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6">
        <div className="text-center">
          <div className="mb-4 text-6xl">::</div>
          <h3 className="text-lg font-medium text-zinc-100">
            Ready to Process
          </h3>
          <p className="mt-2 text-zinc-500">
            Upload expense files to see processing results
          </p>
        </div>

        {pendingCount > 0 && (
          <Link
            href="/validation"
            className="mt-6 block w-full rounded-lg bg-amber-500/10 px-4 py-3 text-center font-medium text-amber-400 transition-colors hover:bg-amber-500/20"
          >
            {pendingCount} items pending validation {"->"}
          </Link>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6">
      <h2 className="mb-4 text-lg font-medium text-zinc-100">
        Processing Results
      </h2>

      {!result.success && result.errors.length > 0 && (
        <div className="mb-4 rounded-lg bg-red-500/10 px-4 py-3 text-sm text-red-400">
          {result.errors.join(", ")}
        </div>
      )}

      {!result.success && (
        <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-3 text-sm text-red-300">
          Processing failed. Fix the error before continuing to validation.
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="rounded-lg bg-zinc-800 p-4">
          <p className="text-sm text-zinc-400">Total Expenses</p>
          <p className="text-2xl font-semibold text-zinc-100">{result.total}</p>
        </div>
        <div className="rounded-lg bg-emerald-500/10 p-4">
          <p className="text-sm text-emerald-400">Auto-Categorized</p>
          <p className="text-2xl font-semibold text-emerald-400">
            {result.categorized}
          </p>
        </div>
        <div className="rounded-lg bg-amber-500/10 p-4">
          <p className="text-sm text-amber-400">Needs Validation</p>
          <p className="text-2xl font-semibold text-amber-400">
            {result.needsValidation}
          </p>
        </div>
        <div className="rounded-lg bg-zinc-800 p-4">
          <p className="text-sm text-zinc-400">Duplicates Found</p>
          <p className="text-2xl font-semibold text-zinc-100">
            {result.duplicates}
          </p>
        </div>
        <div className="rounded-lg bg-zinc-800 p-4">
          <p className="text-sm text-zinc-400">Payments/Credits Skipped</p>
          <p className="text-2xl font-semibold text-zinc-100">
            {result.paymentsOrCreditsSkipped}
          </p>
        </div>
      </div>

      {result.success ? (
        <Link
          href="/validation"
          className="mt-4 block w-full rounded-lg bg-violet-600 px-4 py-3 text-center font-medium text-white transition-colors hover:bg-violet-700"
        >
          Go to Validation Queue {pendingCount > 0 && `(${pendingCount} pending)`} {"->"}
        </Link>
      ) : (
        <div className="mt-4 rounded-lg border border-zinc-800 bg-zinc-950/70 px-4 py-3 text-center text-sm text-zinc-500">
          Validation access is hidden until processing succeeds.
        </div>
      )}

      {result.duplicates > 0 && (
        <p className="mt-3 text-center text-sm text-zinc-500">
          {result.duplicates} duplicate transactions were skipped
        </p>
      )}

      {result.paymentsOrCreditsSkipped > 0 && (
        <p className="mt-2 text-center text-sm text-zinc-500">
          {result.paymentsOrCreditsSkipped} payment or statement credit rows were skipped
        </p>
      )}
    </div>
  );
}
