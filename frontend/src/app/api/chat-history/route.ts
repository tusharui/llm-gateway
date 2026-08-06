import { NextRequest, NextResponse } from "next/server";
import { newRequestId, proxyHeaders, responseHeaders, errorHeaders } from "../_proxy";

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000";

export async function GET() {
  const requestId = newRequestId();
  try {
    const res = await fetch(`${BACKEND}/chat-history/sessions`, { headers: proxyHeaders() });
    if (!res.ok) {
      return NextResponse.json({ error: "Backend unreachable" }, { status: 502, headers: errorHeaders(requestId) });
    }
    const data = await res.json();
    return NextResponse.json(data, { headers: responseHeaders(res, requestId) });
  } catch {
    return NextResponse.json({ error: "Backend unreachable" }, { status: 502, headers: errorHeaders(requestId) });
  }
}

export async function POST(req: NextRequest) {
  const requestId = newRequestId();
  try {
    const body = await req.json();
    const res = await fetch(`${BACKEND}/chat-history/sessions`, {
      method: "POST",
      headers: proxyHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      return NextResponse.json({ error: "Backend unreachable" }, { status: 502, headers: errorHeaders(requestId) });
    }
    const data = await res.json();
    return NextResponse.json(data, { headers: responseHeaders(res, requestId) });
  } catch {
    return NextResponse.json({ error: "Backend unreachable" }, { status: 502, headers: errorHeaders(requestId) });
  }
}
