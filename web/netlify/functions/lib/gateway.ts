/**
 * The HintWise LLM gateway, called with the signed-in person's own token (docs/platform.md
 * section 3): only model, max_tokens, system, messages and tools pass; max_tokens is clamped
 * to 4096; the body stays under 1,000,000 bytes; no streaming. A cap answer (429) comes back
 * to the page unchanged, so it can say which allowance is used up and offer the person's own key.
 */
import { MAX_BODY_BYTES, MAX_TOKENS, type ModelRequest } from '../../../src/core/assemble';
import type { ModelMessage } from '../../../src/core/prompts';
import type { FnEnv } from './env';
import { fakeGateway } from './fake';
import { HttpError, upstreamDetail } from './http';

export const APP = 'udebt';

/**
 * The gateway's anonymous mode (owner's choice A, 2026-11-15; Plabs' spec, claude-built-apps
 * d5bd864): server to server only, the project's anon key as the bearer (the platform checks a
 * project JWT before the function runs) and the app key in this header, which browsers cannot
 * send (not in the gateway's CORS list). The gateway then acts as the app's stand-in user
 * (visitors+udebt@hintwise.org), so every visitor who is not signed in shares its allowance.
 */
export const APP_KEY_HEADER = 'x-hintwise-app-key';

/** The gateway's headers: the person's token, or for a visitor the anon key and the app key. */
function gatewayHeaders(token: string | null, env: FnEnv): Record<string, string> {
  const who: Record<string, string> = token ? { authorization: `Bearer ${token}` } : { authorization: `Bearer ${env.anonKey ?? ''}`, [APP_KEY_HEADER]: env.appKey ?? '' };
  return { ...who, 'content-type': 'application/json' };
}

/** The gateway refused the app key: the server's setting (HINTWISE_APP_KEY or its registration), not the visitor's doing. */
function appKeyRefused(data: unknown): HttpError {
  return new HttpError(503, 'The gateway refused the app key.', { error: 'The HintWise key for visitors who are not signed in is not set up correctly on the server. Sign in, or use your own Anthropic key in Settings.', detail: upstreamDetail('gateway', 401, data) });
}

/** The gateway body: `app` and the five fields it passes, nothing else. */
export function gatewayBody(req: ModelRequest): Record<string, unknown> {
  const body: Record<string, unknown> = { app: APP, max_tokens: Math.min(MAX_TOKENS, Math.max(1, Math.floor(req.max_tokens || MAX_TOKENS))), messages: req.messages };
  if (req.model) body.model = req.model;
  if (req.system) body.system = req.system;
  if (req.tools?.length) body.tools = req.tools;
  return body;
}

export async function callGateway(req: ModelRequest, token: string | null, env: FnEnv, doFetch: typeof fetch = fetch): Promise<ModelMessage> {
  const body = gatewayBody(req);
  const text = JSON.stringify(body);
  if (new TextEncoder().encode(text).length > MAX_BODY_BYTES) throw new HttpError(413, 'Too large for the gateway.', { error: 'The request is larger than the gateway accepts.' });
  if (env.fakes && env.fakeCap) throw new HttpError(429, 'cap', fakeCapAnswer());
  if (env.fakes) return fakeGateway(req);
  if (!env.supabaseUrl) throw new HttpError(503, 'No gateway URL.', { error: 'The server is not configured yet.' });
  let res: Response;
  try {
    res = await doFetch(`${env.supabaseUrl}/functions/v1/llm-gateway`, {
      method: 'POST',
      headers: gatewayHeaders(token, env),
      body: text,
    });
  } catch {
    throw new HttpError(502, 'Gateway unreachable.', { error: 'Could not reach the HintWise gateway. Try again in a moment.' });
  }
  const data: unknown = await res.json().catch(() => ({}));
  if (res.status === 429 && data && typeof data === 'object' && (data as { error?: unknown }).error === 'cap') throw new HttpError(429, 'cap', data);
  if (res.status === 401 && token === null) throw appKeyRefused(data);
  if (res.status === 401) throw new HttpError(401, 'Sign in again.', { error: 'Your sign-in has expired. Sign in again.', detail: upstreamDetail('gateway', 401, data) });
  if (!res.ok) {
    const m = data && typeof data === 'object' ? (data as { message?: unknown; error?: unknown }).message ?? (data as { error?: unknown }).error : undefined;
    throw new HttpError(502, 'Gateway error.', { error: typeof m === 'string' && m ? m : `The HintWise gateway answered ${res.status}.` });
  }
  return data as ModelMessage;
}

/** The fakes' cap answer: the person's daily allowance in this app, until the next UTC midnight. */
function fakeCapAnswer(now: Date = new Date()): Record<string, unknown> {
  const next = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1));
  return { error: 'cap', scope: 'app-user-day', resets_at: next.toISOString() };
}

/**
 * The gateway's check mode (docs/platform.md section 3): `{ app, check: true }` makes no model
 * call and answers the person's allowance (allowed, scope, resets_at, app_open, limits,
 * calls_left), so the page can say a cap is reached before anyone starts a sitting.
 */
export async function checkAllowance(token: string | null, env: FnEnv, doFetch: typeof fetch = fetch): Promise<Record<string, unknown>> {
  if (env.fakes) {
    if (!env.fakeCap) return { allowed: true, uses_credit: false, scope: null, resets_at: null, app_open: true, limits: [], calls_left: null };
    const cap = fakeCapAnswer();
    return { allowed: false, uses_credit: false, scope: cap.scope, resets_at: cap.resets_at, app_open: true, limits: [], calls_left: null };
  }
  if (!env.supabaseUrl) throw new HttpError(503, 'No gateway URL.', { error: 'The server is not configured yet.' });
  let res: Response;
  try {
    res = await doFetch(`${env.supabaseUrl}/functions/v1/llm-gateway`, {
      method: 'POST',
      headers: gatewayHeaders(token, env),
      body: JSON.stringify({ app: APP, check: true }),
    });
  } catch {
    throw new HttpError(502, 'Gateway unreachable.', { error: 'Could not reach the HintWise gateway. Try again in a moment.' });
  }
  const data: unknown = await res.json().catch(() => ({}));
  if (res.status === 401 && token === null) throw appKeyRefused(data);
  if (res.status === 401) throw new HttpError(401, 'Sign in again.', { error: 'Your sign-in has expired. Sign in again.', detail: upstreamDetail('gateway', 401, data) });
  if (!res.ok || !data || typeof data !== 'object' || typeof (data as { allowed?: unknown }).allowed !== 'boolean') {
    throw new HttpError(502, 'Gateway error.', { error: `The HintWise gateway answered ${res.status} to the allowance check.` });
  }
  return data as Record<string, unknown>;
}
