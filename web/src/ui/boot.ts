/**
 * Wires the app for the live site or the preview. Live: Supabase from VITE_SUPABASE_URL and
 * VITE_SUPABASE_ANON_KEY, the functions at /api, the worker at VITE_UDEBT_WORKER_URL, and for a
 * visitor who is not signed in the samples' static JSON in the site (samples/, with their
 * advance quizzes) and a store in this browser. Preview (no Supabase env, as `vite preview` and
 * the Playwright walkthrough build it): the fixture rows the dev server serves at
 * /__udebt/fixture.json (and its samples' advance quizzes at /__udebt/quizzes.json), the fake
 * sign-in, and the functions with their fakes.
 */
import { currentToken, type AuthClient, type SignInOptions } from '../lib/auth';
import { getMode, type LlmMode } from '../model/prefs';
import { getSupabase, hasSupabase } from '../lib/supabase';
import { AdvanceQuizzes, FixtureSamples, LocalStore, StaticSamples, type SampleSource } from '../data/local';
import { MemoryStore } from '../data/memory';
import type { Fixture } from '../data/store';
import { SupabaseStore, type TableClient } from '../data/supabase-store';
import { FakeAuth } from '../fake/auth';
import { allowanceVia, GatewayClient, quizClient, treeVia, type ClientDeps } from '../model/client';
import { HttpWorkerClient, workerUrl } from '../model/worker';
import { App, type AppDeps } from './app';

function safeStorage(kind: 'localStorage' | 'sessionStorage'): Storage | undefined {
  try {
    return typeof window !== 'undefined' ? window[kind] : undefined;
  } catch {
    return undefined;
  }
}

export const PREVIEW_ENV = { VITE_SUPABASE_URL: 'https://preview.invalid', VITE_SUPABASE_ANON_KEY: 'preview' };

/** The delete-account function's answer in the preview: deleted. */
export const previewDeleteFetch: typeof fetch = async () => new Response(JSON.stringify({ status: 'deleted' }), { status: 200, headers: { 'content-type': 'application/json' } });

/** The store and advance quizzes of a visitor who is not signed in, over a source of samples. */
export function anonDeps(source: SampleSource, local?: Storage): Pick<AppDeps, 'anon' | 'advance'> {
  const advance = new AdvanceQuizzes(source);
  return { anon: new LocalStore(source, local ?? null), advance: (repoUrl, sha) => advance.for(repoUrl, sha) };
}

export function previewDeps(fixture: Fixture, opts: { doFetch?: typeof fetch; session?: Storage; local?: Storage; quizzes?: unknown[] } = {}): AppDeps {
  const fake = new FakeAuth(opts.session ?? null);
  const client = fake as unknown as AuthClient;
  const clientDeps: ClientDeps = { getToken: () => fake.token(), fetch: opts.doFetch };
  const signIn: SignInOptions = {
    client,
    profileClient: false,
    completeFromUrl: false,
    sendLink: async () => {
      await fake.auth.signInWithOAuth();
      return { status: 'sent' };
    },
    deleteOptions: { env: PREVIEW_ENV, fetch: previewDeleteFetch },
  };
  const gateway = new GatewayClient(clientDeps);
  return {
    store: new MemoryStore(fixture, opts.session ?? null),
    auth: client,
    signIn,
    getToken: () => fake.token(),
    quiz: (mode?: LlmMode) => quizClient(clientDeps, mode ?? getMode(opts.local)),
    allowance: allowanceVia(clientDeps),
    excerpt: (repo, sha, ref) => gateway.excerpt(repo, sha, ref),
    tree: treeVia(clientDeps),
    worker: null,
    preview: true,
    storage: opts.local,
    ...anonDeps(new FixtureSamples(fixture, opts.quizzes ?? []), opts.local),
  };
}

export function liveDeps(): AppDeps {
  const client = getSupabase();
  const getToken = () => currentToken(client);
  const refreshToken = async (): Promise<string | null> => {
    const { data, error } = await client.auth.refreshSession();
    return error ? null : (data.session?.access_token ?? null);
  };
  const clientDeps: ClientDeps = { getToken, refreshToken };
  const base = workerUrl();
  const gateway = new GatewayClient(clientDeps);
  return {
    store: new SupabaseStore(client as unknown as TableClient),
    auth: client,
    signIn: { client },
    getToken,
    quiz: (mode?: LlmMode) => quizClient(clientDeps, mode ?? getMode()),
    allowance: allowanceVia(clientDeps),
    excerpt: (repo, sha, ref) => gateway.excerpt(repo, sha, ref),
    tree: treeVia(clientDeps),
    worker: base ? new HttpWorkerClient(base) : null,
    preview: false,
    storage: safeStorage('localStorage'),
    ...anonDeps(new StaticSamples('samples/'), safeStorage('localStorage')),
  };
}

export async function boot(root: HTMLElement, doFetch: typeof fetch = (...a) => fetch(...a)): Promise<App> {
  let deps: AppDeps;
  if (hasSupabase()) deps = liveDeps();
  else {
    const res = await doFetch('./__udebt/fixture.json');
    const fixture = (res.ok ? await res.json() : { repositories: [], analyses: [] }) as Fixture;
    const qs = await doFetch('./__udebt/quizzes.json').catch(() => null);
    const quizzes = (qs?.ok ? await qs.json().catch(() => []) : []) as unknown[];
    deps = previewDeps(fixture, { session: safeStorage('sessionStorage'), local: safeStorage('localStorage'), quizzes: Array.isArray(quizzes) ? quizzes : [] });
  }
  const app = new App(root, deps);
  app.start();
  return app;
}
