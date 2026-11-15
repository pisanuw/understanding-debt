import { describe, expect, it } from 'vitest';
import { InMemoryRepository, newId, SupabaseRepository } from '../src/lib/repository';
import type { AppClient } from '../src/lib/supabase';

interface Note {
  id: string;
  title: string;
  tags?: string[];
}

describe('InMemoryRepository', () => {
  it('lists, gets, puts and removes', async () => {
    const repo = new InMemoryRepository<Note>([{ id: 'a', title: 'first' }]);
    expect(await repo.list()).toEqual([{ id: 'a', title: 'first' }]);
    expect(await repo.get('a')).toEqual({ id: 'a', title: 'first' });
    expect(await repo.get('missing')).toBeUndefined();
    expect(await repo.put({ id: 'b', title: 'second' })).toEqual({ id: 'b', title: 'second' });
    await repo.put({ id: 'a', title: 'first, edited' });
    expect((await repo.list()).map((n) => n.title)).toEqual(['first, edited', 'second']);
    await repo.remove('a');
    await repo.remove('never there');
    expect(await repo.list()).toEqual([{ id: 'b', title: 'second' }]);
  });
  it('hands out copies, so callers cannot change stored items by reference', async () => {
    const repo = new InMemoryRepository<Note>([{ id: 'a', title: 'x', tags: ['t'] }]);
    const got = (await repo.get('a'))!;
    got.tags!.push('mutated');
    expect((await repo.get('a'))!.tags).toEqual(['t']);
    const fresh: Note = { id: 'b', title: 'y' };
    await repo.put(fresh);
    fresh.title = 'changed after put';
    expect((await repo.get('b'))!.title).toBe('y');
    (await repo.list())[0].title = 'changed in list';
    expect((await repo.get('a'))!.title).toBe('x');
  });
  it('makes unique ids', () => {
    const a = newId();
    expect(a).not.toBe(newId());
    expect(a).toMatch(/^[0-9a-f-]{36}$/);
  });
});

type Result = { data: unknown; error: { message: string } | null };

/** A fake supabase-js client that records the query chain and resolves to one result. */
function fakeClient(result: Result) {
  const calls: string[] = [];
  const builder = {
    select: (cols?: string) => (calls.push(`select(${cols ?? ''})`), builder),
    order: (col: string, opts: unknown) => (calls.push(`order(${col},${JSON.stringify(opts)})`), builder),
    eq: (col: string, v: unknown) => (calls.push(`eq(${col},${String(v)})`), builder),
    maybeSingle: () => (calls.push('maybeSingle()'), builder),
    single: () => (calls.push('single()'), builder),
    upsert: (row: unknown) => (calls.push(`upsert(${JSON.stringify(row)})`), builder),
    delete: () => (calls.push('delete()'), builder),
    then: (resolve: (r: Result) => unknown) => Promise.resolve(result).then(resolve),
  };
  const client = { from: (table: string) => (calls.push(`from(${table})`), builder) } as unknown as AppClient;
  return { client, calls };
}

describe('SupabaseRepository', () => {
  const rows = [{ id: 'a', title: 'first' }];
  it('lists the table ordered by created_at and lets the database fill user_id', async () => {
    const { client, calls } = fakeClient({ data: rows, error: null });
    const repo = new SupabaseRepository<Note>(client, 'notes');
    expect(await repo.list()).toEqual(rows);
    expect(calls).toEqual(['from(notes)', 'select(*)', 'order(created_at,{"ascending":true})']);
  });
  it('orders by another column when asked and returns [] for null data', async () => {
    const { client, calls } = fakeClient({ data: null, error: null });
    expect(await new SupabaseRepository<Note>(client, 'notes', 'title').list()).toEqual([]);
    expect(calls[2]).toBe('order(title,{"ascending":true})');
  });
  it('gets one row by id, undefined when absent', async () => {
    const hit = fakeClient({ data: rows[0], error: null });
    expect(await new SupabaseRepository<Note>(hit.client, 'notes').get('a')).toEqual(rows[0]);
    expect(hit.calls).toEqual(['from(notes)', 'select(*)', 'eq(id,a)', 'maybeSingle()']);
    const miss = fakeClient({ data: null, error: null });
    expect(await new SupabaseRepository<Note>(miss.client, 'notes').get('zz')).toBeUndefined();
  });
  it('upserts and returns the stored row', async () => {
    const { client, calls } = fakeClient({ data: { id: 'a', title: 'first', user_id: 'u1' }, error: null });
    expect(await new SupabaseRepository<Note>(client, 'notes').put(rows[0])).toEqual({ id: 'a', title: 'first', user_id: 'u1' });
    expect(calls).toEqual(['from(notes)', 'upsert({"id":"a","title":"first"})', 'select()', 'single()']);
  });
  it('deletes by id', async () => {
    const { client, calls } = fakeClient({ data: null, error: null });
    await new SupabaseRepository<Note>(client, 'notes').remove('a');
    expect(calls).toEqual(['from(notes)', 'delete()', 'eq(id,a)']);
  });
  it('turns PostgREST errors into exceptions naming the table', async () => {
    const { client } = fakeClient({ data: null, error: { message: 'permission denied' } });
    const repo = new SupabaseRepository<Note>(client, 'notes');
    await expect(repo.list()).rejects.toThrow('notes: permission denied');
    await expect(repo.get('a')).rejects.toThrow('notes: permission denied');
    await expect(repo.put(rows[0])).rejects.toThrow('notes: permission denied');
    await expect(repo.remove('a')).rejects.toThrow('notes: permission denied');
  });
});
