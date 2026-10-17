import { forward } from "../_auth";
import { requireUser } from "../_session";

export async function GET(request: Request) {
  const denied = await requireUser();
  if (denied) return denied;

  return forward(request, "/chat-history/sessions");
}

export async function POST(request: Request) {
  const denied = await requireUser();
  if (denied) return denied;

  const body = await request.json().catch(() => ({}));
  return forward(request, "/chat-history/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}