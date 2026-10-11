import { forward } from "../../_auth";

// `/chat-history` requires authentication on the gateway: conversation
// transcripts are user data, not a public resource. The proxy injects the
// operator key server-side so the browser never holds a credential.
export async function GET(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  return forward(request, `/chat-history/sessions/${encodeURIComponent(id)}`);
}

export async function DELETE(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  return forward(request, `/chat-history/sessions/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const body = await request.json().catch(() => ({}));
  return forward(request, `/chat-history/sessions/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}