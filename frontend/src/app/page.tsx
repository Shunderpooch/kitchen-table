import { Suspense } from "react";
import { HydrateClient } from "~/trpc/server";
import { Dashboard } from "./_components/dashboard";
import { Navigation } from "./_components/navigation";

export default async function Home() {
  return (
    <HydrateClient>
      <div className="min-h-screen bg-zinc-950">
        <Navigation />
        <main className="container mx-auto px-4 py-8">
          <Suspense fallback={<DashboardSkeleton />}>
            <Dashboard />
          </Suspense>
        </main>
      </div>
    </HydrateClient>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-6">
      <div className="h-8 w-48 animate-pulse rounded bg-zinc-800" />
      <div className="grid gap-4 md:grid-cols-4">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="h-24 animate-pulse rounded-xl bg-zinc-900" />
        ))}
      </div>
      <div className="h-96 animate-pulse rounded-xl bg-zinc-900" />
    </div>
  );
}
