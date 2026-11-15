/**
 * Wires the app for the live site or the preview. Live: Supabase from VITE_SUPABASE_URL and
 * VITE_SUPABASE_ANON_KEY, the functions at /api, the worker at VITE_UDEBT_WORKER_URL.
 * Preview (no Supabase env, as `vite preview` and the Playwright walkthrough build it): the
 * fixture rows the dev server serves at /__udebt/fixture.json, the fake sign-in, and the
 * functions with their fakes.
 */
import { currentToken, type AuthClient, type SignInOptions } from '../lib/auth';
import { getMode, type LlmMode } from '../model/prefs';
import { getSupabase, hasSupabase } from '../lib/supabase';
import { MemoryStore } from '../data/memory';
import type { Fixture } from '../data/store';
import { SupabaseStore, type TableClient } from '../data/supabase-store';
import { FakeAuth } from '../fake/auth';
import { GatewayClient, quizClient, treeVia, type ClientDeps } from '../model/client';
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

export function previewDeps(fixture: Fixture, opts: { doFetch?: typeof fetch; session?: Storage; local?: Storage } = {}): AppDeps {
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
    excerpt: (repo, sha, ref) => gateway.excerpt(repo, sha, ref),
    tree: treeVia(clientDeps),
    worker: null,
    preview: true,
    storage: opts.local,
  };
}

export function liveDeps(): AppDeps {
  const client = getSupabase();
  const getToken = () => currentToken(client);
  const clientDeps: ClientDeps = { getToken };
  const base = workerUrl();
  const gateway = new GatewayClient(clientDeps);
  return {
    store: new SupabaseStore(client as unknown as TableClient),
    auth: client,
    signIn: { client },
    getToken,
    quiz: (mode?: LlmMode) => quizClient(clientDeps, mode ?? getMode()),
    excerpt: (repo, sha, ref) => gateway.excerpt(repo, sha, ref),
    tree: treeVia(clientDeps),
    worker: base ? new HttpWorkerClient(base) : null,
    preview: false,
    storage: safeStorage('localStorage'),
  };
}

export async function boot(root: HTMLElement, doFetch: typeof fetch = (...a) => fetch(...a)): Promise<App> {
  let deps: AppDeps;
  if (hasSupabase()) deps = liveDeps();
  else {
    const res = await doFetch('./__udebt/fixture.json');
    const fixture = (res.ok ? await res.json() : { repositories: [], analyses: [] }) as Fixture;
    deps = previewDeps(fixture, { session: safeStorage('sessionStorage'), local: safeStorage('localStorage') });
  }
  const app = new App(root, deps);
  app.start();
  return app;
}
