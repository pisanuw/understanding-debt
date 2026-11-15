/**
 * Which files stand for a directory when a unit names only directories (no walkthrough, no
 * file among its orientation and paths): the question writer has no read tools, so without
 * this the unit would be quizzed with no code. The same rule as udebt/topfiles.py, which the
 * worker uses to record a unit's `files`; both are checked against
 * test/fixtures/topfiles-parity.json (tests/test_hosted_topfiles_parity.py writes it).
 *
 * - Never: lockfiles, generated files and media (udebt/filekinds.py's mechanical kinds),
 *   symlinks and submodules (left out by the listing), empty files and files over
 *   MAX_FILE_BYTES (the excerpt proxy reads none of these).
 * - Tiers: 0 source code (udebt/structure.py SOURCE_EXTS), 1 anything else (config, data,
 *   styles, manifests), 2 docs, 3 tests and fixtures.
 * - Order: tier, then larger first, then path. Each directory gives its first `limit`; the
 *   unit takes them in turns (the first of each directory, then the second, ...), at most
 *   `limit` in all.
 */
import { MAX_FILE_BYTES } from './excerpts';

/** refsForUnit's limit: the most files one unit is quizzed from. */
export const TOP_FILES = 4;

// udebt/filekinds.py, character for character.
export const LOCK = /(^|\/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|uv\.lock|Pipfile\.lock|Cargo\.lock|go\.sum|composer\.lock|Gemfile\.lock|flake\.lock|[^/]*\.lock)$/;
export const GENERATED = /(^|\/)(dist|build|out|target|node_modules|__pycache__|\.next|coverage|__snapshots__)\/|\.(min\.js|min\.css|map|pyc|snap|ipynb_checkpoints)$|(^|\/)[^/]*\.generated\.[^/]+$/;
export const MEDIA = /\.(png|jpe?g|gif|svg|ico|webp|mp3|mp4|wav|pdf|woff2?|ttf)$/;
export const DOCS = /(^|\/)(docs?|doc)\/|\.(md|rst|txt|adoc)$|(^|\/)(LICENSE|CHANGELOG|README)[^/]*$/;
export const TESTS = /(^|\/)(tests?|__tests__|spec|e2e)\/|(^|\/)test_[^/]+$|[._](test|spec)\.[a-z]+$/;
// udebt/topfiles.py
export const FIXTURES = /(^|\/)(fixtures?|__fixtures__|__mocks__|mocks?|testdata|test-data)\//;

// udebt/structure.py SOURCE_EXTS (the import parsers' extensions and the other languages).
export const SOURCE_EXTS: ReadonlySet<string> = new Set([
  '.bash', '.c', '.cc', '.cjs', '.clj', '.cpp', '.cs', '.cxx', '.dart', '.erl', '.ex', '.exs', '.go', '.h', '.hh', '.hpp', '.hs', '.java', '.jl', '.js', '.jsx', '.kt', '.kts',
  '.lua', '.m', '.mjs', '.ml', '.mm', '.php', '.py', '.pyi', '.r', '.rb', '.rs', '.scala', '.sh', '.sql', '.svelte', '.swift', '.ts', '.tsx', '.vue', '.zsh',
]);

export interface TreeEntry {
  path: string;
  size: number;
}

/** posixpath.splitext's extension, lower-cased ("" for none or a leading dot only). */
function ext(path: string): string {
  const base = path.slice(path.lastIndexOf('/') + 1);
  const stem = base.replace(/^\.+/, '');
  const dot = stem.lastIndexOf('.');
  return dot < 0 ? '' : stem.slice(dot).toLowerCase();
}

/** 0 source, 1 other, 2 docs, 3 tests and fixtures; null for a file never shown. */
export function tier(path: string, size: number): number | null {
  if (LOCK.test(path) || GENERATED.test(path) || MEDIA.test(path)) return null;
  if (!(size > 0) || size > MAX_FILE_BYTES) return null;
  if (TESTS.test(path) || FIXTURES.test(path)) return 3;
  if (DOCS.test(path)) return 2;
  if (SOURCE_EXTS.has(ext(path))) return 0;
  return 1;
}

const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

/** The best `limit` files under `dir` (any depth), best first. */
export function topUnder(entries: readonly TreeEntry[], dir: string, limit = TOP_FILES): TreeEntry[] {
  const prefix = `${dir.replace(/^\/+|\/+$/g, '')}/`;
  return entries
    .filter((e) => e.path.startsWith(prefix))
    .map((e) => ({ e, t: tier(e.path, e.size) }))
    .filter((x): x is { e: TreeEntry; t: number } => x.t !== null)
    .sort((a, b) => a.t - b.t || b.e.size - a.e.size || cmp(a.e.path, b.e.path))
    .slice(0, limit)
    .map((x) => x.e);
}

/** The first of each list, then the second of each, ..., without repeats, at most `limit`. */
export function pickAcross(lists: readonly (readonly string[])[], limit = TOP_FILES): string[] {
  const out: string[] = [];
  const longest = Math.max(0, ...lists.map((x) => x.length));
  for (let i = 0; i < longest; i += 1) {
    for (const xs of lists) if (i < xs.length && !out.includes(xs[i]) && out.length < limit) out.push(xs[i]);
  }
  return out;
}

/** The unit's files under `dirs`, from a listing of each directory (udebt/topfiles.py files_under). */
export function filesUnder(dirs: readonly string[], entries: readonly TreeEntry[], limit = TOP_FILES): string[] {
  return pickAcross(dirs.map((d) => topUnder(entries, d, limit).map((e) => e.path)), limit);
}
