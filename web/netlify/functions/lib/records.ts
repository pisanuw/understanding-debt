/**
 * Writes a graded sitting's records to app_udebt.records with the person's own token
 * (PostgREST; row level security keeps the rows theirs and fills user_id from auth.uid()).
 */
import type { EvidenceRow } from '../../../src/core/quiz';
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
