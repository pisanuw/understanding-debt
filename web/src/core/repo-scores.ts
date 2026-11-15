/**
 * A person's scores on one repository: every unit of the analysis on their own records, each
 * record's evidence faded by the share of the unit changed between the commit it was answered
 * at and the analysis's commit (specs.changed_since, from the worker; 0 at the same commit),
 * and the debt index over the top-level units, as `udebt serve` shows it.
 */
import type { LadderRecord } from './ladder';
import { ordered, sittings } from './ladder';
import { isSelfChecked, type EvidenceRow } from './quiz';
import { basisCounts, debtIndex, scoreAll, type Basis, type UnitScore } from './scoring';
import type { Analysis } from './specs';

export interface RepoScores {
  scores: UnitScore[];
  byId: Map<string, UnitScore>;
  index: number;
  basis: Record<Basis, number>;
  /** Units whose last answer was at a commit the analysis has no change share for. */
  unknownShare: string[];
  /**
   * Units whose understanding rests on self-checked answers (quiz.SELF_CHECKED): one in the
   * latest sitting at some level, or a sub-unit's that the parent rolled up, or the parent's a
   * sub-unit inherited. `selfCheckedIndex`: the index rests on one of the top-level units.
   */
  selfChecked: Set<string>;
  selfCheckedIndex: boolean;
}

/** The share of `unitId` changed since `sha`: 0 at the analysis's own commit, null when unknown. */
export function shareSince(analysis: Analysis, unitId: string, sha: string | null | undefined): number | null {
  if (!sha || sha === analysis.sha) return 0;
  const v = analysis.specs.changed_since?.[sha]?.[unitId];
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

export function repoScores(analysis: Analysis, records: EvidenceRow[]): RepoScores {
  const units = analysis.specs.units;
  const byUnit = new Map<string, EvidenceRow[]>();
  for (const r of records) byUnit.set(r.unit_id, [...(byUnit.get(r.unit_id) ?? []), r]);
  const unknownShare: string[] = [];
  const scores = scoreAll(
    units,
    (id) => (byUnit.get(id) ?? []) as LadderRecord[],
    (id, recs) => {
      const all = ordered(recs as EvidenceRow[]);
      const s = shareSince(analysis, id, all[all.length - 1]?.sha);
      if (s === null) unknownShare.push(id);
      return s;
    },
  );
  const top = new Set(units.filter((u) => !u.parent).map((u) => u.id));
  const byId = new Map(scores.map((s) => [s.unitId, s]));
  const own = new Set(units.filter((u) => [...sittings(byUnit.get(u.id) ?? []).values()].some((sit) => sit.some(isSelfChecked))).map((u) => u.id));
  const selfChecked = new Set<string>();
  for (const s of scores) {
    const kids = units.filter((c) => c.parent === s.unitId).map((c) => c.id);
    if ((s.source === 'quiz' && own.has(s.unitId)) || (s.source === 'children' && kids.some((k) => own.has(k))) || (s.source === 'parent' && s.parent !== null && own.has(s.parent))) selfChecked.add(s.unitId);
  }
  return {
    scores,
    byId,
    index: debtIndex(scores, top),
    basis: basisCounts(scores.filter((s) => top.has(s.unitId))),
    unknownShare,
    selfChecked,
    selfCheckedIndex: [...top].some((id) => selfChecked.has(id) && byId.get(id)?.scope === 'architectural'),
  };
}
