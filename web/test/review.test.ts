/**
 * The TypeScript spaced review against the Python core: every case and answer in
 * fixtures/review-parity.json was computed by udebt/review.py
 * (tests/test_hosted_review_parity.py writes it and fails when it is stale). The records there
 * are the CLI's shape; `hosted` moves variants, reference and evidence into a row's `data`, as
 * the app keeps them.
 */
import { describe, expect, it } from 'vitest';
import parity from './fixtures/review-parity.json';
import { NO_REFERENCE, type Graded } from '../src/core/quiz';
import { EASE, EASE_MIN, FIRST, SECOND, due, dueAfter, fold, items, parseTs, phrasing, pyRound, quality, sittingRows, stamp, stateAt, step, type ReviewRecord, type ReviewState } from '../src/core/review';

type CliRecord = Record<string, unknown> & { ts: string };

function hosted(r: CliRecord): ReviewRecord {
  const { variants, reference, evidence, ...rest } = r as CliRecord & { variants?: string[]; reference?: string; evidence?: string[] };
  return {
    ...(rest as ReviewRecord),
    unit_id: (rest.unit as string | undefined) ?? null,
    data: { ...(variants ? { variants } : {}), ...(reference ? { reference } : {}), ...(evidence ? { evidence } : {}) },
  };
}

/** Each record's review state when written sitting by sitting, as the app writes them. */
function stampedInTurn(records: ReviewRecord[]): (ReviewState | null)[] {
  const written: ReviewRecord[] = [];
  const out: (ReviewState | null)[] = [];
  let i = 0;
  while (i < records.length) {
    let j = i;
    while (j < records.length && records[j].ts === records[i].ts) j += 1;
    const rows = stamp(written, records.slice(i, j));
    out.push(...rows.map((r) => r.data?.review ?? null));
    written.push(...rows);
    i = j;
  }
  return out;
}

describe('parity with udebt/review.py', () => {
  it('uses the same constants', () => {
    expect({ ease: EASE, ease_min: EASE_MIN, first: FIRST, second: SECOND }).toEqual(parity.constants);
  });

  it('maps every verdict and confidence to the same quality', () => {
    for (const c of parity.quality) expect(quality(c.record as ReviewRecord), JSON.stringify(c.record)).toBe(c.quality);
  });

  for (const c of parity.cases) {
    it(`review: ${c.name}`, () => {
      const recs = (c.records as CliRecord[]).map(hosted);
      const folded = [...fold(recs).values()].sort((a, b) => (a.unit < b.unit ? -1 : a.unit > b.unit ? 1 : a.level - b.level));
      expect(folded).toEqual(c.fold);
      expect(stampedInTurn(recs)).toEqual(c.stamped);
      expect(items(recs).map((it) => ({ ...it, question: { ...it.question, from_pool: undefined } }))).toEqual(
        c.items.map((it) => ({ ...it, question: { ...it.question, from_pool: undefined } })),
      );
      for (const d of c.due) {
        const got = due(recs, new Date(parseTs(d.now)), d.unit_ids ? new Set(d.unit_ids) : null, d.everything);
        expect(got.map((it) => [it.unit, it.level]), `${d.now} ${String(d.unit_ids)} ${d.everything}`).toEqual(d.items);
      }
    });
  }
});

describe('the pieces of the port', () => {
  it("rounds as Python's round does, ties to even on the exact value", () => {
    expect(pyRound(2.675, 2)).toBe(2.67); // 2.675 is 2.67499999... in binary
    expect(pyRound(0.125, 2)).toBe(0.12); // an exact tie goes to even
    expect(pyRound(0.375, 2)).toBe(0.38);
    expect(pyRound(18.75, 1)).toBe(18.8);
    expect(pyRound(17.25, 1)).toBe(17.2);
    expect(pyRound(2.3600000000000003, 3)).toBe(2.36);
    expect(pyRound(-1.25, 1)).toBe(-1.2);
    expect(pyRound(0.04, 1)).toBe(0);
    expect(pyRound(7, 0)).toBe(7);
    expect(pyRound(Number.NaN, 1)).toBeNaN();
  });

  it('reads times as Python does and writes due times to the second in UTC', () => {
    expect(parseTs('2026-11-03T10:00:00')).toBe(Date.UTC(2026, 10, 3, 10));
    expect(parseTs('2026-11-03 10:00:00')).toBe(Date.UTC(2026, 10, 3, 10));
    expect(parseTs('2026-11-03T10:00:00+01:00')).toBe(Date.UTC(2026, 10, 3, 9));
    expect(parseTs('2026-11-03')).toBe(Date.UTC(2026, 10, 3));
    expect(dueAfter(Date.parse('2026-11-17T23:59:59.500Z'), 3)).toBe('2026-11-20T23:59:59+00:00');
    expect(dueAfter(Date.parse('2026-11-01T00:00:00Z'), 0.1)).toBe('2026-11-01T02:24:00+00:00');
    expect(dueAfter(Date.parse('1969-12-31T23:59:59.250Z'), 1)).toBe('1970-01-01T23:59:59+00:00');
  });

  it('starts nothing on a first correct answer and steps from now when a record has no time', () => {
    expect(step(null, { ts: '2026-11-01T10:00:00Z', verdict: 'correct', confidence: 2 })).toBeNull();
    const s = step(null, { ts: '', verdict: 'unknown', confidence: 1 }, new Date('2026-11-15T10:00:00Z'));
    expect(s).toEqual({ ease: 2.3, interval: 1, reps: 0, due: '2026-11-16T10:00:00+00:00' });
  });

  it('asks a stored phrasing, never the one asked last, with the stored reference', () => {
    const miss: ReviewRecord = { ts: '2026-11-01T10:00:00Z', unit_id: 'u', level: 1, verdict: 'unknown', question: 'Q?', data: { variants: ['A?', 'B?'], reference: 'R', evidence: ['a.py:1-2'] } };
    expect(phrasing([miss])).toMatchObject({ question: 'A?', variants: ['Q?', 'B?'], reference_answer: 'R', evidence: ['a.py:1-2'], from_pool: true });
    expect(phrasing([{ ...miss, data: null }])).toMatchObject({ question: 'Q?', variants: [], reference_answer: NO_REFERENCE, evidence: [] });
    expect(phrasing([{ ...miss, verdict: 'correct' }])).toBeNull();
    expect(stateAt([miss], 'u', 1)?.reps).toBe(0);
    expect(stateAt([miss], 'u', 2)).toBeUndefined();
  });

  it('writes a sitting with its review state on data, as the CLI writes review', () => {
    const prior: ReviewRecord[] = [{ ts: '2026-11-01T10:00:00.000Z', unit_id: 'u', unit: 'u', level: 1, verdict: 'unknown', confidence: 1, question: 'Q?' }];
    const results: Graded[] = [
      { unit_id: 'u', level: 1, question: 'A?', variants: ['Q?'], reference_answer: 'R', evidence: [], answer: 'It does.', confidence: 3, verdict: 'correct', feedback: '' },
      { unit_id: 'v', level: 2, question: 'W?', variants: [], reference_answer: 'R2', evidence: [], answer: 'Yes', confidence: 2, verdict: 'correct', feedback: '' },
    ];
    const rows = sittingRows(results, prior, { repoId: 1, sha: 'x', ts: '2026-11-02T10:00:00.000Z', source: 'review' });
    expect(rows[0]).toMatchObject({ source: 'review', type: 'quiz', data: { variants: ['Q?'], reference: 'R', review: { ease: 2.4, interval: 1, reps: 1, due: '2026-11-03T10:00:00+00:00' } } });
    expect(rows[1].data).not.toHaveProperty('review');
    const odd = stamp<ReviewRecord>([], [{ ts: 't', unit_id: null, verdict: 'wrong' }]);
    expect(odd[0].data).toBeUndefined();
  });
});
