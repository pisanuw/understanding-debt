/**
 * The app shell: the header, the hash router and the per-repository cache. Screens are plain
 * functions that build DOM (screens/*.ts); everything they need comes through `AppDeps`, so
 * the live site (Supabase, the gateway functions, the worker on Render) and the preview
 * (fixture rows, a fake sign-in, the functions' fakes) run the same screens.
 */
import type { User } from '@supabase/supabase-js';
import type { Excerpt, ExcerptRef } from '../core/excerpts';
import { repoScores, type RepoScores } from '../core/repo-scores';
import type { EvidenceRow } from '../core/quiz';
import { pickAnalysis, type Analysis, type Repository } from '../core/specs';
import type { UdebtStore } from '../data/store';
import { onAuth, type AuthClient, type SignInOptions } from '../lib/auth';
import type { LlmMode } from '../model/prefs';
import type { QuizClient } from '../model/client';
import { TreeCache, type TreeFetch } from '../model/tree';
import type { WorkerClient } from '../model/worker';
import { clear, h } from './dom';
import { addScreen } from './screens/add';
import { repoScreen } from './screens/repo';
import { reposScreen } from './screens/repos';
import { settingsScreen } from './screens/settings';
import { signinScreen } from './screens/signin';

export interface AppDeps {
  store: UdebtStore;
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
  | { name: 'add' };

export function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  if (parts[0] === 'settings') return { name: 'settings' };
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
      const changed = (user?.id ?? null) !== (this.user?.id ?? null);
      this.user = user;
      if (changed) this.cache.clear();
      void this.render();
    }, this.deps.auth);
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
    if (!this.user) return;
    this.who.append(h('span', { class: 'email' }, this.user.email ?? ''), h('a', { href: '#/' }, 'Repositories'), h('a', { href: '#/settings' }, 'Settings'));
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
      if (!this.user) screen = signinScreen(this);
      else if (route.name === 'settings') screen = settingsScreen(this);
      else if (route.name === 'add') screen = await addScreen(this);
      else if (route.name === 'repo' || route.name === 'unit') screen = await repoScreen(this, route);
      else screen = await reposScreen(this);
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
    const { repos } = await this.deps.store.repositories();
    const repo = repos.find((r) => r.id === id) ?? null;
    if (!repo) return null;
    const [analyses, records] = await Promise.all([this.deps.store.analyses(id), this.deps.store.records(id)]);
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
