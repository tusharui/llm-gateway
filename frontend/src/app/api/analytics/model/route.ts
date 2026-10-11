import { forward } from "../../_auth";

// `/analytics` is an admin route on the gateway: it reports aggregate spend
// and per-request rows, so it requires the operator key. The proxy injects it
// server-side.
export async function GET(request: Request) {
  const url = new URL(request.url);
  const days = url.searchParams.get("days") || "7";
  return forward(request, `/analytics/by-model?days=${encodeURIComponent(days)}`);
}