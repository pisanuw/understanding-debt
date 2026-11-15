/**
 * What the app reads: a repository, its analysis at a pinned commit (the worker's
 * `specs.json` and `analysis.json`, loaded by `udebt worker sql`), and the helpers the
 * screens share. Specs hold paths, line ranges and the pinned sha, never code: excerpts are
 * fetched from GitHub at that sha when a walkthrough is shown or an answer graded.
 */

export interface Orientation {
  purpose: string;
  decision: string;
  where: { path: string; what?: string }[];
  shaped_by: string;
  fallback?: boolean;
}

export interface HistoryEntry {
  sha: string;
  date?: string | null;
  klass?: string | null;
  agent?: string | null;
  author?: string | null;
  subject?: string | null;
}

export interface SpecUnit {
  id: string;
  name: string;
  kind?: string | null;
  summary?: string | null;
  decision?: string | null;
  alternatives?: string | null;
  why?: string | null;
  paths: string[];
  centrality?: number | null;
  scope?: string | null;
  importance?: number | null;
  history?: HistoryEntry[];
  parent: string | null;
  orientation?: Orientation | null;
  /** The top tracked files under the unit's directories, recorded by the worker (udebt/topfiles.py); absent in older analyses. */
  files?: string[] | null;
}

export interface WalkStep {
  file: string;
  start_line: number;
  end_line: number;
  narration: string;
  /** Code lines, only in a worker output made with excerpts (never for the hosted app). */
  lines?: string[] | null;
}

export interface Walkthrough {
  flow?: string | null;
  steps: WalkStep[];
  trap?: string | null;
  head?: string | null;
}

export interface Specs {
  units: SpecUnit[];
  connections?: [string, string, string, number][];
  hubs?: string[];
  order?: string[];
  tour?: { markdown: string; minutes: number } | null;
  walkthroughs?: Record<string, Walkthrough>;
  /** Per earlier analysed commit, the share of each unit changed since (the decay's input). */
  changed_since?: Record<string, Record<string, number>>;
}

export interface Manifest {
  repo: string;
  url: string;
  sha: string;
  generated_at?: string;
  counts?: Record<string, unknown>;
  code_excerpts?: boolean;
  map_source?: string;
  llm_calls?: number;
}

export interface Repository {
  id: number;
  url: string;
  name: string;
  license: string | null;
  is_sample: boolean;
}

export interface Analysis {
  id: number;
  repo_id: number;
  sha: string;
  /** Null for an analysis HintWise loaded (a sample); else the person whose worker run wrote it. */
  user_id: string | null;
  created_at: string;
  manifest: Manifest;
  specs: Specs;
}

/** The units without a parent, in the analysis's order. */
export function topLevel(units: SpecUnit[]): SpecUnit[] {
  return units.filter((u) => !u.parent);
}

export function childrenOf(units: SpecUnit[], id: string): SpecUnit[] {
  return units.filter((u) => u.parent === id);
}

export function unitById(units: SpecUnit[], id: string): SpecUnit | undefined {
  return units.find((u) => u.id === id);
}

export function architecturalUnits(units: SpecUnit[]): SpecUnit[] {
  return units.filter((u) => (u.scope ?? 'architectural') === 'architectural');
}

export const MIN_USES = 0.5;

/** uses, used by, changes with (tour.connections over the unit graph's edges). */
export function connections(specs: Specs, id: string): { uses: string[]; used_by: string[]; changes_with: string[] } {
  const edges = specs.connections ?? [];
  const sort = (xs: [string, number][]) => xs.sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0)).map((x) => x[0]);
  const uses = sort(edges.filter((e) => e[2] === 'uses' && e[0] === id && e[3] >= MIN_USES).map((e) => [e[1], e[3]]));
  const usedBy = sort(edges.filter((e) => e[2] === 'uses' && e[1] === id && e[3] >= MIN_USES).map((e) => [e[0], e[3]]));
  const together = sort(
    edges.filter((e) => e[2] === 'together' && (e[0] === id || e[1] === id) && e[3] >= 0.05).map((e) => [e[0] === id ? e[1] : e[0], e[3]]),
  ).slice(0, 3);
  return { uses, used_by: usedBy, changes_with: together };
}

export const MAX_PATHS = 3;

/** The unit's orientation, or one made from its own fields (orientation.fallback). */
export function orientationOf(u: SpecUnit): Orientation {
  if (u.orientation && u.orientation.purpose) return u.orientation;
  const parts = [String(u.decision ?? '').trim()];
  if (u.alternatives) parts.push(`Not chosen: ${String(u.alternatives).trim()}`);
  if (u.why) parts.push(`Why: ${String(u.why).trim()}`);
  const authors: string[] = [];
  for (const h of u.history ?? []) {
    const who = `${h.author || '?'} [${h.klass ?? '?'}]`;
    if (!authors.includes(who)) authors.push(who);
  }
  return {
    purpose: String(u.summary ?? '').trim() || u.name || u.id,
    decision: parts.filter(Boolean).join(' '),
    where: (u.paths ?? []).slice(0, MAX_PATHS).map((p) => ({ path: p, what: '' })),
    shaped_by: authors.length ? `From its history: ${authors.slice(0, 4).join(', ')}.` : 'The history list does not say.',
    fallback: true,
  };
}

function whereText(o: Orientation): string {
  return o.where.map((w) => (w.what ? `${w.path} (${w.what})` : w.path)).join('; ');
}

/** The orientation as plain sentences, for the question writer (orientation.text). */
export function orientationText(o: Orientation): string {
  return `What it is for: ${o.purpose} The decision: ${o.decision} Where it lives: ${whereText(o)}. Who shaped it: ${o.shaped_by}`;
}

const GITHUB = /^https:\/\/github\.com\/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))\/([A-Za-z0-9._-]{1,100}?)(?:\.git)?\/?$/;

/** owner and repository of a public GitHub URL, or null for anything else. */
export function parseGitHubUrl(url: string): { owner: string; repo: string; url: string } | null {
  const m = GITHUB.exec(String(url).trim());
  if (!m || m[2] === '.' || m[2] === '..') return null;
  return { owner: m[1], repo: m[2], url: `https://github.com/${m[1]}/${m[2]}` };
}

/** A link to a file, a line range or a commit on GitHub at the pinned sha. */
export function githubLink(repoUrl: string, sha: string, path?: string, start?: number, end?: number): string | null {
  const g = parseGitHubUrl(repoUrl);
  if (!g || !/^[0-9a-f]{7,40}$/.test(sha)) return null;
  if (!path) return `${g.url}/commit/${sha}`;
  const enc = path.split('/').map(encodeURIComponent).join('/');
  return `${g.url}/blob/${sha}/${enc}${start ? `#L${start}${end && end !== start ? `-L${end}` : ''}` : ''}`;
}

/** The analysis to show for a repository: HintWise's own (a sample), else the person's, else the newest shared. */
export function pickAnalysis(analyses: Analysis[], me: string | null): Analysis | null {
  const newest = (xs: Analysis[]) => [...xs].sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0))[0] ?? null;
  return newest(analyses.filter((a) => a.user_id === null)) ?? newest(analyses.filter((a) => me !== null && a.user_id === me)) ?? newest(analyses);
}
