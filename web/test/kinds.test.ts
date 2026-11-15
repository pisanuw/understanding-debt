// @vitest-environment jsdom
/**
 * Everybody gets advance quizzes (owner, 2026-11-15): a person with a model in use (the HintWise
 * key or their own) on a repository with questions written in advance chooses between those,
 * self-checked with no model call, and the questions the model writes and grades; on the HintWise
 * key the model's quiz first (owner, later on 2026-11-15), with the own key the advance quiz first
 * (DEFAULT_QUIZ_KIND), the choice kept per browser. In jsdom over the preview wiring:
 * the fixture with its advance quizzes, the fake sign-in, and the functions' real handlers behind
 * /api, every request watched. Self-checked and model-graded answers on one unit are one history
 * (the ladder, the debt, the review queue), and Review checks each item the way its latest miss
 * was checked.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fixture from '../fixtures/digitalme.json';
import quizzes from '../fixtures/digitalme.quizzes.json';
import { previewOpen } from '../dev/api-plugin';
import { handleAllowance, handleExcerpt, handleGrade, handleQuestions, handleTree } from '../netlify/functions/lib/handlers';
import type { FnEnv } from '../netlify/functions/lib/env';
import { nextLevel } from '../src/core/ladder';
import { SELF_CHECKED, type EvidenceRow } from '../src/core/quiz';
import { repoScores } from '../src/core/repo-scores';
import { items, lastMiss, type ReviewItem } from '../src/core/review';
import type { Analysis } from '../src/core/specs';
import type { Fixture } from '../src/data/store';
import { LOCAL_PREFIX } from '../src/data/local';
import { memoryStorage } from '../src/lib/llm';
import { CapError, type QuizClient } from '../src/model/client';
import { DEFAULT_QUIZ_KIND, QUIZ_KIND_KEY, getQuizKind, setByokKey, setMode, setQuizKind, type LlmMode } from '../src/model/prefs';
import { App } from '../src/ui/app';
import { previewDeps } from '../src/ui/boot';
import { askFlow } from '../src/ui/screens/quiz';
import { checkedBy } from '../src/ui/screens/review';

const env: FnEnv = { supabaseUrl: null, anonKey: null, fakes: true, fakeRepos: {} };
const open = previewOpen(fixture as unknown as Fixture);
const seen: string[] = [];
/** With the fakes: the HintWise allowance used up (the check and the calls alike). */
let capped = false;
/** The gateway refuses the session (ended elsewhere): a 401 on the allowance check. */
let dead = false;
const apiFetch: typeof fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const req = new Request(new URL(String(input), 'http://site'), init);
  const path = new URL(req.url).pathname;
  seen.push(path);
  if (path === '/api/questions') return handleQuestions(req, { env: { ...env, fakeCap: capped, anonModel: true }, open });
  if (path === '/api/grade') return handleGrade(req, { env: { ...env, fakeCap: capped, anonModel: true }, open });
  if (path === '/api/allowance' && dead) return Response.json({ error: 'Your sign-in has expired. Sign in again.', detail: 'gateway 401: The session token is invalid or expired; sign in again.' }, { status: 401 });
  if (path === '/api/allowance') return handleAllowance(req, { env: { ...env, fakeCap: capped, anonModel: true } });
  if (path === '/api/excerpt') return handleExcerpt(req, { env, open });
  if (path === '/api/tree') return handleTree(req, { env, open });
  return new Response('{}', { status: 404 });
}) as typeof fetch;
const calls = () => seen.filter((p) => p === '/api/questions' || p === '/api/grade');
/** Requests other than the code reads and the allowance check, none of which calls a model. */
const modelish = () => seen.filter((p) => p !== '/api/excerpt' && p !== '/api/tree' && p !== '/api/allowance');

const tick = async (n = 10) => {
  for (let i = 0; i < n; i += 1) await new Promise((r) => setTimeout(r, 0));
};
const $ = <T extends Element = HTMLElement>(sel: string) => document.querySelector(sel) as T | null;
const byText = (sel: string, text: string | RegExp) => [...document.querySelectorAll<HTMLElement>(sel)].find((e) => (typeof text === 'string' ? e.textContent?.trim() === text : text.test(e.textContent ?? '')));
const click = async (sel: string, text: string | RegExp, n = 20) => {
  const el = byText(sel, text);
  if (!el) throw new Error(`no ${sel} with ${String(text)}: ${document.body.textContent?.slice(0, 600)}`);
  el.click();
  await tick(n);
};
const answer = (text: string) => (($<HTMLTextAreaElement>('textarea') as HTMLTextAreaElement).value = text);
const choose = (sel: string, value: string) => (($<HTMLSelectElement>(sel) as HTMLSelectElement).value = value);
const kind = () => $<HTMLFieldSetElement>('[data-testid=quiz-kind]');
const checkedKind = () => $<HTMLInputElement>('[data-testid=quiz-kind] input:checked')?.value ?? null;
const pick = async (k: 'advance' | 'model') => {
  ($<HTMLInputElement>(`[data-testid=quiz-kind] input[value=${k}]`) as HTMLInputElement).click();
  await tick();
};
const at = (iso: string) => vi.setSystemTime(new Date(iso));

let app: App;
let local: Storage;

async function start(opts: { signedIn?: boolean; mode?: LlmMode; quizzes?: unknown[]; local?: Storage; anonModel?: boolean } = {}): Promise<App> {
  const session = memoryStorage();
  if (opts.signedIn ?? true) session.setItem('udebt.previewSignedIn', '1');
  local = opts.local ?? memoryStorage();
  if (opts.mode) setMode(opts.mode, local);
  const root = document.createElement('div');
  root.id = 'app';
  document.body.replaceChildren(root);
  app = new App(root, previewDeps(fixture as unknown as Fixture, { doFetch: apiFetch, session, local, quizzes: opts.quizzes ?? [quizzes], anonModel: opts.anonModel }));
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

/** One self-checked advance question on `unit`, marked `mark`. */
async function advanceSitting(unit: string, mark: 'Correct' | 'Wrong', text = 'It streams the reply and keeps the exchange.'): Promise<void> {
  choose('#quiz-n', '1');
  choose('#quiz-unit', unit);
  await click('button', 'Start');
  expect(byText('p', /^\[1\/1\]/)?.textContent).toMatch(/· self-checked$/);
  answer(text);
  await click('button', 'Check Answer');
  await click('button', mark);
}

/** One question the model writes and grades on `unit`, through the fake gateway. */
async function modelSitting(unit: string, text: string): Promise<void> {
  choose('#quiz-n', '1');
  choose('#quiz-unit', unit);
  await click('button', 'Start', 30);
  expect(byText('p', /^\[1\/1\]/)?.textContent).not.toMatch(/self-checked/);
  answer(text);
  await click('button', 'Check Answer and Grade', 30);
}

/** The debt table's row of a unit. */
const row = (name: string) => [...document.querySelectorAll<HTMLTableRowElement>('table.debt tbody tr')].find((r) => r.querySelector('td a')?.textContent === name) as HTMLTableRowElement;

beforeEach(() => {
  seen.length = 0;
  capped = false;
  dead = false;
  window.location.hash = '';
  vi.useFakeTimers({ toFake: ['Date'], now: new Date('2026-11-15T10:00:00Z') });
});
afterEach(() => {
  app?.stop();
  vi.useRealTimers();
});

describe('the quiz-kind choice', () => {
  it('offers a signed-in person with the HintWise key both kinds, the model\'s quiz first, and keeps the choice per browser', async () => {
    expect(DEFAULT_QUIZ_KIND).toBe('advance'); // with the own key
    await start({ mode: 'gateway' });
    await go('#/r/1/quiz');
    expect(kind()).toBeTruthy();
    expect(checkedKind()).toBe('model');
    expect(byText('p', /^Questions written and graded by the model/)).toBeTruthy();
    expect($('[data-testid=cap-notice]')).toBeNull();
    expect(local.getItem(QUIZ_KIND_KEY)).toBeNull(); // nothing stored until a choice is made
    await pick('advance');
    expect(local.getItem(QUIZ_KIND_KEY)).toBe('advance');
    expect([...(kind() as HTMLElement).querySelectorAll('label strong')].map((s) => s.textContent)).toEqual(['Written in advance, self-checked (no model call)', 'Written and graded by the model (one call each way)']);
    expect(byText('label', /graded by the model, through the HintWise key: each sitting counts against your daily allowance/)).toBeTruthy();
    expect(byText('p', /^Questions written in advance for this repository/)?.textContent).toMatch(/no model call/);
    expect(byText('p', /^Recorded as/)?.textContent).toBe('Recorded as self-checked in your account, with no model call.');

    await pick('model');
    expect(local.getItem(QUIZ_KIND_KEY)).toBe('model');
    expect(checkedKind()).toBe('model');
    expect(document.activeElement).toBe($('[data-testid=quiz-kind] input[value=model]'));
    expect(byText('p', /^Questions written and graded by the model/)).toBeTruthy();
    expect(byText('p', /through the HintWise key \(they count against your daily allowance\)/)).toBeTruthy();
    expect(byText('p', /^Recorded as/)).toBeUndefined();

    // Kept per browser: a new page in the same browser opens on the model's quiz.
    app.stop();
    await start({ local });
    await go('#/r/1/quiz');
    expect(checkedKind()).toBe('model');
    await pick('advance');
    expect(local.getItem(QUIZ_KIND_KEY)).toBe('advance');
    expect(calls()).toEqual([]);
  });

  it('names the own key as the cost, for a visitor who is not signed in too, and records their advance sitting in this browser', async () => {
    await start({ signedIn: false });
    setMode('byok', local);
    setByokKey('sk-test', local);
    await go('#/r/1/quiz');
    expect(checkedKind()).toBe('advance');
    expect(byText('label', /graded by the model, with your own Anthropic key, straight from this browser/)).toBeTruthy();
    expect(byText('p', /^Recorded as/)?.textContent).toBe('Recorded as self-checked in this browser, with no model call.');
    await advanceSitting('chat-flow', 'Correct');
    expect(byText('p', /^1 correct, self-checked \(no model call\)\./)).toBeTruthy();
    const kept = JSON.parse(local.getItem(`${LOCAL_PREFIX}digitalme`) ?? '{}') as { records: EvidenceRow[] };
    expect(kept.records.map((r) => [r.type, r.source, r.unit_id])).toEqual([[SELF_CHECKED, 'advance', 'chat-flow']]);
    await pick('model');
    expect(byText('p', /with your own Anthropic key, straight from this browser: two calls per sitting/)).toBeTruthy();
    expect(modelish()).toEqual([]);
  });

  it('says plainly when the HintWise allowance is used up, and uses the questions written in advance until it resets', async () => {
    capped = true;
    await start(); // signed in, nothing chosen: the HintWise key
    await go('#/r/1/quiz');
    const notice = $('[data-testid=cap-notice]');
    expect(notice?.textContent).toMatch(/^Your daily allowance in this app is used up until .+\. Until then the quiz uses the questions written in advance \(no model call\), or you can use your own Anthropic key in Settings\.$/);
    expect(notice?.getAttribute('role')).toBe('status');
    expect(checkedKind()).toBe('advance');
    expect($<HTMLInputElement>('[data-testid=quiz-kind] input[value=model]')?.disabled).toBe(true);
    expect(byText('label', /Not available until your HintWise allowance resets/)).toBeTruthy();
    expect(local.getItem(QUIZ_KIND_KEY)).toBeNull(); // the person's choice is untouched
    await advanceSitting('chat-flow', 'Correct');
    expect(byText('p', /^1 correct, self-checked \(no model call\)\./)).toBeTruthy();
    expect(calls()).toEqual([]);
    // Reset: the model's quiz again, no notice.
    capped = false;
    await go('#/r/1/specs');
    await go('#/r/1/quiz');
    expect($('[data-testid=cap-notice]')).toBeNull();
    expect(checkedKind()).toBe('model');
    // Nothing written in advance: the notice, and no Start on the HintWise key.
    app.stop();
    capped = true;
    await start({ quizzes: [] });
    await go('#/r/1/quiz');
    expect($('[data-testid=cap-notice]')?.textContent).toMatch(/Until then you can use your own Anthropic key in Settings; no allowance applies to it\.$/);
    expect(($<HTMLButtonElement>('button.primary') as HTMLButtonElement).disabled).toBe(true);
    // Not signed in, or on their own key: no allowance check at all.
    app.stop();
    seen.length = 0;
    await start({ mode: 'byok' });
    setByokKey('sk-test', local);
    await go('#/r/1/quiz');
    expect($('[data-testid=cap-notice]')).toBeNull();
    app.stop();
    await start({ signedIn: false });
    await go('#/r/1/quiz');
    expect($('[data-testid=cap-notice]')).toBeNull();
  });

  it('says before a sitting that the sign-in has ended, keeps the questions written in advance, and offers to sign in again', async () => {
    dead = true;
    await start();
    await go('#/r/1/quiz');
    const notice = $('[data-testid=session-notice]');
    expect(notice?.textContent).toMatch(/^Your sign-in has ended, perhaps by signing out in another HintWise app or tab, so the HintWise key cannot be used\. The questions written in advance still work\. Sign in again to use it\./);
    expect($('[data-testid=cap-notice]')).toBeNull();
    expect(checkedKind()).toBe('advance');
    expect($<HTMLInputElement>('[data-testid=quiz-kind] input[value=model]')?.disabled).toBe(true);
    expect(byText('label', /Not available until you sign in again/)).toBeTruthy();
    await click('button', 'Sign out and sign in again');
    expect(window.location.hash).toBe('#/signin');
    expect(app.anonymous || !app.user).toBe(true);
  });

  it('keeps a sitting in progress when the session\'s token is refreshed', async () => {
    await start();
    await go('#/r/1/quiz');
    await click('button', 'Start', 30);
    const asked = byText('p', /^\[1\//)?.textContent;
    expect(asked).toBeTruthy();
    const auth = app.deps.auth as unknown as { listeners: ((e: string, s: unknown) => void)[]; auth: { getSession(): Promise<{ data: { session: unknown } }> } };
    const { data } = await auth.auth.getSession();
    for (const l of [...auth.listeners]) l('TOKEN_REFRESHED', data.session);
    await tick(20);
    expect(byText('p', /^\[1\//)?.textContent).toBe(asked);
  });

  it('gives a visitor who is not signed in the HintWise key from the shared allowance when it is on, kept in this browser', async () => {
    await start({ signedIn: false, anonModel: true });
    await go('#/r/1/quiz');
    expect(checkedKind()).toBe('model');
    expect(byText('label', /each sitting counts against one daily allowance shared by everyone who is not signed in/)).toBeTruthy();
    await modelSitting('cost-control', 'It keeps the cost of each visitor within the daily budget.');
    expect(byText('p', /^1 correct \(one grading call\)\./)).toBeTruthy();
    const kept = JSON.parse(local.getItem(`${LOCAL_PREFIX}digitalme`) ?? '{}') as { records: EvidenceRow[] };
    expect(kept.records.map((r) => [r.source, r.unit_id, r.verdict])).toEqual([['quiz', 'cost-control', 'correct']]);
    // Settings: the HintWise key on offer, and said to be shared.
    await go('#/settings');
    const radio = $<HTMLInputElement>('input[name=llm-mode][value=gateway]');
    expect([radio?.disabled, radio?.checked]).toEqual([false, true]);
    expect(byText('label', /from one daily allowance shared by everyone who is not signed in/)).toBeTruthy();
    // The shared allowance used up: said as the shared one.
    capped = true;
    await go('#/r/1/quiz');
    expect($('[data-testid=cap-notice]')?.textContent).toMatch(/^The allowance shared by everyone who is not signed in is used up until .+\. Until then the quiz uses the questions written in advance \(no model call\), or sign in for an allowance of your own, or use your own Anthropic key in Settings\.$/);
    expect(checkedKind()).toBe('advance');
    // With the switch off, as before: no HintWise key without signing in.
    app.stop();
    capped = false;
    await start({ signedIn: false });
    await go('#/r/1/quiz');
    expect(kind()).toBeNull();
    expect(byText('p', /^Questions written in advance/)).toBeTruthy();
  });

  it('says so when the allowance runs out during a sitting', async () => {
    await start();
    await go('#/r/1/quiz');
    expect(checkedKind()).toBe('model');
    capped = true; // used up between the page and the call
    await click('button', 'Start', 30);
    expect($('#quiz-flow .error')?.textContent).toMatch(/^Your daily allowance in this app is used up until .+\. You can use your own Anthropic key in Settings/);
  });

  it('shows no choice where only one kind exists', async () => {
    // Signed in with model features turned off: the questions written in advance only.
    await start({ mode: 'off' });
    await go('#/r/1/quiz');
    expect(kind()).toBeNull();
    expect(byText('p', /^Recorded as/)?.textContent).toMatch(/choose the HintWise key or your own key in Settings\.$/);
    // Not signed in without a key of their own: the same.
    app.stop();
    await start({ signedIn: false });
    await go('#/r/1/quiz');
    expect(kind()).toBeNull();
    expect(byText('p', /^Questions written in advance/)).toBeTruthy();
    // A model in use but nothing written in advance for the repository: the model's quiz only.
    app.stop();
    await start({ mode: 'gateway', quizzes: [] });
    setQuizKind('advance', local);
    await go('#/r/1/quiz');
    expect(kind()).toBeNull();
    expect(byText('p', /^Questions written and graded by the model/)).toBeTruthy();
    // And an advance file with no question in it counts as none.
    app.stop();
    await start({ mode: 'gateway', quizzes: [{ ...quizzes, questions: [] }] });
    await go('#/r/1/quiz');
    expect(kind()).toBeNull();
  });

  it('keeps the choice in memory when storage is blocked, and reads anything else as the default', () => {
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
    expect(getQuizKind(blocked)).toBe(DEFAULT_QUIZ_KIND);
    setQuizKind('model', blocked);
    expect(getQuizKind(blocked)).toBe('model');
    setQuizKind('advance', blocked);
    expect(getQuizKind(blocked)).toBe('advance');
    const s = memoryStorage();
    s.setItem(QUIZ_KIND_KEY, 'something else');
    expect(getQuizKind(s)).toBe(DEFAULT_QUIZ_KIND);
  });

  it('locks the choice while a sitting runs, keeps the unit across kinds, and points past level 3 to the model', async () => {
    await start({ mode: 'gateway' });
    await go('#/r/1/quiz');
    app.quizUnit = 'cost-control'; // as "Quiz this unit" sets it
    await go('#/r/1/quiz');
    expect(($<HTMLSelectElement>('#quiz-unit') as HTMLSelectElement).value).toBe('cost-control');
    await pick('model');
    expect(($<HTMLSelectElement>('#quiz-unit') as HTMLSelectElement).value).toBe('cost-control');
    await pick('advance');
    choose('#quiz-n', '1');
    await click('button', 'Start');
    expect(kind()?.disabled).toBe(true);
    expect(kind()?.title).toMatch(/Finish this sitting/);
    await click('button', 'Finish now'); // hidden before an answer, but its handler records nothing
    expect(byText('div', 'Nothing answered, nothing recorded.')).toBeTruthy();
    expect(kind()?.disabled).toBe(false);
    // A model sitting that cannot start releases the lock.
    await pick('model');
    const quiz = app.deps.quiz;
    app.deps.quiz = () => null;
    await click('button', 'Start');
    expect($('#quiz-flow .error')?.textContent).toMatch(/Model features are off/);
    expect(kind()?.disabled).toBe(false);
    app.deps.quiz = quiz;
    // Levels 1-3 passed on a unit: nothing written in advance past them; the model's quiz is one choice away.
    const sha = fixture.analyses[0].sha;
    await app.deps.store.addRecords([1, 2, 3].map((lv) => ({ repo_id: 1, sha, unit_id: 'cost-control', unit: 'cost-control', ts: `2026-11-15T0${lv}:00:00.000Z`, level: lv, verdict: 'correct', confidence: 2, type: 'quiz', source: 'quiz', question: `q${lv}`, revealed: false, forced: false, data: {} })));
    app.forget();
    await pick('advance');
    app.quizUnit = 'cost-control';
    await go('#/r/1/quiz');
    await click('button', 'Start');
    expect(byText('p', /Levels 4 and 5 need code reading/)?.textContent).toMatch(/Choose “Written and graded by the model” above/);
    expect(kind()?.disabled).toBe(false);
  });
});

describe('one history for both kinds', () => {
  it('records a signed-in self-checked sitting on the server with its label, then a model-graded one, both in the debt table', async () => {
    await start({ mode: 'gateway' });
    await go('#/r/1/quiz');
    await pick('advance');
    await advanceSitting('chat-flow', 'Correct');
    expect(byText('h3', 'Recorded')).toBeTruthy();
    expect(byText('p', /^1 correct, self-checked \(no model call\)\./)).toBeTruthy();
    expect(document.querySelectorAll('.result .badge.self-checked')).toHaveLength(1);
    expect($<HTMLElement>('[data-testid=self-checked-index]')?.hidden).toBe(false);
    expect(calls()).toEqual([]);
    expect(local.getItem(`${LOCAL_PREFIX}digitalme`)).toBeNull(); // the server's store, not the browser's
    const first = await app.deps.store.records(1);
    expect(first.map((r) => [r.type, r.source, r.unit_id, r.verdict])).toEqual([[SELF_CHECKED, 'advance', 'chat-flow', 'correct']]);

    await pick('model');
    await modelSitting('cost-control', 'It caps what each visitor can spend in a day.');
    expect(byText('p', /^1 correct \(one grading call\)\./)).toBeTruthy();
    expect(document.querySelectorAll('.result .badge.self-checked')).toHaveLength(0);
    expect(calls()).toEqual(['/api/questions', '/api/grade']);
    const rows = await app.deps.store.records(1);
    expect(rows.map((r) => [r.type, r.source, r.unit_id, r.verdict])).toEqual([
      [SELF_CHECKED, 'advance', 'chat-flow', 'correct'],
      ['quiz', 'quiz', 'cost-control', 'correct'],
    ]);

    await go('#/r/1/debt');
    expect(row('Chat request lifecycle (SSE)').querySelector('.badge.measured')).toBeTruthy();
    expect(row('Chat request lifecycle (SSE)').querySelector('.badge.self-checked')).toBeTruthy();
    expect(row('Cost controls, rate limits and usage metering').querySelector('.badge.measured')).toBeTruthy();
    expect(row('Cost controls, rate limits and usage metering').querySelector('.badge.self-checked')).toBeNull();
    expect($('[data-testid=debt-self-note]')).toBeTruthy();
    // Both moved each unit to level 2, whichever kind graded it.
    expect(row('Chat request lifecycle (SSE)').cells[4].textContent).toBe('2');
    expect(row('Cost controls, rate limits and usage metering').cells[4].textContent).toBe('2');
    await go('#/r/1/u/chat-flow');
    expect(byText('h3', 'Your answers on this unit')?.parentElement?.querySelector('.badge.self-checked')).toBeTruthy();
    await go('#/r/1/u/cost-control');
    expect(byText('h3', 'Your answers on this unit')?.parentElement?.querySelector('.badge.self-checked')).toBeNull();
  });

  it('folds a model-graded miss and a self-checked miss on one unit and level into one review item, asked as the latest', () => {
    const base = { repo_id: 1, sha: null, unit_id: 'u', unit: 'u', level: 1, confidence: 2, revealed: true, forced: false };
    const model: EvidenceRow = { ...base, ts: '2026-11-15T10:00:00.000Z', verdict: 'wrong', type: 'quiz', source: 'quiz', question: 'Model asked?', data: { variants: ['Model other?'], reference: 'The model reference.' } };
    const self: EvidenceRow = { ...base, ts: '2026-11-15T11:00:00.000Z', verdict: 'unknown', type: SELF_CHECKED, source: 'advance', question: 'Advance asked?', data: { variants: ['Advance other?'], reference: 'The advance reference.' } };
    const [one, ...more] = items([model, self]);
    expect(more).toEqual([]);
    expect(one).toMatchObject({ unit: 'u', level: 1, state: { reps: 0, interval: 1, ease: 2.1, due: '2026-11-16T11:00:00+00:00' } });
    expect(one.question).toMatchObject({ question: 'Advance other?', reference_answer: 'The advance reference.' });
    expect(lastMiss([model, self], 'u', 1)).toBe(self);
    const [back] = items([{ ...self, ts: model.ts }, { ...model, ts: self.ts }]);
    expect(back.question).toMatchObject({ question: 'Model other?', reference_answer: 'The model reference.' });
    expect(back.state.ease).toBe(2.1);
    // The ladder reads both too: a model-graded pass at level 1 and a self-checked one at level 2.
    const pass1 = { ...model, verdict: 'correct' as const };
    const pass2 = { ...self, verdict: 'correct' as const, level: 2 };
    expect(nextLevel([pass1, pass2])).toBe(3);
    const analysis = { id: 1, repo_id: 1, sha: 'x', user_id: null, created_at: '', manifest: {}, specs: { units: [{ id: 'u', name: 'U', scope: 'architectural' }] } } as unknown as Analysis;
    const sc = repoScores(analysis, [pass1, pass2]);
    expect(sc.byId.get('u')?.depth).toBe(2);
    expect(sc.selfChecked.has('u')).toBe(true);
  });
});

describe('review with both kinds', () => {
  const item = (unit: string, question: string, reference: string): ReviewItem => ({ unit, level: 1, state: { ease: 2.3, interval: 1, reps: 0, due: '2026-11-16T10:00:00+00:00' }, question: { unit_id: unit, level: 1, question, variants: [], reference_answer: reference, evidence: [] } });
  const miss = (unit: string, type: string): EvidenceRow => ({ repo_id: 1, sha: null, unit_id: unit, unit, ts: '2026-11-15T10:00:00.000Z', level: 1, verdict: 'wrong', confidence: 2, type, source: type === SELF_CHECKED ? 'advance' : 'quiz', question: 'q?', revealed: true, forced: false, data: {} });

  it('checks each item the way its latest miss was, and with no model self-checks what it can', () => {
    const records = [miss('a', SELF_CHECKED), miss('m', 'quiz'), miss('n', 'quiz')];
    const due = [item('a', 'A?', 'The a reference.'), item('m', 'M?', 'The m reference.'), item('n', 'N?', '(graded against the code)')];
    const withModel = checkedBy(due, records, true);
    expect([withModel.self, withModel.model, withModel.waiting].map((l) => l.map((it) => it.unit))).toEqual([['a'], ['m', 'n'], []]);
    const without = checkedBy(due, records, false);
    expect([without.self, without.model, without.waiting].map((l) => l.map((it) => it.unit))).toEqual([['a', 'm'], [], ['n']]);
    expect(checkedBy([item('z', 'Z?', 'ref')], records, true).model).toHaveLength(1); // no miss found: the model
  });

  it('self-checks a signed-in person\'s advance miss and has the model grade their model-graded miss, one button each', async () => {
    await start({ mode: 'gateway' });
    await go('#/r/1/quiz');
    await pick('advance');
    await advanceSitting('chat-flow', 'Wrong', 'It renders the slides.');
    await pick('model');
    await modelSitting('cost-control', 'It is wrong on purpose: it renders the slides.');
    expect(byText('p', /^1 wrong \(one grading call\)\./)).toBeTruthy();
    seen.length = 0;

    at('2026-11-16T11:00:00Z');
    app.forget();
    await go('#/r/1/review');
    expect(byText('h2', 'Due now: 2')).toBeTruthy();
    const lines = [...document.querySelectorAll('[data-testid=review-due] li')];
    expect(lines.map((li) => [li.querySelector('a')?.textContent, !!li.querySelector('.badge.self-checked')])).toEqual([
      ['Chat request lifecycle (SSE)', true],
      ['Cost controls, rate limits and usage metering', false],
    ]);
    expect([...document.querySelectorAll('#review-n ~ button')].map((b) => b.textContent)).toEqual(['Review the self-checked ones', 'Review the model-graded ones']);
    expect($('[data-testid=review-self-note]')?.textContent).toMatch(/^1 item\(s\) marked self-checked: you checked these yourself/);
    expect(byText('p', /^The other 1 were graded by the model when you missed them, and are again: one grading call per sitting through the HintWise key/)).toBeTruthy();

    await click('button', 'Review the self-checked ones');
    expect(byText('p', /^\[1\/1\] Chat request lifecycle \(SSE\)/)?.textContent).toMatch(/self-checked$/);
    answer('It streams the reply as it is written.');
    await click('button', 'Check Answer');
    await click('button', 'Correct');
    expect(byText('p', /^1 correct, self-checked \(no model call\)\./)).toBeTruthy();
    expect(calls()).toEqual([]);

    await click('a', 'Back to Review');
    expect(byText('h2', 'Due now: 1')).toBeTruthy();
    expect([...document.querySelectorAll('#review-n ~ button')].map((b) => b.textContent)).toEqual(['Review what is due']);
    expect($('[data-testid=review-self-note]')).toBeNull();
    expect(byText('p', /^One grading call per sitting through the HintWise key/)).toBeTruthy();
    await click('button', 'Review what is due');
    answer('It caps what each visitor can spend in a day.');
    await click('button', 'Check Answer and Grade', 30);
    expect(byText('p', /^1 correct \(one grading call\)\./)).toBeTruthy();
    expect(calls()).toEqual(['/api/grade']);
    const rows = await app.deps.store.records(1);
    expect(rows.map((r) => [r.unit_id, r.type, r.source, r.verdict])).toEqual([
      ['chat-flow', SELF_CHECKED, 'advance', 'wrong'],
      ['cost-control', 'quiz', 'quiz', 'wrong'],
      ['chat-flow', SELF_CHECKED, 'review', 'correct'],
      ['cost-control', 'quiz', 'review', 'correct'],
    ]);
  });

  it('self-checks only what was self-checked when every due item was, with the own key', async () => {
    await start({ mode: 'byok' });
    setByokKey('sk-test', local);
    await go('#/r/1/quiz');
    await advanceSitting('chat-flow', 'Wrong', 'It renders the slides.');
    at('2026-11-16T11:00:00Z');
    app.forget();
    await go('#/r/1/review');
    expect([...document.querySelectorAll('#review-n ~ button')].map((b) => b.textContent)).toEqual(['Review what is due']);
    expect($('[data-testid=review-self-note]')?.textContent).toMatch(/^Marked self-checked: you checked these yourself/);
    await click('button', 'Review what is due');
    await click('button', "I don't know");
    await click('button', 'Record my answers');
    expect(byText('p', /^1 unknown, self-checked/)).toBeTruthy();
    expect(modelish()).toEqual([]);
  });
});

describe("the model's flow under the choice", () => {
  it('keeps the choice locked through a failed grading, and records nothing for an empty sitting', async () => {
    await start({ mode: 'gateway' });
    await go('#/r/1/quiz');
    await pick('model');
    choose('#quiz-n', '2');
    await click('button', 'Start', 30);
    expect(byText('p', /^\[1\/2\]/)).toBeTruthy();
    await click('button', "I don't know");
    await click('button', 'Next question');
    expect(byText('p', /^\[2\/2\]/)).toBeTruthy();
    answer('It caps what each visitor can spend in a day.');
    const real = app.deps.quiz() as QuizClient;
    app.deps.quiz = () => ({ mode: 'gateway', writeQuestions: (r) => real.writeQuestions(r), excerpt: (...a) => real.excerpt(...a), grade: async () => Promise.reject(new CapError('user-day', '2026-11-16T00:00:00Z')) });
    await click('button', 'Check Answer and Grade', 30);
    expect(byText('p.error', /daily HintWise allowance/)).toBeTruthy();
    expect(kind()?.disabled).toBe(true); // the answers are still there to grade
    app.deps.quiz = () => real;
    await click('button', 'Check Answer and Grade', 30);
    expect(byText('p', /^1 unknown, 1 correct \(one grading call\)\./)).toBeTruthy();
    expect(kind()?.disabled).toBe(false);

    const v = await app.repoView(1);
    const box = document.createElement('div');
    document.body.append(box);
    let done = 0;
    askFlow(app, v!, box, [], null, 'quiz', () => (done += 1));
    expect(box.textContent).toBe('All answered0 answer(s) to grade together.Grade my answers');
    (box.querySelector('button') as HTMLButtonElement).click();
    await tick();
    expect(box.textContent).toBe('Nothing answered, nothing recorded.');
    expect(done).toBe(1);
  });
});
