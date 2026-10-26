/** Session cookie for signing in from other devices (see middleware.ts). */
export const SESSION_COOKIE = "expenses_session";

/** An absolute URL on the address the browser used (next start reports localhost otherwise). */
export function sameHostUrl(request: Request, pathAndQuery: string) {
  const host = request.headers.get("host") ?? "localhost";
  return new URL(pathAndQuery, `http://${host}`);
}

export async function sessionToken(password: string) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`expenses-app:${password}`));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}
