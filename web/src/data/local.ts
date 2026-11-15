/**
 * The store of a visitor who is not signed in (HOSTING.md decision 10): the samples only, read
 * from the site's static JSON (no grants for anonymous visitors in app schemas), and their
 * answers kept in this browser's localStorage, one key per sample. It implements the same
 * UdebtStore as the signed-in stores, so debt, the review queue and the quiz ladder work from it
 * exactly as they do from the server. Every storage access is wrapped: with storage blocked or
 * full, the visit works in memory. Nothing here writes to Supabase or calls a model.
 *
 * At sign-in the page offers once to copy the answers given here into the account (ui/screens/
 * offer.ts): `pending` lists those not yet decided on, and `decide` marks them copied, kept
 * separate or discarded, so the same answers are never offered twice.
 */
import { readAdvance, type AdvanceFile } from '../core/advance';
import type { EvidenceRow } from '../core/quiz';
import { readIndex, type SampleEntry } from '../core/samples';
import type { Analysis, Repository } from '../core/specs';
import { normalRecord, type Fixture, type Job, type SessionQuestion, type UdebtStore } from './store';

/** Where the samples' files come from: the site (StaticSamples) or the preview's fixture rows. */
export interface SampleSource {
  entries(): Promise<SampleEntry[]>;
  rows(entry: SampleEntry): Promise<Fixture | null>;
  quizzes(entry: SampleEntry): Promise<unknown | null>;
}

/** The site's public/samples/ files (`python -m udebt.hosted.static_samples`), each fetched once. */
export class StaticSamples implements SampleSource {
  private index: Promise<SampleEntry[]> | null = null;
  private readonly files = new Map<string, Promise<unknown | null>>();

  constructor(
    private readonly base = 'samples/',
    private readonly doFetch: typeof fetch = (...a) => fetch(...a),
  ) {}

  private get(name: string): Promise<unknown | null> {
    let hit = this.files.get(name);
    if (!hit) {
      hit = this.doFetch(`${this.base}${name}`)
        .then((r) => (r.ok ? (r.json() as Promise<unknown>) : null))
        .catch(() => null);
      this.files.set(name, hit);
    }
    return hit;
  }

  entries(): Promise<SampleEntry[]> {
    this.index ??= this.get('index.json').then(readIndex);
    return this.index;
  }

  async rows(entry: SampleEntry): Promise<Fixture | null> {
    return entry.analysis ? ((await this.get(entry.analysis)) as Fixture | null) : null;
  }

  async quizzes(entry: SampleEntry): Promise<unknown | null> {
    return entry.quizzes ? this.get(entry.quizzes) : null;
  }
}

/** The preview's samples: the fixture's shared analyses, with the advance quizzes the dev server serves. */
export class FixtureSamples implements SampleSource {
  constructor(
    private readonly fixture: Fixture,
    private readonly quizFiles: unknown[] = [],
  ) {}

  async entries(): Promise<SampleEntry[]> {
    const out: SampleEntry[] = [];
    for (const r of this.fixture.repositories) {
      const a = this.fixture.analyses.find((x) => x.repo_url === r.url && !x.user_id);
      if (!r.is_sample || !a) continue;
      out.push({ name: r.name, url: r.url, license: r.license ?? null, sha: a.sha, analysis: `${r.name}.json`, quizzes: this.find(r.url, a.sha) ? `${r.name}.quizzes.json` : null });
    }
    return out;
  }

  private find(url: string, sha: string): unknown | null {
    return this.quizFiles.find((q) => !!q && typeof q === 'object' && (q as { repo?: unknown }).repo === url && (q as { sha?: unknown }).sha === sha) ?? null;
  }

  async rows(entry: SampleEntry): Promise<Fixture | null> {
    const repo = this.fixture.repositories.find((r) => r.url === entry.url);
    const a = this.fixture.analyses.find((x) => x.repo_url === entry.url && x.sha === entry.sha && !x.user_id);
    return repo && a ? { repositories: [repo], analyses: [a] } : null;
  }

  async quizzes(entry: SampleEntry): Promise<unknown | null> {
    return this.find(entry.url, entry.sha);
  }
}

/** The advance quizzes for a repository at a commit, from the samples' files; null when none were written. */
export class AdvanceQuizzes {
  private readonly cache = new Map<string, Promise<AdvanceFile | null>>();
  constructor(private readonly source: SampleSource) {}

  for(repoUrl: string, sha: string): Promise<AdvanceFile | null> {
    const key = `${repoUrl}@${sha}`;
    let hit = this.cache.get(key);
    if (!hit) {
      hit = this.source
        .entries()
        .then(async (es) => {
          const e = es.find((x) => x.url === repoUrl && x.sha === sha);
          const f = e ? readAdvance(await this.source.quizzes(e)) : null;
          return f && f.repo === repoUrl && f.sha === sha ? f : null;
        })
        .catch(() => null);
      this.cache.set(key, hit);
    }
    return hit;
  }
}

type Storageish = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

/** Keys of the answers kept per sample (by name; the version is the format's). */
export const LOCAL_PREFIX = 'udebt.local.v1.';

interface Kept {
  records: EvidenceRow[];
  /** The highest local record id offered at a sign-in and decided on (copied, kept or discarded). */
  decided: number;
}

export interface Pending {
  repo: Repository;
  records: EvidenceRow[];
}

export class LocalStore implements UdebtStore {
  readonly live = false;
  private readonly memory = new Map<string, Kept>();
  private readonly analysesCache = new Map<string, Promise<Analysis[]>>();

  constructor(
    readonly source: SampleSource,
    private readonly storage: Storageish | null = null,
  ) {}

  private load(name: string): Kept {
    const hit = this.memory.get(name);
    if (hit) return hit;
    let kept: Kept = { records: [], decided: 0 };
    try {
      const raw = this.storage?.getItem(LOCAL_PREFIX + name);
      if (raw) {
        const v = JSON.parse(raw) as Partial<Kept>;
        kept = { records: (Array.isArray(v.records) ? v.records : []).map(normalRecord), decided: Number(v.decided) || 0 };
      }
    } catch {
      /* blocked storage or a broken save: start afresh, in memory */
    }
    this.memory.set(name, kept);
    return kept;
  }

  private save(name: string, kept: Kept): void {
    this.memory.set(name, kept);
    try {
      if (kept.records.length || kept.decided) this.storage?.setItem(LOCAL_PREFIX + name, JSON.stringify(kept));
      else this.storage?.removeItem(LOCAL_PREFIX + name);
    } catch {
      /* storage full or blocked: this visit keeps them in memory */
    }
  }

  private async samples(): Promise<{ entry: SampleEntry; repo: Repository }[]> {
    const es = await this.source.entries();
    return es.map((e, i) => ({ entry: e, repo: { id: i + 1, url: e.url, name: e.name, license: e.license, is_sample: true } }));
  }

  private async entryOf(repoId: number): Promise<SampleEntry | null> {
    return (await this.samples()).find((s) => s.repo.id === repoId)?.entry ?? null;
  }

  async repositories(): Promise<{ repos: Repository[]; mine: Set<number> }> {
    return { repos: (await this.samples()).map((s) => ({ ...s.repo })), mine: new Set() };
  }

  async findRepository(url: string): Promise<Repository | null> {
    const s = (await this.samples()).find((x) => x.repo.url === url);
    return s ? { ...s.repo } : null;
  }

  async addMine(): Promise<void> {
    throw new Error('Sign in to add a repository.');
  }

  async removeMine(): Promise<void> {
    throw new Error('Sign in to keep a list of repositories.');
  }

  async analyses(repoId: number): Promise<Analysis[]> {
    const e = await this.entryOf(repoId);
    if (!e) return [];
    let hit = this.analysesCache.get(e.name);
    if (!hit) {
      hit = this.source.rows(e).then((f) =>
        (f?.analyses ?? [])
          .filter((a) => a.repo_url === e.url && a.sha === e.sha)
          .map((a, i) => ({ id: i + 1, repo_id: repoId, sha: a.sha, user_id: null, created_at: a.created_at ?? '2026-11-15T00:00:00.000Z', manifest: a.manifest, specs: a.specs })),
      );
      this.analysesCache.set(e.name, hit);
    }
    return (await hit).map((a) => structuredClone(a));
  }

  async records(repoId: number): Promise<EvidenceRow[]> {
    const e = await this.entryOf(repoId);
    return e ? this.load(e.name).records.map((r) => ({ ...r, repo_id: repoId })) : [];
  }

  async addRecords(rows: EvidenceRow[]): Promise<EvidenceRow[]> {
    const out: EvidenceRow[] = [];
    for (const r of rows) {
      const e = await this.entryOf(r.repo_id);
      if (!e) throw new Error('Not one of the samples: sign in to keep answers on other repositories.');
      const kept = this.load(e.name);
      const next = kept.records.reduce((m, x) => Math.max(m, x.id ?? 0), kept.decided) + 1;
      const row = normalRecord({ ...r, id: next });
      this.save(e.name, { ...kept, records: [...kept.records, row] });
      out.push({ ...row });
    }
    return out;
  }

  async startSession(_repoId: number, _sha: string, _questions: SessionQuestion[]): Promise<number | null> {
    return null;
  }

  async finishSession(): Promise<void> {}

  async jobs(): Promise<Job[]> {
    return [];
  }

  /** The answers given here and not yet decided on at a sign-in, per sample. */
  async pending(): Promise<Pending[]> {
    const out: Pending[] = [];
    for (const { entry, repo } of await this.samples()) {
      const kept = this.load(entry.name);
      const records = kept.records.filter((r) => (r.id ?? 0) > kept.decided).map((r) => ({ ...r, repo_id: repo.id }));
      if (records.length) out.push({ repo, records });
    }
    return out;
  }

  /** Marks a sample's pending answers decided on; with `discard`, deletes every answer kept for it. */
  async decide(repoId: number, discard = false): Promise<void> {
    const e = await this.entryOf(repoId);
    if (!e) return;
    const kept = this.load(e.name);
    const top = kept.records.reduce((m, r) => Math.max(m, r.id ?? 0), kept.decided);
    this.save(e.name, { records: discard ? [] : kept.records, decided: top });
  }

  /** Deletes every answer this browser keeps (Settings, "Delete the answers kept in this browser"). */
  async forgetAll(): Promise<number> {
    let n = 0;
    for (const { entry } of await this.samples()) {
      const kept = this.load(entry.name);
      n += kept.records.length;
      this.save(entry.name, { records: [], decided: 0 });
    }
    return n;
  }
}
