/**
 * Who may read code through /api/excerpt and /api/tree without signing in: visitors on the
 * registered samples (HOSTING.md decision 10), at each sample's pinned commit only. The list is
 * the site's own public/samples/index.json (python -m udebt.hosted.static_samples), bundled with
 * the functions, so a request names nothing the site does not already serve. A request with a
 * token is checked as before (auth.ts), whatever it names.
 */
import index from '../../../public/samples/index.json';
import { atPin, readIndex, type SampleEntry } from '../../../src/core/samples';

export const REGISTERED: readonly SampleEntry[] = readIndex(index);

/** True when `repoUrl` at `sha` is a registered sample at its pin. */
export function openToAnyone(repoUrl: string, sha: string, entries: readonly SampleEntry[] = REGISTERED): boolean {
  return atPin(entries, repoUrl, sha);
}
