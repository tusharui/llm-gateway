import { NextResponse } from "next/server";
import { getSession, type SessionUser } from "@/lib/session";

/**
 * Session guard for the API proxies.
 *
 * `src/proxy.ts` deliberately excludes `/api`, so these routes are not covered
 * by the redirect it performs. They are covered instead here, where the token
 * is actually verified.
 *
 * This matters because the proxies call the gateway with the operator key
 * injected server-side: without a check of their own, anyone who can reach
 * `/api/chat` spends the owner's provider balance, which is exactly the
 * problem the login page exists to close.
 *
 * Returns the error response to return, or null when the caller may proceed.
 */
export async function requireUser(): Promise<NextResponse | null> {
  const user = await getSession();
  if (!user) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }
  return null;
}

export async function requireAdmin(): Promise<NextResponse | null> {
  const user = await getSession();
  if (!user) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }
  if (!user.is_admin) {
    return NextResponse.json(
      { error: "This account is not an administrator" },
      { status: 403 }
    );
  }
  return null;
}

export type { SessionUser };