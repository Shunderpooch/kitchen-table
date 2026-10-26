import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, sameHostUrl, sessionToken } from "~/lib/session";

/**
 * Sign-in for using the app from other devices on the home network.
 *
 * With APP_PASSWORD set (frontend/.env), every page and API needs the session
 * cookie that /login hands out. Without it the app only answers requests
 * addressed to this computer (localhost), as before.
 */
const OPEN_PATHS = ["/login", "/api/login"];

function isLoopback(host: string | null) {
  const name = (host ?? "").replace(/:\d+$/, "").replace(/^\[|\]$/g, "");
  return name === "localhost" || name === "127.0.0.1" || name === "::1";
}

export async function middleware(request: NextRequest) {
  const password = process.env.APP_PASSWORD;
  const { pathname, search } = request.nextUrl;
  if (!password) {
    if (isLoopback(request.headers.get("host"))) return NextResponse.next();
    return new NextResponse("Set APP_PASSWORD in frontend/.env to use the app from other devices.", { status: 403 });
  }
  if (OPEN_PATHS.includes(pathname)) return NextResponse.next();
  if (request.cookies.get(SESSION_COOKIE)?.value === (await sessionToken(password))) return NextResponse.next();
  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "Sign in first" }, { status: 401 });
  }
  const login = sameHostUrl(request, "/login");
  login.searchParams.set("next", pathname + search);
  return NextResponse.redirect(login);
}

export const config = {
  // Node runtime: APP_PASSWORD is read when the server runs, not baked into the build.
  runtime: "nodejs",
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
