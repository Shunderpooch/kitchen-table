export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const { next, error } = await searchParams;
  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-950">
      <form method="post" action="/api/login" className="w-80 space-y-3 rounded-lg border border-zinc-800 bg-zinc-900 p-6">
        <h1 className="text-lg font-semibold text-zinc-100">Expenses</h1>
        <input type="hidden" name="next" value={next ?? "/inbox"} />
        <input
          type="password"
          name="password"
          autoFocus
          placeholder="Password"
          className="w-full rounded border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
        />
        {error && <p className="text-sm text-red-400">Wrong password</p>}
        <button type="submit" className="w-full rounded bg-sky-600 py-2 text-sm text-white hover:bg-sky-500">
          Sign in
        </button>
      </form>
    </div>
  );
}
