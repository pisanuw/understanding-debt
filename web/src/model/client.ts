/**
 * How the browser writes and grades a sitting, in the person's chosen mode (llm.ts):
 *
 *   gateway  POST /api/questions and /api/grade: the Netlify functions fetch the code at the
 *            pinned sha and call the HintWise gateway with the person's session token, within
 *            the app's and the person's caps; grading also records the sitting;
 *   byok     the same prompts built here (src/core/assemble.ts) and sent with the person's
 *            own key straight to api.anthropic.com; excerpts still come from /api/excerpt.
 *
 * Excerpts for the walkthrough view come from /api/excerpt in both modes, and the tree
 * fallback's listings (units that name only directories) from /api/tree.
 */
import type { GradeRequest, QuestionsRequest } from '../core/assemble';
import type { Excerpt, ExcerptRef } from '../core/excerpts';
import type { EvidenceRow, Grade, Question } from '../core/quiz';
import type { AskDeps } from '../lib/llm';
import { getMode, type LlmMode } from './prefs';
import type { TreeAnswer, TreeFetch } from './tree';

export interface GradeOutcome {
  grades: Grade[];
  /** The rows the function wrote with the person's token, or null when the page should write them. */
  recorded: EvidenceRow[] | null;
}

export interface RecordHint {
  repo_id: number;
  ts: string;
  /** Where the questions came from: a quiz (the default) or a review sitting. */
  source?: 'quiz' | 'review';
}

export interface QuizClient {
  readonly mode: LlmMode;
  writeQuestions(req: QuestionsRequest): Promise<Question[]>;
  grade(req: GradeRequest & { items: (GradeRequest['items'][number] & { variants?: string[]; revealed?: boolean; confidence?: number })[] }, record?: RecordHint): Promise<GradeOutcome>;
  excerpt(repoUrl: string, sha: string, ref: ExcerptRef): Promise<Excerpt>;
}

/**
 * A spending cap of the HintWise key is reached (the gateway's 429, passed through by the
 * functions): `scope` and `resetsAt` as lib/llm.ts's CapReached carries them.
 */
export class CapError extends Error {
  constructor(
    public readonly scope: string,
    public readonly resetsAt: string,
    message?: string,
  ) {
    super(message || `The HintWise key's ${scope} cap is reached until ${resetsAt}.`);
    this.name = 'CapReached';
  }
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export interface ClientDeps {
  getToken: () => Promise<string | null>;
  fetch?: typeof fetch;
  /** Where /api lives (default: this site). */
  base?: string;
  /** For byok: the llm.ts dependencies (tests pass a fake Anthropic client). */
  askDeps?: AskDeps;
}

/**
 * One call to /api with the person's token. `anyone`: the code-reading functions (excerpt, tree),
 * which a visitor who is not signed in may call without a token for the samples at their pins;
 * every other call needs the token and never leaves the page without one (the gateway is for
 * signed-in people only).
 */
async function call<T>(deps: ClientDeps, path: string, init: RequestInit = {}, anyone = false): Promise<T> {
  const token = await deps.getToken();
  if (!token && !anyone) throw new ApiError(401, 'Sign in first.');
  const res = await (deps.fetch ?? fetch)(`${deps.base ?? ''}${path}`, {
    ...init,
    headers: { ...(init.headers as Record<string, string> | undefined), ...(token ? { authorization: `Bearer ${token}` } : {}), ...(init.body ? { 'content-type': 'application/json' } : {}) },
  });
  const data: unknown = await res.json().catch(() => ({}));
  const d = (data && typeof data === 'object' ? data : {}) as Record<string, unknown>;
  if (res.status === 429 && d.error === 'cap') throw new CapError(String(d.scope ?? ''), String(d.resets_at ?? ''), typeof d.message === 'string' ? d.message : undefined);
  if (!res.ok) throw new ApiError(res.status, typeof d.error === 'string' && d.error ? d.error : `The server answered ${res.status}.`);
  return data as T;
}

/**
 * The person's HintWise allowance, as the gateway's check mode answers ({ app, check: true }: no
 * model call; docs/platform.md section 3). `allowed` false: a cap is reached until `resets_at`.
 */
export interface Allowance {
  allowed: boolean;
  scope: string | null;
  resets_at: string | null;
  app_open?: boolean;
  calls_left?: number | null;
}

/** POST /api/allowance, or null when the answer is not an allowance (the page then learns of a cap from the call). */
export function allowanceVia(deps: ClientDeps): () => Promise<Allowance | null> {
  return async () => {
    const a = await call<Partial<Allowance>>(deps, '/api/allowance', { method: 'POST', body: '{}' });
    return typeof a.allowed === 'boolean' ? { allowed: a.allowed, scope: a.scope ?? null, resets_at: a.resets_at ?? null, app_open: a.app_open, calls_left: a.calls_left ?? null } : null;
  };
}

/** GET /api/tree for the directories of a sitting's units (model/tree.ts keeps the answers). */
export function treeVia(deps: ClientDeps): TreeFetch {
  return (repoUrl, sha, dirs) => call<TreeAnswer>(deps, `/api/tree?${new URLSearchParams([['repo', repoUrl], ['sha', sha], ...dirs.map((d) => ['path', d])])}`, {}, true);
}

export function excerptVia(deps: ClientDeps) {
  return (repoUrl: string, sha: string, ref: ExcerptRef) =>
    call<Excerpt>(deps, `/api/excerpt?${new URLSearchParams({ repo: repoUrl, sha, path: ref.path, start: String(ref.start), end: String(ref.end) })}`, {}, true);
}

export class GatewayClient implements QuizClient {
  readonly mode = 'gateway' as const;
  constructor(private readonly deps: ClientDeps) {}

  async writeQuestions(req: QuestionsRequest): Promise<Question[]> {
    return (await call<{ questions: Question[] }>(this.deps, '/api/questions', { method: 'POST', body: JSON.stringify(req) })).questions;
  }

  async grade(req: Parameters<QuizClient['grade']>[0], record?: RecordHint): Promise<GradeOutcome> {
    const out = await call<{ grades: Grade[]; recorded?: EvidenceRow[] | null }>(this.deps, '/api/grade', { method: 'POST', body: JSON.stringify({ ...req, ...(record ? { record } : {}) }) });
    return { grades: out.grades ?? [], recorded: out.recorded ?? null };
  }

  excerpt(repoUrl: string, sha: string, ref: ExcerptRef): Promise<Excerpt> {
    return excerptVia(this.deps)(repoUrl, sha, ref);
  }
}

/**
 * The own-key client, loaded on first use: it pulls in the Anthropic browser SDK (through
 * llm.ts's `ask`), which visitors on the HintWise key never download.
 */
export class LazyByokClient implements QuizClient {
  readonly mode = 'byok' as const;
  private real: Promise<QuizClient> | null = null;
  constructor(private readonly deps: ClientDeps) {}

  private load(): Promise<QuizClient> {
    this.real ??= import('./byok').then((m) => new m.ByokClient(this.deps));
    return this.real;
  }

  async writeQuestions(req: QuestionsRequest): Promise<Question[]> {
    return (await this.load()).writeQuestions(req);
  }

  async grade(req: Parameters<QuizClient['grade']>[0], record?: RecordHint): Promise<GradeOutcome> {
    return (await this.load()).grade(req, record);
  }

  excerpt(repoUrl: string, sha: string, ref: ExcerptRef): Promise<Excerpt> {
    return excerptVia(this.deps)(repoUrl, sha, ref);
  }
}

/** The client for the person's mode, or null when model features are off. */
export function quizClient(deps: ClientDeps, mode: LlmMode = getMode()): QuizClient | null {
  if (mode === 'gateway') return new GatewayClient(deps);
  if (mode === 'byok') return new LazyByokClient(deps);
  return null;
}

/** Which allowance a cap scope is, and when it resets in local time (" until 1:00 AM", or ""). */
function capParts(e: { scope: string; resetsAt: string }, now: Date): { what: string; when: string } {
  const s = e.scope;
  const what = s.startsWith('app-user-week')
    ? 'Your weekly allowance in this app is used up'
    : s.startsWith('app-user-day')
      ? 'Your daily allowance in this app is used up'
      : s === 'user-day'
        ? 'Your daily HintWise allowance is used up'
        : "This app's shared allowance is used up";
  const d = new Date(e.resetsAt);
  const when = Number.isNaN(d.getTime())
    ? ''
    : ` until ${d.toDateString() === now.toDateString() ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : d.toLocaleString([], { weekday: 'long', hour: 'numeric', minute: '2-digit' })}`;
  return { what, when };
}

/** A cap answer in plain words (docs/new-app-guide.md section 7), with when it resets in local time. */
export function capMessage(e: { scope: string; resetsAt: string }, now: Date = new Date()): string {
  const { what, when } = capParts(e, now);
  return `${what}${when}. You can use your own Anthropic key in Settings; no allowance applies to it.`;
}

/**
 * The quiz page's notice once the HintWise allowance is used up: which one, until when, and what
 * still works (the questions written in advance, when the repository has them; the person's own key).
 */
export function capNotice(a: { scope: string | null; resets_at: string | null }, hasAdvance: boolean, now: Date = new Date()): string {
  const { what, when } = capParts({ scope: a.scope ?? '', resetsAt: a.resets_at ?? '' }, now);
  const meanwhile = hasAdvance
    ? 'Until then the quiz uses the questions written in advance (no model call), or you can use your own Anthropic key in Settings.'
    : 'Until then you can use your own Anthropic key in Settings; no allowance applies to it.';
  return `${what}${when}. ${meanwhile}`;
}
