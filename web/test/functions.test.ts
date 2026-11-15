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
import { authorize, resetAuthCache } from '../netlify/functions/lib/auth';
import { FAKE_TOKEN, readEnv, type FnEnv } from '../netlify/functions/lib/env';
import { fakeGateway, fakeUnits } from '../netlify/functions/lib/fake';
import { callGateway, gatewayBody } from '../netlify/functions/lib/gateway';
import { excerpt, fetchFile } from '../netlify/functions/lib/github';
import { handleExcerpt, handleGrade, handleQuestions } from '../netlify/functions/lib/handlers';
import { HttpError, bearer, errorResponse, readJson } from '../netlify/functions/lib/http';
import { insertRecords, tableRow } from '../netlify/functions/lib/records';

const SHA = 'f93102b121f1da9055c148e8eccde275ccf18236';
const REPO = 'https://github.com/acme/demo';
const LIVE: FnEnv = { supabaseUrl: 'https://auth.example', anonKey: 'anon', fakes: false, fakeRepos: {} };
const FILE = Array.from({ length: 40 }, (_, i) => `line ${i + 1}`).join('\n');

interface Call {
  url: string;
  init?: RequestInit;
}

function liveFetch(over: Partial<Record<'repo' | 'gateway' | 'raw' | 'records', () => Response>> = {}, calls: Call[] = []): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    if (url.includes('/rest/v1/repositories')) return over.repo?.() ?? Response.json([{ id: 7 }]);
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
    expect(readEnv({ SUPABASE_URL: 'https://a/', SUPABASE_ANON_KEY: 'k' })).toEqual({ supabaseUrl: 'https://a', anonKey: 'k', fakes: false, fakeRepos: {} });
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
    expect([questionsConfig.path, gradeConfig.path, excerptConfig.path]).toEqual(['/api/questions', '/api/grade', '/api/excerpt']);
    for (const fn of [questionsFn, gradeFn, excerptFn]) expect((await fn(new Request('http://x/api', { method: 'PUT' }))).status).toBeGreaterThanOrEqual(400);
  });
});
