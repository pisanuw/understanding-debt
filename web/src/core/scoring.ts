/**
 * The scoring, ported from udebt/scoring.py (the ladder model, Phase 3.5 decay per level),
 * for a learner: the hosted app has no own-commit prior (HOSTING.md, owner decisions), so a
 * unit without answers is understood 0 and its debt is its whole importance.
 *
 *   evidence(r)      = 1 correct | 0.5 partial | 0 wrong | -0.5 confidently wrong
 *   v_n              = mean evidence of the latest sitting at level n
 *   understanding    = min(1, max(0, (v_1 f_1 + v_2 f_2 + v_3 f_3) / 3 + 0.1 (v_4 f_4 + v_5 f_5)))
 *   f_n              = exp(-k_n x share of the unit changed since its last answer)
 *                      k 0.2 at levels 1-3, 1.0 at levels 4-5 (scoring.LEVEL_K)
 *   debt             = importance x (1 - understanding), architectural units only
 *   debt index       = 100 x sum(debt) / sum(importance) over the top-level units
 *
 * Sub-units without answers inherit half a quizzed parent; a parent is understood at least
 * as well as the importance-weighted mean of its sub-units once one has answers.
 */
import { ARCHITECTURAL, DEPTH, LEGACY, depth as ladderDepth, sittings, type LadderRecord } from './ladder';

export const VERDICT_SCORE: Record<string, number> = { correct: 1, partial: 0.5, wrong: 0, unknown: 0 };
export const DEPTH_BONUS = 0.1;
export const K_LOW = 0.2;
export const K_HIGH = 1.0;
/** k per ladder level (scoring.LEVEL_K, Phase 3.5). */
export const LEVEL_K: Record<number, number> = { 1: K_LOW, 2: K_LOW, 3: K_LOW, 4: K_HIGH, 5: K_HIGH };
/** A sub-unit without answers inherits this share of a quizzed parent's understanding. */
export const INHERIT = 0.5;
/** A unit is known at or above this understanding (config known_threshold). */
export const KNOWN = 0.6;

export type Source = 'quiz' | 'children' | 'parent' | 'none';
export type Basis = 'measured' | 'estimated' | 'none';
const BASIS: Record<string, Basis> = { quiz: 'measured', children: 'measured', parent: 'estimated' };

/** Evidence value of one answer; a confidently wrong answer counts against you. */
export function evidence(r: LadderRecord): number {
  const verdict = r.verdict ?? 'wrong';
  if (verdict === 'wrong' && Number(r.confidence ?? 1) >= 3) return -0.5;
  return VERDICT_SCORE[verdict] ?? 0;
}

/** Ladder level -> mean evidence of its latest sitting. */
export function levelValues(records: LadderRecord[]): Map<number, number> {
  const out = new Map<number, number>();
  for (const [n, sit] of sittings(records)) out.set(n, sit.reduce((s, r) => s + evidence(r), 0) / sit.length);
  return out;
}

/** Verified understanding before decay: a third per architectural level plus 0.1 per depth level; null without records. */
export function ladderVerified(records: LadderRecord[]): number | null {
  if (!records.length) return null;
  const v = levelValues(records);
  const arch = ARCHITECTURAL.reduce((s, n) => s + (v.get(n) ?? 0), 0) / ARCHITECTURAL.length;
  const deep = DEPTH_BONUS * DEPTH.reduce((s, n) => s + (v.get(n) ?? 0), 0);
  return Math.max(0, arch + deep);
}

/** What remains at ladder `level` after `share` of the unit changed; an unknown share fades nothing. */
export function factor(share: number | null | undefined, level: number = LEGACY, rates: Record<number, number> = LEVEL_K): number {
  if (share === null || share === undefined) return 1;
  return Math.exp(-(rates[level] ?? K_HIGH) * Math.max(0, share));
}

/** Verified understanding after each level faded at its own rate, before the cap at 1; null without records. */
export function faded(records: LadderRecord[], share: number | null | undefined, rates: Record<number, number> = LEVEL_K): number | null {
  if (!records.length) return null;
  const v = levelValues(records);
  const part = (n: number): number => (v.has(n) ? (v.get(n) as number) * factor(share, n, rates) : 0);
  const arch = ARCHITECTURAL.reduce((s, n) => s + part(n), 0) / ARCHITECTURAL.length;
  return Math.max(0, arch + DEPTH_BONUS * DEPTH.reduce((s, n) => s + part(n), 0));
}

export interface UnitLike {
  id: string;
  importance?: number | null;
  scope?: string | null;
  parent?: string | null;
}

export interface UnitScore {
  unitId: string;
  importance: number;
  verified: number | null;
  understanding: number;
  scope: string;
  source: Source;
  parent: string | null;
  shareSince: number | null;
  depth: number | null;
  levels: number[];
}

export function basisOf(s: Pick<UnitScore, 'source'>): Basis {
  return BASIS[s.source] ?? 'none';
}

export function debtOf(s: Pick<UnitScore, 'scope' | 'importance' | 'understanding'>): number {
  return s.scope === 'architectural' ? s.importance * (1 - s.understanding) : 0;
}

/** One unit on its own answers (scoring.score_unit without the prior). */
export function scoreUnit(unit: UnitLike, records: LadderRecord[], shareSince: number | null | undefined, rates: Record<number, number> = LEVEL_K): UnitScore {
  const v = ladderVerified(records);
  const quizzed = v !== null;
  return {
    unitId: unit.id,
    importance: Number(unit.importance ?? 0.5),
    verified: v,
    understanding: quizzed ? Math.min(1, faded(records, shareSince, rates) ?? 0) : 0,
    scope: unit.scope ?? 'architectural',
    source: quizzed ? 'quiz' : 'none',
    parent: unit.parent ?? null,
    shareSince: quizzed ? (shareSince ?? null) : null,
    depth: ladderDepth(records),
    levels: records.length ? [...sittings(records).keys()].sort((a, b) => a - b) : [],
  };
}

/** A sub-unit without answers inherits half its parent's quiz understanding when that is more. */
export function inherit(own: UnitScore, parentQuiz: number | null): UnitScore {
  if (own.source === 'quiz' || parentQuiz === null) return own;
  const inherited = INHERIT * parentQuiz;
  return inherited > own.understanding ? { ...own, understanding: inherited, source: 'parent' } : own;
}

/** A parent is understood at least as well as its sub-units' importance-weighted mean, once one has answers. */
export function rollup(direct: UnitScore, children: UnitScore[]): UnitScore {
  const arch = children.filter((c) => c.scope === 'architectural');
  const total = arch.reduce((s, c) => s + c.importance, 0);
  if (!arch.length || total <= 0 || !arch.some((c) => c.source === 'quiz')) return direct;
  const mean = arch.reduce((s, c) => s + c.importance * c.understanding, 0) / total;
  return mean > direct.understanding ? { ...direct, understanding: mean, source: 'children' } : direct;
}

/** Every unit of a hierarchy (parents listed before their sub-units, `parent` set on sub-units). */
export function scoreAll(
  units: UnitLike[],
  recordsOf: (unitId: string) => LadderRecord[],
  shareOf: (unitId: string, records: LadderRecord[]) => number | null,
  rates: Record<number, number> = LEVEL_K,
): UnitScore[] {
  const own = new Map<string, UnitScore>();
  for (const u of units) {
    const recs = recordsOf(u.id);
    own.set(u.id, scoreUnit(u, recs, recs.length ? shareOf(u.id, recs) : null, rates));
  }
  const out = new Map(own);
  for (const u of units) {
    const kids = units.filter((c) => c.parent === u.id).map((c) => own.get(c.id) as UnitScore);
    if (!kids.length) continue;
    const p = own.get(u.id) as UnitScore;
    const pq = p.source === 'quiz' ? p.understanding : null;
    for (const k of kids) out.set(k.unitId, inherit(k, pq));
    out.set(u.id, rollup(p, kids));
  }
  return units.map((u) => out.get(u.id) as UnitScore);
}

/** Round half to even at `digits`, as Python's round does for these values. */
export function pyRound(x: number, digits: number): number {
  const m = 10 ** digits;
  const y = x * m;
  const r = Math.round(y);
  return (Math.abs(y % 1) === 0.5 ? (r % 2 === 0 ? r : r - 1) : r) / m;
}

/** 100 x sum(debt) / sum(importance) over architectural units (restricted to `only`), one decimal. */
export function debtIndex(scores: UnitScore[], only?: Set<string>): number {
  const arch = scores.filter((s) => s.scope === 'architectural' && (!only || only.has(s.unitId)));
  const total = arch.reduce((s, x) => s + x.importance, 0);
  if (total === 0) return 0;
  return pyRound((100 * arch.reduce((s, x) => s + debtOf(x), 0)) / total, 1);
}

export function basisCounts(scores: UnitScore[]): Record<Basis, number> {
  const out: Record<Basis, number> = { measured: 0, estimated: 0, none: 0 };
  for (const s of scores) if (s.scope === 'architectural') out[basisOf(s)] += 1;
  return out;
}

/** Units worth quizzing first: the most debt, unmeasured before measured on a tie. */
export function quizPriority(scores: UnitScore[]): UnitScore[] {
  return scores
    .filter((s) => s.scope === 'architectural')
    .map((s, i) => ({ s, i }))
    .sort((a, b) => {
      const d = -(a.s.importance * (1 - a.s.understanding)) - -(b.s.importance * (1 - b.s.understanding));
      if (d !== 0) return d;
      const ma = a.s.source === 'quiz' ? 1 : 0;
      const mb = b.s.source === 'quiz' ? 1 : 0;
      return ma - mb || a.i - b.i;
    })
    .map((x) => x.s);
}

function span(ns: number[]): string {
  const runs: number[][] = [];
  for (const n of ns) {
    const last = runs[runs.length - 1];
    if (last && last[last.length - 1] === n - 1) last.push(n);
    else runs.push([n]);
  }
  return runs.map((r) => (r.length > 1 ? `${r[0]}-${r[r.length - 1]}` : String(r[0]))).join(', ');
}

/** "levels 1-3 (k 0.2) and 4-5 (k 1)": the rate each level's evidence faded at. */
export function fadeText(levels: number[], rates: Record<number, number> = LEVEL_K): string {
  const groups = new Map<number, number[]>();
  for (const n of [...new Set(levels)].sort((a, b) => a - b)) {
    const k = rates[n] ?? K_HIGH;
    groups.set(k, [...(groups.get(k) ?? []), n]);
  }
  return [...groups.entries()]
    .map(([k, ns], i) => `${i === 0 ? (ns.length > 1 ? 'levels ' : 'level ') : ''}${span(ns)} (k ${k})`)
    .join(' and ');
}

/** How a quiz-based understanding faded: "decayed from 1.00 by share 0.52 at levels 1-3 (k 0.2)"; empty when nothing did. */
export function decayNote(s: UnitScore, rates: Record<number, number> = LEVEL_K): string {
  if (s.source !== 'quiz' || s.verified === null || !s.shareSince) return '';
  const at = s.levels.length ? ` at ${fadeText(s.levels, rates)}` : '';
  return `decayed from ${s.verified.toFixed(2)} by share ${s.shareSince.toFixed(2)}${at}`;
}
