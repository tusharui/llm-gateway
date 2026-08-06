import { NextRequest, NextResponse } from "next/server";
import { newRequestId, proxyHeaders, responseHeaders, errorHeaders } from "../../_proxy";

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000";

export async function GET(req: NextRequest) {
  const requestId = newRequestId();
  const limit = req.nextUrl.searchParams.get("limit") || "50";
  try {
    const res = await fetch(`${BACKEND}/analytics/recent?limit=${limit}`, { headers: proxyHeaders() });
    if (!res.ok) {
      return NextResponse.json({ error: "Backend unreachable" }, { status: 502, headers: errorHeaders(requestId) });
    }
    const data = await res.json();
    return NextResponse.json(data, { headers: responseHeaders(res, requestId) });
  } catch {
    return NextResponse.json({ error: "Backend unreachable" }, { status: 502, headers: errorHeaders(requestId) });
  }
}
