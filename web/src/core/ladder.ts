/**
 * The question ladder, ported from udebt/ladder.py (Phase 3.2). A quiz starts at what a unit
 * is for and descends into implementation detail only once the developer has shown the
 * levels above:
 *
 *   1 purpose and placement, 2 decision and alternatives, 3 consequence across units (the
 *   architecture, which drives the score); 4 behaviour under failure, 5 implementation
 *   (optional depth).
 *
 * A *sitting* is the answers one quiz gave on a unit at one level; they share a timestamp.
 * A level is passed when its latest sitting is all correct; depth is the highest level n with
 * every level 1..n passed; the next question is at depth + 1, except that a confidently wrong
 * answer at n >= 2 in the latest sitting drops it to n - 1. web/test/fixtures/parity.json
 * holds the Python answers this port is checked against.
 */
import parts from './prompt-parts.json';

export const TOP = 5;
export const ARCHITECTURAL = [1, 2, 3] as const;
export const DEPTH = [4, 5] as const;
/** The level of a record written before the ladder. */
export const LEGACY = 5;
/** A miss (unknown, wrong, partial) at these levels shows the reference answer. */
export const REVEAL = [1, 2] as const;

export type Verdict = 'correct' | 'partial' | 'wrong' | 'unknown';

/** One graded answer, as the scoring reads it (the CLI's record shape, contract `record`). */
export interface LadderRecord {
  ts: string;
  unit: string;
  verdict: Verdict | string;
  level?: number | null;
  confidence?: number;
  revealed?: boolean;
  question?: string | null;
}

export const LEVEL_NAMES: Record<number, string> = Object.fromEntries(
  Object.entries(parts.levels).map(([k, v]) => [Number(k), v]),
);

/** "level 1 of 5: purpose and placement". */
export function label(n: number): string {
  return `level ${n} of ${TOP}: ${LEVEL_NAMES[n]}`;
}

/** The level a record was asked at; a record from before the ladder is level 5. */
export function levelOf(r: { level?: unknown }): number {
  if (!('level' in r) || r.level === undefined) return LEGACY;
  const n = typeof r.level === 'number' ? Math.trunc(r.level) : typeof r.level === 'string' && /^-?\d+$/.test(r.level.trim()) ? Number(r.level) : NaN;
  if (!Number.isFinite(n)) return LEGACY;
  return Math.min(TOP, Math.max(1, n));
}

/** Records in time order; records sharing a timestamp keep their order (a stable sort). */
export function ordered<T extends { ts?: string }>(records: T[]): T[] {
  return [...records].sort((a, b) => {
    const x = a.ts ?? '';
    const y = b.ts ?? '';
    return x < y ? -1 : x > y ? 1 : 0;
  });
}

/** Level -> its latest sitting: the answers at that level sharing the latest timestamp among them. */
export function sittings<T extends LadderRecord>(records: T[]): Map<number, T[]> {
  const byLevel = new Map<number, T[]>();
  for (const r of ordered(records)) {
    const n = levelOf(r);
    const cur = byLevel.get(n);
    if (cur && cur[cur.length - 1].ts === r.ts) cur.push(r);
    else byLevel.set(n, [r]);
  }
  return byLevel;
}

export function passed(sitting: LadderRecord[] | undefined): boolean {
  return !!sitting && sitting.length > 0 && sitting.every((r) => r.verdict === 'correct');
}

export function confidentlyWrong(r: LadderRecord): boolean {
  return r.verdict === 'wrong' && Number(r.confidence ?? 1) >= 3;
}

/** The answers at levels 1-3 (records that carry a level). */
export function architectural<T extends LadderRecord>(records: T[]): T[] {
  return records.filter((r) => r.level !== undefined && r.level !== null && (ARCHITECTURAL as readonly number[]).includes(levelOf(r)));
}

/** The highest level n with every level 1..n passed; null without any record. */
export function depth(records: LadderRecord[]): number | null {
  if (!records.length) return null;
  const latest = sittings(records);
  let n = 0;
  while (n < TOP && passed(latest.get(n + 1))) n += 1;
  return n;
}

/** The level the next question on this unit is asked at. */
export function nextLevel(records: LadderRecord[]): number {
  if (!records.length) return 1;
  const nxt = Math.min(TOP, (depth(records) ?? 0) + 1);
  const all = ordered(records);
  const last = all[all.length - 1];
  const n = levelOf(last);
  const sitting = all.filter((r) => r.ts === last.ts && levelOf(r) === n);
  if (n >= 2 && sitting.some(confidentlyWrong)) return Math.min(nxt, n - 1);
  return nxt;
}

// -- one question, one fact ---------------------------------------------------------------

const JOINED = /,?\s+and\s+(?:what|which|how|why|who|where|when|whether)\b|;\s|\?\s+(?=\S)/i;
const INVENTORY = /\blist\b|\benumerate\b|\bwhich other (?:parts|components|units|modules|pieces)\b|\bname (?:the|all|every|each)\b/i;

function squash(text: string): string {
  return String(text).split(/\s+/).filter(Boolean).join(' ');
}

function stripChars(s: string, chars: string): string {
  let a = 0;
  let b = s.length;
  while (a < b && chars.includes(s[a])) a += 1;
  while (b > a && chars.includes(s[b - 1])) b -= 1;
  return s.slice(a, b);
}

/** Why a question asks more than one thing, or null when it asks one (ladder.compound). */
export function compound(question: string, level = 1): string | null {
  const q = squash(question);
  if ((q.match(/\?/g) ?? []).length > 1) return 'more than one question mark';
  const m = JOINED.exec(q);
  if (m && /^(and|,|;)/.test(m[0].trim())) return `two questions joined by '${stripChars(m[0].trim(), ' ,')}'`;
  const qi = q.indexOf('?');
  if (qi >= 0 && stripChars(q.slice(qi + 1), ' )"\'')) return 'a second sentence after the question';
  if ((ARCHITECTURAL as readonly number[]).includes(level)) {
    const inv = INVENTORY.exec(q);
    if (inv) return `an inventory ('${inv[0]}')`;
  }
  return null;
}

/** The question trimmed to its first part, ending in a question mark (ladder.first_clause). */
export function firstClause(question: string): string {
  const q = squash(question);
  let cut = q.length;
  const i = q.indexOf('?');
  if (i >= 0) cut = i;
  const m = JOINED.exec(q);
  if (m && m.index < cut) cut = m.index;
  let head = q.slice(0, cut);
  head = head.replace(/[ ,;:.]+$/, '');
  return (head || q.replace(/\?+$/, '')) + '?';
}
