/**
 * The tree fallback: the preference rule against the Python one (fixtures/topfiles-parity.json,
 * written by tests/test_hosted_topfiles_parity.py), the directories a unit names, and the page's
 * session memory of listings (model/tree.ts) with a mocked /api/tree.
 */
import { describe, expect, it } from 'vitest';
import parity from './fixtures/topfiles-parity.json';
import { MAX_FILE_BYTES, dirPaths, refsForUnit, topRefs } from '../src/core/excerpts';
import type { SpecUnit, Walkthrough } from '../src/core/specs';
import { DOCS, FIXTURES, GENERATED, LOCK, MEDIA, SOURCE_EXTS, TESTS, TOP_FILES, filesUnder, pickAcross, tier, topUnder } from '../src/core/topfiles';
import { FAIL_MS, TreeCache, type TreeAnswer } from '../src/model/tree';

const unescaped = (r: RegExp) => r.source.replace(/\\\//g, '/');

describe('parity with udebt/topfiles.py', () => {
  it('uses the same limits, extensions and path patterns', () => {
    expect(TOP_FILES).toBe(parity.limit);
    expect(MAX_FILE_BYTES).toBe(parity.max_bytes);
    expect([...SOURCE_EXTS].sort()).toEqual(parity.source_exts);
    expect({ lock: unescaped(LOCK), generated: unescaped(GENERATED), media: unescaped(MEDIA), docs: unescaped(DOCS), tests: unescaped(TESTS), fixtures: unescaped(FIXTURES) }).toEqual(parity.patterns);
  });

  for (const [path, size, want] of parity.tiers as [string, number, number | null][]) {
    it(`tier of ${path} (${size} bytes)`, () => expect(tier(path, size)).toBe(want));
  }

  for (const c of parity.selections) {
    it(`picks: ${c.name}`, () => {
      const entries = (c.entries as [string, number][]).map(([path, size]) => ({ path, size }));
      expect(filesUnder(c.dirs, entries)).toEqual(c.files);
    });
  }

  for (const c of parity.units) {
    it(`directories of a unit: ${c.name}`, () => {
      const u = c.unit as unknown as SpecUnit;
      const walk = c.walk as Walkthrough | null;
      expect(dirPaths(u)).toEqual(c.dir_paths);
      expect(refsForUnit(u, walk).length === 0 && dirPaths(u).length > 0).toBe(c.needs_tree);
    });
  }
});

describe('the rule', () => {
  it('keeps each directory to the limit and takes them in turns', () => {
    const entries = [3, 2, 1].map((n) => ({ path: `d/f${n}.py`, size: n }));
    expect(topUnder(entries, 'd/', 2).map((e) => e.path)).toEqual(['d/f3.py', 'd/f2.py']);
    expect(pickAcross([['a', 'b', 'c'], ['b', 'x'], []], 3)).toEqual(['a', 'b', 'x']);
    expect(pickAcross([])).toEqual([]);
  });

  it('reads recorded files before any listing, lines 1-60', () => {
    const u: SpecUnit = { id: 'u', name: 'u', parent: null, paths: ['frontend'], files: ['frontend/src/App.jsx', '../x', 'frontend/src/App.jsx'] };
    expect(refsForUnit(u)).toEqual([{ path: 'frontend/src/App.jsx', start: 1, end: 60 }]);
    expect(refsForUnit({ ...u, files: null })).toEqual([]);
    expect(topRefs(['a.py', 'b.py', 'c.py'], 2)).toEqual([
      { path: 'a.py', start: 1, end: 60 },
      { path: 'b.py', start: 1, end: 60 },
    ]);
  });
});

const REPO = 'https://github.com/acme/demo';
const SHA = 'f'.repeat(40);

function listing(dirs: string[]): TreeAnswer {
  return { dirs: Object.fromEntries(dirs.map((d) => [d, { files: [1, 2, 3, 4, 5].map((n) => ({ path: `${d}/f${n}.py`, size: 100 - n })) }])) };
}

describe('the page memory of listings', () => {
  it('asks each directory once per session, all of a sitting in one request, and builds the references', async () => {
    const asked: string[][] = [];
    const cache = new TreeCache(async (_r, _s, dirs) => {
      asked.push(dirs);
      return listing(dirs);
    });
    const dirOnly: SpecUnit = { id: 'a', name: 'A', parent: null, paths: ['x', 'y'] };
    const withFile: SpecUnit = { id: 'b', name: 'B', parent: null, paths: ['z/a.py'] };
    const refs = await cache.refsFor(REPO, SHA, [{ unit: dirOnly }, { unit: withFile }, { unit: { ...dirOnly, id: 'c', paths: ['y', 'w'] } }]);
    expect(asked).toEqual([['x', 'y', 'w']]);
    expect(refs[0].map((r) => r.path)).toEqual(['x/f1.py', 'y/f1.py', 'x/f2.py', 'y/f2.py']);
    expect(refs[1]).toEqual([{ path: 'z/a.py', start: 1, end: 60 }]);
    expect(refs[2][0]).toEqual({ path: 'y/f1.py', start: 1, end: 60 });
    await cache.refsFor(REPO, SHA, [{ unit: dirOnly }]);
    expect(cache.requests).toBe(1);
    await cache.files(REPO, 'e'.repeat(40), ['x']);
    expect(cache.requests).toBe(2);
    expect(await cache.refsFor(REPO, SHA, [{ unit: withFile }])).toEqual([[{ path: 'z/a.py', start: 1, end: 60 }]]);
    expect(cache.requests).toBe(2);
  });

  it('asks again only FAIL_MS after a failure, and splits a long list', async () => {
    let now = 0;
    let fail = true;
    const asked: string[][] = [];
    const cache = new TreeCache(async (_r, _s, dirs) => {
      asked.push(dirs);
      if (fail) throw new Error('503');
      return listing(dirs);
    }, () => now);
    expect((await cache.files(REPO, SHA, ['x'])).get('x')).toEqual([]);
    now = FAIL_MS - 1;
    expect((await cache.files(REPO, SHA, ['x'])).get('x')).toEqual([]);
    expect(asked).toHaveLength(1);
    fail = false;
    now = FAIL_MS;
    expect((await cache.files(REPO, SHA, ['x'])).get('x')).toHaveLength(5);
    const many = Array.from({ length: 25 }, (_, i) => `d${i}`);
    await cache.files(REPO, SHA, many);
    expect(asked.slice(2).map((a) => a.length)).toEqual([20, 5]);
    const odd = new TreeCache(async () => ({ dirs: { x: { files: [{ path: 7 as unknown as string, size: 1 }] } } }) as TreeAnswer);
    expect((await odd.files(REPO, SHA, ['x', 'missing'])).get('missing')).toEqual([]);
  });
});
