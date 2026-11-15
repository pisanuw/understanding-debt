import { afterEach, describe, expect, it, vi } from 'vitest';
import { APP_SCHEMA, APP_SLUG, CLIENT_OPTIONS, getSupabase, hasSupabase, resetSupabase, supabaseEnv } from '../src/lib/supabase';

afterEach(() => {
  vi.unstubAllEnvs();
  resetSupabase();
});

describe('names', () => {
  it('derives the schema from the slug: app_ plus dashes as underscores', () => {
    expect(APP_SCHEMA).toBe(`app_${APP_SLUG.replace(/-/g, '_')}`);
  });
});

describe('supabaseEnv', () => {
  it('needs both public values', () => {
    expect(supabaseEnv({})).toBeNull();
    expect(supabaseEnv({ VITE_SUPABASE_URL: 'https://ref.supabase.co' })).toBeNull();
    expect(supabaseEnv({ VITE_SUPABASE_URL: '', VITE_SUPABASE_ANON_KEY: 'k' })).toBeNull();
    expect(supabaseEnv({ VITE_SUPABASE_URL: 'https://ref.supabase.co', VITE_SUPABASE_ANON_KEY: 'k' })).toEqual({ url: 'https://ref.supabase.co', anonKey: 'k' });
  });
  it('reads import.meta.env by default', () => {
    expect(hasSupabase()).toBe(false);
    vi.stubEnv('VITE_SUPABASE_URL', 'https://ref.supabase.co');
    vi.stubEnv('VITE_SUPABASE_ANON_KEY', 'anon');
    expect(hasSupabase()).toBe(true);
  });
});

describe('getSupabase', () => {
  it('throws a clear error without the env', () => {
    expect(() => getSupabase()).toThrow(/VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY/);
  });
  it('creates one client per page and forgets it on reset', () => {
    vi.stubEnv('VITE_SUPABASE_URL', 'https://ref.supabase.co');
    vi.stubEnv('VITE_SUPABASE_ANON_KEY', 'anon');
    const a = getSupabase();
    expect(a.auth).toBeDefined();
    expect(getSupabase()).toBe(a);
    resetSupabase();
    expect(getSupabase()).not.toBe(a);
  });

  it('uses the PKCE flow and the app schema', () => {
    expect(CLIENT_OPTIONS).toEqual({ db: { schema: APP_SCHEMA }, auth: { flowType: 'pkce' } });
  });
});
