/**
 * The four functions behind /api: question writing and grading through the HintWise
 * gateway, the excerpt proxy and the tree listing for units that name only directories.
 * Each takes a web-standard Request and returns a Response, so Netlify
 * (netlify/functions/*.ts), the dev and preview servers (dev/api-plugin.ts) and the tests
 * call the same code.
 */
import { gradeAnswers, readGradeRequest, readQuestionsRequest, writeQuestions, type Deps } from '../../../src/core/assemble';
import { MAX_LINES, parseRef, safePath, safeSha } from '../../../src/core/excerpts';
import { markRevealed, mergeGrades, type Answered } from '../../../src/core/quiz';
import { sittingRows } from '../../../src/core/review';
import { parseGitHubUrl } from '../../../src/core/specs';
import { authorize } from './auth';
import { readEnv, type FnEnv } from './env';
import { callGateway } from './gateway';
import { GitHubRefused, dirFiles, excerpt } from './github';
import { HttpError, bearer, bearerOrNull, errorResponse, json, readJson } from './http';
import { insertRecords, priorRecords } from './records';
import { openToAnyone } from './samples';

export interface HandlerDeps {
  env?: FnEnv;
  fetch?: typeof fetch;
  /** Who may read without a token (default: the registered samples at their pins, samples.ts). */
  open?: (repoUrl: string, sha: string) => boolean;
}

/**
 * The code-reading functions' check: with a token, the signed-in person and a repository the app
 * knows (auth.ts, unchanged); without one, only a registered sample at its pinned commit, so a
 * visitor who is not signed in can read the samples' code and nothing else (HOSTING.md
 * decision 10). No token never reaches Supabase.
 */
async function mayRead(req: Request, repoUrl: string, sha: string, env: FnEnv, doFetch: typeof fetch, open: (u: string, s: string) => boolean): Promise<void> {
  const token = bearerOrNull(req);
  if (token) {
    await authorize(token, repoUrl, env, doFetch);
    return;
  }
  if (!open(repoUrl, sha)) throw new HttpError(401, 'Sign in first.', { error: 'Sign in first: without signing in, only the samples at their pinned commits can be read.' });
}

function deps(token: string, env: FnEnv, doFetch: typeof fetch): Deps {
  return {
    excerpt: (g, sha, ref) => excerpt(g, sha, ref, env, doFetch),
    ask: (body) => callGateway(body, token, env, doFetch),
  };
}

/** POST /api/questions: {repo, sha, units, per_unit?} -> {questions}. */
export async function handleQuestions(req: Request, opts: HandlerDeps = {}): Promise<Response> {
  const env = opts.env ?? readEnv();
  const doFetch = opts.fetch ?? fetch;
  try {
    const token = bearer(req);
    const body = readQuestionsRequest(await readJson(req));
    await authorize(token, body.repo, env, doFetch);
    const questions = await writeQuestions(body, deps(token, env, doFetch));
    return json(200, { questions });
  } catch (e) {
    return errorResponse(e);
  }
}

/** Where a graded sitting's questions came from, as a record's `source` says. */
const SOURCES = ['quiz', 'review', 'dig'] as const;

/**
 * POST /api/grade: {repo, sha, items, record?: {repo_id, ts, source?}} -> {grades,
 * recorded}. With `record`, the graded sitting is written to app_udebt.records with the
 * person's token, each row carrying its spaced-review state after it (`data.review`, folded
 * from their earlier records on the sitting's units), and `recorded` holds the rows; null
 * means the page records them itself (the fakes, or a read or write PostgREST refused).
 * `source` is `quiz` (the default), `review` (a review sitting) or `dig`.
 */
export async function handleGrade(req: Request, opts: HandlerDeps = {}): Promise<Response> {
  const env = opts.env ?? readEnv();
  const doFetch = opts.fetch ?? fetch;
  try {
    const token = bearer(req);
    const raw = await readJson(req);
    const body = readGradeRequest(raw);
    const repoId = await authorize(token, body.repo, env, doFetch);
    const grades = await gradeAnswers(body, deps(token, env, doFetch));
    const record = (raw as { record?: { repo_id?: unknown; ts?: unknown; source?: unknown } }).record;
    let recorded = null;
    if (record && Number(record.repo_id) === repoId && typeof record.ts === 'string' && !Number.isNaN(Date.parse(record.ts))) {
      const items = (raw as { items: { variants?: unknown; revealed?: unknown; confidence?: unknown }[] }).items;
      const answered: Answered[] = body.items.map((it, i) => ({
        ...it,
        variants: Array.isArray(items[i]?.variants) ? (items[i].variants as unknown[]).filter((v): v is string => typeof v === 'string').slice(0, 2) : [],
        confidence: Number(items[i]?.confidence) || 2,
        revealed: items[i]?.revealed === true,
      }));
      const prior = await priorRecords(repoId, answered.map((a) => a.unit_id), token, env, doFetch);
      if (prior) {
        const rows = sittingRows(markRevealed(mergeGrades(answered, grades)), prior, {
          repoId,
          sha: body.sha,
          ts: new Date(record.ts).toISOString(),
          source: SOURCES.find((s) => s === record.source) ?? 'quiz',
        });
        recorded = await insertRecords(rows, token, env, doFetch);
      }
    }
    return json(200, { grades, recorded });
  } catch (e) {
    return errorResponse(e);
  }
}

/**
 * GET /api/excerpt?repo=&sha=&path=&start=&end= -> {path, start, end, lines, cut}; never cached.
 * Without a token: a registered sample at its pin only (mayRead).
 */
export async function handleExcerpt(req: Request, opts: HandlerDeps = {}): Promise<Response> {
  const env = opts.env ?? readEnv();
  const doFetch = opts.fetch ?? fetch;
  try {
    if (req.method !== 'GET') throw new HttpError(405, 'Use GET.', { error: 'Use GET.' });
    bearerOrNull(req);
    const q = new URL(req.url).searchParams;
    const g = parseGitHubUrl(q.get('repo') ?? '');
    const sha = safeSha(q.get('sha'));
    const start = Number(q.get('start') ?? 1);
    const end = Number(q.get('end') ?? start + 59);
    const ref = parseRef(`${q.get('path') ?? ''}:${Number.isFinite(start) ? start : 1}-${Number.isFinite(end) ? end : 60}`);
    if (!g || !sha || !ref) throw new HttpError(400, 'Bad excerpt request.', { error: 'repo, sha (40 characters) and a path inside the repository are needed.' });
    await mayRead(req, g.url, sha, env, doFetch, opts.open ?? openToAnyone);
    const got = await excerpt(g, sha, { ...ref, end: Math.min(ref.end, ref.start + MAX_LINES - 1) }, env, doFetch);
    if (!got) return json(404, { error: `${ref.path} could not be read at ${sha.slice(0, 8)} (missing, binary or too large).` });
    return json(200, got);
  } catch (e) {
    return errorResponse(e);
  }
}

/** The most directories one /api/tree request lists (a five-question sitting names at most 20). */
export const MAX_TREE_DIRS = 20;

/**
 * GET /api/tree?repo=&sha=&path=<directory>[&path=...] -> {sha, dirs: {<directory>: {files:
 * [{path, size}], total}}, truncated}: the top files under each directory at the sha, by the
 * rule in src/core/topfiles.ts; never cached. 503 with `retry_after` (seconds) while GitHub
 * refuses tree requests. Without a token: a registered sample at its pin only (mayRead).
 */
export async function handleTree(req: Request, opts: HandlerDeps & { now?: () => number } = {}): Promise<Response> {
  const env = opts.env ?? readEnv();
  const doFetch = opts.fetch ?? fetch;
  const now = opts.now ?? Date.now;
  try {
    if (req.method !== 'GET') throw new HttpError(405, 'Use GET.', { error: 'Use GET.' });
    bearerOrNull(req);
    const q = new URL(req.url).searchParams;
    const g = parseGitHubUrl(q.get('repo') ?? '');
    const sha = safeSha(q.get('sha'));
    const asked = q.getAll('path');
    const dirs = [...new Set(asked.map((p) => safePath(p.trim().replace(/\/+$/, ''))).filter((p): p is string => p !== null))];
    if (!g || !sha || !dirs.length || asked.length > MAX_TREE_DIRS)
      throw new HttpError(400, 'Bad tree request.', { error: `repo, sha (40 characters) and 1 to ${MAX_TREE_DIRS} directories inside the repository are needed.` });
    await mayRead(req, g.url, sha, env, doFetch, opts.open ?? openToAnyone);
    const got = await dirFiles(g, sha, dirs, env, doFetch, now());
    if (!got) return json(404, { error: `The tree of ${g.owner}/${g.repo} could not be read at ${sha.slice(0, 8)}.` });
    return json(200, { sha, ...got });
  } catch (e) {
    if (e instanceof GitHubRefused) {
      const wait = String(Math.max(1, Math.ceil((e.until - now()) / 1000)));
      return json(503, { error: 'GitHub is refusing tree requests from this server for now; questions on this unit are written without code until then.', retry_after: Number(wait) }, { 'retry-after': wait });
    }
    return errorResponse(e);
  }
}
