import { forward } from "../../../_auth";

// `/chat-history` requires authentication on the gateway: conversation
// transcripts are user data, not a public resource. The proxy injects the
// operator key server-side so the browser never holds a credential.
export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const body = await request.json().catch(() => ({}));
  return forward(
    request,
    `/chat-history/sessions/${encodeURIComponent(id)}/messages`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }
  );
}