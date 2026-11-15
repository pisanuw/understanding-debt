/**
 * The app's data behind one interface (docs/platform.md section 5): SupabaseStore over the
 * app_udebt schema for the live site, MemoryStore over fixture rows for tests, `npm run dev`,
 * `vite preview` and the Playwright walkthrough, so none of them touches the network.
 */
import type { EvidenceRow } from '../core/quiz';
import type { Analysis, Repository } from '../core/specs';

export interface Job {
  id: number;
  url: string;
  sha: string | null;
  status: 'queued' | 'running' | 'done' | 'failed' | string;
  message: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface SessionQuestion {
  unit_id: string;
  level: number;
  question: string;
}

export interface UdebtStore {
  readonly live: boolean;
  /** Every repository the person sees: the samples and the ones they added. */
  repositories(): Promise<{ repos: Repository[]; mine: Set<number> }>;
  /** A repository the app already knows (any person may have added it), by its URL. */
  findRepository(url: string): Promise<Repository | null>;
  addMine(repoId: number): Promise<void>;
  removeMine(repoId: number): Promise<void>;
  /** Every analysis of a repository the person may read (pickAnalysis chooses one). */
  analyses(repoId: number): Promise<Analysis[]>;
  records(repoId: number): Promise<EvidenceRow[]>;
  addRecords(rows: EvidenceRow[]): Promise<EvidenceRow[]>;
  /** A quiz session as served (questions only, never reference answers); null when it could not be kept. */
  startSession(repoId: number, sha: string, questions: SessionQuestion[]): Promise<number | null>;
  finishSession(id: number): Promise<void>;
  jobs(): Promise<Job[]>;
}

/** A record as read back: ts in one format (so sittings compare), `unit` set for the scoring. */
export function normalRecord(r: EvidenceRow): EvidenceRow {
  const t = Date.parse(r.ts);
  return { ...r, ts: Number.isNaN(t) ? r.ts : new Date(t).toISOString(), unit: r.unit_id, data: r.data ?? {} };
}

/** A fixture: the rows `udebt worker sql --dialect json` writes for one or more analyses. */
export interface Fixture {
  repositories: (Omit<Repository, 'id'> & { id?: number })[];
  analyses: (Omit<Analysis, 'id' | 'repo_id' | 'created_at'> & { repo_url: string; created_at?: string })[];
}
