/**
 * Data access behind a small interface (docs/platform.md, section 5), so the
 * UI and the tests never depend on the network: InMemoryRepository for
 * vitest, `vite preview` and the Playwright walkthrough; SupabaseRepository
 * over one table of the app's schema for the live site.
 */
import type { AppClient } from './supabase';

export interface Entity {
  id: string;
}

export interface Repository<T extends Entity> {
  list(): Promise<T[]>;
  get(id: string): Promise<T | undefined>;
  /** Inserts or replaces the item with that id. */
  put(item: T): Promise<T>;
  remove(id: string): Promise<void>;
}

/** A new id for an entity created in the browser. */
export function newId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** Keeps copies, so callers cannot change stored items by reference. */
export class InMemoryRepository<T extends Entity> implements Repository<T> {
  private readonly items = new Map<string, T>();

  constructor(seed: T[] = []) {
    for (const item of seed) this.items.set(item.id, structuredClone(item));
  }

  async list(): Promise<T[]> {
    return [...this.items.values()].map((i) => structuredClone(i));
  }

  async get(id: string): Promise<T | undefined> {
    const item = this.items.get(id);
    return item ? structuredClone(item) : undefined;
  }

  async put(item: T): Promise<T> {
    this.items.set(item.id, structuredClone(item));
    return structuredClone(item);
  }

  async remove(id: string): Promise<void> {
    this.items.delete(id);
  }
}

/**
 * One table in the app's schema (the client is created with
 * `db: { schema: 'app_udebt' }`). The database fills `user_id` from its
 * `default auth.uid()`, and row level security keeps every call to the
 * signed-in user's rows, so this class never sends or filters by user.
 */
export class SupabaseRepository<T extends Entity> implements Repository<T> {
  constructor(
    private readonly client: AppClient,
    private readonly table: string,
    private readonly orderBy: string = 'created_at',
  ) {}

  async list(): Promise<T[]> {
    const { data, error } = await this.client.from(this.table).select('*').order(this.orderBy, { ascending: true });
    if (error) throw new Error(`${this.table}: ${error.message}`);
    return (data ?? []) as T[];
  }

  async get(id: string): Promise<T | undefined> {
    const { data, error } = await this.client.from(this.table).select('*').eq('id', id).maybeSingle();
    if (error) throw new Error(`${this.table}: ${error.message}`);
    return (data ?? undefined) as T | undefined;
  }

  async put(item: T): Promise<T> {
    const { data, error } = await this.client.from(this.table).upsert(item).select().single();
    if (error) throw new Error(`${this.table}: ${error.message}`);
    return data as T;
  }

  async remove(id: string): Promise<void> {
    const { error } = await this.client.from(this.table).delete().eq('id', id);
    if (error) throw new Error(`${this.table}: ${error.message}`);
  }
}
