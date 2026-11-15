/**
 * /api/tree, the tree fallback's function: the whole tree at a sha from GitHub's recursive git
 * trees API (mocked), kept per repository and sha, a directory's subtree when GitHub truncates
 * the whole one, GitHub's refusals respected, and the fakes' `git ls-tree` of a local clone.
 */
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { beforeEach, describe, expect, it } from 'vitest';
import treeFn, { config as treeConfig } from '../netlify/functions/tree';
import { resetAuthCache } from '../netlify/functions/lib/auth';
import { FAKE_TOKEN, type FnEnv } from '../netlify/functions/lib/env';
import { excerptPaths, fakeGateway } from '../netlify/functions/lib/fake';
import { API_BASE, REFUSAL_MS, dirFiles, listTree, parseLsTree, parseTree, resetTreeCache } from '../netlify/functions/lib/github';
import { MAX_TREE_DIRS, handleTree } from '../netlify/functions/lib/handlers';

const SHA = 'f93102b121f1da9055c148e8eccde275ccf18236';
const OTHER = 'e'.repeat(40);
const REPO = 'https://github.com/acme/demo';
const LIVE: FnEnv = { supabaseUrl: 'https://auth.example', anonKey: 'anon', fakes: false, fakeRepos: {} };

const TREE = {
  sha: SHA,
  truncated: false,
  tree: [
    { path: 'pkg', mode: '040000', type: 'tree' },
    { path: 'pkg/a.py', mode: '100644', type: 'blob', size: 900 },
    { path: 'pkg/b.py', mode: '100644', type: 'blob', size: 300 },
    { path: 'pkg/link.py', mode: '120000', type: 'blob', size: 4 },
    { path: 'pkg/vendor', mode: '160000', type: 'commit' },
    { path: 'pkg/tests/test_a.py', mode: '100644', type: 'blob', size: 5000 },
    { path: 'pkg/yarn.lock', mode: '100644', type: 'blob', size: 99999 },
    { path: 'web/app.ts', mode: '100644', type: 'blob', size: 10 },
  ],
};

const SUBTREE = /\/git\/trees\/[0-9a-f]{40}:/;

interface Call {
  url: string;
  init?: RequestInit;
}

function github(over: { tree?: () => Response; sub?: () => Response } = {}, calls: Call[] = []): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    if (url.includes('/rest/v1/repositories')) return Response.json([{ id: 7 }]);
    if (SUBTREE.test(url)) return over.sub?.() ?? new Response('{}', { status: 404 });
    if (url.startsWith(`${API_BASE}/repos/acme/demo/git/trees/`)) return over.tree?.() ?? Response.json(TREE);
    return new Response('nope', { status: 404 });
  }) as typeof fetch;
}

const get = (q: string, token = 'user-token', method = 'GET') => new Request(`http://x/api/tree?${q}`, { method, headers: { authorization: `Bearer ${token}` } });
const query = (...dirs: string[]) => new URLSearchParams([['repo', REPO], ['sha', SHA], ...dirs.map((d) => ['path', d])]).toString();

beforeEach(() => {
  resetAuthCache();
  resetTreeCache();
});

describe('/api/tree live', () => {
  it('lists the whole tree once per sha and answers the top files of each directory, never cached', async () => {
    const calls: Call[] = [];
    const res = await handleTree(get(query('pkg/', 'web', 'nowhere')), { env: LIVE, fetch: github({}, calls) });
    expect(res.status).toBe(200);
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(await res.json()).toEqual({
      sha: SHA,
      truncated: false,
      dirs: {
        pkg: { files: [{ path: 'pkg/a.py', size: 900 }, { path: 'pkg/b.py', size: 300 }, { path: 'pkg/tests/test_a.py', size: 5000 }], total: 4 },
        web: { files: [{ path: 'web/app.ts', size: 10 }], total: 1 },
        nowhere: { files: [], total: 0 },
      },
    });
    const gh = calls.filter((c) => c.url.startsWith(API_BASE));
    expect(gh.map((c) => c.url)).toEqual([`${API_BASE}/repos/acme/demo/git/trees/${SHA}?recursive=1`]);
    const h = gh[0].init?.headers as Record<string, string>;
    expect(h['user-agent']).toBe('udebt');
    expect(h.authorization).toBeUndefined();
    expect(gh[0].init?.redirect).toBe('error');
    await handleTree(get(query('web')), { env: LIVE, fetch: github({}, calls) });
    expect(calls.filter((c) => c.url.startsWith(API_BASE))).toHaveLength(1);
    await handleTree(get(query('web').replace(SHA, OTHER)), { env: LIVE, fetch: github({}, calls) });
    expect(calls.filter((c) => c.url.startsWith(API_BASE)).map((c) => c.url)[1]).toContain(OTHER);
  });

  it('asks for a directory subtree only when GitHub truncated the whole tree', async () => {
    const calls: Call[] = [];
    const truncated = () => Response.json({ ...TREE, truncated: true });
    const sub = () => Response.json({ truncated: false, tree: [{ path: 'deep/x.go', mode: '100644', type: 'blob', size: 50 }] });
    const res = await handleTree(get(query('big dir', 'pkg')), { env: LIVE, fetch: github({ tree: truncated, sub }, calls) });
    const out = (await res.json()) as { truncated: boolean; dirs: Record<string, { files: { path: string }[] }> };
    expect(out.truncated).toBe(true);
    expect(out.dirs['big dir'].files).toEqual([{ path: 'big dir/deep/x.go', size: 50 }]);
    expect(out.dirs.pkg.files[0].path).toBe('pkg/a.py');
    expect(calls.filter((c) => SUBTREE.test(c.url)).map((c) => c.url)).toEqual([`${API_BASE}/repos/acme/demo/git/trees/${SHA}:big%20dir?recursive=1`]);
    resetTreeCache();
    const five = await dirFiles({ owner: 'acme', repo: 'demo' }, SHA, ['a', 'b', 'c', 'd', 'e'], LIVE, github({ tree: truncated }, calls));
    expect(Object.keys(five?.dirs ?? {})).toHaveLength(5);
    expect(calls.filter((c) => SUBTREE.test(c.url)).length).toBe(1 + 4);
  });

  it('respects a refusal until the time GitHub gives, and asks nothing meanwhile', async () => {
    const calls: Call[] = [];
    const limited = () => new Response('{}', { status: 403, headers: { 'x-ratelimit-remaining': '0', 'x-ratelimit-reset': '2000' } });
    const res = await handleTree(get(query('pkg')), { env: LIVE, fetch: github({ tree: limited }, calls), now: () => 1_000_000 });
    expect(res.status).toBe(503);
    expect(res.headers.get('retry-after')).toBe('1000');
    expect(((await res.json()) as { retry_after: number }).retry_after).toBe(1000);
    const again = await handleTree(get(query('pkg')), { env: LIVE, fetch: github({}, calls), now: () => 1_500_000 });
    expect(again.status).toBe(503);
    expect(calls.filter((c) => c.url.startsWith(API_BASE))).toHaveLength(1);
    expect((await handleTree(get(query('pkg')), { env: LIVE, fetch: github({}, calls), now: () => 2_000_000 })).status).toBe(200);

    resetTreeCache();
    const retry = () => new Response('{}', { status: 429, headers: { 'retry-after': '30' } });
    const r2 = await handleTree(get(query('pkg')), { env: LIVE, fetch: github({ tree: retry }), now: () => 0 });
    expect(r2.headers.get('retry-after')).toBe('30');
    resetTreeCache();
    const plain = () => new Response('{}', { status: 403 });
    const r3 = await handleTree(get(query('pkg')), { env: LIVE, fetch: github({ tree: plain }), now: () => 0 });
    expect(((await r3.json()) as { retry_after: number }).retry_after).toBe(REFUSAL_MS / 1000);
  });

  it('answers 404 when the tree cannot be read and refuses bad requests', async () => {
    for (const tree of [() => new Response('{}', { status: 404 }), () => new Response('not json'), () => Response.json({ message: 'odd' }), () => new Response('x', { headers: { 'content-length': '99000000' } })]) {
      resetTreeCache();
      expect((await handleTree(get(query('pkg')), { env: LIVE, fetch: github({ tree }) })).status).toBe(404);
    }
    const down = (async (u: RequestInfo | URL) => (String(u).startsWith(API_BASE) ? Promise.reject(new Error('down')) : Response.json([{ id: 7 }]))) as typeof fetch;
    expect((await handleTree(get(query('pkg')), { env: LIVE, fetch: down })).status).toBe(404);
    expect((await handleTree(get(query('../etc')), { env: LIVE, fetch: github() })).status).toBe(400);
    expect((await handleTree(get(new URLSearchParams([['repo', REPO], ['sha', 'abc'], ['path', 'pkg']]).toString()), { env: LIVE, fetch: github() })).status).toBe(400);
    expect((await handleTree(get(query(...Array.from({ length: MAX_TREE_DIRS + 1 }, (_, i) => `d${i}`))), { env: LIVE, fetch: github() })).status).toBe(400);
    expect((await handleTree(get(query('pkg'), 'user-token', 'POST'), { env: LIVE })).status).toBe(405);
    expect((await handleTree(new Request(`http://x/api/tree?${query('pkg')}`), { env: LIVE })).status).toBe(401);
    expect(await listTree('ac/me', 'demo', SHA, LIVE, github())).toBeNull();
  });

  it('reads tree answers and ls-tree output, leaving out symlinks and submodules', () => {
    expect(parseTree({ tree: 'no' })).toBeNull();
    expect(parseTree(null)).toBeNull();
    expect(parseTree({ tree: [{ path: 'a', type: 'blob', mode: '100644', size: 3 }, { path: 'b', type: 'blob', mode: '100644' }, null] }, 'x/')).toEqual({ entries: [{ path: 'x/a', size: 3 }], truncated: false });
    const out = ['100644 blob 1111111111111111111111111111111111111111      12\ta b.py', '120000 blob 2222222222222222222222222222222222222222       5\tlink', '160000 commit 3333333333333333333333333333333333333333       -\tsub', ''].join('\u0000');
    expect(parseLsTree(out)).toEqual([{ path: 'a b.py', size: 12 }]);
  });

  it('routes its entry point at /api/tree', async () => {
    expect(treeConfig.path).toBe('/api/tree');
    expect((await treeFn(new Request('http://x/api/tree', { method: 'PUT' }))).status).toBeGreaterThanOrEqual(400);
  });
});

describe('/api/tree with the fakes', () => {
  it('lists a local clone with git ls-tree, and the fake gateway cites the excerpts it was shown', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'udebt-tree-'));
    const git = (...a: string[]) => execFileSync('git', ['-C', dir, ...a], { encoding: 'utf8' });
    git('init', '-q');
    mkdirSync(join(dir, 'frontend', 'src'), { recursive: true });
    writeFileSync(join(dir, 'frontend', 'src', 'App.jsx'), 'export default 1;\n'.repeat(20));
    writeFileSync(join(dir, 'frontend', 'src', 'main.jsx'), 'x\n');
    writeFileSync(join(dir, 'frontend', 'package-lock.json'), '{}\n'.repeat(500));
    symlinkSync('src/App.jsx', join(dir, 'frontend', 'link.jsx'));
    git('add', '.');
    git('-c', 'user.name=t', '-c', 'user.email=t@e', 'commit', '-qm', 'x');
    const sha = git('rev-parse', 'HEAD').trim();
    const env: FnEnv = { supabaseUrl: null, anonKey: null, fakes: true, fakeRepos: { 'acme/demo': dir } };
    const res = await handleTree(get(new URLSearchParams([['repo', REPO], ['sha', sha], ['path', 'frontend']]).toString(), FAKE_TOKEN), { env });
    expect(((await res.json()) as { dirs: { frontend: { files: { path: string }[] } } }).dirs.frontend.files.map((f) => f.path)).toEqual(['frontend/src/App.jsx', 'frontend/src/main.jsx']);
    expect(await listTree('other', 'repo', sha, env)).toBeNull();
    expect(await listTree('acme', 'demo', OTHER, env)).toBeNull();

    const prompt = 'TASK: questions\n- unit_id: fe\n  level: 1\n  paths: frontend\n- unit_id: be\n  level: 1\n  paths: backend/a.py, backend/b.py, backend/c.py\n\n--- frontend/src/App.jsx lines 1-60\n    1 | x\n\n--- frontend/src/App.jsx lines 1-60\n--- other/z.py lines 1-9';
    expect(excerptPaths(prompt)).toEqual(['frontend/src/App.jsx', 'other/z.py']);
    const q = fakeGateway({ max_tokens: 1, messages: [{ role: 'user', content: prompt }] });
    const input = (q.content[0] as { input: { questions: { unit_id: string; evidence: string[] }[] } }).input;
    expect(input.questions.map((x) => [x.unit_id, x.evidence])).toEqual([
      ['fe', ['frontend/src/App.jsx']],
      ['be', ['backend/a.py', 'backend/b.py']],
    ]);
  });
});
