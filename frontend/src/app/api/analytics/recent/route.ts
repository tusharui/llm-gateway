import { forward } from "../../_auth";
import { requireAdmin } from "../_guard";

export async function GET(request: Request) {
  const denied = await requireAdmin();
  if (denied) return denied;

  const url = new URL(request.url);
  const limit = url.searchParams.get("limit") || "50";
  return forward(request, `/analytics/recent?limit=${encodeURIComponent(limit)}`);
}