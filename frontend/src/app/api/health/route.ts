import { NextResponse } from "next/server";
import { newRequestId, proxyHeaders, responseHeaders, errorHeaders } from "../_proxy";

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000";

export async function GET() {
  const requestId = newRequestId();
  try {
    const res = await fetch(`${BACKEND}/health`, { headers: proxyHeaders() });
    if (!res.ok) {
      return NextResponse.json({ error: "Backend unreachable" }, { status: 502, headers: errorHeaders(requestId) });
    }
    const data = await res.json();
    return NextResponse.json(data, { headers: responseHeaders(res, requestId) });
  } catch {
    return NextResponse.json({ error: "Backend unreachable" }, { status: 502, headers: errorHeaders(requestId) });
  }
}
