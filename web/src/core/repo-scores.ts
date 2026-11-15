/**
 * A person's scores on one repository: every unit of the analysis on their own records, each
 * record's evidence faded by the share of the unit changed between the commit it was answered
 * at and the analysis's commit (specs.changed_since, from the worker; 0 at the same commit),
 * and the debt index over the top-level units, as `udebt serve` shows it.
 */
import type { LadderRecord } from './ladder';
import { ordered } from './ladder';
import type { EvidenceRow } from './quiz';
import { basisCounts, debtIndex, scoreAll, type Basis, type UnitScore } from './scoring';
import type { Analysis } from './specs';

export interface RepoScores {
  scores: UnitScore[];
  byId: Map<string, UnitScore>;
  index: number;
  basis: Record<Basis, number>;
  /** Units whose last answer was at a commit the analysis has no change share for. */
  unknownShare: string[];
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
  return {
    scores,
    byId: new Map(scores.map((s) => [s.unitId, s])),
    index: debtIndex(scores, top),
    basis: basisCounts(scores.filter((s) => top.has(s.unitId))),
    unknownShare,
  };
}
