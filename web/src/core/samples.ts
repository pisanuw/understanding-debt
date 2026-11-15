/**
 * The registered samples as the site serves them to visitors who are not signed in (HOSTING.md
 * decision 10): `public/samples/index.json`, written by `python -m udebt.hosted.static_samples`
 * from udebt/hosted/samples.py's SAMPLES and the committed load scripts. Each entry names the
 * sample's pinned commit and, when they exist, its rows (`<name>.json`, what `udebt worker sql
 * --dialect json --public` writes) and its advance quizzes (`<name>.quizzes.json`, from `udebt
 * worker quizzes`). The page reads the index at run time; the functions bundle it to open
 * /api/excerpt and /api/tree to these repositories at these commits without a token.
 */
import { safeSha } from './excerpts';
import { parseGitHubUrl } from './specs';

export const SAMPLES_FORMAT = 'udebt-samples';
export const SAMPLES_VERSION = 1;

export interface SampleEntry {
  name: string;
  url: string;
  license: string | null;
  sha: string;
  /** The rows file next to the index, or null while the sample has no analysis. */
  analysis: string | null;
  /** The advance-quiz file next to the index, or null while none was written. */
  quizzes: string | null;
}

const FILE = /^[A-Za-z0-9._-]{1,120}\.json$/;

function file(v: unknown): string | null {
  return typeof v === 'string' && FILE.test(v) && !v.startsWith('.') ? v : null;
}

/** The index's entries, each checked (a public GitHub URL, a full sha, plain file names); [] for anything else or a newer format. */
export function readIndex(json: unknown): SampleEntry[] {
  const d = (json && typeof json === 'object' ? json : {}) as Record<string, unknown>;
  if (d.format !== SAMPLES_FORMAT || typeof d.version !== 'number' || d.version > SAMPLES_VERSION || !Array.isArray(d.samples)) return [];
  const out: SampleEntry[] = [];
  for (const raw of d.samples as unknown[]) {
    const s = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
    const g = parseGitHubUrl(typeof s.url === 'string' ? s.url : '');
    const sha = safeSha(s.sha);
    if (!g || !sha || g.url !== s.url || typeof s.name !== 'string' || !s.name || out.some((x) => x.url === g.url)) continue;
    out.push({ name: s.name, url: g.url, license: typeof s.license === 'string' ? s.license : null, sha, analysis: file(s.analysis), quizzes: file(s.quizzes) });
  }
  return out;
}

/** True when `repoUrl` at `sha` is a registered sample at its pin. */
export function atPin(entries: readonly SampleEntry[], repoUrl: string, sha: string): boolean {
  return entries.some((s) => s.url === repoUrl && s.sha === sha);
}
