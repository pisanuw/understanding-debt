import { describe, expect, it } from 'vitest';
import { handleExcerpt, handleGrade, handleQuestions } from '../netlify/functions/lib/handlers';
import { FAKE_TOKEN, type FnEnv } from '../netlify/functions/lib/env';
import { fakeGateway } from '../netlify/functions/lib/fake';
import * as llm from '../src/lib/llm';
import { ApiError, CapError, GatewayClient, LazyByokClient, allowanceVia, capMessage, capNotice, quizClient, type ClientDeps } from '../src/model/client';
import * as prefs from '../src/model/prefs';
import { HttpWorkerClient, workerUrl } from '../src/model/worker';

const SHA = 'f93102b121f1da9055c148e8eccde275ccf18236';
const REPO = 'https://github.com/acme/demo';
const env: FnEnv = { supabaseUrl: null, anonKey: null, fakes: true, fakeRepos: {} };

/** fetch for the page that answers /api with the real handlers and their fakes. */
const apiFetch: typeof fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const req = new Request(new URL(String(input), 'http://site'), init);
  const path = new URL(req.url).pathname;
  if (path === '/api/questions') return handleQuestions(req, { env });
  if (path === '/api/grade') return handleGrade(req, { env });
  if (path === '/api/excerpt') return handleExcerpt(req, { env });
  return new Response('{}', { status: 404 });
}) as typeof fetch;

const deps: ClientDeps = { getToken: async () => FAKE_TOKEN, fetch: apiFetch };
const unit = { id: 'u', name: 'Storage', level: 1, summary: 'Keeps records.' };

describe('the gateway client', () => {
  it('writes and grades through /api with the session token', async () => {
    const c = new GatewayClient(deps);
    const qs = await c.writeQuestions({ repo: REPO, sha: SHA, units: [unit] });
    expect(qs[0].question).toContain('Storage');
    const out = await c.grade({ repo: REPO, sha: SHA, items: [{ ...qs[0], answer: 'It keeps them.' }] }, { repo_id: 0, ts: '2026-11-15T00:00:00Z' });
    expect(out).toEqual({ grades: [expect.objectContaining({ verdict: 'correct' })], recorded: null });
    await expect(c.excerpt(REPO, SHA, { path: 'a.py', start: 1, end: 2 })).rejects.toThrow(ApiError);
  });

  it('turns a cap into CapError and refuses without a session', async () => {
    const capFetch = (async () => Response.json({ error: 'cap', scope: 'user-day', resets_at: '2026-11-16T00:00:00Z' }, { status: 429 })) as typeof fetch;
    await expect(new GatewayClient({ ...deps, fetch: capFetch }).writeQuestions({ repo: REPO, sha: SHA, units: [unit] })).rejects.toBeInstanceOf(CapError);
    await expect(new GatewayClient({ getToken: async () => null }).writeQuestions({ repo: REPO, sha: SHA, units: [unit] })).rejects.toThrow('Sign in first.');
    const odd = (async () => new Response('not json', { status: 500 })) as typeof fetch;
    await expect(new GatewayClient({ ...deps, fetch: odd }).writeQuestions({ repo: REPO, sha: SHA, units: [unit] })).rejects.toThrow('The server answered 500.');
  });

  it('says each cap in plain words with its reset time', () => {
    const now = new Date('2026-11-15T12:00:00Z');
    expect(capMessage({ scope: 'app-user-day-calls', resetsAt: '2026-11-15T20:00:00Z' }, now)).toMatch(/^Your daily allowance in this app is used up until /);
    expect(capMessage({ scope: 'app-user-week', resetsAt: 'x' }, now)).toBe('Your weekly allowance in this app is used up. You can use your own Anthropic key in Settings; no allowance applies to it.');
    expect(capMessage({ scope: 'user-day', resetsAt: '2026-11-17T00:00:00Z' }, now)).toMatch(/^Your daily HintWise allowance/);
    expect(capMessage({ scope: 'app-day', resetsAt: '' }, now)).toMatch(/^This app's shared allowance/);
    expect(new CapError('app-day', 'soon').message).toContain('app-day');
  });
});

describe("the person's own key", () => {
  it('builds the same prompts in the browser and sends them with llm.ts in BYOK mode', async () => {
    const storage = llm.memoryStorage();
    llm.setByokKey('sk-test', storage);
    const bodies: unknown[] = [];
    const c = new LazyByokClient({
      ...deps,
      askDeps: {
        storage,
        createAnthropic: (key) => {
          expect(key).toBe('sk-test');
          return { messages: { create: async (body) => (bodies.push(body), fakeGateway(body as never) as never) } };
        },
      },
    });
    const qs = await c.writeQuestions({ repo: REPO, sha: SHA, units: [unit] });
    expect(qs).toHaveLength(1);
    const g = await c.grade({ repo: REPO, sha: SHA, items: [{ ...qs[0], answer: 'partly right' }] });
    expect(g.grades[0].verdict).toBe('partial');
    expect(g.recorded).toBeNull();
    expect(bodies).toHaveLength(2);
    await expect(c.excerpt(REPO, SHA, { path: 'a.py', start: 1, end: 2 })).rejects.toThrow();
  });

  it('chooses the client for the mode', () => {
    expect(quizClient(deps, 'gateway')?.mode).toBe('gateway');
    expect(quizClient(deps, 'byok')?.mode).toBe('byok');
    expect(quizClient(deps, 'off')).toBeNull();
  });

  it('gives a person who has not chosen the fallback, and keeps a choice, off included', () => {
    const s = llm.memoryStorage();
    expect(prefs.getMode(s)).toBe('off');
    expect(prefs.getMode(s, 'gateway')).toBe('gateway');
    prefs.setMode('off', s);
    expect(prefs.getMode(s, 'gateway')).toBe('off');
    prefs.setMode('byok', s);
    expect(prefs.getMode(s, 'gateway')).toBe('byok');
    expect(prefs.getQuizKind(llm.memoryStorage(), 'model')).toBe('model');
    expect(prefs.getQuizKind(llm.memoryStorage())).toBe(prefs.DEFAULT_QUIZ_KIND);
  });

  it('says an allowance is used up, until when, and what still works', () => {
    const now = new Date('2026-11-15T10:00:00Z');
    const a = { scope: 'app-user-day', resets_at: '2026-11-16T00:00:00Z' };
    expect(capNotice(a, true, now)).toMatch(/^Your daily allowance in this app is used up until .+\. Until then the quiz uses the questions written in advance \(no model call\), or you can use your own Anthropic key in Settings\.$/);
    expect(capNotice(a, false, now)).toMatch(/Until then you can use your own Anthropic key in Settings; no allowance applies to it\.$/);
    expect(capNotice({ scope: 'user-day', resets_at: null }, false, now)).toMatch(/^Your daily HintWise allowance is used up\. /);
    expect(capNotice({ scope: 'app-day', resets_at: 'not a date' }, true, now)).toMatch(/^This app's shared allowance is used up\. /);
  });

  it('reads the allowance through /api/allowance, and nothing that is not one', async () => {
    const seen: string[] = [];
    const fetchWith = (body: unknown, status = 200) =>
      (async (u: RequestInfo | URL, init?: RequestInit) => {
        seen.push(`${init?.method} ${String(u)}`);
        return Response.json(body, { status });
      }) as typeof fetch;
    const deps = (body: unknown, status?: number) => ({ getToken: async () => 'tok', fetch: fetchWith(body, status) });
    expect(await allowanceVia(deps({ allowed: false, scope: 'app-user-day', resets_at: 'x' }))()).toEqual({ allowed: false, scope: 'app-user-day', resets_at: 'x', app_open: undefined, calls_left: null });
    expect(seen).toEqual(['POST /api/allowance']);
    expect(await allowanceVia(deps({ nope: 1 }))()).toBeNull();
    await expect(allowanceVia(deps({ error: 'x' }, 502))()).rejects.toThrow('x');
    await expect(allowanceVia({ getToken: async () => null, fetch: fetchWith({}) })()).rejects.toThrow('Sign in first.');
  });

  it('keeps the mode and the key exactly as llm.ts does', () => {
    expect([prefs.MODE_KEY, prefs.BYOK_KEY]).toEqual([llm.MODE_KEY, llm.BYOK_KEY]);
    const s = llm.memoryStorage();
    for (const m of ['byok', 'gateway', 'off'] as const) {
      prefs.setMode(m, s);
      expect(llm.getMode(s)).toBe(m);
    }
    s.setItem(prefs.MODE_KEY, 'weird');
    expect(prefs.getMode(s)).toBe(llm.getMode(s));
    prefs.setByokKey('  sk-x  ', s);
    expect(llm.getByokKey(s)).toBe('sk-x');
    prefs.setByokKey(' ', s);
    expect(llm.getByokKey(s)).toBeNull();
    expect(prefs.getByokKey(s)).toBeNull();
    expect(prefs.getMode()).toBe(llm.getMode());
  });
});

describe('the analysis service client', () => {
  it('posts the job with the token and the key, and reads errors', async () => {
    let seen: { url: string; headers: Record<string, string>; body: unknown } | null = null;
    const w = new HttpWorkerClient('https://udebt-api.example/', (async (u: RequestInfo | URL, i?: RequestInit) => {
      seen = { url: String(u), headers: i?.headers as Record<string, string>, body: JSON.parse(String(i?.body)) };
      return Response.json({ job: 3, status: 'running' }, { status: 202 });
    }) as typeof fetch);
    expect(await w.start({ url: REPO }, 'sk-k', 'tok')).toEqual({ job: 3, status: 'running' });
    expect(seen).toMatchObject({ url: 'https://udebt-api.example/analyses', headers: { authorization: 'Bearer tok', 'x-anthropic-key': 'sk-k' }, body: { url: REPO } });
    const busy = new HttpWorkerClient('https://w', (async () => Response.json({ error: 'busy' }, { status: 429 })) as typeof fetch);
    await expect(busy.start({ url: REPO }, 'k', 't')).rejects.toThrow('busy');
    const down = new HttpWorkerClient('https://w', (async () => { throw new Error('x'); }) as typeof fetch);
    await expect(down.start({ url: REPO }, 'k', 't')).rejects.toThrow(/sleeps when idle/);
    const odd = new HttpWorkerClient('https://w', (async () => new Response('?', { status: 500 })) as typeof fetch);
    await expect(odd.start({ url: REPO }, 'k', 't')).rejects.toThrow('answered 500');
    expect(workerUrl({ VITE_UDEBT_WORKER_URL: 'https://udebt-api.hintwise.org' })).toBe('https://udebt-api.hintwise.org');
    expect(workerUrl({ VITE_UDEBT_WORKER_URL: 'http://insecure' })).toBeNull();
    expect(workerUrl({})).toBeNull();
  });
});
