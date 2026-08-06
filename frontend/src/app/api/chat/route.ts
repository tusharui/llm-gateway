import { NextRequest, NextResponse } from "next/server";
import { newRequestId, proxyHeaders, responseHeaders, errorHeaders } from "../_proxy";

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000";
const GATEWAY_API_KEY = process.env.GATEWAY_API_KEY || "sk-gateway-dev-key";

export async function POST(request: NextRequest) {
  const body = await request.json();
  const requestId = newRequestId();

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${GATEWAY_API_KEY}`,
  };

  const auth = request.headers.get("authorization");
  if (auth) {
    headers["Authorization"] = auth;
  }

  try {
    const res = await fetch(`${BACKEND}/chat`, {
      method: "POST",
      headers: proxyHeaders(headers),
      body: JSON.stringify(body),
    });

    const isStream =
      body.stream === true ||
      (res.headers.get("content-type") || "").includes("text/event-stream");

    if (isStream) {
      return new Response(res.body, {
        status: res.status,
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache, no-transform",
          "X-Accel-Buffering": "no",
          ...responseHeaders(res, requestId),
        },
      });
    }

    const data = await res.json();
    return NextResponse.json(data, { status: res.status, headers: responseHeaders(res, requestId) });
  } catch {
    return NextResponse.json(
      { error: "Backend unreachable" },
      { status: 502, headers: errorHeaders(requestId) }
    );
  }
}
