import { forward } from "../../_auth";

// `/analytics` is an admin route on the gateway: it reports aggregate spend
// and per-request rows, so it requires the operator key. The proxy injects it
// server-side.
export async function GET(request: Request) {
  const url = new URL(request.url);
  const limit = url.searchParams.get("limit") || "50";
  return forward(request, `/analytics/recent?limit=${encodeURIComponent(limit)}`);
}