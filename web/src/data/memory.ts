/**
 * The store in memory, seeded from fixture rows (udebt worker sql --dialect json). Records
 * and repositories added during a visit can be kept in sessionStorage, so a reload in
 * `vite preview` keeps a quiz's answers; nothing leaves the tab.
 */
import type { EvidenceRow } from '../core/quiz';
import type { Analysis, Repository } from '../core/specs';
import { normalRecord, type Fixture, type Job, type SessionQuestion, type UdebtStore } from './store';

interface Saved {
  records: EvidenceRow[];
  mine: number[];
}

export class MemoryStore implements UdebtStore {
  readonly live = false;
  private readonly repos: Repository[] = [];
  private readonly all: Analysis[] = [];
  private rows: EvidenceRow[] = [];
  private mine = new Set<number>();
  private nextRecord = 1;
  private nextSession = 1;
  readonly sessions = new Map<number, { repoId: number; sha: string; questions: SessionQuestion[]; finished: boolean }>();
  jobList: Job[] = [];

  constructor(
    fixture: Fixture,
    private readonly storage: Pick<Storage, 'getItem' | 'setItem'> | null = null,
    private readonly key = 'udebt.preview',
  ) {
    fixture.repositories.forEach((r, i) => this.repos.push({ id: r.id ?? i + 1, url: r.url, name: r.name, license: r.license ?? null, is_sample: !!r.is_sample }));
    fixture.analyses.forEach((a, i) => {
      const repo = this.repos.find((r) => r.url === a.repo_url);
      if (!repo) return;
      this.all.push({ id: i + 1, repo_id: repo.id, sha: a.sha, user_id: a.user_id ?? null, created_at: a.created_at ?? '2026-11-15T00:00:00.000Z', manifest: a.manifest, specs: a.specs });
    });
    this.restore();
  }

  private restore(): void {
    try {
      const raw = this.storage?.getItem(this.key);
      if (!raw) return;
      const saved = JSON.parse(raw) as Saved;
      this.rows = (saved.records ?? []).map(normalRecord);
      this.mine = new Set(saved.mine ?? []);
      this.nextRecord = this.rows.reduce((m, r) => Math.max(m, (r.id ?? 0) + 1), 1);
    } catch {
      /* a broken save starts afresh */
    }
  }

  private save(): void {
    try {
      this.storage?.setItem(this.key, JSON.stringify({ records: this.rows, mine: [...this.mine] } satisfies Saved));
    } catch {
      /* storage full or blocked: the visit still works */
    }
  }

  async repositories(): Promise<{ repos: Repository[]; mine: Set<number> }> {
    return { repos: this.repos.filter((r) => r.is_sample || this.mine.has(r.id)).map((r) => ({ ...r })), mine: new Set(this.mine) };
  }

  async findRepository(url: string): Promise<Repository | null> {
    const r = this.repos.find((x) => x.url === url);
    return r ? { ...r } : null;
  }

  async addMine(repoId: number): Promise<void> {
    this.mine.add(repoId);
    this.save();
  }

  async removeMine(repoId: number): Promise<void> {
    this.mine.delete(repoId);
    this.save();
  }

  async analyses(repoId: number): Promise<Analysis[]> {
    return this.all.filter((a) => a.repo_id === repoId).map((a) => structuredClone(a));
  }

  async records(repoId: number): Promise<EvidenceRow[]> {
    return this.rows.filter((r) => r.repo_id === repoId).map((r) => ({ ...r }));
  }

  async addRecords(rows: EvidenceRow[]): Promise<EvidenceRow[]> {
    const added = rows.map((r) => normalRecord({ ...r, id: this.nextRecord++ }));
    this.rows.push(...added);
    this.save();
    return added.map((r) => ({ ...r }));
  }

  async startSession(repoId: number, sha: string, questions: SessionQuestion[]): Promise<number | null> {
    const id = this.nextSession++;
    this.sessions.set(id, { repoId, sha, questions, finished: false });
    return id;
  }

  async finishSession(id: number): Promise<void> {
    const s = this.sessions.get(id);
    if (s) s.finished = true;
  }

  async jobs(): Promise<Job[]> {
    return [...this.jobList];
  }
}
