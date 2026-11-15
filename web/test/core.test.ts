import { describe, expect, it } from 'vitest';
import fixture from '../fixtures/digitalme.json';
import { BadRequest, ModelAnswerError, fitted, gradeAnswers, readGradeRequest, readQuestionsRequest, writeQuestions, type Deps, type ModelRequest } from '../src/core/assemble';
import { formatExcerpts, parseRef, refsForUnit, safePath, safeSha, sliceLines, withinBudget } from '../src/core/excerpts';
import { label, levelOf, nextLevel } from '../src/core/ladder';
import { SUBMIT_GRADES, SUBMIT_QUESTIONS, gradePrompt, questionsPrompt, submitted, unitLines } from '../src/core/prompts';
import { markRevealed, mergeGrades, oneFact, orientationWanted, pickUnits, recordRows, revealText, revealedOn, revealedVariant, reveals, sittingTs, levelsFor, type EvidenceRow } from '../src/core/quiz';
import { repoScores, shareSince } from '../src/core/repo-scores';
import { childrenOf, connections, githubLink, orientationOf, orientationText, parseGitHubUrl, pickAnalysis, topLevel, unitById, architecturalUnits, type Analysis } from '../src/core/specs';

const SHA = 'f93102b121f1da9055c148e8eccde275ccf18236';
const analysis = { ...fixture.analyses[0], id: 1, repo_id: 1, created_at: '2026-11-15T00:00:00Z' } as unknown as Analysis;

function row(unit: string, ts: string, level: number, verdict: EvidenceRow['verdict'], extra: Partial<EvidenceRow> = {}): EvidenceRow {
  return { repo_id: 1, sha: SHA, unit_id: unit, unit, ts, level, verdict, confidence: 2, type: 'quiz', source: 'quiz', question: `q ${unit} ${level}`, revealed: false, forced: false, data: {}, ...extra };
}

describe('excerpts', () => {
  it('keeps paths inside the repository', () => {
    expect(safePath('src/a.ts')).toBe('src/a.ts');
    expect(safePath('./src/a.ts')).toBe('src/a.ts');
    for (const bad of ['', '/etc/passwd', '../x', 'a/../b', 'a//b', '.git/config', '.udebt/map.json', 'a\\b', 'a\u0001b', 42, 'x'.repeat(401)]) expect(safePath(bad)).toBeNull();
    expect(safeSha(SHA)).toBe(SHA);
    expect(safeSha('abc')).toBeNull();
  });

  it('reads references in every form the writer uses', () => {
    expect(parseRef('a.py')).toEqual({ path: 'a.py', start: 1, end: 80 });
    expect(parseRef('a.py:10-20')).toEqual({ path: 'a.py', start: 10, end: 20 });
    expect(parseRef('a.py:10')).toEqual({ path: 'a.py', start: 10, end: 49 });
    expect(parseRef('a.py#L5-L9')).toEqual({ path: 'a.py', start: 5, end: 9 });
    expect(parseRef('a.py (lines 3-4)')).toEqual({ path: 'a.py', start: 3, end: 4 });
    expect(parseRef('a.py:1-999')).toEqual({ path: 'a.py', start: 1, end: 200 });
    expect(parseRef('../a.py')).toBeNull();
  });

  it('slices lines and says what the cap cut', () => {
    const text = Array.from({ length: 300 }, (_, i) => `line ${i + 1}`).join('\r\n') + '\n';
    const x = sliceLines(text, { path: 'f', start: 2, end: 4 });
    expect(x).toEqual({ path: 'f', start: 2, end: 4, lines: ['line 2', 'line 3', 'line 4'], cut: 0 });
    const big = sliceLines(text, { path: 'f', start: 1, end: 250 });
    expect(big.lines).toHaveLength(200);
    expect(big.cut).toBe(50);
    expect(sliceLines('a\nb', { path: 'f', start: 9, end: 12 }).lines).toEqual([]);
    expect(formatExcerpts([big]).split('\n')[0]).toBe('--- f lines 1-200');
    expect(formatExcerpts([big])).toContain('... 50 more line(s)');
    expect(withinBudget([x, x, x], formatExcerpts([x]).length * 2 + 4)).toHaveLength(2);
  });

  it("picks a unit's walkthrough steps, else the files its orientation names", () => {
    const u = unitById(analysis.specs.units, 'chat-flow');
    expect(u).toBeDefined();
    const refs = refsForUnit(u!, analysis.specs.walkthroughs?.['chat-flow']);
    expect(refs.length).toBeGreaterThan(2);
    expect(refs[0].path).toBe('frontend/src/App.jsx');
    const noWalk = refsForUnit({ id: 'x', name: 'x', parent: null, paths: ['src/', 'src/a.ts', 'src/b.py', 'c'], orientation: null });
    expect(noWalk).toEqual([
      { path: 'src/a.ts', start: 1, end: 60 },
      { path: 'src/b.py', start: 1, end: 60 },
    ]);
  });
});

describe('specs', () => {
  it('walks units, sub-units and connections', () => {
    const units = analysis.specs.units;
    expect(topLevel(units)).toHaveLength(12);
    expect(childrenOf(units, 'persona-retrieval').map((u) => u.id)).toEqual(['persona-retrieval/prompt-builder', 'persona-retrieval/router-cascade', 'persona-retrieval/rag']);
    expect(architecturalUnits(units).length).toBe(units.length);
    const c = connections(analysis.specs, 'chat-flow');
    expect(c.uses).toContain('cost-control');
    expect(c.changes_with.length).toBeLessThanOrEqual(3);
    expect(connections({ units: [] }, 'x')).toEqual({ uses: [], used_by: [], changes_with: [] });
  });

  it('falls back to an orientation from the unit itself', () => {
    const sub = unitById(analysis.specs.units, 'persona-retrieval/rag')!;
    const o = orientationOf(sub);
    expect(o.fallback).toBe(true);
    expect(o.purpose).toBe(sub.summary);
    expect(orientationText(o)).toMatch(/^What it is for: /);
    expect(orientationOf({ id: 'x', name: 'X', parent: null, paths: [], history: [] }).shaped_by).toBe('The history list does not say.');
    expect(orientationOf(unitById(analysis.specs.units, 'chat-flow')!).fallback).toBeUndefined();
  });

  it('accepts public GitHub URLs only and links at the pinned sha', () => {
    expect(parseGitHubUrl('https://github.com/huggingface/huggingface_hub.git')).toEqual({ owner: 'huggingface', repo: 'huggingface_hub', url: 'https://github.com/huggingface/huggingface_hub' });
    for (const bad of ['http://github.com/a/b', 'https://gitlab.com/a/b', 'https://github.com/a', 'https://github.com/a/b/c', 'https://github.com/a/..', 'file:///etc']) expect(parseGitHubUrl(bad)).toBeNull();
    expect(githubLink('https://github.com/a/b', SHA)).toBe(`https://github.com/a/b/commit/${SHA}`);
    expect(githubLink('https://github.com/a/b', SHA, 'src/x y.ts', 3, 9)).toBe(`https://github.com/a/b/blob/${SHA}/src/x%20y.ts#L3-L9`);
    expect(githubLink('https://github.com/a/b', SHA, 'src/x.ts', 3, 3)).toBe(`https://github.com/a/b/blob/${SHA}/src/x.ts#L3`);
    expect(githubLink('/local/path', SHA)).toBeNull();
  });

  it("prefers HintWise's analysis, then the person's, then the newest shared one", () => {
    const a = (id: number, user: string | null, at: string) => ({ ...analysis, id, user_id: user, created_at: at });
    expect(pickAnalysis([a(1, 'me', '2026-01-02'), a(2, null, '2026-01-01')], 'me')?.id).toBe(2);
    expect(pickAnalysis([a(1, 'other', '2026-01-03'), a(2, 'me', '2026-01-01')], 'me')?.id).toBe(2);
    expect(pickAnalysis([a(1, 'x', '2026-01-01'), a(2, 'y', '2026-01-03')], null)?.id).toBe(2);
    expect(pickAnalysis([], null)).toBeNull();
  });
});

describe('the quiz flow', () => {
  it('asks one fact: a compound question is replaced by a variant, else trimmed', () => {
    const q = { unit_id: 'u', level: 1, question: 'What does it do, and why does it matter?', variants: ['What does it do?', 'Why is it there?'], reference_answer: 'r', evidence: [] };
    const one = oneFact(q, 1);
    expect(one.question).toBe('What does it do?');
    expect(one.rejected?.[0].why).toMatch(/joined by/);
    const trimmed = oneFact({ ...q, variants: ['List the parts that call it.'] }, 1);
    expect(trimmed.trimmed).toBe(true);
    expect(trimmed.question).toBe('What does it do?');
    expect(oneFact({ ...q, question: 'Plain?', variants: [] }, 1).variants).toEqual([]);
  });

  it('reveals a miss at levels 1-2 only, and grades blanks as known unknowns', () => {
    expect(reveals({ level: 1 }, 'partial')).toBe(true);
    expect(reveals({ level: 2 }, 'unknown')).toBe(true);
    expect(reveals({ level: 3 }, 'wrong')).toBe(false);
    expect(reveals({ level: 1 }, 'correct')).toBe(false);
    expect(revealText({ reference_answer: '  It  does  X. ' })).toBe('It does X.');
    expect(revealText({ reference_answer: '(graded against the code)' })).toBe('');
    const base = { unit_id: 'u', level: 1, question: 'q', variants: [], reference_answer: 'ref', evidence: ['a.py'], confidence: 2 };
    const merged = mergeGrades(
      [
        { ...base, answer: 'one' },
        { ...base, answer: '' },
        { ...base, answer: 'three' },
        { ...base, answer: 'four' },
      ],
      [
        { index: 0, verdict: 'correct', feedback: 'good' },
        { index: 1, verdict: 'great' as never, feedback: 'odd' },
      ],
    );
    expect(merged.map((m) => m.verdict)).toEqual(['correct', 'unknown', 'wrong', 'wrong']);
    expect(merged[3].feedback).toBe('Grader returned nothing for this.');
    expect(markRevealed(merged).map((m) => !!m.revealed)).toEqual([false, true, true, true]);
  });

  it("finds each unit's level, orientation, revealed questions and stored variants", () => {
    const recs = [
      row('a', '2026-11-01T00:00:00.000Z', 1, 'unknown', { revealed: true, question: 'First?', data: { variants: ['Again A?', 'Again B?'], reference: 'It does a.' } }),
      row('b', '2026-11-01T00:00:00.000Z', 1, 'correct'),
    ];
    expect(Object.fromEntries(levelsFor([{ id: 'a' }, { id: 'b' }, { id: 'c' }], recs))).toEqual({ a: 1, b: 2, c: 1 });
    expect(orientationWanted(recs.filter((r) => r.unit === 'a'), 1)).toBe(true);
    expect(orientationWanted(recs.filter((r) => r.unit === 'b'), 1)).toBe(false);
    expect(orientationWanted([], 2)).toBe(false);
    expect(revealedOn(recs.filter((r) => r.unit === 'a'), 1)).toEqual(['First?']);
    const v = revealedVariant('a', 1, recs.filter((r) => r.unit === 'a'));
    expect(v).toMatchObject({ question: 'Again A?', variants: ['Again B?'], reference_answer: 'It does a.', from_pool: true });
    const asked = [...recs, row('a', '2026-11-02T00:00:00.000Z', 1, 'wrong', { question: 'Again A?' })];
    expect(revealedVariant('a', 1, asked.filter((r) => r.unit === 'a'))?.question).toBe('Again B?');
    expect(revealedVariant('a', 1, [...asked, row('a', '2026-11-03T00:00:00.000Z', 1, 'correct')].filter((r) => r.unit === 'a'))).toBeNull();
    expect(revealedOn([...asked, row('a', '2026-11-03T00:00:00.000Z', 1, 'correct')], 1)).toEqual([]);
  });

  it('picks the units with the most debt, or the one named', () => {
    const sc = repoScores(analysis, []);
    const picked = pickUnits(analysis.specs.units, sc.scores, 2);
    expect(picked.map((u) => u.id)).toEqual(['chat-flow', 'cost-control']);
    expect(pickUnits(analysis.specs.units, sc.scores, 2, 'persona-retrieval/rag').map((u) => u.id)).toEqual(['persona-retrieval/rag']);
    expect(pickUnits(analysis.specs.units, sc.scores, 2, 'nope')).toEqual([]);
  });

  it('stamps a sitting after the latest record and writes metadata-only rows', () => {
    const now = new Date('2026-11-15T10:00:00.000Z');
    expect(sittingTs([], now)).toBe('2026-11-15T10:00:00.000Z');
    expect(sittingTs([{ ts: '2026-11-15T10:00:00.400Z' }], now)).toBe('2026-11-15T10:00:01.000Z');
    const graded = markRevealed(
      mergeGrades([{ unit_id: 'a', level: 1, question: 'Q?', variants: ['V?'], reference_answer: 'Ref.', evidence: ['x.py'], answer: 'my words', confidence: 3 }], [{ index: 0, verdict: 'partial', feedback: 'half' }]),
    );
    const [r] = recordRows(graded, { repoId: 7, sha: SHA, ts: '2026-11-15T10:00:00.000Z' });
    expect(r).toMatchObject({ repo_id: 7, unit_id: 'a', level: 1, verdict: 'partial', confidence: 3, revealed: true, forced: false, data: { variants: ['V?'], reference: 'Ref.', evidence: ['x.py'] } });
    expect(JSON.stringify(r)).not.toContain('my words');
    expect(JSON.stringify(r)).not.toContain('half');
  });
});

describe('scores on a repository', () => {
  it('starts at 100 and fades answers given at an earlier commit by the worker share', () => {
    expect(repoScores(analysis, []).index).toBe(100);
    const recs = [row('chat-flow', '2026-11-01T00:00:00.000Z', 1, 'correct')];
    const same = repoScores(analysis, recs);
    expect(same.index).toBeLessThan(100);
    expect(same.byId.get('chat-flow')?.understanding).toBeCloseTo(1 / 3, 12);
    const old = 'a'.repeat(40);
    const later = { ...analysis, specs: { ...analysis.specs, changed_since: { [old]: { 'chat-flow': 1.5 } } } } as Analysis;
    const faded = repoScores(later, [{ ...recs[0], sha: old }]);
    expect(faded.byId.get('chat-flow')?.understanding).toBeCloseTo(Math.exp(-0.2 * 1.5) / 3, 12);
    const unknown = repoScores(later, [{ ...recs[0], sha: 'b'.repeat(40) }]);
    expect(unknown.unknownShare).toEqual(['chat-flow']);
    expect(shareSince(analysis, 'x', null)).toBe(0);
    expect(nextLevel(recs)).toBe(2);
    expect(label(levelOf({ level: '2' }))).toBe('level 2 of 5: decision and alternatives');
  });
});

describe('the hosted prompts', () => {
  const units = [{ id: 'chat-flow', name: 'Chat', parent_name: 'Core', level: 1, summary: 's', paths: ['a.py'], history: [{ sha: 'abc1234', date: '2026-05-01', klass: 'human', subject: 'first' }], shown_orientation: 'O.', revealed: ['Old?'] }];
  const ctx = { repo: 'https://github.com/a/b', sha: SHA, excerpts: [{ path: 'a.py', start: 1, end: 1, lines: ['x = 1'], cut: 0 }] };

  it('carries the CLI rules, the units and the code at the commit', () => {
    const p = questionsPrompt(units, 1, ctx);
    expect(p.startsWith('TASK: questions')).toBe(true);
    expect(p).toContain('Every question has exactly one part');
    expect(p).toContain('orientation the developer has just read: O.');
    expect(p).toContain('was then shown the answer to');
    expect(p).toContain('    1 | x = 1');
    expect(p).toContain('You cannot open files');
    expect(unitLines(units[0])).toContain('name: Chat (a part of Core)');
    expect(questionsPrompt([{ id: 'u', name: 'U', level: 2 }], 1, { ...ctx, excerpts: [] })).toContain('No code excerpts could be fetched');
    const g = gradePrompt([{ unit_id: 'u', level: 2, question: 'Q?', reference_answer: 'R', evidence: ['a.py'], answer: 'A' }], ctx);
    expect(g.startsWith('TASK: grade')).toBe(true);
    expect(g).toContain('### 0\nUNIT: u\nLEVEL: 2 (decision and alternatives)');
    expect(SUBMIT_QUESTIONS.input_schema).toHaveProperty('required', ['questions']);
    expect(SUBMIT_GRADES.input_schema).toHaveProperty('required', ['grades']);
  });

  it('reads the submit tool, or JSON in a text answer', () => {
    expect(submitted({ content: [{ type: 'tool_use', name: 'submit', input: { a: 1 } }] })).toEqual({ a: 1 });
    expect(submitted({ content: [{ type: 'text', text: 'Here: {"b": 2} done' }] })).toEqual({ b: 2 });
    expect(submitted({ content: [{ type: 'text', text: 'no json' }] })).toBeNull();
    expect(submitted({ content: [{ type: 'text', text: '{broken' + '}' }] })).toBeNull();
    expect(submitted({ content: [{ type: 'text', text: '[1, 2] {' }] })).toBeNull();
  });
});

describe('writing and grading a sitting', () => {
  const ok = { repo: 'https://github.com/a/b', sha: SHA };
  function deps(answer: Record<string, unknown>, seen: ModelRequest[] = []): Deps {
    return {
      excerpt: async (_g, _sha, ref) => (ref.path === 'missing.py' ? null : { ...ref, end: ref.start, lines: ['code'], cut: 0 }),
      ask: async (body) => {
        seen.push(body);
        return { content: [{ type: 'tool_use', name: 'submit', input: answer }] };
      },
    };
  }

  it('checks and caps every field of a request', () => {
    expect(() => readQuestionsRequest({ ...ok, repo: 'https://evil.example/a/b', units: [{ id: 'u' }] })).toThrow(BadRequest);
    expect(() => readQuestionsRequest({ ...ok, sha: 'abc', units: [{ id: 'u' }] })).toThrow(/sha/);
    expect(() => readQuestionsRequest({ ...ok, units: [] })).toThrow(/units/);
    expect(() => readQuestionsRequest({ ...ok, units: Array.from({ length: 13 }, (_, i) => ({ id: `u${i}` })) })).toThrow(/at most/);
    expect(() => readQuestionsRequest({ ...ok, units: [{}] })).toThrow(/id/);
    expect(() => readQuestionsRequest(null)).toThrow(BadRequest);
    const r = readQuestionsRequest({ ...ok, per_unit: 9, units: [{ id: 'u', level: 7, refs: [{ path: 'a.py', start: 2, end: 3 }, { path: '../x' }], history: [{ sha: 'abc', subject: 's' }], revealed: ['Q?', 3] }] });
    expect(r.per_unit).toBe(3);
    expect(r.units[0].level).toBe(5);
    expect(r.units[0].refs).toEqual([{ path: 'a.py', start: 2, end: 3 }]);
    expect(r.units[0].revealed).toEqual(['Q?']);
    expect(() => readGradeRequest({ ...ok, items: [] })).toThrow(/items/);
    expect(() => readGradeRequest({ ...ok, items: Array.from({ length: 21 }, () => ({ question: 'q' })) })).toThrow(/at most/);
    expect(() => readGradeRequest({ ...ok, items: [{ answer: 'a' }] })).toThrow(/question/);
    expect(readGradeRequest({ ...ok, items: [{ question: 'q', level: 2, evidence: ['a.py', 5], answer: 'a' }] }).items[0]).toMatchObject({ level: 2, evidence: ['a.py'], answer: 'a' });
  });

  it('writes the questions in one call, one fact each, at the level asked', async () => {
    const seen: ModelRequest[] = [];
    const qs = await writeQuestions(
      readQuestionsRequest({ ...ok, units: [{ id: 'u', name: 'U', level: 1, refs: [{ path: 'a.py', start: 1, end: 5 }, { path: 'missing.py', start: 1, end: 5 }] }] }),
      deps({ questions: [{ unit_id: 'u', level: 4, question: 'What is it for? Why?', variants: ['What job does it do?', 'Why is it needed?'], reference_answer: 'R', evidence: ['a.py'] }, { unit_id: 'other', question: 'x' }] }, seen),
    );
    expect(seen).toHaveLength(1);
    expect(seen[0].tools?.[0].name).toBe('submit');
    expect(seen[0].max_tokens).toBe(4096);
    expect(seen[0].messages[0].content).toContain('--- a.py lines 1-1');
    expect(seen[0].messages[0].content).not.toContain('missing.py lines');
    expect(qs).toHaveLength(1);
    expect(qs[0]).toMatchObject({ unit_id: 'u', level: 1, question: 'What job does it do?' });
    await expect(writeQuestions(readQuestionsRequest({ ...ok, units: [{ id: 'u' }] }), deps({ questions: [] }))).rejects.toThrow(ModelAnswerError);
  });

  it('grades the typed answers in one call and none for blanks', async () => {
    const seen: ModelRequest[] = [];
    const req = readGradeRequest({ ...ok, items: [{ unit_id: 'u', level: 1, question: 'Q?', reference_answer: 'R', evidence: ['a.py:1-3'], answer: 'A' }, { unit_id: 'u', level: 1, question: 'Q2?', answer: ' ' }] });
    const grades = await gradeAnswers(req, deps({ grades: [{ index: 0, verdict: 'correct', feedback: 'f' }, { index: 1, verdict: '??', feedback: 'g' }] }, seen));
    expect(grades.map((g) => g.verdict)).toEqual(['correct', 'wrong']);
    expect(seen[0].messages[0].content).toContain('ANSWER: A');
    expect(seen[0].messages[0].content).not.toContain('Q2?');
    expect(await gradeAnswers(readGradeRequest({ ...ok, items: [{ question: 'Q', answer: '' }] }), deps({}))).toEqual([]);
    await expect(gradeAnswers(req, deps({ nothing: true }))).rejects.toThrow(/no grades/);
  });

  it('drops excerpts until the body fits the gateway, and refuses what never fits', async () => {
    const ex = Array.from({ length: 5 }, (_, i) => ({ path: `f${i}`, start: 1, end: 1, lines: ['x'.repeat(300_000)], cut: 0 }));
    const make = (e: typeof ex): ModelRequest => ({ max_tokens: 10, messages: [{ role: 'user', content: e.map((x) => x.lines[0]).join('') }] });
    const body = await fitted(make, ex);
    expect(body.messages[0].content.length).toBe(900_000);
    await expect(fitted(() => ({ max_tokens: 1, messages: [{ role: 'user', content: 'y'.repeat(1_100_000) }] }), [])).rejects.toThrow(BadRequest);
  });
});
