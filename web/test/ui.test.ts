// @vitest-environment jsdom
/**
 * The screens in jsdom over the preview wiring: fixture rows, the fake sign-in, and /api
 * answered by the real function handlers with their fakes, so a quiz here runs the same code
 * as the Playwright walkthrough.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fixture from '../fixtures/digitalme.json';
import { handleExcerpt, handleGrade, handleQuestions } from '../netlify/functions/lib/handlers';
import type { FnEnv } from '../netlify/functions/lib/env';
import type { Fixture } from '../src/data/store';
import { memoryStorage } from '../src/lib/llm';
import { setByokKey, setMode } from '../src/model/prefs';
import { App, parseRoute, repoHref, unitHref } from '../src/ui/app';
import { boot, previewDeps, previewDeleteFetch } from '../src/ui/boot';
import { renderMarkdown } from '../src/ui/markdown';
import { busy, day, extLink, fmt, toast } from '../src/ui/dom';
import { errorText, evidenceLine } from '../src/ui/screens/quiz';
import { countBadge, showCount } from '../src/ui/screens/review';
import { CapError, LazyByokClient } from '../src/model/client';
import { fakeGateway } from '../netlify/functions/lib/fake';
import { FAKE_TOKEN } from '../netlify/functions/lib/env';

const env: FnEnv = { supabaseUrl: null, anonKey: null, fakes: true, fakeRepos: {} };
const apiFetch: typeof fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const req = new Request(new URL(String(input), 'http://site'), init);
  const path = new URL(req.url).pathname;
  if (path === '/api/questions') return handleQuestions(req, { env });
  if (path === '/api/grade') return handleGrade(req, { env });
  if (path === '/api/excerpt') return handleExcerpt(req, { env });
  if (path.endsWith('/__udebt/fixture.json')) return Response.json(fixture);
  return new Response('{}', { status: 404 });
}) as typeof fetch;

const tick = async (n = 8) => {
  for (let i = 0; i < n; i += 1) await new Promise((r) => setTimeout(r, 0));
};
const $ = <T extends Element = HTMLElement>(sel: string) => document.querySelector(sel) as T | null;
const byText = (sel: string, text: string | RegExp) => [...document.querySelectorAll<HTMLElement>(sel)].find((e) => (typeof text === 'string' ? e.textContent?.trim() === text : text.test(e.textContent ?? '')));
const click = async (sel: string, text: string | RegExp) => {
  const el = byText(sel, text);
  if (!el) throw new Error(`no ${sel} with ${String(text)}: ${document.body.textContent?.slice(0, 400)}`);
  el.click();
  await tick();
};

let app: App;
let local: Storage;

async function start(signedIn = true): Promise<App> {
  const session = memoryStorage();
  if (signedIn) session.setItem('udebt.previewSignedIn', '1');
  local = memoryStorage();
  const root = document.createElement('div');
  root.id = 'app';
  document.body.replaceChildren(root);
  app = new App(root, previewDeps(fixture as unknown as Fixture, { doFetch: apiFetch, session, local }));
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

beforeEach(() => {
  window.location.hash = '';
});
afterEach(() => app?.stop());

describe('routes', () => {
  it('reads every address the app makes', () => {
    expect(parseRoute('')).toEqual({ name: 'repos' });
    expect(parseRoute('#/settings')).toEqual({ name: 'settings' });
    expect(parseRoute('#/add')).toEqual({ name: 'add' });
    expect(parseRoute('#/r/3')).toEqual({ name: 'repo', id: 3, tab: 'specs' });
    expect(parseRoute('#/r/3/quiz')).toEqual({ name: 'repo', id: 3, tab: 'quiz' });
    expect(parseRoute('#/r/3/debt')).toEqual({ name: 'repo', id: 3, tab: 'debt' });
    expect(parseRoute('#/r/3/review')).toEqual({ name: 'repo', id: 3, tab: 'review' });
    expect(repoHref(3, 'review')).toBe('#/r/3/review');
    expect(parseRoute(unitHref(3, 'a/b'))).toEqual({ name: 'unit', id: 3, unit: 'a/b' });
    expect(parseRoute('#/r/x')).toEqual({ name: 'repos' });
    expect(repoHref(2)).toBe('#/r/2');
  });
});

describe('signing in', () => {
  it('shows the panel, signs in with the fake Google button and lists the repositories', async () => {
    await start(false);
    expect(byText('h1', 'Understand the code an AI wrote')).toBeTruthy();
    expect($('.preview-banner')).toBeTruthy();
    await click('button', 'Continue with Google');
    await tick();
    expect(byText('h1', 'Repositories')).toBeTruthy();
    expect($('a.repo-card')?.textContent).toContain('100.0');
    expect(document.title).toBe('Repositories · udebt');
  });

  it('signs in with an emailed link too (the preview sends none)', async () => {
    await start(false);
    const input = $<HTMLInputElement>('input[type=email]') as HTMLInputElement;
    input.value = 'me@example.com';
    $('form')?.dispatchEvent(new Event('submit', { cancelable: true }));
    await tick();
    expect(byText('h1', 'Repositories')).toBeTruthy();
  });
});

describe('the specs', () => {
  it('shows the tour, the units and sub-units, and a unit page', async () => {
    await start();
    await go('#/r/1');
    expect(byText('h2', 'Units')).toBeTruthy();
    expect(byText('summary', /Architecture tour/)).toBeTruthy();
    expect(document.querySelectorAll('.unit-row.child')).toHaveLength(3);
    expect($('.tour .prose h3')).toBeTruthy();
    await go(unitHref(1, 'chat-flow'));
    expect(byText('h2', 'Chat request lifecycle (SSE)')).toBeTruthy();
    expect(byText('h3', 'Orientation')).toBeTruthy();
    expect(byText('h3', 'The commits that shaped it')).toBeTruthy();
    expect(document.querySelectorAll('.walkthrough .step').length).toBeGreaterThan(2);
    await tick(20);
    expect($('.walkthrough .excerpt .error')?.textContent).toMatch(/could not be read/);
    await go(unitHref(1, 'persona-retrieval/rag'));
    expect(byText('p', /no orientation was written for it/)).toBeTruthy();
    expect(byText('p', /No walkthrough was written/)).toBeTruthy();
    await go(unitHref(1, 'nope'));
    expect(byText('p', 'Unknown unit: nope')).toBeTruthy();
    await go('#/r/42');
    expect(byText('h1', 'Not found')).toBeTruthy();
  });

  it('shows code lines a worker output carries, without fetching them', async () => {
    const f = structuredClone(fixture) as unknown as Fixture;
    const w = f.analyses[0].specs.walkthroughs!['chat-flow'];
    w.steps = w.steps.map((s) => ({ ...s, lines: ['carried line'] }));
    const root = document.createElement('div');
    document.body.replaceChildren(root);
    const session = memoryStorage();
    session.setItem('udebt.previewSignedIn', '1');
    app = new App(root, previewDeps(f, { doFetch: apiFetch, session, local: memoryStorage() }));
    window.location.hash = unitHref(1, 'chat-flow');
    app.start();
    await tick();
    expect($('pre.code')?.textContent).toContain('carried line');
  });
});

describe('the quiz', () => {
  it('asks two level-1 questions with orientations, grades them in one call and lowers the debt', async () => {
    await start();
    setMode('gateway', local);
    await go('#/r/1/quiz');
    ($<HTMLSelectElement>('#quiz-n') as HTMLSelectElement).value = '2';
    await click('button', 'Start');
    await tick(20);
    expect(byText('p', /^\[1\/2\]/)?.textContent).toContain('level 1 of 5: purpose and placement');
    expect(byText('summary', 'Orientation: read this first')).toBeTruthy();
    await click('button', 'Next');
    expect(byText('p', /^\[1\/2\]/)).toBeTruthy();
    ($<HTMLTextAreaElement>('textarea') as HTMLTextAreaElement).value = 'It streams replies.';
    await click('button', 'Next');
    expect(byText('p', /^\[2\/2\]/)).toBeTruthy();
    await click('button', "I don't know");
    expect(byText('p', /A known unknown/)).toBeTruthy();
    expect(byText('.answer strong', 'The answer')).toBeTruthy();
    await click('button', 'Grade my answers');
    await tick(20);
    expect(byText('h3', 'Graded')).toBeTruthy();
    expect(byText('p', /1 correct, 1 unknown/)).toBeTruthy();
    expect($('[data-testid=debt-delta]')?.textContent).toMatch(/100\.0 → 96\.0/);
    expect($('[data-testid=debt-index]')?.textContent).toBe('96.0');
    await go('#/r/1/debt');
    expect($('[data-testid=debt-tile]')?.textContent).toBe('96.0');
    expect(document.querySelectorAll('table.debt tbody tr')).toHaveLength(15);
    await go(unitHref(1, 'chat-flow'));
    expect(byText('h3', 'Your answers on this unit')?.parentElement?.textContent).toContain('correct');
  });

  it('asks a missed question again in other words without a call, and finishes early', async () => {
    await start();
    setMode('gateway', local);
    await go('#/r/1/quiz');
    ($<HTMLSelectElement>('#quiz-n') as HTMLSelectElement).value = '1';
    await click('button', 'Start');
    await tick(20);
    const first = byText('p.q-text', /./)?.textContent;
    await click('button', "I don't know");
    await click('button', 'Grade my answers');
    await tick(20);
    await go('#/r/1/quiz');
    ($<HTMLSelectElement>('#quiz-n') as HTMLSelectElement).value = '2';
    await click('button', 'Start');
    await tick(20);
    const again = byText('p.q-text', /./)?.textContent;
    expect(again).not.toBe(first);
    ($<HTMLTextAreaElement>('textarea') as HTMLTextAreaElement).value = 'this is wrong on purpose';
    await click('button', 'Next');
    await click('button', 'Finish now');
    await tick(20);
    expect(byText('p', /1 wrong/)).toBeTruthy();
  });

  it('quizzes the unit asked for from its page, on the HintWise key by default, and says when model features are off', async () => {
    await start();
    await go('#/r/1/quiz');
    // Signed in and nothing chosen: the HintWise key (owner, 2026-11-15).
    expect(($<HTMLButtonElement>('button.primary') as HTMLButtonElement).disabled).toBe(false);
    expect(byText('p', /through the HintWise key/)).toBeTruthy();
    setMode('off', local);
    await go('#/r/1/specs');
    await go('#/r/1/quiz');
    expect(($<HTMLButtonElement>('button.primary') as HTMLButtonElement).disabled).toBe(true);
    expect(byText('p', /Model features are off/)).toBeTruthy();
    setMode('gateway', local);
    await go(unitHref(1, 'cost-control'));
    await click('button', 'Quiz this unit');
    await tick();
    expect(($<HTMLSelectElement>('#quiz-unit') as HTMLSelectElement).value).toBe('cost-control');
    setMode('off', local);
    await click('button', 'Start');
    await tick(10);
    expect($('#quiz-flow .error')?.textContent).toMatch(/Model features are off/);
  });

  it('words errors for the person', () => {
    expect(errorText(new CapError('user-day', ''))).toMatch(/daily HintWise allowance/);
    expect(errorText('plain')).toBe('plain');
  });

  it('quizzes a unit that names only directories from the top files under them, one listing per directory per session', async () => {
    const trees: string[] = [];
    const sent: { units: { id: string; refs: { path: string; start: number; end: number }[] }[] }[] = [];
    const withTree: typeof fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), 'http://site');
      if (url.pathname === '/api/tree') {
        trees.push(url.search);
        const files = ['frontend/src/AdminPage.jsx', 'frontend/src/App.jsx'].map((path) => ({ path, size: 100 }));
        return Response.json({ dirs: { frontend: { files, total: 2 } }, truncated: false });
      }
      if (url.pathname === '/api/questions') sent.push(JSON.parse(String(init?.body)));
      return apiFetch(input, init);
    }) as typeof fetch;
    const session = memoryStorage();
    session.setItem('udebt.previewSignedIn', '1');
    local = memoryStorage();
    const root = document.createElement('div');
    document.body.replaceChildren(root);
    app = new App(root, previewDeps(fixture as unknown as Fixture, { doFetch: withTree, session, local }));
    app.start();
    await tick();
    setMode('gateway', local);
    for (let sitting = 0; sitting < 2; sitting += 1) {
      await go('#/r/1/quiz');
      ($<HTMLSelectElement>('#quiz-n') as HTMLSelectElement).value = '1';
      ($<HTMLSelectElement>('#quiz-unit') as HTMLSelectElement).value = 'frontend-spa';
      await click('button', 'Start');
      await tick(20);
      expect(byText('p', /^\[1\/1\] React\/Vite frontend/)).toBeTruthy();
      ($<HTMLTextAreaElement>('textarea') as HTMLTextAreaElement).value = 'It renders the chat.';
      await click('button', 'Check Answer and Grade');
      await tick(20);
    }
    expect(trees).toHaveLength(1);
    expect(new URLSearchParams(trees[0]).getAll('path')).toEqual(['frontend']);
    expect(sent).toHaveLength(2);
    expect(sent[0].units[0]).toMatchObject({ id: 'frontend-spa', refs: [{ path: 'frontend/src/AdminPage.jsx', start: 1, end: 60 }, { path: 'frontend/src/App.jsx', start: 1, end: 60 }] });
    const line = $('[data-testid=evidence]');
    expect(line?.textContent).toBe('In the code: frontend');
    expect(line?.querySelector('a')?.getAttribute('href')).toBe('https://github.com/pisanuw/digitalme/blob/f93102b121f1da9055c148e8eccde275ccf18236/frontend');
  });

  it('links the evidence of a graded question at the sha, with its line range when it names one', () => {
    const line = evidenceLine('https://github.com/a/b', 'f'.repeat(40), ['src/a.py:3-9', '../x', 'src/b.py']);
    expect(line?.textContent).toBe('In the code: src/a.py:3-9, src/b.py');
    expect([...(line?.querySelectorAll('a') ?? [])].map((a) => a.getAttribute('href'))).toEqual([`https://github.com/a/b/blob/${'f'.repeat(40)}/src/a.py#L3-L9`, `https://github.com/a/b/blob/${'f'.repeat(40)}/src/b.py`]);
    expect(evidenceLine('https://github.com/a/b', 'f'.repeat(40), ['../x'])).toBeNull();
  });
});

describe('the review queue', () => {
  const at = (iso: string) => vi.setSystemTime(new Date(iso));
  const badgeText = () => {
    const b = $('[data-testid=review-count]');
    return b && !b.hidden ? b.textContent : null;
  };
  beforeEach(() => vi.useFakeTimers({ toFake: ['Date'], now: new Date('2026-11-15T10:00:00Z') }));
  afterEach(() => vi.useRealTimers());

  /** A one-question quiz answered "I don't know" (no call): an item due the next day. */
  async function miss(): Promise<string> {
    await go('#/r/1/quiz');
    ($<HTMLSelectElement>('#quiz-n') as HTMLSelectElement).value = '1';
    await click('button', 'Start');
    await tick(20);
    const asked = byText('p.q-text', /./)?.textContent ?? '';
    await click('button', "I don't know");
    await click('button', 'Grade my answers');
    await tick(20);
    return asked;
  }

  it('brings a missed question back the next day in other words, and moves it on a correct answer', async () => {
    await start();
    setMode('gateway', local);
    await go('#/r/1/review');
    expect(byText('h2', 'Nothing due now')).toBeTruthy();
    expect(byText('p', /Nothing to review yet/)).toBeTruthy();
    expect(badgeText()).toBeNull();
    expect(document.title).toBe('digitalme review · udebt');

    const asked = await miss();
    expect(byText('.result p', /back in review on/)).toBeTruthy();
    const [row] = await app.deps.store.records(1);
    expect(row.data.review).toEqual({ ease: 2.3, interval: 1, reps: 0, due: '2026-11-16T10:00:00+00:00' });
    expect(badgeText()).toBeNull();
    await go('#/r/1/review');
    expect(byText('p', /The next item is due on/)).toBeTruthy();
    expect(document.querySelectorAll('[data-testid=review-later] li')).toHaveLength(1);

    at('2026-11-16T11:00:00Z');
    await go('#/');
    expect($('[data-testid=repo-review-due]')?.textContent).toBe('1 to review');
    await go(unitHref(1, row.unit_id));
    expect(byText('p', /^In review: level 1, due since/)).toBeTruthy();
    await go('#/r/1/review');
    expect(badgeText()).toBe('1');
    expect(byText('h2', 'Due now: 1')).toBeTruthy();
    expect($('[data-testid=review-due] li .meta')?.textContent).toMatch(/^level 1 of 5: purpose and placement · due since /);
    const before = Number($('[data-testid=debt-index]')?.textContent);
    await click('button', 'Review what is due');
    expect(byText('p', /^\[1\/1\]/)).toBeTruthy();
    expect(byText('summary', 'Orientation: read this first')).toBeFalsy();
    const again = byText('p.q-text', /./)?.textContent;
    expect(again).toBeTruthy();
    expect(again).not.toBe(asked);
    ($<HTMLTextAreaElement>('textarea') as HTMLTextAreaElement).value = 'It streams the reply as it is written.';
    ($<HTMLInputElement>('input[value="3"]') as HTMLInputElement).checked = true;
    await click('button', 'Check Answer and Grade');
    await tick(20);
    expect(byText('p', /^1 correct \(one grading call\)/)).toBeTruthy();
    expect(byText('.result p', /back in review on/)).toBeTruthy();
    expect(badgeText()).toBeNull();
    expect(Number($('[data-testid=debt-index]')?.textContent)).toBeLessThan(before);
    const rows = await app.deps.store.records(1);
    expect(rows[1]).toMatchObject({ source: 'review', question: again, verdict: 'correct', confidence: 3 });
    expect(rows[1].data.review).toEqual({ ease: 2.4, interval: 1, reps: 1, due: '2026-11-17T11:00:00+00:00' });
    expect(rows[1].data.variants).toContain(asked);
    await click('a', 'Back to Review');
    expect(byText('h2', 'Nothing due now')).toBeTruthy();
    await go('#/r/1/debt');
    expect(Number($('[data-testid=debt-tile]')?.textContent)).toBeLessThan(before);
  });

  it('shows the answer at once on "I don\'t know" in a review, and resets the item on a wrong answer', async () => {
    await start();
    setMode('gateway', local);
    await miss();
    at('2026-11-16T10:00:00Z');
    await go('#/r/1/review');
    await click('button', 'Review what is due');
    await click('button', "I don't know");
    expect(byText('.answer strong', 'The answer')).toBeTruthy();
    await click('button', 'Grade my answers');
    await tick(20);
    expect(byText('p', /no call: every answer was blank/)).toBeTruthy();
    at('2026-11-17T10:00:00Z');
    await go('#/r/1/review');
    await click('button', 'Review what is due');
    ($<HTMLTextAreaElement>('textarea') as HTMLTextAreaElement).value = 'this is wrong on purpose';
    await click('button', 'Check Answer and Grade');
    await tick(20);
    expect(byText('p', /^1 wrong/)).toBeTruthy();
    expect(byText('.answer strong', 'The answer')).toBeTruthy();
    const rows = await app.deps.store.records(1);
    expect(rows.map((r) => [r.source, r.verdict, r.data.review?.reps, r.data.review?.ease])).toEqual([
      ['quiz', 'unknown', 0, 2.3],
      ['review', 'unknown', 0, 2.1],
      ['review', 'wrong', 0, 1.9],
    ]);
    expect(rows[2].data.review?.due).toBe('2026-11-18T10:00:00+00:00');
  });

  it("reviews with the person's own key, the page recording the sitting", async () => {
    await start();
    setMode('byok', local);
    setByokKey('sk-test', local);
    const calls: unknown[] = [];
    app.deps.quiz = () =>
      new LazyByokClient({
        getToken: async () => FAKE_TOKEN,
        fetch: apiFetch,
        askDeps: { storage: local, createAnthropic: () => ({ messages: { create: async (body) => (calls.push(body), fakeGateway(body as never) as never) } }) },
      });
    await miss();
    at('2026-11-16T10:00:00Z');
    await go('#/r/1/review');
    expect(byText('p', /with your own Anthropic key/)).toBeTruthy();
    await click('button', 'Review what is due');
    ($<HTMLTextAreaElement>('textarea') as HTMLTextAreaElement).value = 'partly: it streams replies.';
    await click('button', 'Check Answer and Grade');
    await tick(30);
    expect(calls).toHaveLength(2); // the quiz's questions, then the review's grading; none writes review questions
    expect(JSON.stringify(calls[1])).toContain('TASK: grade');
    expect(byText('p', /^1 partial/)).toBeTruthy();
    const rows = await app.deps.store.records(1);
    expect(rows[1]).toMatchObject({ source: 'review', verdict: 'partial', revealed: true });
    expect(rows[1].data.review).toMatchObject({ reps: 0, ease: 2.1 });
  });

  it('self-checks with no model, waits for one without a reference, leaves out units the analysis no longer has, and keeps the badge in step', async () => {
    await start();
    setMode('gateway', local);
    await miss();
    const store = app.deps.store as unknown as { rows: { unit_id: string; unit: string; data: { reference?: string } }[] };
    store.rows.push({ ...structuredClone(store.rows[0]), unit_id: 'gone', unit: 'gone' });
    app.forget();
    at('2026-11-16T10:00:00Z');
    setMode('off', local);
    await go('#/r/1/review');
    expect(byText('h2', 'Due now: 1')).toBeTruthy();
    expect(($<HTMLButtonElement>('button.primary') as HTMLButtonElement).disabled).toBe(false);
    expect(byText('p', /No model is in use: you compare each answer/)).toBeTruthy();
    for (const r of store.rows) delete r.data.reference;
    app.forget();
    await go('#/r/1/review');
    expect(($<HTMLButtonElement>('button.primary') as HTMLButtonElement).disabled).toBe(true);
    expect(byText('p', /1 item\(s\) have no reference answer to check against and wait for a model/)).toBeTruthy();
    const b = countBadge(0);
    document.body.append(b);
    expect(b.hidden).toBe(true);
    showCount(document, 2);
    expect(document.querySelectorAll('[data-testid=review-count]')[0].textContent).toBe('2');
    const other = document.implementation.createHTMLDocument('x');
    showCount(other, 1);
    expect(other.querySelector('[data-testid=review-count]')).toBeNull();
  });
});

describe('settings and adding a repository', () => {
  it('switches the mode, saves and forgets the own key, and deletes the account', async () => {
    await start();
    await go('#/settings');
    const byok = $<HTMLInputElement>('input[value=byok]') as HTMLInputElement;
    byok.checked = true;
    byok.dispatchEvent(new Event('change'));
    expect(local.getItem('hintwise.llmMode')).toBe('byok');
    expect(($('.byok') as HTMLElement).hidden).toBe(false);
    ($<HTMLInputElement>('.byok input') as HTMLInputElement).value = 'sk-abc';
    await click('button', 'Save the key');
    expect(local.getItem('hintwise.anthropicKey')).toBe('sk-abc');
    expect($('#toast')?.textContent).toMatch(/saved/);
    await click('button', 'Forget the key');
    expect(local.getItem('hintwise.anthropicKey')).toBeNull();
    expect(byText('h2', 'What udebt stores')).toBeTruthy();
    await click('summary', 'Delete my account');
    const confirm = $<HTMLInputElement>('.hw-account-delete input') as HTMLInputElement;
    confirm.value = 'DELETE';
    confirm.dispatchEvent(new Event('input'));
    $('.hw-account-delete form')?.dispatchEvent(new Event('submit', { cancelable: true }));
    await tick(20);
    expect(byText('h1', 'Understand the code an AI wrote')).toBeTruthy();
    expect(await (await previewDeleteFetch('x')).json()).toEqual({ status: 'deleted' });
  });

  it('adds a known repository at once and explains what a new one needs', async () => {
    await start();
    await go('#/add');
    const url = $<HTMLInputElement>('#add-url') as HTMLInputElement;
    const submit = async (v: string) => {
      url.value = v;
      $('form')?.dispatchEvent(new Event('submit', { cancelable: true }));
      await tick(10);
    };
    await submit('not a url');
    expect($('.error')?.textContent).toMatch(/public GitHub URL/);
    await submit('https://github.com/pisanuw/digitalme');
    expect(byText('p', /is in your list/)).toBeTruthy();
    await submit('https://github.com/octocat/hello-world');
    expect($('.warn')?.textContent).toMatch(/your own Anthropic key/);
    setByokKey('sk-k', local);
    await submit('https://github.com/octocat/hello-world');
    expect($('.warn')?.textContent).toMatch(/does not run in the preview/);
  });

  it('starts an analysis with the worker and follows the job', async () => {
    await start();
    setByokKey('sk-k', local);
    const started: unknown[] = [];
    app.deps.worker = { base: 'https://w', start: async (req, key, token) => (started.push([req, key, token]), { job: 1, status: 'running' }) };
    const store = app.deps.store as unknown as { jobList: { id: number; url: string; sha: null; status: string; message: string | null; created_at: string; finished_at: null }[] };
    store.jobList = [{ id: 1, url: 'https://github.com/octocat/hello-world', sha: null, status: 'running', message: null, created_at: '2026-11-15T00:00:00Z', finished_at: null }];
    await go('#/add');
    ($<HTMLInputElement>('#add-url') as HTMLInputElement).value = 'https://github.com/octocat/hello-world';
    ($<HTMLInputElement>('#add-rev') as HTMLInputElement).value = 'abc1234';
    $('form')?.dispatchEvent(new Event('submit', { cancelable: true }));
    await tick(20);
    expect(started).toHaveLength(1);
    expect(started[0]).toMatchObject([{ url: 'https://github.com/octocat/hello-world', rev: 'abc1234' }, 'sk-k', 'preview-token']);
    expect(byText('p', /Analysing octocat\/hello-world/)).toBeTruthy();
    expect(byText('h2', 'Your analyses')).toBeTruthy();
    await click('button', 'Back to the repositories');
    expect(byText('h1', 'Repositories')).toBeTruthy();
  });
});

describe('small pieces', () => {
  it('renders the tour Markdown as text, never as HTML', () => {
    const div = document.createElement('div');
    div.append(renderMarkdown('# Title\n\nIntro with **bold**, *it*, _it2_ and `code` <img src=x onerror=alert(1)>.\n\n1. One\n   more\n2. Two\n\n- a\n\n---\nEnd'));
    expect(div.querySelector('img')).toBeNull();
    expect(div.querySelector('h2')?.textContent).toBe('Title');
    expect(div.querySelector('strong')?.textContent).toBe('bold');
    expect(div.querySelectorAll('em')).toHaveLength(2);
    expect(div.querySelector('ol')?.children).toHaveLength(2);
    expect(div.querySelector('ol li')?.textContent).toBe('One more');
    expect(div.querySelector('ul li')?.textContent).toBe('a');
    expect(div.querySelector('hr')).toBeTruthy();
    expect(div.textContent).toContain('<img src=x onerror=alert(1)>');
  });

  it('formats and toasts', () => {
    expect(fmt(null)).toBe('-');
    expect(fmt(0.456, 1)).toBe('0.5');
    expect(day(null)).toBe('');
    expect(day('nonsense-date')).toBe('nonsense-d');
    expect(extLink(null, 'x').tagName).toBe('SPAN');
    toast('hello');
    toast('again');
    expect(document.getElementById('toast')?.textContent).toBe('again');
    const b = document.createElement('button');
    b.textContent = 'Go';
    const done = busy(b, 'Going');
    expect(b.disabled).toBe(true);
    done();
    expect(b.textContent).toBe('Go');
  });

  it('boots the preview from the fixture the dev server serves', async () => {
    const root = document.createElement('div');
    document.body.replaceChildren(root);
    const a = await boot(root, apiFetch);
    await tick();
    expect(a.deps.preview).toBe(true);
    expect(root.textContent).toContain('udebt');
    a.stop();
    const empty = await boot(root, (async () => new Response('', { status: 404 })) as typeof fetch);
    expect((await empty.deps.store.repositories()).repos).toEqual([]);
    empty.stop();
  });

  it("drops a sign-in panel's subscription when its screen is replaced", async () => {
    await start();
    const listeners = () => (app.deps.auth as unknown as { listeners: unknown[] }).listeners.length;
    const base = listeners();
    await go('#/settings');
    expect(listeners()).toBe(base + 1);
    await go('#/');
    await go('#/settings');
    expect(listeners()).toBe(base + 1);
    await go('#/');
    expect(listeners()).toBe(base);
    const slow = app.render();
    await go('#/settings');
    await slow;
    expect(listeners()).toBe(base + 1);
  });

  it('shows an error screen when a screen fails', async () => {
    await start();
    app.deps.store.repositories = async () => {
      throw new Error('database down');
    };
    app.forget();
    await go('#/');
    expect(byText('h1', 'Something went wrong')).toBeTruthy();
    expect(byText('p', 'database down')).toBeTruthy();
  });
});
