import { forward } from "../../_auth";
import { requireAdmin } from "../_guard";

export async function GET(request: Request) {
  const denied = await requireAdmin();
  if (denied) return denied;

  const url = new URL(request.url);
  const days = url.searchParams.get("days") || "7";
  return forward(request, `/analytics/by-provider?days=${encodeURIComponent(days)}`);
}