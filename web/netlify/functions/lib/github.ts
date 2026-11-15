/**
 * One file of a public GitHub repository at a commit, for one request: fetched from
 * raw.githubusercontent.com (the URL is built from a checked owner, name, 40-character sha
 * and a path that cannot leave the repository), at most MAX_FILE_BYTES, never cached or
 * stored. With the fakes on it is read from a local clone with `git show <sha>:<path>`.
 *
 * And the tree fallback's listing: the files of the repository at a commit, with their sizes,
 * from GitHub's recursive git trees API (one unauthenticated call for the whole tree at that
 * sha; a directory's own subtree only when GitHub truncates the whole one), kept in memory per
 * repository and sha (a tree at a sha never changes; never across shas, never stored). When
 * GitHub refuses (its rate limit), no call is made until the time it gives. With the fakes on
 * it is `git ls-tree -r -l <sha>` of the local clone.
 */
import { execFile } from 'node:child_process';
import { MAX_FILE_BYTES, safePath, safeSha, sliceLines, type Excerpt, type ExcerptRef } from '../../../src/core/excerpts';
import { TOP_FILES, topUnder, type TreeEntry } from '../../../src/core/topfiles';
import type { FnEnv } from './env';

export const RAW_BASE = 'https://raw.githubusercontent.com';
export const API_BASE = 'https://api.github.com';
const NAME = /^[A-Za-z0-9._-]{1,100}$/;

async function readCapped(res: Response, cap: number): Promise<string | null> {
  const declared = Number(res.headers.get('content-length') ?? 0);
  if (declared > cap) return null;
  if (!res.body) return (await res.text()).slice(0, cap);
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.length;
    if (size > cap) {
      await reader.cancel().catch(() => undefined);
      return null;
    }
    chunks.push(value);
  }
  const all = new Uint8Array(size);
  let at = 0;
  for (const c of chunks) {
    all.set(c, at);
    at += c.length;
  }
  return new TextDecoder('utf-8', { fatal: false }).decode(all);
}

function gitShow(root: string, sha: string, path: string): Promise<string | null> {
  return new Promise((resolve) => {
    execFile('git', ['-C', root, 'show', `${sha}:${path}`], { maxBuffer: MAX_FILE_BYTES, encoding: 'utf8' }, (err, stdout) => resolve(err ? null : stdout));
  });
}

/** The file's text, or null when it does not exist at that commit, is too large, or cannot be read. */
export async function fetchFile(owner: string, repo: string, sha: string, path: string, env: FnEnv, doFetch: typeof fetch = fetch): Promise<string | null> {
  const p = safePath(path);
  if (!NAME.test(owner) || !NAME.test(repo) || !safeSha(sha) || !p) return null;
  if (env.fakes) {
    const root = env.fakeRepos[`${owner}/${repo}`] ?? env.fakeRepos['*'];
    return root ? gitShow(root, sha, p) : null;
  }
  const url = `${RAW_BASE}/${owner}/${repo}/${sha}/${p.split('/').map(encodeURIComponent).join('/')}`;
  let res: Response;
  try {
    res = await doFetch(url, { redirect: 'error', headers: { accept: 'text/plain' } });
  } catch {
    return null;
  }
  if (res.status !== 200) return null;
  return readCapped(res, MAX_FILE_BYTES);
}

/** The lines of `ref` at `sha`, or null. */
export async function excerpt(g: { owner: string; repo: string }, sha: string, ref: ExcerptRef, env: FnEnv, doFetch: typeof fetch = fetch): Promise<Excerpt | null> {
  const text = await fetchFile(g.owner, g.repo, sha, ref.path, env, doFetch);
  if (text === null) return null;
  if (text.includes('\u0000')) return null; // a binary file
  return sliceLines(text, ref);
}

/** The most bytes of one tree listing (GitHub stops a recursive tree at 7 MB). */
export const TREE_MAX_BYTES = 10_000_000;
/** How long a refusal that names no time keeps the function from asking GitHub again (ms). */
export const REFUSAL_MS = 60_000;
/** Whole-repository listings kept in memory (most recent first out). */
const MAX_TREES = 4;
/** Directory subtrees asked for in one request when the whole listing was truncated. */
const MAX_SUBTREES = 4;

export interface Listing {
  entries: TreeEntry[];
  truncated: boolean;
}

/** GitHub refused a tree request (403 or 429); no call is made before `until` (ms since the epoch). */
export class GitHubRefused extends Error {
  constructor(public readonly until: number) {
    super('GitHub refused the tree request.');
    this.name = 'GitHubRefused';
  }
}

const trees = new Map<string, Listing>();
let refusedUntil = 0;

export function resetTreeCache(): void {
  trees.clear();
  refusedUntil = 0;
}

function remember(key: string, listing: Listing): Listing {
  trees.delete(key);
  if (trees.size >= MAX_TREES) trees.delete(trees.keys().next().value as string);
  trees.set(key, listing);
  return listing;
}

/** Blobs of a GitHub tree answer (symlinks and submodules left out), paths under `prefix`. */
export function parseTree(data: unknown, prefix = ''): Listing | null {
  const d = (data && typeof data === 'object' ? data : {}) as { tree?: unknown; truncated?: unknown };
  if (!Array.isArray(d.tree)) return null;
  const entries: TreeEntry[] = [];
  for (const t of d.tree as Record<string, unknown>[]) {
    if (t?.type === 'blob' && t.mode !== '120000' && typeof t.path === 'string' && typeof t.size === 'number') entries.push({ path: prefix + t.path, size: t.size });
  }
  return { entries, truncated: d.truncated === true };
}

async function githubTree(owner: string, repo: string, treeish: string, prefix: string, doFetch: typeof fetch, now: number): Promise<Listing | null> {
  if (now < refusedUntil) throw new GitHubRefused(refusedUntil);
  let res: Response;
  try {
    res = await doFetch(`${API_BASE}/repos/${owner}/${repo}/git/trees/${treeish}?recursive=1`, {
      redirect: 'error',
      headers: { accept: 'application/vnd.github+json', 'x-github-api-version': '2022-11-28', 'user-agent': 'udebt' },
    });
  } catch {
    return null;
  }
  if (res.status === 403 || res.status === 429) {
    const retry = Number(res.headers.get('retry-after'));
    const reset = Number(res.headers.get('x-ratelimit-reset'));
    refusedUntil = retry > 0 ? now + retry * 1000 : res.headers.get('x-ratelimit-remaining') === '0' && reset * 1000 > now ? reset * 1000 : now + REFUSAL_MS;
    throw new GitHubRefused(refusedUntil);
  }
  if (res.status !== 200) return null;
  const text = await readCapped(res, TREE_MAX_BYTES);
  if (text === null) return null;
  try {
    return parseTree(JSON.parse(text), prefix);
  } catch {
    return null;
  }
}

/** `git ls-tree -r -l -z` of a local clone (the fakes): regular files with their sizes. */
export function parseLsTree(out: string): TreeEntry[] {
  const entries: TreeEntry[] = [];
  for (const rec of out.split('\u0000')) {
    const tab = rec.indexOf('\t');
    const meta = rec.slice(0, tab).split(/\s+/);
    if (tab > 0 && meta.length === 4 && meta[1] === 'blob' && meta[0] !== '120000') entries.push({ path: rec.slice(tab + 1), size: Number(meta[3]) });
  }
  return entries;
}

function gitLsTree(root: string, sha: string): Promise<Listing | null> {
  return new Promise((resolve) => {
    execFile('git', ['-C', root, 'ls-tree', '-r', '-l', '-z', '--full-tree', sha], { maxBuffer: TREE_MAX_BYTES, encoding: 'utf8' }, (err, stdout) =>
      resolve(err ? null : { entries: parseLsTree(stdout), truncated: false }),
    );
  });
}

/** Every file of the repository at `sha` with its size, or null when GitHub has no such tree; throws GitHubRefused. */
export async function listTree(owner: string, repo: string, sha: string, env: FnEnv, doFetch: typeof fetch = fetch, now: number = Date.now()): Promise<Listing | null> {
  if (!NAME.test(owner) || !NAME.test(repo) || !safeSha(sha)) return null;
  const key = `${owner}/${repo}@${sha}`;
  const hit = trees.get(key);
  if (hit) return remember(key, hit);
  let got: Listing | null;
  if (env.fakes) {
    const root = env.fakeRepos[`${owner}/${repo}`] ?? env.fakeRepos['*'];
    got = root ? await gitLsTree(root, sha) : null;
  } else got = await githubTree(owner, repo, sha, '', doFetch, now);
  return got ? remember(key, got) : null;
}

export interface DirFiles {
  /** Per directory asked: its top files by the shared rule, and how many files it holds. */
  dirs: Record<string, { files: TreeEntry[]; total: number }>;
  /** GitHub cut the whole listing short (a very large repository). */
  truncated: boolean;
}

/** The top files under each of `dirs` at `sha`, or null when the tree cannot be read; throws GitHubRefused. */
export async function dirFiles(g: { owner: string; repo: string }, sha: string, dirs: readonly string[], env: FnEnv, doFetch: typeof fetch = fetch, now: number = Date.now()): Promise<DirFiles | null> {
  const all = await listTree(g.owner, g.repo, sha, env, doFetch, now);
  if (!all) return null;
  const out: DirFiles = { dirs: {}, truncated: all.truncated };
  let subtrees = 0;
  for (const dir of dirs) {
    const prefix = `${dir}/`;
    let under = all.entries.filter((e) => e.path.startsWith(prefix));
    if (!under.length && all.truncated && subtrees < MAX_SUBTREES) {
      subtrees += 1;
      const treeish = `${sha}:${dir.split('/').map(encodeURIComponent).join('/')}`;
      under = (await githubTree(g.owner, g.repo, treeish, prefix, doFetch, now))?.entries ?? [];
    }
    out.dirs[dir] = { files: topUnder(under, dir, TOP_FILES), total: under.length };
  }
  return out;
}
