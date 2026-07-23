import { NextRequest, NextResponse } from "next/server";

const BACKEND = "http://localhost:8000/chat-history";

export async function GET() {
  try {
    const res = await fetch(`${BACKEND}/sessions`);
    const data = await res.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ error: "Backend unreachable" }, { status: 502 });
  }
}

export async function POST(request: NextRequest) {
  const body = await request.json();
  try {
    const res = await fetch(`${BACKEND}/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: "Backend unreachable" }, { status: 502 });
  }
}
