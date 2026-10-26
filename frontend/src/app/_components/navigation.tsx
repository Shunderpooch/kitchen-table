"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const navItems = [
  { href: "/", label: "Dashboard", icon: "D" },
  { href: "/inbox", label: "Inbox", icon: "I" },
  { href: "/upload", label: "Upload & Process", icon: "U" },
  { href: "/export", label: "Export", icon: "E" },
  { href: "/review", label: "Review & Reflect", icon: "R" },
  { href: "/validation", label: "Validation Queue", icon: "V" },
  { href: "/fixer", label: "Table Fixer", icon: "F" },
  { href: "/scans", label: "Scans", icon: "S" },
  { href: "/documents", label: "Documents", icon: "R" },
];

export function Navigation() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-50 border-b border-zinc-800 bg-zinc-950/80 backdrop-blur-sm">
      <div className="container mx-auto px-4">
        <div className="flex h-16 items-center justify-between">
          <Link href="/" className="flex items-center gap-2">
            <span className="rounded-md bg-zinc-800 px-2 py-1 text-sm font-semibold text-zinc-100">
              AE
            </span>
            <span className="font-semibold text-zinc-100">
              Expenses
            </span>
          </Link>

          <nav className="flex items-center gap-1">
            {navItems.map((item) => {
              const isActive = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                    isActive
                      ? "bg-zinc-800 text-zinc-100"
                      : "text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-100"
                  }`}
                >
                  <span className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-zinc-700 text-[11px]">
                    {item.icon}
                  </span>
                  <span className="hidden sm:inline">{item.label}</span>
                </Link>
              );
            })}
          </nav>

          <div className="flex items-center gap-4">
            <StatementFreshnessIndicator />
          </div>
        </div>
      </div>
    </header>
  );
}

function StatementFreshnessIndicator() {
  const staleCount = 0;

  if (staleCount === 0) {
    return (
      <div className="flex items-center gap-2 rounded-full bg-emerald-500/10 px-3 py-1 text-sm text-emerald-400">
        <span className="h-2 w-2 rounded-full bg-emerald-400" />
        <span className="hidden sm:inline">All current</span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2 rounded-full bg-amber-500/10 px-3 py-1 text-sm text-amber-400">
      <span className="h-2 w-2 animate-pulse rounded-full bg-amber-400" />
      <span className="hidden sm:inline">{staleCount} stale</span>
    </div>
  );
}
