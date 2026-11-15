/**
 * Model calls for HintWise apps (docs/platform.md, section 3). The visitor
 * chooses a mode:
 *
 *   'byok'     their own Anthropic key, kept in this browser's localStorage
 *              and sent only to api.anthropic.com (browser SDK);
 *   'gateway'  the owner's key through the llm-gateway edge function, for
 *              signed-in users, within the app's caps;
 *   'off'      no model calls (the default; the app works without a model
 *              wherever deterministic code does the job).
 *
 * `ask` takes its dependencies (storage, fetch, token, SDK factory) as an
 * optional second argument so tests never touch the network.
 */
import Anthropic from '@anthropic-ai/sdk';
import { currentToken } from './auth';
import { APP_SLUG } from './supabase';

export type LlmMode = 'byok' | 'gateway' | 'off';

export const MODE_KEY = 'hintwise.llmMode';
export const BYOK_KEY = 'hintwise.anthropicKey';
export const DEFAULT_MODEL = 'claude-opus-5-5';
export const DEFAULT_MAX_TOKENS = 1024;
/** The row in platform.llm_apps this app calls the gateway as. */
export const GATEWAY_APP = APP_SLUG;

export type Message = Anthropic.Messages.Message;
export type MessageParam = Anthropic.Messages.MessageParam;
export type Tool = Anthropic.Messages.Tool;

export interface AskRequest {
  system?: string;
  messages: MessageParam[];
  model?: string;
  max_tokens?: number;
  tools?: Tool[];
}

/** The minimum of the Anthropic client that BYOK mode uses. */
export interface AnthropicLike {
  messages: { create(body: Anthropic.Messages.MessageCreateParamsNonStreaming): Promise<Message> };
}

export interface AskDeps {
  mode?: LlmMode;
  storage?: Storage;
  fetch?: typeof fetch;
  getToken?: () => Promise<string | null>;
  createAnthropic?: (apiKey: string) => AnthropicLike;
  gatewayUrl?: string;
}

/** A spending cap of the owner's key is reached; `resetsAt` is an ISO time. */
export class CapReached extends Error {
  constructor(
    public readonly scope:
      | 'app-day'
      | 'app-week'
      | 'app-month'
      | 'app-user-week-calls'
      | 'app-user-week'
      | 'app-user-day-calls'
      | 'app-user-day'
      | 'user-day'
      | string,
    public readonly resetsAt: string,
    message?: string,
  ) {
    super(message || `The HintWise key's ${scope} cap is reached until ${resetsAt}.`);
    this.name = 'CapReached';
  }
}

/** The gateway answered with an error other than a cap. */
export class GatewayError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: unknown,
  ) {
    super(gatewayMessage(status, body));
    this.name = 'GatewayError';
  }
}

/** Mode 'off', or a mode that cannot run yet (no key pasted, not signed in). */
export class LlmUnavailable extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'LlmUnavailable';
  }
}

function gatewayMessage(status: number, body: unknown): string {
  const m = typeof body === 'object' && body !== null ? (body as { message?: unknown }).message : undefined;
  return typeof m === 'string' && m ? m : `The HintWise gateway answered ${status}.`;
}

/** A Storage that lives in memory: for tests and for browsers that block localStorage. */
export function memoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length() {
      return map.size;
    },
    clear: () => map.clear(),
    getItem: (k) => map.get(k) ?? null,
    key: (i) => [...map.keys()][i] ?? null,
    removeItem: (k) => void map.delete(k),
    setItem: (k, v) => void map.set(k, String(v)),
  };
}

function defaultStorage(): Storage {
  try {
    if (typeof localStorage !== 'undefined') return localStorage;
  } catch {
    // access denied (privacy mode): fall through
  }
  return memoryStorage();
}

export function getMode(storage: Storage = defaultStorage()): LlmMode {
  const v = storage.getItem(MODE_KEY);
  return v === 'byok' || v === 'gateway' ? v : 'off';
}

export function setMode(mode: LlmMode, storage: Storage = defaultStorage()): void {
  storage.setItem(MODE_KEY, mode);
}

export function getByokKey(storage: Storage = defaultStorage()): string | null {
  const v = storage.getItem(BYOK_KEY);
  return v && v.trim() ? v.trim() : null;
}

/** Stores the visitor's key (null removes it). It never leaves this browser except to api.anthropic.com. */
export function setByokKey(key: string | null, storage: Storage = defaultStorage()): void {
  if (key && key.trim()) storage.setItem(BYOK_KEY, key.trim());
  else storage.removeItem(BYOK_KEY);
}

/** The gateway URL for the configured Supabase project. */
export function gatewayUrl(env: Record<string, unknown> = import.meta.env): string {
  const url = env.VITE_SUPABASE_URL;
  if (typeof url !== 'string' || !url) throw new LlmUnavailable('The HintWise key needs VITE_SUPABASE_URL to be configured.');
  return `${url.replace(/\/$/, '')}/functions/v1/llm-gateway`;
}

/** The Messages API body (without `app`); the gateway clamps max_tokens to the app's limit. */
export function requestBody(req: AskRequest): Anthropic.Messages.MessageCreateParamsNonStreaming {
  const body: Anthropic.Messages.MessageCreateParamsNonStreaming = {
    model: req.model ?? DEFAULT_MODEL,
    max_tokens: req.max_tokens ?? DEFAULT_MAX_TOKENS,
    messages: req.messages,
  };
  if (req.system) body.system = req.system;
  if (req.tools) body.tools = req.tools;
  return body;
}

/**
 * One non-streaming call in the chosen mode. Throws LlmUnavailable when the
 * mode cannot run, CapReached on a gateway cap, GatewayError on other
 * gateway errors; the Anthropic SDK's own errors pass through in BYOK mode.
 */
export async function ask(req: AskRequest, deps: AskDeps = {}): Promise<Message> {
  const storage = deps.storage ?? defaultStorage();
  const mode = deps.mode ?? getMode(storage);
  const body = requestBody(req);

  if (mode === 'off') throw new LlmUnavailable('Model features are off. Choose your own key or the HintWise key in the settings.');

  if (mode === 'byok') {
    const apiKey = getByokKey(storage);
    if (!apiKey) throw new LlmUnavailable('Paste your Anthropic API key first.');
    const create = deps.createAnthropic ?? ((key: string) => new Anthropic({ apiKey: key, dangerouslyAllowBrowser: true }));
    return create(apiKey).messages.create(body);
  }

  const token = await (deps.getToken ?? currentToken)();
  if (!token) throw new LlmUnavailable('Sign in to use the HintWise key.');
  const doFetch = deps.fetch ?? fetch;
  const res = await doFetch(deps.gatewayUrl ?? gatewayUrl(), {
    method: 'POST',
    headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
    body: JSON.stringify({ app: GATEWAY_APP, ...body }),
  });
  const data: unknown = await res.json().catch(() => ({}));
  if (res.status === 429 && isCap(data)) throw new CapReached(data.scope, data.resets_at, data.message);
  if (!res.ok) throw new GatewayError(res.status, data);
  return data as Message;
}

function isCap(data: unknown): data is { error: 'cap'; scope: string; resets_at: string; message?: string } {
  return typeof data === 'object' && data !== null && (data as { error?: unknown }).error === 'cap';
}

/** The response's text blocks joined; empty for a refusal or a tool-only answer. */
export function textOf(message: Message): string {
  return message.content
    .filter((b): b is Anthropic.Messages.TextBlock => b.type === 'text')
    .map((b) => b.text)
    .join('');
}

/** A safety classifier declined the request (HTTP 200 with stop_reason "refusal"). */
export function isRefusal(message: Message): boolean {
  return message.stop_reason === 'refusal';
}
