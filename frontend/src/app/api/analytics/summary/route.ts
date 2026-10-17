import { forward } from "../../_auth";
import { requireAdmin } from "../_guard";

export async function GET(request: Request) {
  const denied = await requireAdmin();
  if (denied) return denied;

  return forward(request, "/analytics/summary");
}