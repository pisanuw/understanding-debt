/**
 * Writing and grading a sitting's questions, the same in the Netlify functions (the HintWise
 * gateway, with the person's token) and in the browser (the person's own key): validate the
 * request, fetch the code excerpts at the pinned sha, build the prompt, make one model call,
 * read the `submit` tool's answer. `deps` carries the two things that differ: how an excerpt
 * is fetched and how the model is called.
 */
import { safeSha, parseRef, refsForUnit, withinBudget, type Excerpt, type ExcerptRef } from './excerpts';
import { levelOf } from './ladder';
import { SUBMIT_GRADES, SUBMIT_QUESTIONS, gradePrompt, questionsPrompt, submitted, type GradeItem, type ModelMessage, type QuestionUnit, type Tool } from './prompts';
import { oneFact, type Grade, type Question } from './quiz';
import { parseGitHubUrl } from './specs';

/** The gateway's limits (docs/new-app-guide.md section 7): a body of at most 1,000,000 bytes, max_tokens clamped to 4096. */
export const MAX_BODY_BYTES = 1_000_000;
export const MAX_TOKENS = 4096;
/** The most characters of code excerpts in one request (about 15,000 tokens): a quiz call stays cheap under the gateway caps. */
export const EXCERPT_BUDGET = 60_000;
export const MAX_UNITS = 12;
export const MAX_ITEMS = 20;

export interface ModelRequest {
  model?: string;
  max_tokens: number;
  system?: string;
  messages: { role: 'user' | 'assistant'; content: string }[];
  tools?: Tool[];
}

export interface Deps {
  excerpt(repo: { owner: string; repo: string; url: string }, sha: string, ref: ExcerptRef): Promise<Excerpt | null>;
  ask(body: ModelRequest): Promise<ModelMessage>;
}

export interface QuestionsRequest {
  repo: string;
  sha: string;
  per_unit?: number;
  units: (QuestionUnit & { refs?: ExcerptRef[] })[];
}

export interface GradeRequest {
  repo: string;
  sha: string;
  items: GradeItem[];
}

export class BadRequest extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'BadRequest';
  }
}

export class ModelAnswerError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ModelAnswerError';
  }
}

const str = (v: unknown, max: number): string => (typeof v === 'string' ? v.slice(0, max) : '');
const strs = (v: unknown, n: number, max: number): string[] => (Array.isArray(v) ? v.filter((x) => typeof x === 'string').slice(0, n).map((x: string) => x.slice(0, max)) : []);

function repoAndSha(body: Record<string, unknown>) {
  const repo = parseGitHubUrl(str(body.repo, 300));
  if (!repo) throw new BadRequest('repo must be a public https://github.com/<owner>/<name> URL.');
  const sha = safeSha(body.sha);
  if (!sha) throw new BadRequest('sha must be a full 40-character commit sha.');
  return { repo, sha };
}

/** A questions request with every field checked and capped. */
export function readQuestionsRequest(body: unknown): QuestionsRequest {
  const b = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>;
  const { repo, sha } = repoAndSha(b);
  if (!Array.isArray(b.units) || !b.units.length) throw new BadRequest('units: at least one unit to ask.');
  if (b.units.length > MAX_UNITS) throw new BadRequest(`units: at most ${MAX_UNITS} in one sitting.`);
  const units = b.units.map((x: unknown) => {
    const u = (x && typeof x === 'object' ? x : {}) as Record<string, unknown>;
    const id = str(u.id, 200);
    if (!id) throw new BadRequest('Each unit needs an id.');
    const refs = (Array.isArray(u.refs) ? u.refs : [])
      .slice(0, 6)
      .map((r: unknown) => {
        const o = (r && typeof r === 'object' ? r : {}) as Record<string, unknown>;
        const ref = parseRef(`${str(o.path, 400)}:${Number(o.start) || 1}-${Number(o.end) || 60}`);
        return ref;
      })
      .filter((r): r is ExcerptRef => r !== null);
    return {
      id,
      name: str(u.name, 300) || id,
      parent_name: str(u.parent_name, 300) || undefined,
      level: levelOf({ level: u.level }),
      summary: str(u.summary, 2000),
      decision: str(u.decision, 3000),
      alternatives: str(u.alternatives, 2000),
      why: str(u.why, 2000),
      paths: strs(u.paths, 40, 400),
      history: (Array.isArray(u.history) ? u.history : []).slice(0, 5).map((h: unknown) => {
        const o = (h && typeof h === 'object' ? h : {}) as Record<string, unknown>;
        return { sha: str(o.sha, 40), date: str(o.date, 30), klass: str(o.klass, 20), subject: str(o.subject, 200) };
      }),
      shown_orientation: str(u.shown_orientation, 3000) || undefined,
      revealed: strs(u.revealed, 3, 1000),
      refs,
    };
  });
  const per = Math.min(3, Math.max(1, Math.floor(Number(b.per_unit) || 1)));
  return { repo: repo.url, sha, per_unit: per, units };
}

/** A grading request with every field checked and capped. */
export function readGradeRequest(body: unknown): GradeRequest {
  const b = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>;
  const { repo, sha } = repoAndSha(b);
  if (!Array.isArray(b.items) || !b.items.length) throw new BadRequest('items: at least one answer to grade.');
  if (b.items.length > MAX_ITEMS) throw new BadRequest(`items: at most ${MAX_ITEMS} in one sitting.`);
  const items = b.items.map((x: unknown) => {
    const it = (x && typeof x === 'object' ? x : {}) as Record<string, unknown>;
    const question = str(it.question, 2000);
    if (!question) throw new BadRequest('Each item needs its question.');
    return {
      unit_id: str(it.unit_id, 200),
      level: levelOf({ level: it.level }),
      question,
      reference_answer: str(it.reference_answer, 3000),
      evidence: strs(it.evidence, 6, 400),
      answer: str(it.answer, 4000),
    };
  });
  return { repo: repo.url, sha, items };
}

async function fetchAll(deps: Deps, repo: string, sha: string, refs: ExcerptRef[]): Promise<Excerpt[]> {
  const g = parseGitHubUrl(repo);
  if (!g) return [];
  const seen = new Set<string>();
  const unique = refs.filter((r) => {
    const k = `${r.path}:${r.start}-${r.end}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  const got = await Promise.all(unique.map((r) => deps.excerpt(g, sha, r).catch(() => null)));
  return withinBudget(got.filter((x): x is Excerpt => x !== null && x.lines.length > 0), EXCERPT_BUDGET);
}

function bodyBytes(body: ModelRequest): number {
  return new TextEncoder().encode(JSON.stringify({ app: 'udebt', ...body })).length;
}

/** Drops excerpts from the end until the request fits the gateway's body limit. */
export async function fitted(make: (excerpts: Excerpt[]) => ModelRequest, excerpts: Excerpt[]): Promise<ModelRequest> {
  let keep = excerpts;
  let body = make(keep);
  while (bodyBytes(body) > MAX_BODY_BYTES && keep.length) {
    keep = keep.slice(0, -1);
    body = make(keep);
  }
  if (bodyBytes(body) > MAX_BODY_BYTES) throw new BadRequest('The request is larger than the gateway accepts, even without code excerpts.');
  return body;
}

/** One call writes every question; each keeps its unit's level and asks one fact. */
export async function writeQuestions(req: QuestionsRequest, deps: Deps): Promise<Question[]> {
  const refs = req.units.flatMap((u) => u.refs ?? []);
  const excerpts = await fetchAll(deps, req.repo, req.sha, refs);
  const units = req.units.map(({ refs: _refs, ...u }) => u);
  const body = await fitted(
    (ex) => ({ max_tokens: MAX_TOKENS, messages: [{ role: 'user', content: questionsPrompt(units, req.per_unit ?? 1, { repo: req.repo, sha: req.sha, excerpts: ex }) }], tools: [SUBMIT_QUESTIONS] }),
    excerpts,
  );
  const answer = submitted(await deps.ask(body));
  const asked = new Map(req.units.map((u) => [u.id, u.level]));
  const raw = Array.isArray(answer?.questions) ? (answer.questions as Record<string, unknown>[]) : [];
  const qs = raw
    .filter((q) => typeof q?.unit_id === 'string' && asked.has(q.unit_id as string) && typeof q.question === 'string' && q.question)
    .map((q) =>
      oneFact(
        {
          unit_id: q.unit_id as string,
          level: asked.get(q.unit_id as string) as number,
          question: q.question as string,
          variants: strs(q.variants, 2, 2000),
          reference_answer: str(q.reference_answer, 3000),
          evidence: strs(q.evidence, 6, 400),
        },
        asked.get(q.unit_id as string) as number,
      ),
    );
  if (!qs.length) throw new ModelAnswerError('No questions were written. Try again in a moment.');
  return qs;
}

/** One call grades every non-blank answer; returns the grades by item index. */
export async function gradeAnswers(req: GradeRequest, deps: Deps): Promise<Grade[]> {
  const items = req.items.filter((it) => it.answer.trim());
  if (!items.length) return [];
  const refs = items.flatMap((it) => it.evidence.map((e) => parseRef(e)).filter((r): r is ExcerptRef => r !== null).slice(0, 4));
  const excerpts = await fetchAll(deps, req.repo, req.sha, refs);
  const body = await fitted(
    (ex) => ({ max_tokens: MAX_TOKENS, messages: [{ role: 'user', content: gradePrompt(items, { repo: req.repo, sha: req.sha, excerpts: ex }) }], tools: [SUBMIT_GRADES] }),
    excerpts,
  );
  const answer = submitted(await deps.ask(body));
  if (!Array.isArray(answer?.grades)) throw new ModelAnswerError('The grader returned no grades. Your answers are kept; try again.');
  return (answer.grades as Record<string, unknown>[]).map((g) => ({
    index: Number(g.index),
    verdict: (['correct', 'partial', 'wrong', 'unknown'].includes(String(g.verdict)) ? g.verdict : 'wrong') as Grade['verdict'],
    feedback: str(g.feedback, 2000),
  }));
}

export { refsForUnit };
