/**
 * The quiz ladder's flow, ported from udebt/quiz.py and udebt/orientation.py: which units a
 * sitting asks, at which level, with which orientation shown first, a stored variant of a
 * question just missed before a new one, one fact per question, reveal on a miss at levels
 * 1-2, and the records a sitting leaves (contract `record`, metadata only: the person's answer
 * text and the grader's feedback are shown, never stored).
 */
import { REVEAL, architectural, compound, firstClause, levelOf, nextLevel, ordered, passed, sittings, type LadderRecord, type Verdict } from './ladder';
import { quizPriority, type UnitScore } from './scoring';
import type { ReviewState } from './review';
import type { SpecUnit } from './specs';

export interface Question {
  unit_id: string;
  level: number;
  question: string;
  variants: string[];
  reference_answer: string;
  evidence: string[];
  /** Asked again from a stored variant, without a model call. */
  from_pool?: boolean;
  rejected?: { question: string; why: string }[];
  trimmed?: boolean;
}

/** One record as the app keeps it (app_udebt.records); `unit` mirrors `unit_id` for the scoring. */
export interface EvidenceRow extends LadderRecord {
  id?: number;
  repo_id: number;
  sha: string | null;
  unit_id: string;
  level: number;
  verdict: Verdict;
  confidence: number;
  type: string;
  source: string;
  question: string;
  revealed: boolean;
  forced: boolean;
  /** The question's other phrasings, its reference and cited files; the review state after this answer (review.ts). */
  data: { variants?: string[]; reference?: string; evidence?: string[]; review?: ReviewState };
}

export interface Answered extends Question {
  answer: string;
  confidence: number;
  revealed?: boolean;
}

export interface Graded extends Answered {
  verdict: Verdict;
  feedback: string;
}

export interface Grade {
  index: number;
  verdict: Verdict;
  feedback: string;
}

export const REVEALED_VERDICTS: Verdict[] = ['unknown', 'wrong', 'partial'];
export const NO_REFERENCE = '(graded against the code)';
export const UNKNOWN_FEEDBACK = 'Marked as a known unknown. No misconception recorded.';
export const MISSING_FEEDBACK = 'Grader returned nothing for this.';

/** The question with one part: its own phrasing if that asks one fact, else a variant that does, else its first clause. */
export function oneFact(q: Question, level: number): Question {
  const phrasings = [String(q.question ?? ''), ...(q.variants ?? []).map(String)].filter((p) => p.trim());
  const rejected: { question: string; why: string }[] = [];
  let chosen: string | null = null;
  for (const text of phrasings) {
    const why = compound(text, level);
    if (why === null) {
      chosen = text;
      break;
    }
    rejected.push({ question: text, why });
  }
  let trimmed = false;
  if (chosen === null) {
    chosen = firstClause(phrasings[0] ?? '');
    trimmed = true;
  }
  const pick = chosen;
  const variants = phrasings.filter((p) => p !== pick && compound(p, level) === null);
  if (variants.length < 2) {
    for (const t of phrasings.map(firstClause)) if (t !== pick && !variants.includes(t)) variants.push(t);
  }
  const out: Question = { ...q, question: pick, variants: variants.slice(0, 2) };
  if (rejected.length) out.rejected = rejected;
  if (trimmed) out.trimmed = true;
  return out;
}

/** A miss at levels 1-2 shows the reference answer (Phases 3.3, 3.4). */
export function reveals(q: { level?: number | null }, verdict: string): boolean {
  return (REVEAL as readonly number[]).includes(levelOf(q)) && (REVEALED_VERDICTS as string[]).includes(verdict);
}

/** The reference answer to show, or '' when there is none to show. */
export function revealText(q: { reference_answer?: string }): string {
  const ref = String(q.reference_answer ?? '').split(/\s+/).filter(Boolean).join(' ');
  return !ref || ref === NO_REFERENCE ? '' : ref;
}

/** Grades merged with the answers in order: a blank answer is a known unknown, a missing grade wrong (quiz.grade). */
export function mergeGrades(answered: Answered[], grades: Grade[]): Graded[] {
  const byIndex = new Map(grades.map((g) => [Number(g.index), g]));
  let gi = 0;
  return answered.map((a) => {
    if (!a.answer.trim()) return { ...a, verdict: 'unknown', feedback: UNKNOWN_FEEDBACK };
    const g = byIndex.get(gi) ?? { index: gi, verdict: 'wrong' as Verdict, feedback: MISSING_FEEDBACK };
    gi += 1;
    const verdict = (['correct', 'partial', 'wrong', 'unknown'] as string[]).includes(g.verdict) ? g.verdict : 'wrong';
    return { ...a, verdict, feedback: String(g.feedback ?? '') };
  });
}

/** After grading, a wrong or partial answer at levels 1-2 is revealed (a blank one was when it was asked). */
export function markRevealed(results: Graded[]): Graded[] {
  return results.map((r) => (!r.revealed && reveals(r, r.verdict) && revealText(r) ? { ...r, revealed: true } : r));
}

/** unit id -> the level to ask it at (each unit's next ladder level). */
export function levelsFor(units: { id: string }[], records: LadderRecord[]): Map<string, number> {
  return new Map(units.map((u) => [u.id, nextLevel(records.filter((r) => r.unit === u.id))]));
}

/** Show the orientation before every level 1 question until level 1 is passed (orientation.wanted). */
export function orientationWanted(recordsOnUnit: LadderRecord[], level: number): boolean {
  if (level !== 1) return false;
  return !passed(sittings(architectural(recordsOnUnit)).get(1));
}

/** The questions at `level` missed and revealed since the last correct answer there (at most n, newest last). */
export function revealedOn(recordsOnUnit: EvidenceRow[], level: number, n = 3): string[] {
  const at = ordered(recordsOnUnit).filter((r) => levelOf(r) === level);
  let since = at;
  for (let i = at.length - 1; i >= 0; i -= 1) {
    if (at[i].verdict === 'correct') {
      since = at.slice(i + 1);
      break;
    }
  }
  const out = [...new Set(since.filter((r) => r.revealed && r.question).map((r) => String(r.question)))];
  return out.slice(-n);
}

/** A stored variant of the question last missed and revealed at `level`, not asked yet: no model call. */
export function revealedVariant(unitId: string, level: number, recordsOnUnit: EvidenceRow[]): Question | null {
  const asked = new Set(recordsOnUnit.map((r) => String(r.question)));
  const at = ordered(recordsOnUnit).filter((r) => levelOf(r) === level);
  for (let i = at.length - 1; i >= 0; i -= 1) {
    const r = at[i];
    if (r.verdict === 'correct') break;
    if (!r.revealed) continue;
    const variants = r.data?.variants ?? [];
    for (const v of variants) {
      if (!asked.has(v)) {
        return {
          unit_id: unitId,
          level,
          question: v,
          variants: variants.filter((x) => x !== v),
          reference_answer: r.data?.reference || NO_REFERENCE,
          evidence: [...(r.data?.evidence ?? [])],
          from_pool: true,
        };
      }
    }
  }
  return null;
}

/** The units a sitting asks: the named one, else the `n` top-level units with the most debt. */
export function pickUnits(units: SpecUnit[], scores: UnitScore[], n: number, only?: string | null): SpecUnit[] {
  if (only) {
    const u = units.find((x) => x.id === only);
    return u && (u.scope ?? 'architectural') === 'architectural' ? [u] : [];
  }
  const top = new Set(units.filter((u) => !u.parent).map((u) => u.id));
  const byId = new Map(units.map((u) => [u.id, u]));
  return quizPriority(scores.filter((s) => top.has(s.unitId)))
    .slice(0, n)
    .map((s) => byId.get(s.unitId) as SpecUnit);
}

/** A sitting's timestamp: now, or one second after the latest earlier record when that is not earlier (quiz._after). */
export function sittingTs(prior: { ts: string }[], now: Date = new Date()): string {
  const latest = prior.reduce((m, r) => Math.max(m, Date.parse(r.ts) || 0), 0);
  const t = now.getTime() > latest ? now.getTime() : Math.floor(latest / 1000) * 1000 + 1000;
  return new Date(t).toISOString();
}

/** The records a graded sitting leaves: one timestamp, metadata only. */
export function recordRows(results: Graded[], opts: { repoId: number; sha: string | null; ts: string; source?: string; forced?: boolean }): EvidenceRow[] {
  return results.map((r) => ({
    repo_id: opts.repoId,
    sha: opts.sha,
    unit_id: r.unit_id,
    unit: r.unit_id,
    ts: opts.ts,
    level: levelOf(r),
    verdict: r.verdict,
    confidence: r.answer.trim() ? Math.min(3, Math.max(1, Math.round(r.confidence || 2))) : 1,
    type: 'quiz',
    source: opts.source ?? 'quiz',
    question: r.question,
    revealed: !!r.revealed,
    forced: !!opts.forced,
    data: {
      ...(r.variants?.length ? { variants: [...r.variants] } : {}),
      ...(revealText(r) ? { reference: r.reference_answer } : {}),
      ...(r.evidence?.length ? { evidence: [...r.evidence] } : {}),
    },
  }));
}
