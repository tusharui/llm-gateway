import { NextResponse } from "next/server";
import { newRequestId, proxyHeaders, responseHeaders, errorHeaders } from "./_proxy";

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000";

/**
 * The operator key, injected server-side for routes the gateway treats as
 * privileged.
 *
 * `/analytics` and `/chat-history` require authentication, and key management
 * requires the operator key specifically. These proxies call the API from the
 * server, so the browser never holds the key and the request is made as the
 * operator. A browser-supplied Authorization header still wins, which is what
 * lets a developer point the dashboard at their own key without redeploying.
 *
 * Never expose this to the client. `NEXT_PUBLIC_` would ship it to the
 * browser; this is a plain server-only variable.
 */
const GATEWAY_API_KEY = process.env.GATEWAY_API_KEY || "sk-gateway-dev-key";

export function authHeaders(request: Request): Record<string, string> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${GATEWAY_API_KEY}`,
  };
  const auth = request.headers.get("authorization");
  if (auth) {
    headers["Authorization"] = auth;
  }
  return headers;
}

/**
 * Proxy a backend response without masking its status code.
 *
 * The previous shape returned `502 "Backend unreachable"` for every non-2xx,
 * so a genuine 401 or 429 from the gateway surfaced in the dashboard as a
 * connectivity failure. That is actively misleading during exactly the
 * incidents it matters: an expired key looked like a network problem.
 *
 * Only a genuine transport failure -- or an upstream that says it is itself
 * unavailable -- is a 502 here. Anything the backend actually answered with is
 * passed through with its own status and body, so the error envelope and the
 * request id survive the hop.
 */
export async function forward(
  request: Request,
  path: string,
  init: RequestInit = {}
): Promise<Response> {
  const requestId = newRequestId();
  try {
    const res = await fetch(`${BACKEND}${path}`, {
      ...init,
      headers: {
        ...(init.headers as Record<string, string> | undefined),
        ...proxyHeaders(authHeaders(request)),
      },
    });

    if (res.status === 502 || res.status === 503 || res.status === 504) {
      return NextResponse.json(
        { error: "Backend unreachable" },
        { status: 502, headers: errorHeaders(requestId) }
      );
    }

    const data = await res.json().catch(() => null);
    return NextResponse.json(data, {
      status: res.status,
      headers: responseHeaders(res, requestId),
    });
  } catch {
    return NextResponse.json(
      { error: "Backend unreachable" },
      { status: 502, headers: errorHeaders(requestId) }
    );
  }
}