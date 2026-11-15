/**
 * Spaced review, ported from udebt/review.py (Phase 3.3): the questions a person missed come
 * back at growing intervals until they stick. SM-2 style, one item per (unit, level), folded
 * from the records.
 *
 * An item starts on a miss (partial, wrong or unknown, a revealed answer included) at a unit
 * and level; a correct answer there before any miss starts nothing. Then every later answer
 * there (quiz or review) moves it:
 *
 *   quality   correct 5 (confidence 3), 4 (2), 3 (1); partial 2; wrong or unknown 1;
 *             confidently wrong 0
 *   q < 3     reps 0, interval 1 day, ease - 0.2 (at least 1.3)
 *   q >= 3    reps + 1; interval 1 day at the first, 3 days at the second, then the last
 *             interval x ease; ease + 0.1 - (5 - q) x (0.08 + (5 - q) x 0.02)
 *   due       the answer's time + the interval, to the second, in UTC
 *
 * The fold is the source of truth; each new record also carries its item's state after it on
 * `data.review` (the CLI's `review`), so an export reads it without folding. A review asks a
 * stored phrasing of the missed question (the question or one of its stored variants, not the
 * one asked last) with its stored reference, so a review sitting costs one call: the grading.
 * web/test/fixtures/review-parity.json holds the Python answers this port is checked against
 * (tests/test_hosted_review_parity.py writes it).
 */
import { levelOf, ordered } from './ladder';
import { NO_REFERENCE, recordRows, type EvidenceRow, type Graded, type Question } from './quiz';

export const EASE = 2.5;
export const EASE_MIN = 1.3;
/** Days, after the first and the second correct answer. */
export const FIRST = 1.0;
export const SECOND = 3.0;
/** A sitting of five items is about five minutes. */
export const MINUTES_PER_ITEM = 1;
/** Items per sitting unless the person asks for another number (udebt serve's /api/review). */
export const SITTING = 5;

export interface ReviewState {
  ease: number;
  /** Days. */
  interval: number;
  reps: number;
  /** ISO time in UTC, to the second ("2026-11-16T10:00:00+00:00", as Python writes it). */
  due: string;
}

/** What the review reads of a record: a hosted row (variants, reference and evidence in `data`). */
export interface ReviewRecord {
  ts: string;
  unit_id?: string | null;
  unit?: string | null;
  level?: number | null;
  verdict?: string | null;
  confidence?: number | string | null;
  question?: string | null;
  data?: { variants?: string[]; reference?: string; evidence?: string[]; review?: ReviewState } | null;
}

export interface ReviewItem {
  unit: string;
  level: number;
  state: ReviewState;
  /** What to ask: unit_id, level, the phrasing, its reference, evidence and the other phrasings. */
  question: Question;
}

const DAY_US = 86_400_000_000;

/** Python's round(x, n): the exact decimal value of the double, rounded half to even. */
export function pyRound(x: number, n: number): number {
  if (!Number.isFinite(x)) return x;
  const [whole, frac] = Math.abs(x).toFixed(100).split('.');
  const digits = whole + frac.slice(0, n);
  const rest = frac.slice(n);
  const up = rest[0] > '5' || (rest[0] === '5' && (/[1-9]/.test(rest.slice(1)) || Number(digits[digits.length - 1]) % 2 === 1));
  const v = (BigInt(digits) + (up ? 1n : 0n)).toString().padStart(n + 1, '0');
  const out = Number(n ? `${v.slice(0, -n)}.${v.slice(-n)}` : v);
  return x < 0 ? -out : out;
}

/** Round half to even, as CPython rounds a timedelta's leftover microseconds. */
function roundHalfEven(x: number): number {
  const r = Math.round(x);
  return Math.abs(r - x) === 0.5 && r % 2 !== 0 ? r - 1 : r;
}

/** An ISO time in milliseconds since the epoch; one without an offset is UTC (ledger.parse_ts). */
export function parseTs(ts: string): number {
  const t = String(ts).trim().replace(/^(\d{4}-\d\d-\d\d) (?=\d)/, '$1T');
  return Date.parse(/(?:[zZ]|[+-]\d\d:?\d\d)$/.test(t) || !t.includes('T') ? t : `${t}Z`);
}

/** `when` plus `interval` days, truncated to the second, as Python's isoformat of a UTC time. */
export function dueAfter(whenMs: number, interval: number): string {
  // timedelta(days=x): whole days exactly, the fraction to the nearest microsecond.
  const whole = Math.trunc(interval);
  const us = whole * DAY_US + roundHalfEven((interval - whole) * DAY_US);
  const msPart = ((whenMs % 1000) + 1000) % 1000;
  const sec = Math.floor((whenMs - msPart) / 1000) + Math.floor((msPart * 1000 + us) / 1_000_000);
  return new Date(sec * 1000).toISOString().replace(/\.\d{3}Z$/, '+00:00');
}

/** The answer's quality on the SM-2 scale (review.quality). */
export function quality(r: Pick<ReviewRecord, 'verdict' | 'confidence'>): number {
  const conf = Math.trunc(Number(r.confidence ?? 2) || 2);
  if (r.verdict === 'correct') return conf === 3 ? 5 : conf === 2 ? 4 : 3;
  if (r.verdict === 'partial') return 2;
  if (r.verdict === 'wrong' && conf >= 3) return 0;
  return 1;
}

/** The item's state after answer `r`; null while no miss has started one (review.step). */
export function step(state: ReviewState | null | undefined, r: Pick<ReviewRecord, 'ts' | 'verdict' | 'confidence'>, now: Date = new Date()): ReviewState | null {
  const q = quality(r);
  if (!state && q >= 3) return null;
  let ease = state ? state.ease : EASE;
  let interval = state ? state.interval : 0;
  let reps = state ? state.reps : 0;
  if (q < 3) {
    reps = 0;
    interval = FIRST;
    ease = Math.max(EASE_MIN, ease - 0.2);
  } else {
    reps += 1;
    interval = reps === 1 ? FIRST : reps === 2 ? SECOND : pyRound(interval * ease, 1);
    ease = Math.max(EASE_MIN, pyRound(ease + 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02), 3));
  }
  const when = r.ts ? parseTs(r.ts) : now.getTime();
  return { ease: pyRound(ease, 3), interval, reps, due: dueAfter(when, interval) };
}

function unitOf(r: ReviewRecord): string | null {
  const u = r.unit_id ?? r.unit;
  return u ? String(u) : null;
}

function keyOf(unit: string, level: number): string {
  return `${level}\u0000${unit}`;
}

/** (unit, level) -> state over every record in time order (review.fold); keys as "level\0unit". */
export function fold(records: ReviewRecord[]): Map<string, { unit: string; level: number; state: ReviewState }> {
  const out = new Map<string, { unit: string; level: number; state: ReviewState }>();
  for (const r of ordered(records)) {
    const unit = unitOf(r);
    if (!unit || !r.verdict) continue;
    const level = levelOf(r);
    const k = keyOf(unit, level);
    const s = step(out.get(k)?.state, r);
    if (s) out.set(k, { unit, level, state: s });
  }
  return out;
}

/** The new rows, each with its item's state after it on `data.review`, folding `prior` first (review.stamp). */
export function stamp<T extends ReviewRecord>(prior: ReviewRecord[], rows: T[]): T[] {
  const states = fold(prior);
  return rows.map((r) => {
    const unit = unitOf(r);
    if (!unit || !r.verdict) return r;
    const k = keyOf(unit, levelOf(r));
    const s = step(states.get(k)?.state, r);
    if (!s) return r;
    states.set(k, { unit, level: levelOf(r), state: s });
    return { ...r, data: { ...(r.data ?? {}), review: s } };
  });
}

/** The records a graded sitting leaves (quiz.recordRows), each stamped with its review state. */
export function sittingRows(results: Graded[], prior: ReviewRecord[], opts: Parameters<typeof recordRows>[1]): EvidenceRow[] {
  return stamp(prior, recordRows(results, opts));
}

/**
 * The latest miss on a unit and level, the record an item's question comes from (`phrasing`'s
 * miss): the page checks the item the way that miss was checked (ui/screens/review.ts checkedBy).
 */
export function lastMiss<T extends ReviewRecord>(records: T[], unit: string, level: number): T | undefined {
  const misses = ordered(records).filter((r) => unitOf(r) === unit && levelOf(r) === level && r.verdict !== 'correct' && r.question);
  return misses[misses.length - 1];
}

/**
 * What to ask for an item: the latest missed question's phrasings (the question and its
 * variants), the first one not asked last, with that miss's reference (review._phrasing).
 */
export function phrasing(atKey: ReviewRecord[]): Question | null {
  const misses = atKey.filter((r) => r.verdict !== 'correct' && r.question);
  if (!misses.length) return null;
  const miss = misses[misses.length - 1];
  const options = [String(miss.question), ...(miss.data?.variants ?? []).map(String)];
  const lastAsked = String(atKey[atKey.length - 1].question ?? '');
  const pick = options.find((o) => o !== lastAsked) ?? options[0];
  return {
    unit_id: unitOf(miss) as string,
    level: levelOf(miss),
    question: pick,
    reference_answer: miss.data?.reference || NO_REFERENCE,
    evidence: [...(miss.data?.evidence ?? [])],
    variants: options.filter((o) => o !== pick).slice(0, 2),
    from_pool: true,
  };
}

/** Every item with something to ask, soonest due first (review.items). */
export function items(records: ReviewRecord[], unitIds?: Set<string> | null): ReviewItem[] {
  const all = ordered(records);
  const out: (ReviewItem & { at: number })[] = [];
  for (const { unit, level, state } of fold(records).values()) {
    if (unitIds && !unitIds.has(unit)) continue;
    const question = phrasing(all.filter((r) => unitOf(r) === unit && levelOf(r) === level));
    if (question) out.push({ unit, level, state, question, at: parseTs(state.due) });
  }
  out.sort((a, b) => a.at - b.at || a.level - b.level || (a.unit < b.unit ? -1 : a.unit > b.unit ? 1 : 0));
  return out.map(({ at: _at, ...it }) => it);
}

/** The items due by `now` (all of them with `everything`), most overdue first (review.due). */
export function due(records: ReviewRecord[], now: Date = new Date(), unitIds?: Set<string> | null, everything = false): ReviewItem[] {
  return items(records, unitIds).filter((it) => everything || parseTs(it.state.due) <= now.getTime());
}

/** The item's state on one unit and level, or undefined (for "back in review on ..."). */
export function stateAt(records: ReviewRecord[], unit: string, level: number): ReviewState | undefined {
  return fold(records).get(keyOf(unit, level))?.state;
}
