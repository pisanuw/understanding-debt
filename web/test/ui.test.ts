// @vitest-environment jsdom
/**
 * The screens in jsdom over the preview wiring: fixture rows, the fake sign-in, and /api
 * answered by the real function handlers with their fakes, so a quiz here runs the same code
 * as the Playwright walkthrough.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
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
import { errorText } from '../src/ui/screens/quiz';
import { CapError } from '../src/model/client';

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

  it('quizzes the unit asked for from its page, and says when model features are off', async () => {
    await start();
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
