/**
 * The TypeScript ladder and scoring against the Python core: every case and answer in
 * fixtures/parity.json was computed by udebt/ladder.py and udebt/scoring.py
 * (tests/test_hosted_parity.py writes it and fails when it is stale).
 */
import { describe, expect, it } from 'vitest';
import parity from './fixtures/parity.json';
import { compound, depth, firstClause, nextLevel, type LadderRecord } from '../src/core/ladder';
import {
  LEVEL_K,
  basisCounts,
  basisOf,
  debtIndex,
  debtOf,
  decayNote,
  faded,
  ladderVerified,
  levelValues,
  quizPriority,
  scoreAll,
  scoreUnit,
} from '../src/core/scoring';

const close = (a: number | null, b: number | null) => {
  if (a === null || b === null) expect(a).toBe(b);
  else expect(a).toBeCloseTo(b, 12);
};

describe('parity with the Python core', () => {
  it('uses the same rates per level', () => {
    expect(Object.fromEntries(Object.entries(LEVEL_K).map(([k, v]) => [k, v]))).toEqual(parity.level_k);
  });

  for (const c of parity.ladder) {
    it(`ladder: ${c.name}`, () => {
      const recs = c.records as LadderRecord[];
      expect(Object.fromEntries([...levelValues(recs)].map(([k, v]) => [String(k), v]))).toEqual(c.level_values);
      close(ladderVerified(recs), c.verified);
      expect(depth(recs)).toBe(c.depth);
      expect(nextLevel(recs)).toBe(c.next_level);
      for (const s of c.scores) {
        const unit = { id: 'u', importance: 0.8, scope: 'architectural' };
        const got = scoreUnit(unit, recs, s.share);
        close(faded(recs, s.share), s.faded);
        close(got.understanding, s.understanding);
        close(debtOf(got), s.debt);
        expect(got.source).toBe(s.source);
        expect(got.levels).toEqual(c.levels);
        expect(decayNote(got)).toBe(s.note.replace(/ \(mass [\d.]+\)$/, ''));
      }
    });
  }

  for (const c of parity.hierarchy.cases) {
    it(`hierarchy: ${c.name} at share ${c.share}`, () => {
      const units = parity.hierarchy.units;
      const records = c.records as Record<string, LadderRecord[]>;
      const scores = scoreAll(units, (id) => records[id] ?? [], () => c.share);
      c.scores.forEach((want, i) => {
        const got = scores[i];
        expect(got.unitId).toBe(want.unit);
        close(got.understanding, want.understanding);
        expect(got.source).toBe(want.source);
        expect(basisOf(got)).toBe(want.basis);
        close(debtOf(got), want.debt);
        expect(got.depth).toBe(want.depth);
      });
      const top = new Set(units.filter((u) => u.parent === null).map((u) => u.id));
      expect(debtIndex(scores, top)).toBe(c.index);
      expect(basisCounts(scores.filter((s) => top.has(s.unitId)))).toEqual(c.basis_counts);
      expect(quizPriority(scores.filter((s) => top.has(s.unitId))).map((s) => s.unitId)).toEqual(c.priority);
    });
  }

  it('finds compound questions and trims them as the CLI does', () => {
    for (const c of parity.text) {
      expect(compound(c.question, c.level)).toBe(c.compound);
      expect(firstClause(c.question)).toBe(c.first_clause);
    }
  });
});
