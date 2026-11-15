/**
 * The three functions behind /api: question writing and grading through the HintWise
 * gateway, and the excerpt proxy. Each takes a web-standard Request and returns a Response,
 * so Netlify (netlify/functions/*.ts), the dev and preview servers (dev/api-plugin.ts) and
 * the tests call the same code.
 */
import { gradeAnswers, readGradeRequest, readQuestionsRequest, writeQuestions, type Deps } from '../../../src/core/assemble';
import { MAX_LINES, parseRef, safeSha } from '../../../src/core/excerpts';
import { markRevealed, mergeGrades, recordRows, type Answered } from '../../../src/core/quiz';
import { parseGitHubUrl } from '../../../src/core/specs';
import { authorize } from './auth';
import { readEnv, type FnEnv } from './env';
import { callGateway } from './gateway';
import { excerpt } from './github';
import { HttpError, bearer, errorResponse, json, readJson } from './http';
import { insertRecords } from './records';

export interface HandlerDeps {
  env?: FnEnv;
  fetch?: typeof fetch;
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

/**
 * POST /api/grade: {repo, sha, items, record?: {repo_id, ts, source?}} -> {grades,
 * recorded}. With `record`, the graded sitting is written to app_udebt.records with the
 * person's token and `recorded` holds the rows; null means the page records them itself.
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
      const rows = recordRows(markRevealed(mergeGrades(answered, grades)), {
        repoId,
        sha: body.sha,
        ts: new Date(record.ts).toISOString(),
        source: record.source === 'dig' ? 'dig' : 'quiz',
      });
      recorded = await insertRecords(rows, token, env, doFetch);
    }
    return json(200, { grades, recorded });
  } catch (e) {
    return errorResponse(e);
  }
}

/** GET /api/excerpt?repo=&sha=&path=&start=&end= -> {path, start, end, lines, cut}; never cached. */
export async function handleExcerpt(req: Request, opts: HandlerDeps = {}): Promise<Response> {
  const env = opts.env ?? readEnv();
  const doFetch = opts.fetch ?? fetch;
  try {
    if (req.method !== 'GET') throw new HttpError(405, 'Use GET.', { error: 'Use GET.' });
    const token = bearer(req);
    const q = new URL(req.url).searchParams;
    const g = parseGitHubUrl(q.get('repo') ?? '');
    const sha = safeSha(q.get('sha'));
    const start = Number(q.get('start') ?? 1);
    const end = Number(q.get('end') ?? start + 59);
    const ref = parseRef(`${q.get('path') ?? ''}:${Number.isFinite(start) ? start : 1}-${Number.isFinite(end) ? end : 60}`);
    if (!g || !sha || !ref) throw new HttpError(400, 'Bad excerpt request.', { error: 'repo, sha (40 characters) and a path inside the repository are needed.' });
    await authorize(token, g.url, env, doFetch);
    const got = await excerpt(g, sha, { ...ref, end: Math.min(ref.end, ref.start + MAX_LINES - 1) }, env, doFetch);
    if (!got) return json(404, { error: `${ref.path} could not be read at ${sha.slice(0, 8)} (missing, binary or too large).` });
    return json(200, got);
  } catch (e) {
    return errorResponse(e);
  }
}
