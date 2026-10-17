import { cookies } from "next/headers";

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000";

export const SESSION_COOKIE = process.env.SESSION_COOKIE_NAME || "gw_session";

export interface SessionUser {
  id: string;
  email: string;
  is_active: boolean;
  is_admin: boolean;
  created_at: string | null;
  last_login_at: string | null;
}

async function backend(path: string, init: RequestInit = {}): Promise<Response> {
  const jar = await cookies();
  const token = jar.get(SESSION_COOKIE)?.value;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init.headers as Record<string, string>) || {}),
  };
  if (token) {
    headers["Cookie"] = `${SESSION_COOKIE}=${token}`;
  }

  return fetch(`${BACKEND}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
}

export /**
 * Copy the backend's session cookie into the Next.js cookie store.
 *
 * The backend sets `Set-Cookie` on its response, but that response goes to this
 * server, not to the browser: `login()` runs server-side, and a cookie set on a
 * fetch response is discarded when the fetch ends. Without this the login
 * returns 200, the browser is sent on without a cookie, and the next
 * `/auth/me` is a 401 -- which is exactly the symptom.
 */
async function adoptSessionCookie(res: Response): Promise<void> {
  const setCookie = res.headers.get("set-cookie");
  if (!setCookie) return;

  const pair = setCookie.split(";")[0];
  const eq = pair.indexOf("=");
  if (eq < 0) return;

  const value = pair.slice(eq + 1);
  if (!value) return;

  const jar = await cookies();
  jar.set(SESSION_COOKIE, decodeURIComponent(value), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
  });
}

export async function getSession(): Promise<SessionUser | null> {
  try {
    const res = await backend("/auth/me");
    if (!res.ok) return null;
    const data = (await res.json()) as { user: SessionUser };
    return data.user ?? null;
  } catch {
    return null;
  }
}

export async function login(
  email: string,
  password: string
): Promise<{ user: SessionUser } | { error: string }> {
  const res = await backend("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    return { error: (data && data.detail) || "Unable to sign in" };
  }
  await adoptSessionCookie(res);
  return data as { user: SessionUser };
}

export async function register(
  email: string,
  password: string
): Promise<{ user: SessionUser } | { error: string }> {
  const res = await backend("/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    return { error: (data && data.detail) || "Unable to create the account" };
  }
  await adoptSessionCookie(res);
  return data as { user: SessionUser };
}

export async function logout(): Promise<void> {
  await backend("/auth/logout", { method: "POST" }).catch(() => null);

  // Delete locally too. The backend's Set-Cookie arrives here, not in the
  // browser, so clearing only the backend side would leave the Next.js cookie
  // in place and the user still appears signed in.
  const jar = await cookies();
  jar.delete(SESSION_COOKIE);
}