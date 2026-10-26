import { NextResponse } from "next/server";
import { SESSION_COOKIE, sameHostUrl, sessionToken } from "~/lib/session";

const DAYS = 180;

export async function POST(request: Request) {
  const form = await request.formData();
  const password = process.env.APP_PASSWORD;
  const given = String(form.get("password") ?? "");
  const next = String(form.get("next") ?? "/");
  const target = next.startsWith("/") && !next.startsWith("//") ? next : "/";
  if (!password || (await sessionToken(given)) !== (await sessionToken(password))) {
    await new Promise((resolve) => setTimeout(resolve, 800)); // slow down guessing
    return NextResponse.redirect(sameHostUrl(request, `/login?error=1&next=${encodeURIComponent(target)}`), 303);
  }
  const response = NextResponse.redirect(sameHostUrl(request, target), 303);
  response.cookies.set(SESSION_COOKIE, await sessionToken(password), {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: DAYS * 24 * 3600,
  });
  return response;
}
