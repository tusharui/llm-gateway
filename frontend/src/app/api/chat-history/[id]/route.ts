import { NextRequest, NextResponse } from "next/server";
import { newRequestId, proxyHeaders, responseHeaders, errorHeaders } from "../../_proxy";

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const requestId = newRequestId();
  try {
    const res = await fetch(`${BACKEND}/chat-history/sessions/${id}`, { headers: proxyHeaders() });
    if (!res.ok) {
      return NextResponse.json({ error: "Backend unreachable" }, { status: 502, headers: errorHeaders(requestId) });
    }
    const data = await res.json();
    return NextResponse.json(data, { headers: responseHeaders(res, requestId) });
  } catch {
    return NextResponse.json({ error: "Backend unreachable" }, { status: 502, headers: errorHeaders(requestId) });
  }
}

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const requestId = newRequestId();
  try {
    const res = await fetch(`${BACKEND}/chat-history/sessions/${id}`, {
      method: "DELETE",
      headers: proxyHeaders(),
    });
    if (!res.ok) {
      return NextResponse.json({ error: "Backend unreachable" }, { status: 502, headers: errorHeaders(requestId) });
    }
    return NextResponse.json({ ok: true }, { headers: responseHeaders(res, requestId) });
  } catch {
    return NextResponse.json({ error: "Backend unreachable" }, { status: 502, headers: errorHeaders(requestId) });
  }
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const requestId = newRequestId();
  try {
    const body = await req.json();
    const res = await fetch(`${BACKEND}/chat-history/sessions/${id}`, {
      method: "PATCH",
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
