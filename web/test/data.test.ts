import { describe, expect, it } from 'vitest';
import fixture from '../fixtures/digitalme.json';
import { MemoryStore } from '../src/data/memory';
import { normalRecord, type Fixture } from '../src/data/store';
import { SupabaseStore, type Query, type TableClient } from '../src/data/supabase-store';
import { memoryStorage } from '../src/lib/llm';
import type { EvidenceRow } from '../src/core/quiz';

const SHA = 'f93102b121f1da9055c148e8eccde275ccf18236';
const row = (over: Partial<EvidenceRow> = {}): EvidenceRow => ({ repo_id: 1, sha: SHA, unit_id: 'chat-flow', unit: 'chat-flow', ts: '2026-11-15T10:00:00+00:00', level: 1, verdict: 'correct', confidence: 2, type: 'quiz', source: 'quiz', question: 'Q?', revealed: false, forced: false, data: {}, ...over });

describe('the in-memory store', () => {
  it('seeds from fixture rows and keeps records in session storage', async () => {
    const storage = memoryStorage();
    const s = new MemoryStore(fixture as unknown as Fixture, storage);
    const { repos } = await s.repositories();
    expect(repos.map((r) => [r.id, r.name, r.is_sample, r.license])).toEqual([[1, 'digitalme', true, 'MIT']]);
    expect((await s.analyses(1))[0].sha).toBe(SHA);
    expect(await s.findRepository('https://github.com/pisanuw/digitalme')).toMatchObject({ id: 1 });
    expect(await s.findRepository('https://github.com/x/y')).toBeNull();
    const added = await s.addRecords([row()]);
    expect(added[0]).toMatchObject({ id: 1, ts: '2026-11-15T10:00:00.000Z', unit: 'chat-flow' });
    const again = new MemoryStore(fixture as unknown as Fixture, storage);
    expect(await again.records(1)).toHaveLength(1);
    await again.addMine(1);
    expect((await new MemoryStore(fixture as unknown as Fixture, storage).repositories()).mine.has(1)).toBe(true);
    await again.removeMine(1);
    const id = await again.startSession(1, SHA, [{ unit_id: 'a', level: 1, question: 'q' }]);
    await again.finishSession(id as number);
    expect(again.sessions.get(id as number)?.finished).toBe(true);
    expect(await again.jobs()).toEqual([]);
  });

  it('starts afresh from a broken save and skips analyses of unknown repositories', async () => {
    const storage = memoryStorage();
    storage.setItem('udebt.preview', '{not json');
    const f = { repositories: [{ url: 'https://github.com/a/b', name: 'b', license: null, is_sample: false }], analyses: [{ ...fixture.analyses[0], repo_url: 'https://github.com/x/y' }] } as unknown as Fixture;
    const s = new MemoryStore(f, storage);
    expect(await s.records(1)).toEqual([]);
    expect((await s.repositories()).repos).toEqual([]);
    expect(await s.analyses(1)).toEqual([]);
    const blocked = new MemoryStore(f, { getItem: () => null, setItem: () => { throw new Error('full'); } });
    await expect(blocked.addRecords([row()])).resolves.toHaveLength(1);
  });

  it('normalises timestamps so one sitting compares equal', () => {
    expect(normalRecord(row({ ts: '2026-11-15T10:00:00Z' })).ts).toBe(normalRecord(row({ ts: '2026-11-15T10:00:00.000+00:00' })).ts);
    expect(normalRecord(row({ ts: 'garbage' })).ts).toBe('garbage');
  });
});

/** A fake of the supabase-js query builder: records each call, answers from a table of results. */
function fakeClient(results: Record<string, unknown>, log: string[] = []): TableClient {
  return {
    from(table: string) {
      const steps: string[] = [table];
      const q: Query = {
        select: (c) => (steps.push(`select(${c ?? ''})`), q),
        eq: (c, v) => (steps.push(`eq(${c},${String(v)})`), q),
        order: (c) => (steps.push(`order(${c})`), q),
        limit: (n) => (steps.push(`limit(${n})`), q),
        insert: (rows) => (steps.push(`insert(${JSON.stringify(rows)})`), q),
        update: (v) => (steps.push(`update(${Object.keys(v as object).join(',')})`), q),
        delete: () => (steps.push('delete'), q),
        single: () => (steps.push('single'), q),
        then: (ok, bad) => {
          log.push(steps.join('.'));
          const key = Object.keys(results).find((k) => steps.join('.').startsWith(k));
          const v = key ? results[key] : { data: [], error: null };
          return Promise.resolve(v as never).then(ok, bad);
        },
      };
      return q;
    },
  };
}

describe('the Supabase store', () => {
  it('lists the samples and my repositories, and reads analyses and records', async () => {
    const log: string[] = [];
    const s = new SupabaseStore(
      fakeClient(
        {
          my_repositories: { data: [{ repo_id: 2 }], error: null },
          repositories: { data: [{ id: 1, url: 'u1', name: 'a', license: null, is_sample: true }, { id: 2, url: 'u2', name: 'b', license: null, is_sample: false }, { id: 3, url: 'u3', name: 'c', license: null, is_sample: false }], error: null },
          analyses: { data: [{ id: 5 }], error: null },
          records: { data: [row({ ts: '2026-11-15T10:00:00+00:00' })], error: null },
          jobs: { data: [{ id: 1, status: 'done' }], error: null },
        },
        log,
      ),
    );
    const { repos, mine } = await s.repositories();
    expect(repos.map((r) => r.id)).toEqual([1, 2]);
    expect(mine.has(2)).toBe(true);
    expect(await s.analyses(2)).toEqual([{ id: 5 }]);
    expect((await s.records(2))[0].ts).toBe('2026-11-15T10:00:00.000Z');
    expect(await s.jobs()).toEqual([{ id: 1, status: 'done' }]);
    expect(log).toContain('analyses.select(id,repo_id,sha,user_id,created_at,manifest,specs).eq(repo_id,2).order(created_at).limit(20)');
  });

  it('writes records without the scoring alias, sessions without references, and joins a repository once', async () => {
    const log: string[] = [];
    const s = new SupabaseStore(
      fakeClient(
        {
          'records.insert': { data: [row()], error: null },
          'quiz_sessions.insert': { data: { id: 9 }, error: null },
          'my_repositories.insert': { data: null, error: { message: 'duplicate key value violates unique constraint' } },
          'repositories.select(id,url,name,license,is_sample).eq(url,https://github.com/a/b)': { data: [{ id: 4 }], error: null },
        },
        log,
      ),
    );
    await s.addRecords([row({ id: 3 })]);
    expect(log[0]).not.toContain('"unit":');
    expect(log[0]).not.toContain('"id":');
    expect(await s.addRecords([])).toEqual([]);
    expect(await s.startSession(1, SHA, [{ unit_id: 'a', level: 1, question: 'q' }])).toBe(9);
    await s.finishSession(9);
    await s.addMine(4);
    await s.removeMine(4);
    expect(await s.findRepository('https://github.com/a/b')).toEqual({ id: 4 });
    expect(log.some((l) => l.startsWith('quiz_sessions.update(finished_at).eq(id,9)'))).toBe(true);
  });

  it('raises database errors with the table named', async () => {
    const s = new SupabaseStore(fakeClient({ records: { data: null, error: { message: 'permission denied' } }, 'my_repositories.insert': { data: null, error: { message: 'boom' } }, quiz_sessions: { data: null, error: { message: 'x' } } }));
    await expect(s.records(1)).rejects.toThrow('records: permission denied');
    await expect(s.addMine(1)).rejects.toThrow('my_repositories: boom');
    expect(await s.startSession(1, SHA, [])).toBeNull();
  });
});
