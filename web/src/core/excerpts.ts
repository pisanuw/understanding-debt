/**
 * Code excerpts by reference: a path and a line range in the repository at the analysis's
 * pinned sha. Nothing here stores code; the excerpt proxy (netlify/functions/excerpt.ts)
 * fetches the file from GitHub for one request and returns only the lines asked for.
 */
import type { SpecUnit, Walkthrough } from './specs';

export interface ExcerptRef {
  path: string;
  start: number;
  end: number;
}

export interface Excerpt extends ExcerptRef {
  lines: string[];
  /** Lines of the range left out by the cap. */
  cut: number;
}

/** The most lines one excerpt returns (the proxy's cap; a walkthrough step is at most 25). */
export const MAX_LINES = 200;
/** Lines read from the top of a file cited without a range. */
export const DEFAULT_LINES = 80;
/** A file larger than this is not read (bytes). */
export const MAX_FILE_BYTES = 2_000_000;

/** A repository-relative path that cannot leave the repository, or null. */
export function safePath(path: unknown): string | null {
  if (typeof path !== 'string') return null;
  const p = path.trim().replace(/^\.\//, '');
  if (!p || p.length > 400 || p.startsWith('/') || p.includes('\\') || [...p].some((c) => c.charCodeAt(0) < 32 || c.charCodeAt(0) === 127)) return null;
  const segs = p.split('/');
  if (segs.some((s) => s === '..' || s === '.' || s === '')) return null;
  if (segs[0] === '.git' || segs[0] === '.udebt') return null;
  return p;
}

const SHA = /^[0-9a-f]{40}$/;

export function safeSha(sha: unknown): string | null {
  return typeof sha === 'string' && SHA.test(sha) ? sha : null;
}

function clampRange(start: number, end: number): { start: number; end: number } {
  const s = Number.isFinite(start) && start >= 1 ? Math.floor(start) : 1;
  const e = Number.isFinite(end) && end >= s ? Math.floor(end) : s + DEFAULT_LINES - 1;
  return { start: s, end: Math.min(e, s + MAX_LINES - 1) };
}

/** "path", "path:12", "path:12-30", "path#L12-L30" or "path (lines 12-30)" as a reference; null when the path is unsafe. */
export function parseRef(text: string, fallbackLines = DEFAULT_LINES): ExcerptRef | null {
  const t = String(text).trim();
  const m = /^(.+?)(?::(\d+)(?:\s*-\s*(\d+))?|#L(\d+)(?:-L?(\d+))?|\s+\(lines?\s+(\d+)(?:\s*-\s*(\d+))?\))?$/.exec(t);
  if (!m) return null;
  const path = safePath(m[1]);
  if (!path) return null;
  const a = m[2] ?? m[4] ?? m[6];
  const b = m[3] ?? m[5] ?? m[7];
  if (!a) return { path, start: 1, end: fallbackLines };
  const start = Number(a);
  return { path, ...clampRange(start, b ? Number(b) : start + Math.min(fallbackLines, 40) - 1) };
}

/** The lines `start`..`end` (1-based, inclusive) of a file's text, at most MAX_LINES. */
export function sliceLines(text: string, ref: ExcerptRef): Excerpt {
  const all = text.replace(/\r\n?/g, '\n').split('\n');
  if (all.length && all[all.length - 1] === '') all.pop();
  const { start, end } = clampRange(ref.start, ref.end);
  const want = Math.max(0, Math.min(end, all.length) - start + 1);
  const lines = all.slice(start - 1, start - 1 + want);
  const asked = Math.max(0, Math.min(ref.end, all.length) - start + 1);
  return { path: ref.path, start, end: start + lines.length - 1, lines, cut: Math.max(0, asked - lines.length) };
}

/** What the question writer sees of a unit: its walkthrough's steps, else the top of the files its orientation points to. */
export function refsForUnit(u: SpecUnit, walk?: Walkthrough | null, limit = 4): ExcerptRef[] {
  const out: ExcerptRef[] = [];
  const add = (r: ExcerptRef | null) => {
    if (r && out.length < limit && !out.some((x) => x.path === r.path && x.start === r.start)) out.push(r);
  };
  for (const st of walk?.steps ?? []) {
    const path = safePath(st.file);
    if (path) add({ path, ...clampRange(st.start_line, st.end_line) });
  }
  if (!out.length) {
    const where = u.orientation?.where?.map((w) => w.path) ?? [];
    for (const p of [...where, ...(u.paths ?? [])]) {
      const path = safePath(p);
      if (path && !path.endsWith('/') && /\.[A-Za-z0-9]{1,6}$/.test(path)) add({ path, start: 1, end: 60 });
    }
  }
  return out;
}

/** Excerpts as the prompts show them: a header per excerpt, then numbered lines. */
export function formatExcerpts(excerpts: Excerpt[]): string {
  return excerpts
    .map((x) => {
      const head = `--- ${x.path} lines ${x.start}-${x.end}`;
      const body = x.lines.map((l, i) => `${String(x.start + i).padStart(5)} | ${l}`).join('\n');
      return x.cut ? `${head}\n${body}\n      ... ${x.cut} more line(s)` : `${head}\n${body}`;
    })
    .join('\n\n');
}

/** Keeps excerpts in order until their formatted size reaches `maxChars`. */
export function withinBudget(excerpts: Excerpt[], maxChars: number): Excerpt[] {
  const out: Excerpt[] = [];
  let used = 0;
  for (const x of excerpts) {
    const size = formatExcerpts([x]).length + 2;
    if (used + size > maxChars) break;
    out.push(x);
    used += size;
  }
  return out;
}
