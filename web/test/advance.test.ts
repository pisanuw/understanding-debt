/**
 * Using udebt without signing in, the pure parts and the functions: the samples' index, the
 * advance-quiz file and which question to ask, the store in the browser over the site's static
 * files, the self-checked provenance in the scores, the code-reading functions open to the
 * registered samples at their pins only, the client sending no token it does not have, and the
 * hosted question prompt checked character for character against the Python port that writes
 * the advance quizzes (fixtures/advance-prompt-parity.json, written by tests/test_advance.py).
 */
import { describe, expect, it } from 'vitest';
import parity from './fixtures/advance-prompt-parity.json';
import index from '../public/samples/index.json';
import { ADVANCE_FORMAT, advancePicks, pickAdvance, poolOf, readAdvance, type AdvanceFile } from '../src/core/advance';
import { readQuestionsRequest } from '../src/core/assemble';
import { formatExcerpts, refsForUnit, sliceLines, withinBudget, type Excerpt } from '../src/core/excerpts';
import { questionsPrompt } from '../src/core/prompts';
import { SELF_CHECKED, isSelfChecked, recordRows, type EvidenceRow } from '../src/core/quiz';
import { repoScores } from '../src/core/repo-scores';
import { atPin, readIndex } from '../src/core/samples';
import { orientationOf, orientationText, type Analysis, type SpecUnit, type Walkthrough } from '../src/core/specs';
import { AdvanceQuizzes, FixtureSamples, LocalStore, StaticSamples } from '../src/data/local';
import type { Fixture } from '../src/data/store';
import { handleExcerpt, handleTree } from '../netlify/functions/lib/handlers';
import { FAKE_TOKEN, type FnEnv } from '../netlify/functions/lib/env';
import { bearerOrNull } from '../netlify/functions/lib/http';
import { REGISTERED, openToAnyone } from '../netlify/functions/lib/samples';
import { excerptVia, treeVia } from '../src/model/client';
import { getByokKey, getMode, setByokKey, setMode } from '../src/model/prefs';
import { memoryStorage } from '../src/lib/llm';

const SHA = 'a'.repeat(40);
const URL_ = 'https://github.com/acme/shop';

describe('the hosted question prompt, as udebt/hosted/advance.py writes it', () => {
  for (const c of parity.cases) {
    it(`builds the same prompt: ${c.name}`, () => {
      const req = readQuestionsRequest({ repo: c.repo, sha: c.sha, per_unit: c.per_unit, units: c.units });
      expect(req.units.map((u) => u.refs)).toEqual(c.units.map((u) => u.refs));
      const units = req.units.map(({ refs: _refs, ...u }) => u);
      expect(questionsPrompt(units, req.per_unit ?? 1, { repo: req.repo, sha: req.sha, excerpts: c.excerpts as Excerpt[] })).toBe(c.prompt);
    });
  }
  it('slices, references, orients and budgets the same way', () => {
    for (const s of parity.slices) expect(sliceLines(s.text, s.ref)).toEqual(s.excerpt);
    for (const r of parity.refs) expect(refsForUnit(r.unit as unknown as SpecUnit, r.walk as unknown as Walkthrough | null)).toEqual(r.refs);
    for (const o of parity.orientations) {
      expect(orientationOf(o.unit as unknown as SpecUnit)).toEqual(o.orientation);
      expect(orientationText(orientationOf(o.unit as unknown as SpecUnit))).toBe(o.text);
    }
    expect(withinBudget(parity.budget.excerpts as Excerpt[], parity.budget.max)).toHaveLength(parity.budget.kept);
    expect(formatExcerpts(parity.cases[0].excerpts as Excerpt[])).toContain('... 100 more line(s)');
  });
});

describe('the samples index', () => {
  it('reads the committed index: the four registered samples at their pins', () => {
    const entries = readIndex(index);
    expect(entries.map((e) => e.name)).toEqual(['huggingface_hub', 'sentry-javascript', 'mlflow', 'understanding-debt']);
    expect(entries.every((e) => /^[0-9a-f]{40}$/.test(e.sha))).toBe(true);
    expect(REGISTERED).toEqual(entries);
    const ud = entries[3];
    expect(openToAnyone(ud.url, ud.sha)).toBe(true);
    expect(openToAnyone(ud.url, SHA)).toBe(false);
    expect(openToAnyone(URL_, ud.sha)).toBe(false);
  });

  it('drops what it cannot trust and refuses a newer format', () => {
    const ok = { name: 'shop', url: URL_, sha: SHA, license: 'MIT', analysis: 'shop.json', quizzes: '../x.json' };
    expect(readIndex({ format: 'udebt-samples', version: 1, samples: [ok, { ...ok }, { ...ok, url: 'https://gitlab.com/a/b' }, { ...ok, url: 'https://github.com/a/b', sha: 'x' }, { ...ok, url: 'https://github.com/a/c', name: '' }, null] })).toEqual([
      { name: 'shop', url: URL_, sha: SHA, license: 'MIT', analysis: 'shop.json', quizzes: null },
    ]);
    expect(readIndex({ format: 'udebt-samples', version: 2, samples: [ok] })).toEqual([]);
    expect(readIndex(null)).toEqual([]);
    expect(atPin([{ ...ok, quizzes: null }], URL_, SHA)).toBe(true);
  });
});

const q = (unit: string, level: number, question: string, extra: Record<string, unknown> = {}) => ({ unit_id: unit, level, question, variants: [`${question} (a)`, `${question} (b)`], reference_answer: `ref ${question}`, evidence: ['src/a.py'], ...extra });
const FILE = { format: ADVANCE_FORMAT, version: 1, repo: URL_, sha: SHA, questions: [q('a', 1, 'A1', { orientation: { purpose: 'P', decision: 'D', where: [{ path: 'src/a.py' }, { nope: 1 }], shaped_by: 'S', fallback: true } }), q('a', 1, 'A1b'), q('a', 2, 'A2'), q('b', 1, 'B1'), q('b', 4, 'B4'), q('c', 1, 'C1', { reference_answer: '(graded against the code)' }), q('d', 1, '  ')] };

function row(unit: string, level: number, verdict: string, ts: string, question: string, extra: Partial<EvidenceRow> = {}): EvidenceRow {
  return { repo_id: 1, sha: SHA, unit_id: unit, unit, ts, level, verdict: verdict as EvidenceRow['verdict'], confidence: 2, type: SELF_CHECKED, source: 'advance', question, revealed: true, forced: false, data: {}, ...extra };
}

describe('advance quizzes', () => {
  it('keeps only questions it can self-check, at levels 1-3, with the orientation at level 1', () => {
    const f = readAdvance(FILE) as AdvanceFile;
    expect(f.questions.map((x) => `${x.unit_id}${x.level}:${x.question}`)).toEqual(['a1:A1', 'a1:A1b', 'a2:A2', 'b1:B1']);
    expect(f.questions[0].orientation).toEqual({ purpose: 'P', decision: 'D', where: [{ path: 'src/a.py', what: '' }], shaped_by: 'S', fallback: true });
    expect(readAdvance({ ...FILE, questions: [q('a', 1, 'x', { orientation: { decision: 'no purpose' } })] })?.questions[0].orientation).toBeUndefined();
    expect(readAdvance({ ...FILE, version: 2 })).toBeNull();
    expect(readAdvance({ ...FILE, format: 'other' })).toBeNull();
    expect(readAdvance('nope')).toBeNull();
    expect(poolOf(f, 'a', 1)).toHaveLength(2);
  });

  it('asks a missed question in other words, then the next one not asked, then the one asked longest ago', () => {
    const f = readAdvance(FILE) as AdvanceFile;
    expect(pickAdvance(f, 'a', 1, [])?.question).toBe('A1');
    expect(pickAdvance(f, 'zzz', 1, [])).toBeNull();
    const missed = [row('a', 1, 'wrong', '2026-11-15T10:00:00.000Z', 'A1', { data: { variants: ['A1 (a)', 'A1 (b)'], reference: 'ref A1' } })];
    const again = pickAdvance(f, 'a', 1, missed);
    expect(again).toMatchObject({ question: 'A1 (a)', reference_answer: 'ref A1', from_pool: true });
    expect(again?.orientation?.purpose).toBe('P');
    const right = [row('a', 1, 'correct', '2026-11-15T10:00:00.000Z', 'A1')];
    expect(pickAdvance(f, 'a', 1, right)?.question).toBe('A1b');
    const both = [...right, row('a', 1, 'correct', '2026-11-16T10:00:00.000Z', 'A1b')];
    expect(pickAdvance(f, 'a', 1, both)?.question).toBe('A1');
  });

  it('picks units by debt at their next level, skipping those past level 3 or without a question', () => {
    const f = readAdvance(FILE) as AdvanceFile;
    const units = ['a', 'b', 'c', 'x'].map((id) => ({ id, name: id.toUpperCase(), paths: [], parent: null, importance: id === 'b' ? 0.9 : 0.5 })) as SpecUnit[];
    units.push({ id: 'a/sub', name: 'Sub', paths: [], parent: 'a' });
    const analysis = { id: 1, repo_id: 1, sha: SHA, user_id: null, created_at: '', manifest: { repo: 'shop', url: URL_, sha: SHA }, specs: { units } } as Analysis;
    const passed = [1, 2, 3].map((lv) => row('b', lv, 'correct', `2026-11-1${lv}T10:00:00.000Z`, `B${lv}`));
    const sc = repoScores(analysis, []);
    const all = advancePicks(f, units, sc.scores, [], 5);
    expect(all.picks.map((p) => `${p.unit.id}@${p.level}`)).toEqual(['b@1', 'a@1']);
    expect(all.skipped.map((s) => s.unit.id)).toEqual(['c', 'x']);
    const later = advancePicks(f, units, repoScores(analysis, passed).scores, passed, 5);
    expect(later.picks.map((p) => p.unit.id)).toEqual(['a']);
    expect(later.skipped.map((s) => `${s.unit.id}@${s.level}`)).toEqual(['c@1', 'x@1', 'b@4']);
    expect(advancePicks(f, units, sc.scores, [], 1).skipped).toEqual([]);
    expect(advancePicks(f, units, sc.scores, [], 2, 'a/sub').picks).toEqual([]);
    expect(advancePicks(f, units, sc.scores, [], 2, 'nope').picks).toEqual([]);
  });
});

describe('the self-checked provenance in the scores', () => {
  const units: SpecUnit[] = [
    { id: 'p', name: 'P', paths: [], parent: null, importance: 1 },
    { id: 'p/c', name: 'C', paths: [], parent: 'p', importance: 1 },
    { id: 'q', name: 'Q', paths: [], parent: null, importance: 1 },
  ];
  const analysis = { id: 1, repo_id: 1, sha: SHA, user_id: null, created_at: '', manifest: { repo: 'r', url: URL_, sha: SHA }, specs: { units } } as Analysis;

  it('labels a unit whose latest sittings hold a self-checked answer, and what rolls up from it', () => {
    const graded = row('q', 1, 'correct', '2026-11-15T10:00:00.000Z', 'x', { type: 'quiz' });
    expect(isSelfChecked(graded)).toBe(false);
    const none = repoScores(analysis, [graded]);
    expect([...none.selfChecked]).toEqual([]);
    expect(none.selfCheckedIndex).toBe(false);
    const child = repoScores(analysis, [graded, row('p/c', 1, 'correct', '2026-11-15T10:00:00.000Z', 'y')]);
    expect([...child.selfChecked].sort()).toEqual(['p', 'p/c']);
    expect(child.selfCheckedIndex).toBe(true);
    const parent = repoScores(analysis, [row('p', 1, 'correct', '2026-11-15T10:00:00.000Z', 'z')]);
    expect([...parent.selfChecked].sort()).toEqual(['p', 'p/c']); // the sub-unit inherits half of it
    const superseded = repoScores(analysis, [row('q', 1, 'wrong', '2026-11-15T10:00:00.000Z', 'x'), { ...graded, ts: '2026-11-16T10:00:00.000Z' }]);
    expect([...superseded.selfChecked]).toEqual([]); // a later graded sitting at that level replaces it
  });

  it('records the provenance on the row', () => {
    const rows = recordRows([{ ...q('a', 1, 'A'), answer: 'x', confidence: 2, verdict: 'correct', feedback: '', revealed: true }], { repoId: 1, sha: SHA, ts: 't', source: 'advance', type: SELF_CHECKED });
    expect(rows[0]).toMatchObject({ type: 'self-checked', source: 'advance', revealed: true, data: { reference: 'ref A' } });
    expect(recordRows([{ ...q('a', 1, 'A'), answer: 'x', confidence: 2, verdict: 'correct', feedback: '' }], { repoId: 1, sha: SHA, ts: 't' })[0].type).toBe('quiz');
  });
});

describe('the store in the browser', () => {
  const files: Record<string, unknown> = {
    'samples/index.json': { format: 'udebt-samples', version: 1, samples: [{ name: 'shop', url: URL_, sha: SHA, license: null, analysis: 'shop.json', quizzes: 'shop.quizzes.json' }, { name: 'next', url: 'https://github.com/acme/next', sha: SHA, license: 'MIT', analysis: null, quizzes: null }] },
    'samples/shop.json': { repositories: [{ url: URL_, name: 'shop', license: null, is_sample: true }], analyses: [{ repo_url: URL_, sha: SHA, user_id: null, manifest: { repo: 'shop', url: URL_, sha: SHA }, specs: { units: [{ id: 'a', name: 'A', paths: [], parent: null }] } }, { repo_url: URL_, sha: 'b'.repeat(40), manifest: {}, specs: { units: [] } }] },
    'samples/shop.quizzes.json': FILE,
  };
  const asked: string[] = [];
  const doFetch = (async (u: RequestInfo | URL) => {
    asked.push(String(u));
    const f = files[String(u)];
    return f ? Response.json(f) : new Response('', { status: 404 });
  }) as typeof fetch;

  it('lists the registered samples, one without an analysis, from the site files, each fetched once', async () => {
    const store = new LocalStore(new StaticSamples('samples/', doFetch), memoryStorage());
    const { repos, mine } = await store.repositories();
    expect(repos.map((r) => [r.id, r.name, r.is_sample])).toEqual([[1, 'shop', true], [2, 'next', true]]);
    expect(mine.size).toBe(0);
    expect((await store.analyses(1)).map((a) => [a.sha, a.user_id])).toEqual([[SHA, null]]);
    expect(await store.analyses(2)).toEqual([]);
    expect(await store.analyses(9)).toEqual([]);
    await store.analyses(1);
    expect(asked.filter((u) => u === 'samples/shop.json')).toHaveLength(1);
    expect(await store.findRepository(URL_)).toMatchObject({ id: 1 });
    expect(await store.findRepository('https://github.com/x/y')).toBeNull();
    await expect(store.addMine()).rejects.toThrow('Sign in');
    await expect(store.removeMine()).rejects.toThrow('Sign in');
    await expect(store.addRecords([row('a', 1, 'correct', 't', 'x', { repo_id: 9 })])).rejects.toThrow('Not one of the samples');
    expect(await store.startSession(1, SHA, [])).toBeNull();
    await store.finishSession();
    expect(await store.jobs()).toEqual([]);
    expect(await store.records(9)).toEqual([]);
    const advance = new AdvanceQuizzes(new StaticSamples('samples/', doFetch));
    expect((await advance.for(URL_, SHA))?.questions).toHaveLength(4);
    expect(await advance.for(URL_, 'b'.repeat(40))).toBeNull();
    expect(await advance.for('https://github.com/acme/next', SHA)).toBeNull();
    const broken = new StaticSamples('samples/', (async () => {
      throw new Error('offline');
    }) as typeof fetch);
    expect(await broken.entries()).toEqual([]);
  });

  it('keeps answers per sample in localStorage, offers each once, and survives broken saves', async () => {
    const storage = memoryStorage();
    const store = new LocalStore(new StaticSamples('samples/', doFetch), storage);
    const added = await store.addRecords([row('a', 1, 'correct', '2026-11-15T10:00:00Z', 'x'), row('a', 2, 'wrong', '2026-11-15T10:00:00Z', 'y')]);
    expect(added.map((r) => r.id)).toEqual([1, 2]);
    expect(JSON.parse(storage.getItem('udebt.local.v1.shop') ?? '{}').records).toHaveLength(2);
    const again = new LocalStore(new StaticSamples('samples/', doFetch), storage);
    expect((await again.records(1)).map((r) => [r.id, r.ts])).toEqual([[1, '2026-11-15T10:00:00.000Z'], [2, '2026-11-15T10:00:00.000Z']]);
    expect((await again.pending()).map((p) => [p.repo.name, p.records.length])).toEqual([['shop', 2]]);
    await again.decide(1);
    expect(await again.pending()).toEqual([]);
    await again.addRecords([row('a', 1, 'correct', '2026-11-16T10:00:00Z', 'z')]);
    expect((await again.pending())[0].records.map((r) => r.id)).toEqual([3]);
    await again.decide(1, true);
    expect(await again.records(1)).toEqual([]);
    expect(JSON.parse(storage.getItem('udebt.local.v1.shop') ?? '{}')).toEqual({ records: [], decided: 3 });
    expect((await again.addRecords([row('a', 1, 'correct', 't', 'w')]))[0].id).toBe(4);
    await again.decide(9);
    expect(await again.forgetAll()).toBe(1);
    expect(storage.getItem('udebt.local.v1.shop')).toBeNull();
    storage.setItem('udebt.local.v1.shop', '{broken');
    expect(await new LocalStore(new StaticSamples('samples/', doFetch), storage).records(1)).toEqual([]);
  });

  it('serves the preview fixture as its samples, with their advance quizzes', async () => {
    const fx: Fixture = {
      repositories: [{ url: URL_, name: 'shop', license: 'MIT', is_sample: true }, { url: 'https://github.com/acme/own', name: 'own', license: null, is_sample: false }],
      analyses: [{ repo_url: URL_, sha: SHA, user_id: null, manifest: { repo: 'shop', url: URL_, sha: SHA }, specs: { units: [] } }, { repo_url: 'https://github.com/acme/own', sha: SHA, user_id: null, manifest: { repo: 'own', url: '', sha: SHA }, specs: { units: [] } }],
    };
    const src = new FixtureSamples(fx, [FILE, null, 'x']);
    expect(await src.entries()).toEqual([{ name: 'shop', url: URL_, license: 'MIT', sha: SHA, analysis: 'shop.json', quizzes: 'shop.quizzes.json' }]);
    expect((await src.rows((await src.entries())[0]))?.analyses).toHaveLength(1);
    expect(await src.rows({ name: 'x', url: 'https://github.com/x/y', sha: SHA, license: null, analysis: null, quizzes: null })).toBeNull();
    expect(await new FixtureSamples(fx).quizzes((await src.entries())[0])).toBeNull();
  });
});

describe('the code-reading functions without a token', () => {
  const env: FnEnv = { supabaseUrl: 'https://auth.example', anonKey: 'anon', fakes: false, fakeRepos: {} };
  const ud = readIndex(index)[3];
  const calls: string[] = [];
  const doFetch = (async (u: RequestInfo | URL) => {
    calls.push(String(u));
    if (String(u).startsWith('https://raw.githubusercontent.com/')) return new Response('one\ntwo\nthree\n');
    if (String(u).startsWith('https://api.github.com/')) return Response.json({ sha: ud.sha, truncated: false, tree: [{ path: 'web/src/a.ts', type: 'blob', mode: '100644', size: 99 }] });
    return new Response('{}', { status: 500 });
  }) as typeof fetch;
  const get = (path: string, auth?: string) => new Request(`http://x${path}`, { headers: auth ? { authorization: auth } : {} });

  it('read a registered sample at its pin and never ask Supabase', async () => {
    calls.length = 0;
    const ex = await handleExcerpt(get(`/api/excerpt?repo=${encodeURIComponent(ud.url)}&sha=${ud.sha}&path=README.md&start=1&end=2`), { env, fetch: doFetch });
    expect(ex.status).toBe(200);
    expect(await ex.json()).toMatchObject({ lines: ['one', 'two'] });
    const tree = await handleTree(get(`/api/tree?repo=${encodeURIComponent(ud.url)}&sha=${ud.sha}&path=web`), { env, fetch: doFetch, now: () => 0 });
    expect(tree.status).toBe(200);
    expect(calls.some((c) => c.includes('/rest/v1/'))).toBe(false);
  });

  it('refuse any other repository or commit, and a malformed header, with a 401', async () => {
    for (const path of [`/api/excerpt?repo=${encodeURIComponent(ud.url)}&sha=${SHA}&path=README.md`, `/api/excerpt?repo=${encodeURIComponent(URL_)}&sha=${ud.sha}&path=a.py`, `/api/tree?repo=${encodeURIComponent(URL_)}&sha=${SHA}&path=web`]) {
      const res = path.startsWith('/api/tree') ? await handleTree(get(path), { env, fetch: doFetch }) : await handleExcerpt(get(path), { env, fetch: doFetch });
      expect(res.status).toBe(401);
      expect(((await res.json()) as { error: string }).error).toMatch(/only the samples at their pinned commits/);
    }
    const bad = await handleExcerpt(get(`/api/excerpt?repo=${encodeURIComponent(ud.url)}&sha=${ud.sha}&path=a.py`, 'Basic xyz'), { env, fetch: doFetch });
    expect(bad.status).toBe(401);
    expect(bearerOrNull(get('/x'))).toBeNull();
    expect(bearerOrNull(get('/x', `Bearer ${FAKE_TOKEN}`))).toBe(FAKE_TOKEN);
  });

  it('check a token as before, whatever the repository', async () => {
    calls.length = 0;
    const res = await handleExcerpt(get(`/api/excerpt?repo=${encodeURIComponent(ud.url)}&sha=${ud.sha}&path=README.md`, 'Bearer user-token-1'), { env, fetch: doFetch });
    expect(res.status).toBe(502); // the stub answers PostgREST with a 500: the token is checked, not skipped
    expect(calls[0]).toContain('/rest/v1/repositories');
  });
});

describe('the mode and key with storage blocked', () => {
  it('keeps them in memory for the page', () => {
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
    };
    setMode('byok', blocked);
    expect(getMode(blocked)).toBe('byok');
    setByokKey('sk-x', blocked);
    expect(getByokKey(blocked)).toBe('sk-x');
    setByokKey(null, blocked);
    expect(getByokKey(blocked)).toBeNull();
    setMode('off', blocked);
  });
});

describe('the client without a token', () => {
  it('reads code with no Authorization header and nothing else', async () => {
    const sent: (string | null)[] = [];
    const doFetch = (async (_u: RequestInfo | URL, init?: RequestInit) => {
      sent.push(new Headers(init?.headers).get('authorization'));
      return Response.json({ path: 'a', start: 1, end: 1, lines: ['x'], cut: 0, dirs: {} });
    }) as typeof fetch;
    const deps = { getToken: async () => null, fetch: doFetch };
    await excerptVia(deps)(URL_, SHA, { path: 'a', start: 1, end: 1 });
    await treeVia(deps)(URL_, SHA, ['web']);
    expect(sent).toEqual([null, null]);
    await excerptVia({ ...deps, getToken: async () => 'tok' })(URL_, SHA, { path: 'a', start: 1, end: 1 });
    expect(sent[2]).toBe('Bearer tok');
  });
});
