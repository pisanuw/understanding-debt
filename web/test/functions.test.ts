/**
 * The Netlify functions with fake fetches: live mode (PostgREST for the token check and the
 * records, the gateway, raw.githubusercontent.com) and the preview's fakes.
 */
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { beforeEach, describe, expect, it } from 'vitest';
import excerptFn, { config as excerptConfig } from '../netlify/functions/excerpt';
import gradeFn, { config as gradeConfig } from '../netlify/functions/grade';
import questionsFn, { config as questionsConfig } from '../netlify/functions/questions';
import allowanceFn, { config as allowanceConfig } from '../netlify/functions/allowance';
import { authorize, resetAuthCache } from '../netlify/functions/lib/auth';
import { FAKE_TOKEN, readEnv, type FnEnv } from '../netlify/functions/lib/env';
import { fakeGateway, fakeUnits } from '../netlify/functions/lib/fake';
import { APP_KEY_HEADER, callGateway, gatewayBody } from '../netlify/functions/lib/gateway';
import { excerpt, fetchFile } from '../netlify/functions/lib/github';
import { handleAllowance, handleExcerpt, handleGrade, handleQuestions } from '../netlify/functions/lib/handlers';
import { HttpError, bearer, errorResponse, readJson } from '../netlify/functions/lib/http';
import { inList, insertRecords, priorRecords, tableRow } from '../netlify/functions/lib/records';

const SHA = 'f93102b121f1da9055c148e8eccde275ccf18236';
const REPO = 'https://github.com/acme/demo';
const LIVE: FnEnv = { supabaseUrl: 'https://auth.example', anonKey: 'anon', fakes: false, fakeRepos: {} };
const FILE = Array.from({ length: 40 }, (_, i) => `line ${i + 1}`).join('\n');

interface Call {
  url: string;
  init?: RequestInit;
}

function liveFetch(over: Partial<Record<'repo' | 'gateway' | 'raw' | 'records' | 'prior', () => Response>> = {}, calls: Call[] = []): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    if (url.includes('/rest/v1/repositories')) return over.repo?.() ?? Response.json([{ id: 7 }]);
    if (url.includes('/rest/v1/records') && (init?.method ?? 'GET') === 'GET') return over.prior?.() ?? Response.json([]);
    if (url.includes('/rest/v1/records')) return over.records?.() ?? Response.json(JSON.parse(String(init?.body)).map((r: object, i: number) => ({ ...r, id: i + 1 })));
    if (url.includes('/functions/v1/llm-gateway')) return over.gateway?.() ?? Response.json(fakeGateway(JSON.parse(String(init?.body))));
    if (url.startsWith('https://raw.githubusercontent.com/')) return over.raw?.() ?? new Response(FILE, { status: 200 });
    return new Response('nope', { status: 404 });
  }) as typeof fetch;
}

function post(path: string, body: unknown, token: string | null = 'user-token'): Request {
  return new Request(`http://x${path}`, { method: 'POST', headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: JSON.stringify(body) });
}

const unit = { id: 'storage', name: 'Storage', level: 1, summary: 'Keeps the records.', paths: ['src/store.py'], refs: [{ path: 'src/store.py', start: 1, end: 10 }] };

beforeEach(() => resetAuthCache());

describe('the functions in live mode', () => {
  it('writes questions through the gateway with the person token, after checking it on PostgREST', async () => {
    const calls: Call[] = [];
    const res = await handleQuestions(post('/api/questions', { repo: REPO, sha: SHA, units: [unit] }), { env: LIVE, fetch: liveFetch({}, calls) });
    expect(res.status).toBe(200);
    const { questions } = (await res.json()) as { questions: { unit_id: string; level: number; question: string }[] };
    expect(questions[0]).toMatchObject({ unit_id: 'storage', level: 1 });
    const check = calls.find((c) => c.url.includes('/rest/v1/repositories'));
    expect(check?.url).toContain(`url=eq.${encodeURIComponent(REPO)}`);
    expect((check?.init?.headers as Record<string, string>)['accept-profile']).toBe('app_udebt');
    const gw = calls.find((c) => c.url.endsWith('/functions/v1/llm-gateway'));
    expect((gw?.init?.headers as Record<string, string>).authorization).toBe('Bearer user-token');
    const body = JSON.parse(String(gw?.init?.body));
    expect(Object.keys(body).sort()).toEqual(['app', 'max_tokens', 'messages', 'tools']);
    expect(body.app).toBe('udebt');
    expect(body.max_tokens).toBeLessThanOrEqual(4096);
    expect(calls.some((c) => c.url === `https://raw.githubusercontent.com/acme/demo/${SHA}/src/store.py`)).toBe(true);
  });

  it('grades in one call and records the sitting with the person token', async () => {
    const calls: Call[] = [];
    const items = [
      { unit_id: 'storage', level: 1, question: 'What does it keep?', reference_answer: 'The records.', evidence: ['src/store.py:1-5'], answer: 'It keeps the records.', variants: ['V1?', 'V2?'], confidence: 3 },
      { unit_id: 'storage', level: 1, question: 'Second?', reference_answer: 'R2', evidence: [], answer: '', revealed: true },
    ];
    const res = await handleGrade(post('/api/grade', { repo: REPO, sha: SHA, items, record: { repo_id: 7, ts: '2026-11-15T10:00:00Z' } }), { env: LIVE, fetch: liveFetch({}, calls) });
    expect(res.status).toBe(200);
    const out = (await res.json()) as { grades: { verdict: string }[]; recorded: { verdict: string; unit: string; data: object }[] };
    expect(out.grades.map((g) => g.verdict)).toEqual(['correct']);
    expect(out.recorded.map((r) => r.verdict)).toEqual(['correct', 'unknown']);
    expect(out.recorded[0].unit).toBe('storage');
    const ins = calls.find((c) => c.url.endsWith('/rest/v1/records'));
    const h = ins?.init?.headers as Record<string, string>;
    expect(h.authorization).toBe('Bearer user-token');
    expect(h['content-profile']).toBe('app_udebt');
    const rows = JSON.parse(String(ins?.init?.body));
    expect(rows[0]).not.toHaveProperty('unit');
    expect(rows[0]).not.toHaveProperty('answer');
    expect(rows[1].revealed).toBe(true);
    expect(rows[0].ts).toBe('2026-11-15T10:00:00.000Z');
  });

  it('stamps each recorded row with its review state, folded from the earlier records on its units', async () => {
    const calls: Call[] = [];
    const prior = [{ ts: '2026-11-14T10:00:00+00:00', unit_id: 'storage', level: 1, verdict: 'unknown', confidence: 1 }];
    const items = [
      { unit_id: 'storage', level: 1, question: 'Why is it needed?', reference_answer: 'R', evidence: [], answer: 'It keeps the records.', variants: ['What does it keep?'], confidence: 3 },
      { unit_id: 'cache', level: 2, question: 'What was chosen?', reference_answer: 'R2', evidence: [], answer: '', revealed: true },
    ];
    const res = await handleGrade(post('/api/grade', { repo: REPO, sha: SHA, items, record: { repo_id: 7, ts: '2026-11-15T10:00:00Z', source: 'review' } }), { env: LIVE, fetch: liveFetch({ prior: () => Response.json(prior) }, calls) });
    const out = (await res.json()) as { recorded: { source: string; data: { review?: { reps: number; due: string; ease: number } } }[] };
    expect(out.recorded.map((r) => r.source)).toEqual(['review', 'review']);
    expect(out.recorded[0].data.review).toEqual({ ease: 2.4, interval: 1, reps: 1, due: '2026-11-16T10:00:00+00:00' });
    expect(out.recorded[1].data.review).toEqual({ ease: 2.3, interval: 1, reps: 0, due: '2026-11-16T10:00:00+00:00' });
    const read = calls.find((c) => c.url.includes('/rest/v1/records?'));
    const q = new URL(read?.url ?? 'http://x').searchParams;
    expect(Object.fromEntries(q)).toEqual({ select: 'ts,unit_id,level,verdict,confidence', repo_id: 'eq.7', unit_id: 'in.("storage","cache")', order: 'ts.asc,id.asc' });
    expect((read?.init?.headers as Record<string, string>)['accept-profile']).toBe('app_udebt');
    expect((read?.init?.headers as Record<string, string>).authorization).toBe('Bearer user-token');
    const odd = await handleGrade(post('/api/grade', { repo: REPO, sha: SHA, items: items.slice(0, 1), record: { repo_id: 7, ts: '2026-11-15T10:00:00Z', source: 'admin' } }), { env: LIVE, fetch: liveFetch() });
    expect(((await odd.json()) as { recorded: { source: string; data: object }[] }).recorded[0]).toMatchObject({ source: 'quiz', data: {} });
  });

  it('leaves recording to the page when the earlier records cannot be read', async () => {
    const calls: Call[] = [];
    const items = [{ unit_id: 'u', level: 1, question: 'Q?', answer: 'A' }];
    const res = await handleGrade(post('/api/grade', { repo: REPO, sha: SHA, items, record: { repo_id: 7, ts: '2026-11-15T10:00:00Z' } }), { env: LIVE, fetch: liveFetch({ prior: () => new Response('no', { status: 500 }) }, calls) });
    expect(((await res.json()) as { recorded: unknown }).recorded).toBeNull();
    expect(calls.some((c) => c.init?.method === 'POST' && c.url.endsWith('/rest/v1/records'))).toBe(false);
    expect(await priorRecords(7, ['u'], 't', LIVE, (async () => Response.json({ not: 'a list' })) as typeof fetch)).toBeNull();
    expect(await priorRecords(7, ['u'], 't', LIVE, (async () => { throw new Error('down'); }) as typeof fetch)).toBeNull();
    expect(await priorRecords(7, [], 't', LIVE, liveFetch())).toEqual([]);
    expect(await priorRecords(7, ['u'], 't', { ...LIVE, fakes: true })).toBeNull();
    expect(await priorRecords(7, ['u'], 't', LIVE, (async () => Response.json([{ ts: 'soon', unit_id: 'u' }])) as typeof fetch)).toEqual([{ ts: 'soon', unit_id: 'u' }]);
    expect(inList(['a', 'b "c"', 'd\\e'])).toBe('("a","b \\"c\\"","d\\\\e")');
  });

  it('records nothing for another repository id, and leaves recording to the page when PostgREST refuses', async () => {
    const items = [{ unit_id: 'u', level: 1, question: 'Q?', answer: 'A' }];
    const other = await handleGrade(post('/api/grade', { repo: REPO, sha: SHA, items, record: { repo_id: 99, ts: '2026-11-15T10:00:00Z' } }), { env: LIVE, fetch: liveFetch() });
    expect(((await other.json()) as { recorded: unknown }).recorded).toBeNull();
    const refused = await handleGrade(post('/api/grade', { repo: REPO, sha: SHA, items, record: { repo_id: 7, ts: '2026-11-15T10:00:00Z' } }), { env: LIVE, fetch: liveFetch({ records: () => new Response('no', { status: 403 }) }) });
    expect(((await refused.json()) as { recorded: unknown }).recorded).toBeNull();
  });

  it('passes a gateway cap through and turns other failures into plain messages', async () => {
    const cap = { error: 'cap', scope: 'app-user-day', resets_at: '2026-11-16T00:00:00Z', message: 'used up' };
    const req = () => post('/api/questions', { repo: REPO, sha: SHA, units: [unit] });
    const capped = await handleQuestions(req(), { env: LIVE, fetch: liveFetch({ gateway: () => Response.json(cap, { status: 429 }) }) });
    expect(capped.status).toBe(429);
    expect(await capped.json()).toEqual(cap);
    const expired = await handleQuestions(req(), { env: LIVE, fetch: liveFetch({ gateway: () => new Response('{}', { status: 401 }) }) });
    expect(expired.status).toBe(401);
    const broken = await handleQuestions(req(), { env: LIVE, fetch: liveFetch({ gateway: () => Response.json({ message: 'overloaded' }, { status: 529 }) }) });
    expect(broken.status).toBe(502);
    expect(await broken.json()).toEqual({ error: 'overloaded' });
    const empty = await handleQuestions(req(), { env: LIVE, fetch: liveFetch({ gateway: () => Response.json({ content: [{ type: 'text', text: 'sorry' }] }) }) });
    expect(empty.status).toBe(502);
    const down = await handleQuestions(req(), {
      env: LIVE,
      fetch: (async (u: RequestInfo | URL, i?: RequestInit) => {
        if (String(u).includes('llm-gateway')) throw new Error('down');
        return liveFetch()(u, i);
      }) as typeof fetch,
    });
    expect(down.status).toBe(502);
  });

  it('asks the gateway for the person\'s allowance in check mode, with their token and no model call', async () => {
    const calls: Call[] = [];
    const used = { allowed: false, uses_credit: false, scope: 'app-user-day', resets_at: '2026-11-16T00:00:00Z', app_open: true, limits: [], calls_left: null };
    const res = await handleAllowance(post('/api/allowance', {}), { env: LIVE, fetch: liveFetch({ gateway: () => Response.json(used) }, calls) });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(used);
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe('https://auth.example/functions/v1/llm-gateway');
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ app: 'udebt', check: true });
    expect((calls[0].init?.headers as Record<string, string>).authorization).toBe('Bearer user-token');
    expect((await handleAllowance(post('/api/allowance', {}, null), { env: LIVE, fetch: liveFetch() })).status).toBe(401);
    expect((await handleAllowance(new Request('http://x/api/allowance', { headers: { authorization: 'Bearer user-token' } }), { env: LIVE })).status).toBe(405);
    expect((await handleAllowance(post('/api/allowance', {}), { env: LIVE, fetch: liveFetch({ gateway: () => new Response('{}', { status: 401 }) }) })).status).toBe(401);
    expect((await handleAllowance(post('/api/allowance', {}), { env: LIVE, fetch: liveFetch({ gateway: () => Response.json({ content: [] }) }) })).status).toBe(502);
    expect((await handleAllowance(post('/api/allowance', {}), { env: { ...LIVE, supabaseUrl: null }, fetch: liveFetch() })).status).toBe(503);
    const down = (async () => {
      throw new Error('down');
    }) as typeof fetch;
    expect((await handleAllowance(post('/api/allowance', {}), { env: LIVE, fetch: down })).status).toBe(502);
  });

  it('says which check refused a token, and what it said', async () => {
    const req = () => post('/api/grade', { repo: REPO, sha: SHA, items: [{ question: 'Q?', answer: 'a' }] });
    const db = await handleGrade(req(), { env: LIVE, fetch: liveFetch({ repo: () => Response.json({ code: 'PGRST301', message: 'JWT expired' }, { status: 401 }) }) });
    expect([db.status, await db.json()]).toEqual([401, { error: 'Your sign-in has expired. Sign in again.', detail: 'database 401: JWT expired' }]);
    resetAuthCache();
    const denied = await handleGrade(req(), { env: LIVE, fetch: liveFetch({ repo: () => Response.json({ code: '42501', message: 'permission denied for table repositories' }, { status: 403 }) }) });
    expect([denied.status, await denied.json()]).toEqual([401, { error: 'The database refused this for your account. Sign in again.', detail: 'database 403: permission denied for table repositories' }]);
    resetAuthCache();
    // The site's anon key is not the project's: the server's setting, so a 503 the page does not retry.
    const key = await handleGrade(req(), { env: LIVE, fetch: liveFetch({ repo: () => Response.json({ message: 'Invalid API key', hint: 'Double check your Supabase `anon` or `service_role` API key.' }, { status: 401 }) }) });
    expect(key.status).toBe(503);
    expect(await key.json()).toEqual({ error: "The server is misconfigured: its database key (SUPABASE_ANON_KEY on Netlify) is not this project's. Nothing is wrong with your sign-in.", detail: 'database 401: Invalid API key' });
    resetAuthCache();
    const gw = await handleGrade(req(), { env: LIVE, fetch: liveFetch({ gateway: () => Response.json({ error: 'auth', message: 'The session token is invalid or expired; sign in again.' }, { status: 401 }) }) });
    expect([gw.status, await gw.json()]).toEqual([401, { error: 'Your sign-in has expired. Sign in again.', detail: 'gateway 401: The session token is invalid or expired; sign in again.' }]);
  });

  it('serves visitors who are not signed in from the shared allowance, only with the switch on and only for a sample at its pin', async () => {
    const anon = { ...LIVE, anonModel: true, appKey: 'app-secret' };
    const open = (u: string, s: string) => u === REPO && s === SHA;
    const body = { repo: REPO, sha: SHA, units: [unit] };
    // Off (the default): the 401 it always was, and nothing leaves the function.
    const offCalls: Call[] = [];
    expect((await handleQuestions(post('/api/questions', body, null), { env: LIVE, fetch: liveFetch({}, offCalls), open })).status).toBe(401);
    expect(offCalls).toEqual([]);
    // On: the gateway is asked with the anonymous mark and no token, and PostgREST is not asked at all.
    const calls: Call[] = [];
    const q = await handleQuestions(post('/api/questions', body, null), { env: anon, fetch: liveFetch({}, calls), open });
    expect(q.status).toBe(200);
    const gw = calls.filter((c) => c.url.includes('llm-gateway'));
    expect(gw).toHaveLength(1);
    // Plabs' spec: the project's anon key as the bearer and the app key in its header.
    expect(gw[0].init?.headers).toEqual({ authorization: 'Bearer anon', [APP_KEY_HEADER]: 'app-secret', 'content-type': 'application/json' });
    expect(calls.some((c) => c.url.includes('/rest/v1/'))).toBe(false);
    // Another repository or commit: refused.
    const other = await handleQuestions(post('/api/questions', { ...body, sha: 'a'.repeat(40) }, null), { env: anon, fetch: liveFetch(), open });
    expect([other.status, ((await other.json()) as { error: string }).error]).toEqual([401, 'Sign in first: without signing in, only the samples at their pinned commits can be quizzed.']);
    // Grading: graded, never recorded on the server (the page keeps it in this browser).
    const gcalls: Call[] = [];
    const g = await handleGrade(post('/api/grade', { repo: REPO, sha: SHA, items: [{ question: 'Q?', answer: 'a' }], record: { repo_id: 7, ts: '2026-11-15T10:00:00Z' } }, null), { env: anon, fetch: liveFetch({}, gcalls), open });
    expect(g.status).toBe(200);
    expect(((await g.json()) as { recorded: unknown }).recorded).toBeNull();
    expect(gcalls.some((c) => c.url.includes('/rest/v1/'))).toBe(false);
    // The allowance check, the same way.
    const acalls: Call[] = [];
    const a = await handleAllowance(post('/api/allowance', {}, null), { env: anon, fetch: liveFetch({ gateway: () => Response.json({ allowed: true, scope: null, resets_at: null }) }, acalls) });
    expect(a.status).toBe(200);
    expect(JSON.parse(String(acalls[0].init?.body))).toEqual({ app: 'udebt', check: true });
    expect(acalls[0].init?.headers).toEqual({ authorization: 'Bearer anon', [APP_KEY_HEADER]: 'app-secret', 'content-type': 'application/json' });
    expect((await handleAllowance(post('/api/allowance', {}, null), { env: LIVE, fetch: liveFetch() })).status).toBe(401);
    expect(readEnv({ VITE_UDEBT_ANON_MODEL: '1' }).anonModel).toBe(true);
    expect(readEnv({ HINTWISE_APP_KEY: '  s3cret \n' })).toMatchObject({ appKey: 's3cret', anonModel: true }); // the key turns the functions' side on
    expect(readEnv({ HINTWISE_APP_KEY: '  ' })).toMatchObject({ appKey: null, anonModel: false });
    // Switched on without the app key: the server's setting, said so, and the gateway not called.
    const nokey: Call[] = [];
    const n = await handleQuestions(post('/api/questions', body, null), { env: { ...LIVE, anonModel: true }, fetch: liveFetch({}, nokey), open });
    expect([n.status, nokey.length]).toEqual([503, 0]);
    // The gateway refuses the app key: a 503 naming the server, not "sign in again".
    const refused = await handleQuestions(post('/api/questions', body, null), { env: anon, fetch: liveFetch({ gateway: () => Response.json({ error: 'auth', anonymous: true, message: 'Unknown app key.' }, { status: 401 }) }), open });
    expect(refused.status).toBe(503);
    expect(((await refused.json()) as { detail: string }).detail).toBe('gateway 401: Unknown app key.');
    // The shared allowance used up: the gateway's 429 passes through, marked anonymous.
    const cap = { error: 'cap', scope: 'user-day', resets_at: '2026-11-16T00:00:00Z', anonymous: true };
    const c = await handleQuestions(post('/api/questions', body, null), { env: anon, fetch: liveFetch({ gateway: () => Response.json(cap, { status: 429 }) }), open });
    expect([c.status, await c.json()]).toEqual([429, cap]);
  });

  it('refuses a missing or expired token, an unknown repository and a bad request', async () => {
    expect((await handleQuestions(post('/api/questions', {}, null), { env: LIVE, fetch: liveFetch() })).status).toBe(401);
    expect((await handleQuestions(post('/api/questions', { repo: REPO, sha: SHA, units: [unit] }), { env: LIVE, fetch: liveFetch({ repo: () => new Response('{}', { status: 401 }) }) })).status).toBe(401);
    const unknown = await handleQuestions(post('/api/questions', { repo: REPO, sha: SHA, units: [unit] }), { env: LIVE, fetch: liveFetch({ repo: () => Response.json([]) }) });
    expect(unknown.status).toBe(403);
    expect(await unknown.json()).toEqual({ error: 'Add this repository in udebt first.' });
    expect((await handleQuestions(post('/api/questions', { repo: 'https://gitlab.com/a/b', sha: SHA, units: [unit] }), { env: LIVE, fetch: liveFetch() })).status).toBe(400);
    expect((await handleQuestions(new Request('http://x/api/questions', { method: 'POST', headers: { authorization: 'Bearer user-token' }, body: 'not json' }), { env: LIVE })).status).toBe(400);
    expect((await handleQuestions(new Request('http://x/api/questions', { headers: { authorization: 'Bearer user-token' } }), { env: LIVE })).status).toBe(405);
    expect((await handleQuestions(post('/api/questions', { repo: REPO, sha: SHA, units: [unit] }), { env: { ...LIVE, supabaseUrl: null }, fetch: liveFetch() })).status).toBe(503);
    expect((await handleQuestions(post('/api/questions', { repo: REPO, sha: SHA, units: [unit] }), { env: LIVE, fetch: liveFetch({ repo: () => new Response('x', { status: 500 }) }) })).status).toBe(502);
  });

  it('serves an excerpt at the sha, never cached, and refuses what it cannot read', async () => {
    const url = `http://x/api/excerpt?repo=${encodeURIComponent(REPO)}&sha=${SHA}&path=src/store.py&start=3&end=5`;
    const get = (u: string, token = 'user-token') => new Request(u, { headers: { authorization: `Bearer ${token}` } });
    const res = await handleExcerpt(get(url), { env: LIVE, fetch: liveFetch() });
    expect(res.status).toBe(200);
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(await res.json()).toEqual({ path: 'src/store.py', start: 3, end: 5, lines: ['line 3', 'line 4', 'line 5'], cut: 0 });
    expect((await handleExcerpt(get(url), { env: LIVE, fetch: liveFetch({ raw: () => new Response('', { status: 404 }) }) })).status).toBe(404);
    expect((await handleExcerpt(get(url.replace('src/store.py', '../etc/passwd')), { env: LIVE, fetch: liveFetch() })).status).toBe(400);
    expect((await handleExcerpt(new Request(url, { method: 'POST', headers: { authorization: 'Bearer user-token' } }), { env: LIVE })).status).toBe(405);
    expect((await handleExcerpt(get(url), { env: LIVE, fetch: liveFetch({ raw: () => new Response('a\u0000b') }) })).status).toBe(404);
  });

  it('caches a token check for five minutes per repository', async () => {
    const calls: Call[] = [];
    const f = liveFetch({}, calls);
    expect(await authorize('t', REPO, LIVE, f, 0)).toBe(7);
    expect(await authorize('t', REPO, LIVE, f, 60_000)).toBe(7);
    expect(calls).toHaveLength(1);
    await authorize('t', REPO, LIVE, f, 6 * 60_000);
    expect(calls).toHaveLength(2);
    await expect(authorize('t', REPO, LIVE, (async () => { throw new Error('x'); }) as typeof fetch, 99 * 60_000)).rejects.toThrow(HttpError);
  });

  it('reads a raw file within the size cap', async () => {
    expect(await fetchFile('acme', 'demo', SHA, 'a.py', LIVE, liveFetch())).toBe(FILE);
    expect(await fetchFile('acme', 'demo', SHA, 'a.py', LIVE, liveFetch({ raw: () => new Response('x', { headers: { 'content-length': '9000000' } }) }))).toBeNull();
    expect(await fetchFile('acme', 'demo', SHA, 'a.py', LIVE, liveFetch({ raw: () => new Response(new Uint8Array(2_100_000)) }))).toBeNull();
    expect(await fetchFile('ac/me', 'demo', SHA, 'a.py', LIVE, liveFetch())).toBeNull();
    expect(await fetchFile('acme', 'demo', SHA, 'a.py', LIVE, (async () => { throw new Error('x'); }) as typeof fetch)).toBeNull();
  });
});

describe('the functions with the fakes', () => {
  const env: FnEnv = { supabaseUrl: null, anonKey: null, fakes: true, fakeRepos: {} };

  it('accept only the preview token and answer from the fake gateway', async () => {
    const res = await handleQuestions(post('/api/questions', { repo: REPO, sha: SHA, units: [unit] }, FAKE_TOKEN), { env });
    expect(res.status).toBe(200);
    const bad = await handleQuestions(post('/api/questions', { repo: REPO, sha: SHA, units: [unit] }, 'some-other-token'), { env });
    expect(bad.status).toBe(401);
    const g = await handleGrade(post('/api/grade', { repo: REPO, sha: SHA, items: [{ question: 'Q?', answer: 'this is wrong' }, { question: 'Q2?', answer: 'partly so' }] }, FAKE_TOKEN), { env });
    expect(((await g.json()) as { grades: { verdict: string }[] }).grades.map((x) => x.verdict)).toEqual(['wrong', 'partial']);
  });

  it('fake an open allowance, or with UDEBT_FAKE_CAP one used up until the next UTC midnight, on the check and the calls alike', async () => {
    const open = (await (await handleAllowance(post('/api/allowance', {}, FAKE_TOKEN), { env })).json()) as { allowed: boolean };
    expect(open.allowed).toBe(true);
    const capEnv = { ...env, fakeCap: true };
    const used = (await (await handleAllowance(post('/api/allowance', {}, FAKE_TOKEN), { env: capEnv })).json()) as { allowed: boolean; scope: string; resets_at: string };
    expect([used.allowed, used.scope]).toEqual([false, 'app-user-day']);
    expect(new Date(used.resets_at).getUTCHours()).toBe(0);
    const q = await handleQuestions(post('/api/questions', { repo: REPO, sha: SHA, units: [unit] }, FAKE_TOKEN), { env: capEnv });
    expect(q.status).toBe(429);
    expect(((await q.json()) as { error: string }).error).toBe('cap');
    expect(readEnv({ UDEBT_FAKES: '1', UDEBT_FAKE_CAP: '1' }).fakeCap).toBe(true);
    expect(readEnv({ UDEBT_FAKE_CAP: '1' }).fakeCap).toBe(false);
    expect(readEnv({ UDEBT_FAKES: '1', UDEBT_FAKE_CAP: '1', NETLIFY: 'true' }).fakeCap).toBe(false);
  });

  it('read excerpts from a local clone with git show', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'udebt-fake-'));
    const git = (...a: string[]) => execFileSync('git', ['-C', dir, ...a], { encoding: 'utf8' });
    git('init', '-q');
    writeFileSync(join(dir, 'a.py'), 'one\ntwo\nthree\n');
    git('add', '.');
    git('-c', 'user.name=t', '-c', 'user.email=t@e', 'commit', '-qm', 'x');
    const sha = git('rev-parse', 'HEAD').trim();
    const local = { ...env, fakeRepos: { 'acme/demo': dir } };
    expect(await excerpt({ owner: 'acme', repo: 'demo' }, sha, { path: 'a.py', start: 2, end: 3 }, local)).toMatchObject({ lines: ['two', 'three'] });
    expect(await excerpt({ owner: 'acme', repo: 'demo' }, sha, { path: 'nope.py', start: 1, end: 3 }, local)).toBeNull();
    expect(await fetchFile('other', 'repo', sha, 'a.py', local)).toBeNull();
  });

  it('parse units the way the hosted prompt writes them', () => {
    const units = fakeUnits('- unit_id: a\n  name: A (a part of P)\n  level: 3 (x)\n  summary: Does a.\n  paths: x.py, y.py\n- unit_id: b\n  level: 9\n');
    expect(units).toEqual([
      { id: 'a', name: 'A', level: 3, summary: 'Does a.', paths: ['x.py', 'y.py'] },
      { id: 'b', name: 'b', level: 9, summary: '', paths: [] },
    ]);
    const q = fakeGateway({ max_tokens: 1, messages: [{ role: 'user', content: 'TASK: questions\n- unit_id: b\n  level: 9\n' }] });
    expect(JSON.stringify(q)).toContain('What job does b do');
    expect(fakeGateway({ max_tokens: 1, messages: [{ role: 'user', content: 'TASK: other' }] }).content[0].type).toBe('text');
  });
});

describe('the small parts', () => {
  it('reads its settings and never fakes on Netlify', () => {
    expect(readEnv({ SUPABASE_URL: 'https://a/', SUPABASE_ANON_KEY: 'k' })).toEqual({ supabaseUrl: 'https://a', anonKey: 'k', fakes: false, fakeRepos: {}, fakeCap: false, anonModel: false, appKey: null });
    expect(readEnv({ VITE_SUPABASE_URL: 'https://b', UDEBT_FAKES: '1', UDEBT_FAKE_REPOS: '{"a/b":"/x","c":3}' })).toMatchObject({ supabaseUrl: 'https://b', fakes: true, fakeRepos: { 'a/b': '/x' } });
    expect(readEnv({ UDEBT_FAKES: '1', NETLIFY: 'true' }).fakes).toBe(false);
    expect(readEnv({ UDEBT_FAKES: '1', AWS_LAMBDA_FUNCTION_NAME: 'f' }).fakes).toBe(false);
    expect(readEnv({ UDEBT_FAKE_REPOS: '/one/clone' }).fakeRepos).toEqual({ '*': '/one/clone' });
    expect(readEnv({ UDEBT_FAKE_REPOS: '[1]' }).fakeRepos).toEqual({});
  });

  it('builds the gateway body from the five fields it passes, clamped', async () => {
    expect(gatewayBody({ model: 'm', max_tokens: 99_999, system: 's', messages: [], tools: [] })).toEqual({ app: 'udebt', max_tokens: 4096, messages: [], model: 'm', system: 's' });
    await expect(callGateway({ max_tokens: 1, messages: [{ role: 'user', content: 'x'.repeat(1_000_001) }] }, 't', LIVE)).rejects.toMatchObject({ status: 413 });
    await expect(callGateway({ max_tokens: 1, messages: [] }, 't', { ...LIVE, supabaseUrl: null })).rejects.toMatchObject({ status: 503 });
  });

  it('reads bearer tokens and JSON bodies with limits', async () => {
    expect(() => bearer(new Request('http://x', { headers: { authorization: 'Basic abc' } }))).toThrow(HttpError);
    await expect(readJson(new Request('http://x', { method: 'POST', headers: { 'content-length': '9999999' }, body: '{}' }))).rejects.toMatchObject({ status: 413 });
    await expect(readJson(new Request('http://x', { method: 'POST', body: 'x'.repeat(1_600_000) }))).rejects.toMatchObject({ status: 413 });
    const e = errorResponse(new Error('boom'));
    expect(e.status).toBe(500);
    expect(errorResponse('plain').status).toBe(500);
  });

  it('writes records only live, and strips the scoring alias', async () => {
    const r = { repo_id: 1, sha: SHA, unit_id: 'u', unit: 'u', id: 3, ts: 't', level: 1, verdict: 'correct' as const, confidence: 2, type: 'quiz', source: 'quiz', question: 'q', revealed: false, forced: false, data: {} };
    expect(tableRow(r)).not.toHaveProperty('unit');
    expect(tableRow(r)).not.toHaveProperty('id');
    expect(await insertRecords([r], 't', { ...LIVE, fakes: true })).toBeNull();
    expect(await insertRecords([r], 't', LIVE, (async () => { throw new Error('x'); }) as typeof fetch)).toBeNull();
    expect(await insertRecords([r], 't', LIVE, (async () => Response.json({ not: 'a list' })) as typeof fetch)).toBeNull();
  });

  it('routes each entry point at its /api path', async () => {
    expect([questionsConfig.path, gradeConfig.path, excerptConfig.path, allowanceConfig.path]).toEqual(['/api/questions', '/api/grade', '/api/excerpt', '/api/allowance']);
    for (const fn of [questionsFn, gradeFn, excerptFn, allowanceFn]) expect((await fn(new Request('http://x/api', { method: 'PUT' }))).status).toBeGreaterThanOrEqual(400);
  });
});
