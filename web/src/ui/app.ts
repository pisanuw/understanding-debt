/**
 * The app shell: the header, the hash router and the per-repository cache. Screens are plain
 * functions that build DOM (screens/*.ts); everything they need comes through `AppDeps`, so
 * the live site (Supabase, the gateway functions, the worker on Render) and the preview
 * (fixture rows, a fake sign-in, the functions' fakes) run the same screens.
 *
 * Signed in, the store is the server's (`deps.store`); not signed in, it is the browser's
 * (`deps.anon`, data/local.ts: the samples' static JSON and answers kept in localStorage), and
 * quizzes run on the visitor's own key or, without one, from the questions written in advance,
 * self-checked. Nothing a visitor who is not signed in does calls the gateway or writes to
 * Supabase; at sign-in the page offers once to copy their answers into the account.
 */
import type { User } from '@supabase/supabase-js';
import type { AdvanceFile } from '../core/advance';
import type { Excerpt, ExcerptRef } from '../core/excerpts';
import { repoScores, type RepoScores } from '../core/repo-scores';
import type { EvidenceRow } from '../core/quiz';
import { pickAnalysis, type Analysis, type Repository } from '../core/specs';
import type { LocalStore } from '../data/local';
import type { UdebtStore } from '../data/store';
import { onAuth, type AuthClient, type SignInOptions } from '../lib/auth';
import { getByokKey, getMode, type LlmMode } from '../model/prefs';
import type { QuizClient } from '../model/client';
import { TreeCache, type TreeFetch } from '../model/tree';
import type { WorkerClient } from '../model/worker';
import { clear, h } from './dom';
import { addScreen } from './screens/add';
import { homeScreen } from './screens/home';
import { offerCard } from './screens/offer';
import { repoScreen } from './screens/repo';
import { reposScreen } from './screens/repos';
import { settingsScreen } from './screens/settings';
import { signinScreen } from './screens/signin';

export interface AppDeps {
  /** The signed-in person's store (Supabase live, fixture rows in the preview). */
  store: UdebtStore;
  /** A visitor's store while not signed in: the samples, answers in this browser. Without it every screen asks to sign in. */
  anon?: LocalStore;
  /** The questions written in advance for a repository at a commit (the samples' files), or null. */
  advance?: (repoUrl: string, sha: string) => Promise<AdvanceFile | null>;
  auth: AuthClient;
  signIn: SignInOptions;
  getToken: () => Promise<string | null>;
  /** The quiz client for the person's current mode, or null when model features are off. */
  quiz: (mode?: LlmMode) => QuizClient | null;
  /** A line range of a file at a commit, through /api/excerpt (no model call). */
  excerpt: (repoUrl: string, sha: string, ref: ExcerptRef) => Promise<Excerpt>;
  /** The top files under directories at a commit, through /api/tree (the tree fallback; none: no fallback). */
  tree?: TreeFetch;
  worker: WorkerClient | null;
  preview: boolean;
  storage?: Storage;
}

export interface RepoView {
  repo: Repository;
  analysis: Analysis | null;
  analyses: Analysis[];
  records: EvidenceRow[];
  scores: RepoScores | null;
}

export type RepoTab = 'specs' | 'quiz' | 'review' | 'debt';

export type Route =
  | { name: 'repos' }
  | { name: 'repo'; id: number; tab: RepoTab }
  | { name: 'unit'; id: number; unit: string }
  | { name: 'settings' }
  | { name: 'signin' }
  | { name: 'add' };

export function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  if (parts[0] === 'settings') return { name: 'settings' };
  if (parts[0] === 'signin') return { name: 'signin' };
  if (parts[0] === 'add') return { name: 'add' };
  if (parts[0] === 'r' && /^\d+$/.test(parts[1] ?? '')) {
    const id = Number(parts[1]);
    if (parts[2] === 'u' && parts[3]) return { name: 'unit', id, unit: decodeURIComponent(parts.slice(3).join('/')) };
    if (parts[2] === 'quiz' || parts[2] === 'review' || parts[2] === 'debt') return { name: 'repo', id, tab: parts[2] };
    return { name: 'repo', id, tab: 'specs' };
  }
  return { name: 'repos' };
}

export function repoHref(id: number, tab: RepoTab = 'specs'): string {
  return tab === 'specs' ? `#/r/${id}` : `#/r/${id}/${tab}`;
}

export function unitHref(id: number, unit: string): string {
  return `#/r/${id}/u/${encodeURIComponent(unit)}`;
}

export class App {
  user: User | null = null;
  private readonly cache = new Map<number, RepoView>();
  private readonly main: HTMLElement;
  private readonly who: HTMLElement;
  private renderSeq = 0;
  private stopAuth: (() => void) | null = null;
  /** The quiz unit the next quiz screen preselects (set by "Quiz this unit"). */
  quizUnit: string | null = null;
  /** What the current screen asked to undo when it is replaced (a sign-in panel's subscription). */
  private leaving: (() => void)[] = [];
  /** The same for the screen being built. */
  private building: (() => void)[] = [];
  /** Directory listings for units that name only directories, kept for this page session (model/tree.ts). */
  readonly trees: TreeCache | null;
  /** Whether the first sign-in state (the session restored at load) has arrived. */
  private authKnown = false;

  constructor(
    readonly root: HTMLElement,
    readonly deps: AppDeps,
    readonly win: Window = window,
  ) {
    this.trees = deps.tree ? new TreeCache(deps.tree) : null;
    this.who = h('nav', { class: 'who', 'aria-label': 'Account' });
    const top = h('header', { class: 'top' }, h('div', { class: 'wrap' }, h('a', { class: 'brand', href: '#/' }, h('strong', null, 'udebt'), h('span', null, 'understanding debt')), this.who));
    this.main = h('main', { class: 'wrap', id: 'main' });
    clear(root).append(top, this.main);
    if (deps.preview) root.prepend(h('p', { class: 'preview-banner' }, 'Preview: fixture data and fake sign-in, gateway and GitHub; nothing leaves this tab.'));
  }

  start(): void {
    this.win.addEventListener('hashchange', () => void this.render());
    this.stopAuth = onAuth((user) => {
      const before = this.user;
      const changed = (user?.id ?? null) !== (before?.id ?? null);
      const first = !this.authKnown;
      this.authKnown = true;
      this.user = user;
      if (changed) this.cache.clear();
      if (changed && !first && this.deps.anon) {
        // Repository ids differ between the browser's samples and the signed-in store: signing in
        // or out starts again from the list (a repository page, the sign-in page, or any page on
        // signing out).
        const r = parseRoute(this.win.location.hash);
        if ((before && !user) || r.name === 'repo' || r.name === 'unit' || r.name === 'signin') {
          if (this.win.location.hash !== '#/') {
            this.win.location.hash = '#/';
            return;
          }
        }
      }
      void this.render();
    }, this.deps.auth);
  }

  /** The store in use: the server's when signed in, else this browser's (the samples). */
  get store(): UdebtStore {
    return this.user || !this.deps.anon ? this.deps.store : this.deps.anon;
  }

  /** True while a visitor uses udebt without signing in. */
  get anonymous(): boolean {
    return !this.user && !!this.deps.anon;
  }

  /**
   * The quiz client: signed in, the person's mode (the gateway, their own key, or off); not signed
   * in, their own key when they saved one, else none (the questions written in advance). Never
   * the gateway without signing in.
   */
  quiz(): QuizClient | null {
    if (this.user || !this.deps.anon) return this.deps.quiz();
    const storage = this.deps.storage;
    return getMode(storage) === 'byok' && getByokKey(storage) ? this.deps.quiz('byok') : null;
  }

  /** The mode quizzes run in now ('off': no model, the questions written in advance). */
  mode(): LlmMode {
    if (this.user || !this.deps.anon) return getMode(this.deps.storage);
    return this.quiz() ? 'byok' : 'off';
  }

  /** The questions written in advance for this repository's analysis, or null. */
  advanceFor(v: RepoView): Promise<AdvanceFile | null> {
    return v.analysis && this.deps.advance ? this.deps.advance(v.repo.url, v.analysis.sha).catch(() => null) : Promise.resolve(null);
  }

  stop(): void {
    this.stopAuth?.();
  }

  /** Runs `fn` when the screen drawn now is replaced. */
  onLeave(fn: () => void): void {
    this.building.push(fn);
  }

  go(hash: string): void {
    if (this.win.location.hash === hash) void this.render();
    else this.win.location.hash = hash;
  }

  private header(): void {
    clear(this.who);
    if (this.user) this.who.append(h('span', { class: 'email' }, this.user.email ?? ''), h('a', { href: '#/' }, 'Repositories'), h('a', { href: '#/settings' }, 'Settings'));
    else if (this.deps.anon) this.who.append(h('a', { href: '#/' }, 'Samples'), h('a', { href: '#/settings' }, 'Settings'), h('a', { href: '#/signin', class: 'signin-link' }, 'Sign in'));
  }

  /** Draws the screen for the current address; a slower earlier draw never replaces a newer one. */
  async render(): Promise<void> {
    const seq = ++this.renderSeq;
    const mine: (() => void)[] = [];
    this.building = mine;
    this.header();
    const route = parseRoute(this.win.location.hash);
    let screen: HTMLElement;
    try {
      if (!this.user && !this.deps.anon) screen = signinScreen(this);
      else if (route.name === 'signin') screen = this.user ? await reposScreen(this) : signinScreen(this);
      else if (route.name === 'settings') screen = settingsScreen(this);
      else if (route.name === 'add') screen = await addScreen(this);
      else if (route.name === 'repo' || route.name === 'unit') screen = await repoScreen(this, route);
      else screen = this.user ? await reposScreen(this) : await homeScreen(this);
      if (this.user && this.deps.anon) {
        const offer = await offerCard(this);
        if (offer) screen.prepend(offer);
      }
    } catch (e) {
      screen = h('section', null, h('h1', null, 'Something went wrong'), h('p', { class: 'error' }, e instanceof Error ? e.message : String(e)), h('p', null, h('a', { href: '#/' }, 'Back to the repositories')));
    }
    if (seq !== this.renderSeq) {
      for (const fn of mine) fn();
      return;
    }
    for (const fn of this.leaving.splice(0)) fn();
    this.leaving = mine;
    clear(this.main).append(screen);
    this.root.ownerDocument.title = screen.dataset.title ? `${screen.dataset.title} · udebt` : 'udebt';
  }

  /** A repository with its analysis, the person's records and scores (cached until `forget`). */
  async repoView(id: number): Promise<RepoView | null> {
    const hit = this.cache.get(id);
    if (hit) return hit;
    const store = this.store;
    const { repos } = await store.repositories();
    const repo = repos.find((r) => r.id === id) ?? null;
    if (!repo) return null;
    const [analyses, records] = await Promise.all([store.analyses(id), store.records(id)]);
    const analysis = pickAnalysis(analyses, this.user?.id ?? null);
    const view: RepoView = { repo, analysis, analyses, records, scores: analysis ? repoScores(analysis, records) : null };
    this.cache.set(id, view);
    return view;
  }

  /** Recomputes a cached repository after new records. */
  refresh(view: RepoView, records: EvidenceRow[]): RepoView {
    const next = { ...view, records, scores: view.analysis ? repoScores(view.analysis, records) : null };
    this.cache.set(view.repo.id, next);
    return next;
  }

  forget(id?: number): void {
    if (id === undefined) this.cache.clear();
    else this.cache.delete(id);
  }
}
