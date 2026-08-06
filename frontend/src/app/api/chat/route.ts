import { NextRequest, NextResponse } from "next/server";

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000";

export async function POST(request: NextRequest) {
  const body = await request.json();

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  const auth = request.headers.get("authorization");
  if (auth) {
    headers["Authorization"] = auth;
  }

  try {
    const res = await fetch(`${BACKEND}/chat`, {
      method: "POST",
      headers,
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
        },
      });
    }

    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json(
      { error: "Backend unreachable" },
      { status: 502 }
    );
  }
}
