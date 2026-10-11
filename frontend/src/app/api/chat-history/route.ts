import { forward } from "../_auth";

// `/chat-history` requires authentication on the gateway: conversation
// transcripts are user data, not a public resource. The proxy injects the
// operator key server-side so the browser never holds a credential.
export async function GET(request: Request) {
  return forward(request, "/chat-history/sessions");
}

export async function POST(request: Request) {
  const body = await request.json().catch(() => ({}));
  return forward(request, "/chat-history/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}