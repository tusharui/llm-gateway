import { NextRequest, NextResponse } from "next/server";

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000";

export async function GET(req: NextRequest) {
  const limit = req.nextUrl.searchParams.get("limit") || "50";
  try {
    const res = await fetch(`${BACKEND}/analytics/recent?limit=${limit}`);
    if (!res.ok) {
      return NextResponse.json({ error: "Backend unreachable" }, { status: 502 });
    }
    const data = await res.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ error: "Backend unreachable" }, { status: 502 });
  }
}
