/**
 * The tree fallback on the page: for a unit that names only directories (no walkthrough, no
 * file among its orientation and paths, no `files` recorded by the worker), the top files
 * under those directories at the analysis's sha, from /api/tree, for the question writer to
 * read. The same in both modes (the own-key client fetches excerpts through /api/excerpt too).
 *
 * The listings are kept in memory per analysis (repository and sha) and directory for this page
 * session, never in storage: each directory is asked at most once, and every directory a sitting
 * needs goes in one request. A failed request (GitHub's refusal, a network error) is not asked
 * again for FAIL_MS; until then the unit's question is written without code, with the prompt's
 * note, as before.
 */
import { dirPaths, refsForUnit, topRefs, type ExcerptRef } from '../core/excerpts';
import { pickAcross } from '../core/topfiles';
import type { SpecUnit, Walkthrough } from '../core/specs';

export interface TreeAnswer {
  sha?: string;
  dirs: Record<string, { files: { path: string; size: number }[]; total?: number }>;
  truncated?: boolean;
}

export type TreeFetch = (repoUrl: string, sha: string, dirs: string[]) => Promise<TreeAnswer>;

/** How long a failed listing is not asked again (ms). */
export const FAIL_MS = 10 * 60 * 1000;
/** The most directories in one request (the function's MAX_TREE_DIRS). */
export const DIRS_PER_REQUEST = 20;

export class TreeCache {
  private readonly known = new Map<string, Promise<string[] | null>>();
  private readonly retryAt = new Map<string, number>();
  /** Requests made to /api/tree, for tests and the walkthrough. */
  requests = 0;

  constructor(
    private readonly fetchTree: TreeFetch,
    private readonly now: () => number = Date.now,
  ) {}

  /** The top files of each directory at `sha` ([] for one that cannot be listed now). */
  async files(repoUrl: string, sha: string, dirs: readonly string[]): Promise<Map<string, string[]>> {
    const key = (d: string) => `${repoUrl}\n${sha}\n${d}`;
    const now = this.now();
    for (const d of dirs) {
      const until = this.retryAt.get(key(d));
      if (until !== undefined && until <= now) {
        this.known.delete(key(d));
        this.retryAt.delete(key(d));
      }
    }
    const want = [...new Set(dirs)].filter((d) => !this.known.has(key(d)));
    for (let i = 0; i < want.length; i += DIRS_PER_REQUEST) {
      const chunk = want.slice(i, i + DIRS_PER_REQUEST);
      this.requests += 1;
      const answer = this.fetchTree(repoUrl, sha, chunk).catch(() => null);
      for (const d of chunk) {
        this.known.set(
          key(d),
          answer.then((a) => {
            if (!a) {
              this.retryAt.set(key(d), now + FAIL_MS);
              return null;
            }
            return (a.dirs?.[d]?.files ?? []).map((f) => f.path).filter((p) => typeof p === 'string');
          }),
        );
      }
    }
    const out = new Map<string, string[]>();
    for (const d of dirs) out.set(d, (await this.known.get(key(d))) ?? []);
    return out;
  }

  /**
   * Each unit's excerpt references: refsForUnit's, and for every unit it finds no file for, the
   * top files under its directories (one request for all of them), lines 1-60 of each.
   */
  async refsFor(repoUrl: string, sha: string, units: readonly { unit: SpecUnit; walk?: Walkthrough | null }[]): Promise<ExcerptRef[][]> {
    const refs = units.map((x) => refsForUnit(x.unit, x.walk));
    const dirs = units.map((x, i) => (refs[i].length ? [] : dirPaths(x.unit)));
    const all = dirs.flat();
    if (!all.length) return refs;
    const listed = await this.files(repoUrl, sha, all);
    return refs.map((r, i) => (r.length ? r : topRefs(pickAcross(dirs[i].map((d) => listed.get(d) ?? [])))));
  }
}
