/**
 * The live store: the app_udebt schema through supabase-js with the signed-in person's
 * session. Row level security does the scoping: repositories and analyses are shared
 * (public repositories only), my_repositories, records, quiz_sessions and jobs are the
 * person's own, and the database fills user_id from auth.uid().
 */
import type { EvidenceRow } from '../core/quiz';
import type { Analysis, Repository } from '../core/specs';
import { normalRecord, type Job, type SessionQuestion, type UdebtStore } from './store';

interface Result<T> {
  data: T | null;
  error: { message: string } | null;
}

/** The part of the supabase-js query builder this store uses (a fake in tests). */
export interface Query<T = unknown> extends PromiseLike<Result<T>> {
  select(cols?: string): Query<T>;
  eq(col: string, v: unknown): Query<T>;
  order(col: string, opts?: { ascending?: boolean }): Query<T>;
  limit(n: number): Query<T>;
  insert(rows: unknown): Query<T>;
  update(values: unknown): Query<T>;
  delete(): Query<T>;
  single(): Query<T>;
}

export interface TableClient {
  from(table: string): Query;
}

function check<T>(table: string, r: Result<unknown>): T {
  if (r.error) throw new Error(`${table}: ${r.error.message}`);
  return r.data as T;
}

/** The row as the table takes it: no id, no `unit` (the scoring's alias of unit_id). */
function tableRow(r: EvidenceRow): Record<string, unknown> {
  const { id: _id, unit: _unit, ...rest } = r;
  return rest;
}

export class SupabaseStore implements UdebtStore {
  readonly live = true;
  constructor(private readonly client: TableClient) {}

  async repositories(): Promise<{ repos: Repository[]; mine: Set<number> }> {
    const mine = new Set(check<{ repo_id: number }[]>('my_repositories', await this.client.from('my_repositories').select('repo_id')).map((r) => Number(r.repo_id)));
    const all = check<Repository[]>('repositories', await this.client.from('repositories').select('id,url,name,license,is_sample').order('id'));
    return { repos: all.filter((r) => r.is_sample || mine.has(Number(r.id))), mine };
  }

  async findRepository(url: string): Promise<Repository | null> {
    const rows = check<Repository[]>('repositories', await this.client.from('repositories').select('id,url,name,license,is_sample').eq('url', url).limit(1));
    return rows[0] ?? null;
  }

  async addMine(repoId: number): Promise<void> {
    const r = await this.client.from('my_repositories').insert({ repo_id: repoId });
    if (r.error && !/duplicate key/i.test(r.error.message)) throw new Error(`my_repositories: ${r.error.message}`);
  }

  async removeMine(repoId: number): Promise<void> {
    check('my_repositories', await this.client.from('my_repositories').delete().eq('repo_id', repoId));
  }

  async analyses(repoId: number): Promise<Analysis[]> {
    return check<Analysis[]>(
      'analyses',
      await this.client.from('analyses').select('id,repo_id,sha,user_id,created_at,manifest,specs').eq('repo_id', repoId).order('created_at', { ascending: false }).limit(20),
    );
  }

  async records(repoId: number): Promise<EvidenceRow[]> {
    // By time, then as inserted: the review fold (review.ts) reads one sitting's answers on a key in order.
    return check<EvidenceRow[]>('records', await this.client.from('records').select('*').eq('repo_id', repoId).order('ts').order('id')).map(normalRecord);
  }

  async addRecords(rows: EvidenceRow[]): Promise<EvidenceRow[]> {
    if (!rows.length) return [];
    return check<EvidenceRow[]>('records', await this.client.from('records').insert(rows.map(tableRow)).select()).map(normalRecord);
  }

  async startSession(repoId: number, sha: string, questions: SessionQuestion[]): Promise<number | null> {
    const r = (await this.client.from('quiz_sessions').insert({ repo_id: repoId, sha, questions }).select('id').single()) as Result<{ id: number }>;
    return r.error || !r.data ? null : Number(r.data.id);
  }

  async finishSession(id: number): Promise<void> {
    await this.client.from('quiz_sessions').update({ finished_at: new Date().toISOString() }).eq('id', id);
  }

  async jobs(): Promise<Job[]> {
    return check<Job[]>('jobs', await this.client.from('jobs').select('id,url,sha,status,message,created_at,finished_at').order('created_at', { ascending: false }).limit(10));
  }
}
