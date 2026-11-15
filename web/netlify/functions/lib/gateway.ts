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
import { HttpError } from './http';

export const APP = 'udebt';

/** The gateway body: `app` and the five fields it passes, nothing else. */
export function gatewayBody(req: ModelRequest): Record<string, unknown> {
  const body: Record<string, unknown> = { app: APP, max_tokens: Math.min(MAX_TOKENS, Math.max(1, Math.floor(req.max_tokens || MAX_TOKENS))), messages: req.messages };
  if (req.model) body.model = req.model;
  if (req.system) body.system = req.system;
  if (req.tools?.length) body.tools = req.tools;
  return body;
}

export async function callGateway(req: ModelRequest, token: string, env: FnEnv, doFetch: typeof fetch = fetch): Promise<ModelMessage> {
  const body = gatewayBody(req);
  const text = JSON.stringify(body);
  if (new TextEncoder().encode(text).length > MAX_BODY_BYTES) throw new HttpError(413, 'Too large for the gateway.', { error: 'The request is larger than the gateway accepts.' });
  if (env.fakes) return fakeGateway(req);
  if (!env.supabaseUrl) throw new HttpError(503, 'No gateway URL.', { error: 'The server is not configured yet.' });
  let res: Response;
  try {
    res = await doFetch(`${env.supabaseUrl}/functions/v1/llm-gateway`, {
      method: 'POST',
      headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
      body: text,
    });
  } catch {
    throw new HttpError(502, 'Gateway unreachable.', { error: 'Could not reach the HintWise gateway. Try again in a moment.' });
  }
  const data: unknown = await res.json().catch(() => ({}));
  if (res.status === 429 && data && typeof data === 'object' && (data as { error?: unknown }).error === 'cap') throw new HttpError(429, 'cap', data);
  if (res.status === 401) throw new HttpError(401, 'Sign in again.', { error: 'Your sign-in has expired. Sign in again.' });
  if (!res.ok) {
    const m = data && typeof data === 'object' ? (data as { message?: unknown; error?: unknown }).message ?? (data as { error?: unknown }).error : undefined;
    throw new HttpError(502, 'Gateway error.', { error: typeof m === 'string' && m ? m : `The HintWise gateway answered ${res.status}.` });
  }
  return data as ModelMessage;
}
