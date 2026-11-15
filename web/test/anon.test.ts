// @vitest-environment jsdom
/**
 * Using udebt without signing in (HOSTING.md decision 10), in jsdom over the preview wiring: the
 * samples from the fixture with their advance quizzes, answers kept in a fake localStorage, the
 * functions' real handlers behind /api. A self-checked advance quiz lowers the debt with its label,
 * a miss comes back in review and is self-checked there, an own key gets model-written questions
 * without the gateway, and at sign-in the copy offer copies, keeps or discards. Every request the
 * page makes is watched: nothing anonymous calls the gateway functions or Supabase, and the code
 * is read without a token.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fixture from '../fixtures/digitalme.json';
import quizzes from '../fixtures/digitalme.quizzes.json';
import { previewOpen } from '../dev/api-plugin';
import { handleExcerpt, handleGrade, handleQuestions, handleTree } from '../netlify/functions/lib/handlers';
import type { FnEnv } from '../netlify/functions/lib/env';
import { fakeGateway } from '../netlify/functions/lib/fake';
import type { Fixture } from '../src/data/store';
import { LOCAL_PREFIX, LocalStore } from '../src/data/local';
import { memoryStorage } from '../src/lib/llm';
import { GatewayClient, LazyByokClient } from '../src/model/client';
import { setByokKey, setMode } from '../src/model/prefs';
import { App, parseRoute } from '../src/ui/app';
import { previewDeps } from '../src/ui/boot';
import { selfCheckFlow } from '../src/ui/screens/quiz';
import { StaticSamples } from '../src/data/local';

const env: FnEnv = { supabaseUrl: null, anonKey: null, fakes: true, fakeRepos: {} };
const open = previewOpen(fixture as unknown as Fixture);
const seen: { path: string; auth: string | null }[] = [];
const apiFetch: typeof fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const req = new Request(new URL(String(input), 'http://site'), init);
  const path = new URL(req.url).pathname;
  seen.push({ path, auth: req.headers.get('authorization') });
  if (path === '/api/questions') return handleQuestions(req, { env });
  if (path === '/api/grade') return handleGrade(req, { env });
  if (path === '/api/excerpt') return handleExcerpt(req, { env, open });
  if (path === '/api/tree') return handleTree(req, { env, open });
  return new Response('{}', { status: 404 });
}) as typeof fetch;

const tick = async (n = 10) => {
  for (let i = 0; i < n; i += 1) await new Promise((r) => setTimeout(r, 0));
};
const $ = <T extends Element = HTMLElement>(sel: string) => document.querySelector(sel) as T | null;
const byText = (sel: string, text: string | RegExp) => [...document.querySelectorAll<HTMLElement>(sel)].find((e) => (typeof text === 'string' ? e.textContent?.trim() === text : text.test(e.textContent ?? '')));
const click = async (sel: string, text: string | RegExp, n = 10) => {
  const el = byText(sel, text);
  if (!el) throw new Error(`no ${sel} with ${String(text)}: ${document.body.textContent?.slice(0, 600)}`);
  el.click();
  await tick(n);
};
const answer = (text: string) => (($<HTMLTextAreaElement>('textarea') as HTMLTextAreaElement).value = text);
const at = (iso: string) => vi.setSystemTime(new Date(iso));

let app: App;
let local: Storage;
let session: Storage;

async function start(opts: { quizzes?: unknown[]; storage?: Storage } = {}): Promise<App> {
  session = memoryStorage();
  local = opts.storage ?? memoryStorage();
  const root = document.createElement('div');
  root.id = 'app';
  document.body.replaceChildren(root);
  app = new App(root, previewDeps(fixture as unknown as Fixture, { doFetch: apiFetch, session, local, quizzes: opts.quizzes ?? [quizzes] }));
  window.location.hash = '#/';
  app.start();
  await tick();
  return app;
}

async function go(hash: string): Promise<void> {
  window.location.hash = hash;
  await app.render();
  await tick();
}

function kept(): { records: { type: string; source: string; verdict: string; revealed: boolean; level: number; question: string; data: { reference?: string; review?: unknown } }[]; decided: number } {
  return JSON.parse(local.getItem(`${LOCAL_PREFIX}digitalme`) ?? '{"records":[],"decided":0}');
}

/** A self-checked level-1 sitting of two: the first marked `first`, the second "I don't know". */
async function sitting(first: 'Correct' | 'Wrong' | 'Partly right' = 'Correct'): Promise<void> {
  await go('#/r/1/quiz');
  await click('button', 'Start');
  expect(byText('p', /^\[1\/2\]/)?.textContent).toMatch(/level 1 of 5: purpose and placement · self-checked/);
  expect(byText('summary', 'Orientation: read this first')).toBeTruthy();
  await click('button', 'Check Answer');
  expect($('[data-testid=reference]')).toBeNull(); // nothing typed: nothing shown
  answer('It streams the reply and keeps the exchange.');
  await click('button', 'Check Answer');
  expect($('[data-testid=reference]')?.textContent).toMatch(/^The answer/);
  expect($<HTMLTextAreaElement>('textarea')?.disabled).toBe(true);
  expect([...document.querySelectorAll<HTMLInputElement>('.conf input')].every((r) => r.disabled)).toBe(true);
  expect(document.querySelectorAll('.marks button.primary')).toHaveLength(0);
  await click('button', first);
  expect(byText('p', /^\[2\/2\]/)).toBeTruthy();
  await click('button', "I don't know");
  expect(byText('p', /A known unknown/)).toBeTruthy();
  expect($('[data-testid=reference]')).toBeTruthy();
  await click('button', 'Record my answers', 20);
}

beforeEach(() => {
  seen.length = 0;
  window.location.hash = '';
  vi.useFakeTimers({ toFake: ['Date'], now: new Date('2026-11-15T10:00:00Z') });
});
afterEach(() => {
  app?.stop();
  vi.useRealTimers();
});

describe('without signing in', () => {
  it('lists the samples with sign-in in the header, and reads a unit with code fetched without a token', async () => {
    await start();
    expect(byText('h1', 'Understand the code an AI wrote')).toBeTruthy();
    expect(byText('h2', 'Start with a sample: no account needed')).toBeTruthy();
    expect($('a.repo-card')?.textContent).toContain('100.0');
    expect([...document.querySelectorAll('.who a')].map((a) => a.textContent)).toEqual(['Samples', 'Settings', 'Sign in']);
    expect(document.title).toBe('Samples · udebt');
    expect(parseRoute('#/signin')).toEqual({ name: 'signin' });
    await go('#/r/1');
    expect($('[data-testid=local-note]')?.textContent).toMatch(/stay in this browser/);
    expect($<HTMLElement>('[data-testid=self-checked-index]')?.hidden).toBe(true);
    await go('#/r/1/u/chat-flow');
    await tick(30);
    expect(byText('h3', 'Orientation')).toBeTruthy();
    const ex = seen.filter((r) => r.path === '/api/excerpt');
    expect(ex.length).toBeGreaterThan(0);
    expect(ex.every((r) => r.auth === null)).toBe(true);
    // no clone in this test: GitHub's fake answers 404 (could not be read), not 401 (sign in)
    expect($('.walkthrough .excerpt .error')?.textContent).toMatch(/could not be read/);
  });

  it('takes a self-checked advance quiz: the debt falls with the label, kept in this browser with no model call', async () => {
    await start();
    await go('#/r/1/quiz');
    expect(byText('p', /Questions written in advance for this repository/)).toBeTruthy();
    expect(byText('p', /Recorded as self-checked in this browser/)).toBeTruthy();
    await sitting('Correct');
    expect(byText('h3', 'Recorded')).toBeTruthy();
    expect(byText('p', /^1 correct, 1 unknown, self-checked \(no model call\)\./)).toBeTruthy();
    const after = Number($('[data-testid=debt-delta] strong:nth-of-type(2)')?.textContent);
    expect(after).toBeLessThan(100);
    expect($<HTMLElement>('[data-testid=self-checked-index]')?.hidden).toBe(false);
    expect($('[data-testid=debt-index]')?.textContent).toBe(after.toFixed(1));
    expect(document.querySelectorAll('.result .badge.self-checked')).toHaveLength(2);
    const k = kept();
    expect(k.records.map((r) => [r.type, r.source, r.verdict, r.revealed, r.level])).toEqual([
      ['self-checked', 'advance', 'correct', true, 1],
      ['self-checked', 'advance', 'unknown', true, 1],
    ]);
    expect(k.records.every((r) => r.data.reference && !('answer' in r))).toBe(true);
    expect(k.records[1].data.review).toMatchObject({ reps: 0, interval: 1 });
    await go('#/r/1/debt');
    expect($('[data-testid=debt-tile]')?.textContent).toBe(after.toFixed(1));
    expect($('[data-testid=debt-self-note]')?.textContent).toMatch(/rests in part on self-checked answers/);
    expect(document.querySelectorAll('table.debt .badge.self-checked').length).toBeGreaterThan(0);
    expect($('[data-testid=self-checked-note]')).toBeTruthy();
    expect(seen.filter((r) => r.path === '/api/questions' || r.path === '/api/grade')).toEqual([]);
    expect(seen.every((r) => r.auth === null)).toBe(true);
  });

  it('brings a miss back in review the next day and self-checks it there', async () => {
    await start();
    await sitting('Wrong');
    await go('#/r/1/review');
    expect(byText('h2', 'Nothing due now')).toBeTruthy();
    at('2026-11-16T11:00:00Z');
    app.forget();
    await go('#/r/1/review');
    expect(byText('h2', 'Due now: 2')).toBeTruthy();
    expect(byText('p', /No model is in use: you compare each answer/)).toBeTruthy();
    const before = Number($('[data-testid=debt-index]')?.textContent);
    await click('button', 'Review what is due');
    answer('It streams the reply as it is written.');
    await click('button', 'Check Answer');
    await click('button', 'Correct');
    await click('button', 'Finish now', 20);
    expect(byText('p', /^1 correct, self-checked/)).toBeTruthy();
    expect(Number($('[data-testid=debt-index]')?.textContent)).toBeLessThan(before);
    const r = kept().records;
    expect(r[r.length - 1]).toMatchObject({ type: 'self-checked', source: 'review', verdict: 'correct' });
    await click('a', 'Back to Review');
    expect(byText('h2', 'Due now: 1')).toBeTruthy();
  });

  it('asks a missed question again in other words, explains levels past 3, and records nothing for nothing', async () => {
    await start();
    await sitting('Wrong');
    const first = kept().records[0].question;
    app.forget();
    await go('#/r/1/quiz');
    await click('button', 'Start');
    const again = byText('p.q-text', /./)?.textContent;
    expect(again).not.toBe(first);
    await click('button', "I don't know");
    await click('button', 'Next question');
    await click('button', 'Finish now', 20);
    expect(byText('h3', 'Recorded')).toBeTruthy();
    // levels 1-3 passed on a unit: its next level is 4, which is never written in advance
    const store = app.deps.anon as LocalStore;
    const unit = 'cost-control';
    const ts = (n: number) => `2026-11-15T1${n}:00:00.000Z`; // after the sittings above
    await store.addRecords([1, 2, 3].map((lv) => ({ repo_id: 1, sha: fixture.analyses[0].sha, unit_id: unit, ts: ts(lv), level: lv, verdict: 'correct', confidence: 2, type: 'self-checked', source: 'advance', question: `q${lv}`, revealed: true, forced: false, data: {}, unit })));
    app.forget();
    app.quizUnit = unit;
    await go('#/r/1/quiz');
    expect(($<HTMLSelectElement>('#quiz-unit') as HTMLSelectElement).value).toBe(unit);
    await click('button', 'Start');
    expect(byText('p', /Levels 4 and 5 need code reading/)).toBeTruthy();
    // a sitting with every question left: nothing recorded
    app.quizUnit = null;
    await go('#/r/1/quiz');
    await click('button', 'Start');
    const stop = byText('button', 'Finish now') as HTMLButtonElement;
    expect(stop.hidden).toBe(true);
  });

  it('without advance questions, says how to get a quiz; with an own key, the model writes and grades with no gateway', async () => {
    await start({ quizzes: [] });
    await go('#/r/1/quiz');
    expect(byText('p', /No questions were written in advance for this repository yet/)).toBeTruthy();
    expect(($<HTMLButtonElement>('button.primary') as HTMLButtonElement).disabled).toBe(true);
    setMode('gateway', local);
    expect(app.quiz()).toBeNull(); // never the gateway without signing in
    setMode('byok', local);
    expect(app.quiz()).toBeNull(); // no key saved yet
    setByokKey('sk-test', local);
    const calls: unknown[] = [];
    app.deps.quiz = (mode) => {
      expect(mode).toBe('byok');
      return new LazyByokClient({
        getToken: async () => null,
        fetch: apiFetch,
        askDeps: { storage: local, createAnthropic: () => ({ messages: { create: async (body) => (calls.push(body), fakeGateway(body as never) as never) } }) },
      });
    };
    await go('#/r/1/quiz');
    expect(byText('p', /with your own Anthropic key/)).toBeTruthy();
    ($<HTMLSelectElement>('#quiz-n') as HTMLSelectElement).value = '1';
    await click('button', 'Start', 30);
    answer('It streams the reply to the browser.');
    await click('button', 'Check Answer and Grade', 40);
    expect(byText('p', /^1 correct \(one grading call\)/)).toBeTruthy();
    expect(calls).toHaveLength(2);
    expect(kept().records.map((r) => [r.type, r.verdict])).toEqual([['quiz', 'correct']]);
    expect(seen.filter((r) => r.path === '/api/questions' || r.path === '/api/grade')).toEqual([]);
    expect(seen.every((r) => r.auth === null)).toBe(true);
  });

  it('keeps settings honest without an account, and needs one to add a repository', async () => {
    await start();
    setMode('gateway', local);
    await go('#/settings');
    expect($<HTMLInputElement>('input[value=gateway]')?.disabled).toBe(true);
    expect(byText('p', /Not signed in: until you sign in or choose your own key/)).toBeTruthy();
    expect(byText('h2', 'Sign in')).toBeTruthy();
    expect(byText('li', /stay in this browser/)).toBeTruthy();
    await sitting();
    await go('#/settings');
    await click('button', 'Delete the answers kept in this browser');
    expect($('#toast')?.textContent).toMatch(/Deleted 2 answer/);
    expect(local.getItem(`${LOCAL_PREFIX}digitalme`)).toBeNull();
    await click('button', 'Delete the answers kept in this browser');
    expect($('#toast')?.textContent).toMatch(/keeps no answers/);
    await go('#/add');
    expect(byText('p', /Adding a repository needs an account/)).toBeTruthy();
    await go('#/signin');
    expect(byText('a', 'start with one')).toBeTruthy();
  });

  it('records nothing for an empty sitting, says why a recording failed, and lists a sample with no analysis yet', async () => {
    await start();
    const v = await app.repoView(1);
    const box = document.createElement('div');
    document.body.append(box);
    selfCheckFlow(app, v!, box, [], 'advance');
    await tick();
    expect(box.textContent).toBe('Nothing answered, nothing recorded.');
    (app.deps.anon as LocalStore).addRecords = async () => {
      throw new Error('storage gone');
    };
    await sitting();
    expect(byText('p', 'storage gone')).toBeTruthy();
    app.forget(1);
    const files: Record<string, unknown> = { 'samples/index.json': { format: 'udebt-samples', version: 1, samples: [{ name: 'next', url: 'https://github.com/acme/next', sha: 'a'.repeat(40), license: null, analysis: null, quizzes: null }] } };
    app.deps.anon = new LocalStore(new StaticSamples('samples/', (async (u: RequestInfo | URL) => (files[String(u)] ? Response.json(files[String(u)]) : new Response('', { status: 404 }))) as typeof fetch));
    app.forget();
    await go('#/');
    expect(byText('div', 'No analysis yet.')).toBeTruthy();
    app.deps.anon = new LocalStore(new StaticSamples('samples/', (async () => new Response('', { status: 404 })) as typeof fetch));
    app.forget();
    await go('#/');
    expect(byText('p', /The samples could not be loaded/)).toBeTruthy();
  });

  it('asks to sign in everywhere when the site has no store for visitors', async () => {
    await start();
    app.stop();
    const root = document.createElement('div');
    document.body.replaceChildren(root);
    const deps = previewDeps(fixture as unknown as Fixture, { doFetch: apiFetch, session: memoryStorage(), local: memoryStorage() });
    delete deps.anon;
    app = new App(root, deps);
    app.start();
    await tick();
    await go('#/r/1');
    expect(byText('h1', 'Understand the code an AI wrote')).toBeTruthy();
    expect(byText('p', /Four public repositories are ready/)).toBeTruthy();
    expect(document.querySelectorAll('.who a')).toHaveLength(0);
    expect(app.quiz()).toBeNull();
    await click('button', 'Continue with Google', 20);
    await go('#/settings');
    expect($('button')?.textContent).not.toBe('Delete the answers kept in this browser');
    expect(byText('button', 'Delete the answers kept in this browser')).toBeUndefined();
  });

  it('works in memory when storage is blocked', async () => {
    const blocked = {
      getItem: () => {
        throw new Error('blocked');
      },
      setItem: () => {
        throw new Error('blocked');
      },
      removeItem: () => {
        throw new Error('blocked');
      },
    } as unknown as Storage;
    await start({ storage: blocked });
    await sitting();
    expect(byText('h3', 'Recorded')).toBeTruthy();
    expect((await app.store.records(1)).length).toBe(2);
  });
});

describe('signing in with answers kept in this browser', () => {
  async function signIn(): Promise<void> {
    await go('#/signin');
    await click('button', 'Continue with Google', 20);
    await tick(20);
    await app.render();
    await tick(20);
  }

  it('offers once to copy them; copied, they are on the server with their provenance and never offered again', async () => {
    await start();
    await sitting();
    await signIn();
    expect(app.user).not.toBeNull();
    expect(window.location.hash).toBe('#/');
    expect(byText('h1', 'Repositories')).toBeTruthy();
    const offer = $('[data-testid=offer]');
    expect(offer?.textContent).toMatch(/This browser keeps 2 answers you gave without an account \(2 on digitalme\)\. Copy them into your account\? They stay marked as self-checked\./);
    expect([...document.querySelectorAll('.who a')].map((a) => a.textContent)).toEqual(['Repositories', 'Settings']);
    await click('button', 'Copy into my account', 30);
    expect($('#toast')?.textContent).toMatch(/Copied 2 answers into your account/);
    const server = await app.deps.store.records(1);
    expect(server.map((r) => [r.type, r.source, r.verdict])).toEqual([
      ['self-checked', 'advance', 'correct'],
      ['self-checked', 'advance', 'unknown'],
    ]);
    expect(JSON.parse(session.getItem('udebt.preview') ?? '{}').records).toHaveLength(2);
    await go('#/');
    expect($('[data-testid=offer]')).toBeNull();
    await go('#/r/1/debt');
    expect($('[data-testid=debt-self-note]')).toBeTruthy();
    // signed out again: back to the samples, the answers still in this browser, not offered twice
    await app.deps.auth.auth.signOut();
    await tick(20);
    await app.render();
    await tick();
    expect(window.location.hash).toBe('#/');
    expect(byText('h2', 'Start with a sample: no account needed')).toBeTruthy();
    expect((await app.store.records(1)).length).toBe(2);
    await signIn();
    expect($('[data-testid=offer]')).toBeNull();
  });

  it('keeps them separate, or discards them, when asked; new answers later are offered on their own', async () => {
    await start();
    await sitting();
    await signIn();
    await click('button', 'Keep them separate', 20);
    expect($('#toast')?.textContent).toMatch(/Kept in this browser only/);
    expect(await app.deps.store.records(1)).toEqual([]);
    expect($('[data-testid=offer]')).toBeNull();
    expect(kept().records).toHaveLength(2);
    await app.deps.auth.auth.signOut();
    await tick(20);
    at('2026-11-15T12:00:00Z');
    await sitting();
    await signIn();
    expect($('[data-testid=offer]')?.textContent).toMatch(/keeps 2 answers/);
    await click('button', 'Discard them', 20);
    expect($('#toast')?.textContent).toMatch(/deleted/);
    expect(kept().records).toEqual([]);
    expect(await app.deps.store.records(1)).toEqual([]);
  });

  it('keeps answers on a sample the server does not know, and says why', async () => {
    await start();
    await sitting();
    app.deps.store.findRepository = async () => null;
    await signIn();
    await click('button', 'Copy into my account', 30);
    expect($('#toast')?.textContent).toMatch(/digitalme is not in udebt's database yet/);
    expect($('[data-testid=offer]')).toBeNull();
    expect(kept().records).toHaveLength(2);
  });

  it('keeps the offer when the copy fails, so it can be tried again', async () => {
    await start();
    await sitting();
    app.deps.store.addRecords = async () => {
      throw new Error('database down');
    };
    await signIn();
    await click('button', 'Copy into my account', 30);
    expect(byText('p', /Not copied: database down/)).toBeTruthy();
    expect(kept().decided).toBe(0);
  });

  it('never sends a token-less call to the gateway functions', async () => {
    await start();
    const g = new GatewayClient({ getToken: async () => null, fetch: apiFetch });
    await expect(g.writeQuestions({ repo: 'https://github.com/pisanuw/digitalme', sha: fixture.analyses[0].sha, units: [] })).rejects.toThrow('Sign in first.');
    expect(seen).toEqual([]);
  });
});
