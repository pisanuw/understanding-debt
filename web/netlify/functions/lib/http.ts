/** Small helpers for the functions' web-standard Request and Response. */

export const MAX_REQUEST_BYTES = 1_500_000;

export class HttpError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly body?: unknown,
  ) {
    super(message);
    this.name = 'HttpError';
  }
}

export function json(status: number, data: unknown, extra: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff', ...extra },
  });
}

export function errorResponse(e: unknown): Response {
  if (e instanceof HttpError) return json(e.status, e.body ?? { error: e.message });
  const name = e instanceof Error ? e.name : '';
  const message = e instanceof Error ? e.message : String(e);
  if (name === 'BadRequest') return json(400, { error: message });
  if (name === 'ModelAnswerError') return json(502, { error: message });
  return json(500, { error: 'Something went wrong on the server. Try again in a moment.' });
}

/** The bearer token of the request, or a 401. */
export function bearer(req: Request): string {
  const t = bearerOrNull(req);
  if (!t) throw new HttpError(401, 'Sign in first.', { error: 'Sign in first.' });
  return t;
}

/** The bearer token of the request; null when it sends no Authorization header (a 401 for a malformed one). */
export function bearerOrNull(req: Request): string | null {
  const h = (req.headers.get('authorization') ?? '').trim();
  if (!h) return null;
  const m = /^Bearer\s+([A-Za-z0-9._~+/=-]{8,4096})$/.exec(h);
  if (!m) throw new HttpError(401, 'Sign in first.', { error: 'Sign in first.' });
  return m[1];
}

/** The JSON body, refused past MAX_REQUEST_BYTES. */
export async function readJson(req: Request): Promise<unknown> {
  if (req.method !== 'POST') throw new HttpError(405, 'Use POST.', { error: 'Use POST.' });
  const declared = Number(req.headers.get('content-length') ?? 0);
  if (declared > MAX_REQUEST_BYTES) throw new HttpError(413, 'The request is too large.', { error: 'The request is too large.' });
  const text = await req.text();
  if (new TextEncoder().encode(text).length > MAX_REQUEST_BYTES) throw new HttpError(413, 'The request is too large.', { error: 'The request is too large.' });
  try {
    return JSON.parse(text);
  } catch {
    throw new HttpError(400, 'The body is not JSON.', { error: 'The body is not JSON.' });
  }
}
