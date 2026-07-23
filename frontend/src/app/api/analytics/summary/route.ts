import { NextRequest, NextResponse } from "next/server";

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000";

export async function GET(req: NextRequest) {
  const days = req.nextUrl.searchParams.get("days") || "7";
  try {
    const res = await fetch(`${BACKEND}/analytics/summary?days=${days}`);
    if (!res.ok) {
      return NextResponse.json({ error: "Backend unreachable" }, { status: 502 });
    }
    const data = await res.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ error: "Backend unreachable" }, { status: 502 });
  }
}
