export function newRequestId(): string {
  return crypto.randomUUID();
}

export function proxyHeaders(base: Record<string, string> = {}): Record<string, string> {
  return { "X-Request-ID": newRequestId(), ...base };
}

export function responseHeaders(res: Response, fallback: string): Record<string, string> {
  return { "X-Request-ID": res.headers.get("x-request-id") || fallback };
}

export function errorHeaders(requestId: string): Record<string, string> {
  return { "X-Request-ID": requestId };
}
