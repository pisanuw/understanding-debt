/**
 * Writes a graded sitting's records to app_udebt.records with the person's own token
 * (PostgREST; row level security keeps the rows theirs and fills user_id from auth.uid()),
 * and reads their earlier records on the sitting's units, which the review state stamped on
 * each new row is folded from (src/core/review.ts).
 */
import type { EvidenceRow } from '../../../src/core/quiz';
import type { ReviewRecord } from '../../../src/core/review';
import type { FnEnv } from './env';

/** The row as the table takes it: no id, no `unit` (the scoring's alias of unit_id). */
export function tableRow(r: EvidenceRow): Record<string, unknown> {
  const { id: _id, unit: _unit, ...rest } = r;
  return rest;
}

export async function insertRecords(rows: EvidenceRow[], token: string, env: FnEnv, doFetch: typeof fetch = fetch): Promise<EvidenceRow[] | null> {
  if (env.fakes || !env.supabaseUrl || !env.anonKey || !rows.length) return null;
  try {
    const res = await doFetch(`${env.supabaseUrl}/rest/v1/records`, {
      method: 'POST',
      headers: {
        apikey: env.anonKey,
        authorization: `Bearer ${token}`,
        'content-type': 'application/json',
        'content-profile': 'app_udebt',
        prefer: 'return=representation',
      },
      body: JSON.stringify(rows.map(tableRow)),
    });
    if (!res.ok) return null;
    const got: unknown = await res.json();
    return Array.isArray(got) ? (got as EvidenceRow[]).map((r) => ({ ...r, unit: r.unit_id })) : null;
  } catch {
    return null;
  }
}

/** A value inside a PostgREST `in.(...)` list: double-quoted, with quotes and backslashes escaped. */
export function inList(values: string[]): string {
  return `(${values.map((v) => `"${v.replace(/["\\]/g, (c) => `\\${c}`)}"`).join(',')})`;
}

/**
 * The person's earlier records on these units of the repository, oldest first (by time, then
 * as inserted), with what the review fold reads; null when they cannot be read (the fakes, no
 * Supabase, a refusal), and the page then records the sitting itself.
 */
export async function priorRecords(repoId: number, unitIds: string[], token: string, env: FnEnv, doFetch: typeof fetch = fetch): Promise<ReviewRecord[] | null> {
  if (env.fakes || !env.supabaseUrl || !env.anonKey) return null;
  const units = [...new Set(unitIds)];
  if (!units.length) return [];
  const q = new URLSearchParams({ select: 'ts,unit_id,level,verdict,confidence', repo_id: `eq.${repoId}`, unit_id: `in.${inList(units)}`, order: 'ts.asc,id.asc' });
  try {
    const res = await doFetch(`${env.supabaseUrl}/rest/v1/records?${q}`, {
      headers: { apikey: env.anonKey, authorization: `Bearer ${token}`, 'accept-profile': 'app_udebt' },
    });
    if (!res.ok) return null;
    const got: unknown = await res.json();
    if (!Array.isArray(got)) return null;
    return (got as ReviewRecord[]).map((r) => {
      const t = Date.parse(r.ts);
      return { ...r, ts: Number.isNaN(t) ? r.ts : new Date(t).toISOString() };
    });
  } catch {
    return null;
  }
}
