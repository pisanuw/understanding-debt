import { describe, expect, it, vi } from 'vitest';
import {
  ask, BYOK_KEY, CapReached, DEFAULT_MAX_TOKENS, DEFAULT_MODEL, GATEWAY_APP, GatewayError, gatewayUrl, getByokKey, getMode,
  isRefusal, LlmUnavailable, memoryStorage, MODE_KEY, requestBody, setByokKey, setMode, textOf, type AnthropicLike, type Message,
} from '../src/lib/llm';

const reply = {
  id: 'msg_1', type: 'message', role: 'assistant', model: 'claude-opus-5-5',
  content: [{ type: 'text', text: 'Hello ' }, { type: 'text', text: 'there' }],
  stop_reason: 'end_turn', stop_sequence: null,
  usage: { input_tokens: 10, output_tokens: 5, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 },
} as unknown as Message;

const messages = [{ role: 'user' as const, content: 'Hi' }];

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

describe('mode and key storage', () => {
  it('defaults to off and keeps the chosen mode', () => {
    const s = memoryStorage();
    expect(getMode(s)).toBe('off');
    setMode('gateway', s);
    expect(getMode(s)).toBe('gateway');
    expect(s.getItem(MODE_KEY)).toBe('gateway');
    s.setItem(MODE_KEY, 'nonsense');
    expect(getMode(s)).toBe('off');
  });
  it('stores the visitor key under hintwise.anthropicKey, trimmed, and removes it on null', () => {
    const s = memoryStorage();
    expect(BYOK_KEY).toBe('hintwise.anthropicKey');
    expect(getByokKey(s)).toBeNull();
    setByokKey('  sk-ant-test  ', s);
    expect(s.getItem(BYOK_KEY)).toBe('sk-ant-test');
    expect(getByokKey(s)).toBe('sk-ant-test');
    setByokKey('   ', s);
    expect(getByokKey(s)).toBeNull();
    setByokKey('k', s);
    setByokKey(null, s);
    expect(getByokKey(s)).toBeNull();
  });
  it('memoryStorage behaves like Storage', () => {
    const s = memoryStorage();
    s.setItem('a', '1');
    s.setItem('b', '2');
    expect(s.length).toBe(2);
    expect(s.key(0)).toBe('a');
    expect(s.key(5)).toBeNull();
    s.removeItem('a');
    expect(s.getItem('a')).toBeNull();
    s.clear();
    expect(s.length).toBe(0);
  });
  it('falls back to a memory store when no storage is passed in Node', () => {
    expect(getMode()).toBe('off');
    expect(getByokKey()).toBeNull();
    setMode('off');
    setByokKey(null);
  });
});

describe('request shape', () => {
  it('fills the defaults and keeps system and tools when given', () => {
    expect(requestBody({ messages })).toEqual({ model: DEFAULT_MODEL, max_tokens: DEFAULT_MAX_TOKENS, messages });
    const tools = [{ name: 't', input_schema: { type: 'object' as const } }];
    expect(requestBody({ messages, system: 'S', model: 'claude-haiku-5-5', max_tokens: 50, tools })).toEqual({
      model: 'claude-haiku-5-5', max_tokens: 50, messages, system: 'S', tools,
    });
  });
  it('builds the gateway URL from the Supabase URL', () => {
    expect(gatewayUrl({ VITE_SUPABASE_URL: 'https://ref.supabase.co/' })).toBe('https://ref.supabase.co/functions/v1/llm-gateway');
    expect(() => gatewayUrl({})).toThrow(LlmUnavailable);
  });
  it('reads text and refusals from a response', () => {
    expect(textOf(reply)).toBe('Hello there');
    expect(isRefusal(reply)).toBe(false);
    const refused = { ...reply, content: [], stop_reason: 'refusal' } as unknown as Message;
    expect(textOf(refused)).toBe('');
    expect(isRefusal(refused)).toBe(true);
  });
});

describe('ask in each mode', () => {
  it('refuses when model features are off', async () => {
    await expect(ask({ messages }, { storage: memoryStorage() })).rejects.toThrow(LlmUnavailable);
    await expect(ask({ messages }, { mode: 'off' })).rejects.toThrow(/off/);
  });
  it('byok: needs a key, then calls the browser SDK directly', async () => {
    const storage = memoryStorage();
    await expect(ask({ messages }, { mode: 'byok', storage })).rejects.toThrow(/Paste your Anthropic API key/);
    setByokKey('sk-ant-test', storage);
    const create = vi.fn(async () => reply);
    const createAnthropic = vi.fn((_key: string): AnthropicLike => ({ messages: { create } }));
    const fetchSpy = vi.fn();
    const out = await ask({ messages, system: 'S' }, { mode: 'byok', storage, createAnthropic, fetch: fetchSpy });
    expect(out).toBe(reply);
    expect(createAnthropic).toHaveBeenCalledWith('sk-ant-test');
    expect(create).toHaveBeenCalledWith({ model: DEFAULT_MODEL, max_tokens: DEFAULT_MAX_TOKENS, messages, system: 'S' });
    expect(fetchSpy).not.toHaveBeenCalled();
  });
  it('byok mode is read from storage when not forced', async () => {
    const storage = memoryStorage();
    setMode('byok', storage);
    setByokKey('k', storage);
    const create = vi.fn(async () => reply);
    await ask({ messages }, { storage, createAnthropic: () => ({ messages: { create } }) });
    expect(create).toHaveBeenCalledTimes(1);
  });
  it('gateway: needs a session, then posts to the edge function with the token and app', async () => {
    await expect(ask({ messages }, { mode: 'gateway', getToken: async () => null })).rejects.toThrow(/Sign in/);
    const fetchMock = vi.fn(async () => jsonResponse(200, reply));
    const out = await ask({ messages, max_tokens: 99 }, {
      mode: 'gateway', getToken: async () => 'jwt-1', fetch: fetchMock, gatewayUrl: 'https://ref.supabase.co/functions/v1/llm-gateway',
    });
    expect(out).toEqual(reply);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://ref.supabase.co/functions/v1/llm-gateway');
    expect(init.method).toBe('POST');
    expect((init.headers as Record<string, string>).authorization).toBe('Bearer jwt-1');
    expect(JSON.parse(init.body as string)).toEqual({ app: GATEWAY_APP, model: DEFAULT_MODEL, max_tokens: 99, messages });
  });
  it('gateway: a 429 cap becomes CapReached with the scope and reset time', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(429, { error: 'cap', scope: 'user-day', resets_at: '2026-11-11T00:00:00.000Z', message: 'Cap reached.' }));
    const err = await ask({ messages }, { mode: 'gateway', getToken: async () => 'jwt', fetch: fetchMock, gatewayUrl: 'u' }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(CapReached);
    const cap = err as CapReached;
    expect(cap.scope).toBe('user-day');
    expect(cap.resetsAt).toBe('2026-11-11T00:00:00.000Z');
    expect(cap.message).toBe('Cap reached.');
    expect(cap.name).toBe('CapReached');
    expect(new CapReached('app-day', 'T').message).toContain('app-day');
  });
  it('gateway: other errors become GatewayError with the status and message', async () => {
    const upstream = vi.fn(async () => jsonResponse(502, { error: 'upstream', message: 'Anthropic said no' }));
    const e1 = await ask({ messages }, { mode: 'gateway', getToken: async () => 'jwt', fetch: upstream, gatewayUrl: 'u' }).catch((e: unknown) => e);
    expect(e1).toBeInstanceOf(GatewayError);
    expect((e1 as GatewayError).status).toBe(502);
    expect((e1 as GatewayError).message).toBe('Anthropic said no');
    const plain429 = vi.fn(async () => jsonResponse(429, { error: 'rate' }));
    const e2 = await ask({ messages }, { mode: 'gateway', getToken: async () => 'jwt', fetch: plain429, gatewayUrl: 'u' }).catch((e: unknown) => e);
    expect(e2).toBeInstanceOf(GatewayError);
    expect((e2 as GatewayError).message).toBe('The HintWise gateway answered 429.');
    const notJson = vi.fn(async () => new Response('<html>', { status: 500 }));
    const e3 = await ask({ messages }, { mode: 'gateway', getToken: async () => 'jwt', fetch: notJson, gatewayUrl: 'u' }).catch((e: unknown) => e);
    expect(e3).toBeInstanceOf(GatewayError);
    expect((e3 as GatewayError).body).toEqual({});
  });
  it('gateway: without a URL override it needs VITE_SUPABASE_URL', async () => {
    await expect(ask({ messages }, { mode: 'gateway', getToken: async () => 'jwt', fetch: vi.fn() })).rejects.toThrow(LlmUnavailable);
  });
});
