/**
 * Advance quizzes (HOSTING.md decision 10): questions written ahead of time by `udebt worker
 * quizzes` (udebt/hosted/advance.py, with the hosted quiz's own prompt) for each unit at ladder
 * levels 1-3, each with its reference answer and, at level 1, the orientation shown before it.
 * Whoever has no model to hand (a visitor who is not signed in and has no key of their own, or
 * a signed-in person with model features off) answers one, reads the reference and marks their
 * own answer: a `self-checked` record (quiz.ts SELF_CHECKED), with no model call.
 *
 * Which question: a missed one comes back in other words first (quiz.revealedVariant, as in a
 * model-written quiz: every self-checked answer saw its reference), else the first one at that
 * unit and level not asked yet, else the one asked longest ago. Levels 4-5 are never asked in
 * advance: they need code reading and real grading.
 */
import { levelOf, nextLevel, ordered } from './ladder';
import { revealText, revealedVariant, type EvidenceRow, type Question } from './quiz';
import { quizPriority, type UnitScore } from './scoring';
import type { Orientation, SpecUnit } from './specs';

export const ADVANCE_FORMAT = 'udebt-advance-quizzes';
export const ADVANCE_VERSION = 1;
export const ADVANCE_LEVELS = [1, 2, 3] as const;

export interface AdvanceQuestion extends Question {
  /** At level 1: the orientation the question was written to follow. */
  orientation?: Orientation | null;
}

export interface AdvanceFile {
  repo: string;
  sha: string;
  generated_at?: string | null;
  questions: AdvanceQuestion[];
}

const strs = (v: unknown, n: number): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && !!x.trim()).slice(0, n) : []);

function orientation(v: unknown): Orientation | null {
  const o = (v && typeof v === 'object' ? v : null) as Record<string, unknown> | null;
  if (!o || typeof o.purpose !== 'string' || !o.purpose) return null;
  const where = (Array.isArray(o.where) ? o.where : [])
    .map((w: unknown) => (w && typeof w === 'object' ? (w as Record<string, unknown>) : {}))
    .filter((w) => typeof w.path === 'string')
    .map((w) => ({ path: w.path as string, what: typeof w.what === 'string' ? w.what : '' }));
  return { purpose: o.purpose, decision: typeof o.decision === 'string' ? o.decision : '', where, shaped_by: typeof o.shaped_by === 'string' ? o.shaped_by : '', ...(o.fallback ? { fallback: true } : {}) };
}

/** The file's questions, each checked (levels 1-3, a reference answer to check against); null for another format or a newer version. */
export function readAdvance(json: unknown): AdvanceFile | null {
  const d = (json && typeof json === 'object' ? json : {}) as Record<string, unknown>;
  if (d.format !== ADVANCE_FORMAT || typeof d.version !== 'number' || d.version > ADVANCE_VERSION || typeof d.repo !== 'string' || typeof d.sha !== 'string' || !Array.isArray(d.questions)) return null;
  const questions: AdvanceQuestion[] = [];
  for (const raw of d.questions as unknown[]) {
    const q = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
    const level = levelOf({ level: q.level });
    if (typeof q.unit_id !== 'string' || !q.unit_id || typeof q.question !== 'string' || !q.question.trim() || !(ADVANCE_LEVELS as readonly number[]).includes(level) || q.level !== level) continue;
    const reference = typeof q.reference_answer === 'string' ? q.reference_answer : '';
    if (!revealText({ reference_answer: reference })) continue;
    const o = level === 1 ? orientation(q.orientation) : null;
    questions.push({ unit_id: q.unit_id, level, question: q.question, variants: strs(q.variants, 2), reference_answer: reference, evidence: strs(q.evidence, 6), from_pool: true, ...(o ? { orientation: o } : {}) });
  }
  return { repo: d.repo, sha: d.sha, generated_at: typeof d.generated_at === 'string' ? d.generated_at : null, questions };
}

/** The file's questions on one unit at one level, in the file's order. */
export function poolOf(file: AdvanceFile, unitId: string, level: number): AdvanceQuestion[] {
  return file.questions.filter((q) => q.unit_id === unitId && q.level === level);
}

/** The question to ask on a unit at a level, or null when the file has none there. */
export function pickAdvance(file: AdvanceFile, unitId: string, level: number, recordsOnUnit: EvidenceRow[]): AdvanceQuestion | null {
  const pool = poolOf(file, unitId, level);
  if (!pool.length) return null;
  const again = revealedVariant(unitId, level, recordsOnUnit);
  if (again) return { ...again, orientation: pool[0].orientation ?? null };
  const last = new Map<string, string>();
  for (const r of ordered(recordsOnUnit)) last.set(String(r.question), r.ts);
  const fresh = pool.find((q) => ![q.question, ...q.variants].some((p) => last.has(p)));
  if (fresh) return fresh;
  const askedAt = (q: AdvanceQuestion) => [q.question, ...q.variants].map((p) => last.get(p) ?? '').sort().pop() ?? '';
  return [...pool].sort((a, b) => (askedAt(a) < askedAt(b) ? -1 : askedAt(a) > askedAt(b) ? 1 : 0))[0];
}

export interface AdvancePick {
  unit: SpecUnit;
  level: number;
  question: AdvanceQuestion;
}

/**
 * The units an advance sitting asks: the named one, else the top-level units with the most debt
 * first; each at its next ladder level, skipping a unit past level 3 or with no question at its
 * level (`skipped` says which, for the page to explain).
 */
export function advancePicks(file: AdvanceFile, units: SpecUnit[], scores: UnitScore[], records: EvidenceRow[], n: number, only?: string | null): { picks: AdvancePick[]; skipped: { unit: SpecUnit; level: number }[] } {
  const byId = new Map(units.map((u) => [u.id, u]));
  const arch = (u: SpecUnit | undefined) => !!u && (u.scope ?? 'architectural') === 'architectural';
  const top = new Set(units.filter((u) => !u.parent).map((u) => u.id));
  const order = only ? (arch(byId.get(only)) ? [byId.get(only) as SpecUnit] : []) : quizPriority(scores.filter((s) => top.has(s.unitId))).map((s) => byId.get(s.unitId) as SpecUnit);
  const picks: AdvancePick[] = [];
  const skipped: { unit: SpecUnit; level: number }[] = [];
  for (const u of order) {
    if (picks.length >= n) break;
    const on = records.filter((r) => r.unit_id === u.id);
    const level = nextLevel(on);
    const q = (ADVANCE_LEVELS as readonly number[]).includes(level) ? pickAdvance(file, u.id, level, on) : null;
    if (q) picks.push({ unit: u, level, question: q });
    else skipped.push({ unit: u, level });
  }
  return { picks, skipped };
}
