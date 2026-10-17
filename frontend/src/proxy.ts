import { NextResponse, type NextRequest } from "next/server";

const SESSION_COOKIE = process.env.SESSION_COOKIE_NAME || "gw_session";

/**
 * Cheap presence check only.
 *
 * This runs on every matched request, so it must not do IO: verifying the token
 * against the database here would put a network round-trip in front of every
 * page render. A forged cookie gets past this check and is then rejected by
 * getSession() in the layout and by the proxy route handlers, which do verify.
 *
 * Its whole job is to send a request with no cookie to /login instead of
 * rendering a dashboard that would immediately fail to load data.
 */
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  if (!request.cookies.get(SESSION_COOKIE)) {
    const url = new URL("/login", request.url);
    if (pathname !== "/") {
      url.searchParams.set("next", `${pathname}${search}`);
    }
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    // Everything except static assets, image optimization and the auth pages
    // themselves. Without the negative match, Proxy runs on CSS and JS too and
    // an expired session turns the whole app into a redirect loop.
    "/((?!api|_next/static|_next/image|favicon.ico|login|register|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};