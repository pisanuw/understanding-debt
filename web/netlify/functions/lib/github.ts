/**
 * One file of a public GitHub repository at a commit, for one request: fetched from
 * raw.githubusercontent.com (the URL is built from a checked owner, name, 40-character sha
 * and a path that cannot leave the repository), at most MAX_FILE_BYTES, never cached or
 * stored. With the fakes on it is read from a local clone with `git show <sha>:<path>`.
 */
import { execFile } from 'node:child_process';
import { MAX_FILE_BYTES, safePath, safeSha, sliceLines, type Excerpt, type ExcerptRef } from '../../../src/core/excerpts';
import type { FnEnv } from './env';

export const RAW_BASE = 'https://raw.githubusercontent.com';
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
