/**
 * Who may use the functions: a signed-in HintWise person, for a repository the app knows.
 * One PostgREST read with the person's own token checks both: app_udebt.repositories is
 * readable by signed-in people only (row level security, nothing for anon), so a bad or
 * expired token is refused and an unknown repository returns no row. Answers are kept for
 * five minutes per token and repository so a walkthrough's excerpts cost one check.
 */
import { FAKE_TOKEN, type FnEnv } from './env';
import { HttpError, upstreamDetail } from './http';

const TTL_MS = 5 * 60 * 1000;
const MAX_CACHED = 500;
const cache = new Map<string, { repoId: number; until: number }>();

export function resetAuthCache(): void {
  cache.clear();
}

export async function authorize(token: string, repoUrl: string, env: FnEnv, doFetch: typeof fetch = fetch, now: number = Date.now()): Promise<number> {
  if (env.fakes) {
    if (token !== FAKE_TOKEN) throw new HttpError(401, 'Sign in again.', { error: 'Sign in again.' });
    return 0;
  }
  if (!env.supabaseUrl || !env.anonKey) throw new HttpError(503, 'Supabase is not configured for the functions.', { error: 'The server is not configured yet.' });
  const key = `${token}|${repoUrl}`;
  const hit = cache.get(key);
  if (hit && hit.until > now) return hit.repoId;
  let res: Response;
  try {
    res = await doFetch(`${env.supabaseUrl}/rest/v1/repositories?select=id&url=eq.${encodeURIComponent(repoUrl)}`, {
      headers: { apikey: env.anonKey, authorization: `Bearer ${token}`, 'accept-profile': 'app_udebt' },
    });
  } catch {
    throw new HttpError(502, 'Could not reach the sign-in service.', { error: 'Could not reach the sign-in service. Try again.' });
  }
  if (res.status === 401 || res.status === 403) {
    const data: unknown = await res.json().catch(() => ({}));
    const detail = upstreamDetail('database', res.status, data);
    // The site's SUPABASE_ANON_KEY is not this project's key: the server's setting, not the
    // person's sign-in, so not a 401 (the page would refresh the session and try again for nothing).
    const said = data && typeof data === 'object' ? String((data as { message?: unknown }).message ?? '') : '';
    if (res.status === 401 && /invalid api key/i.test(said)) {
      throw new HttpError(503, 'The functions have the wrong Supabase key.', { error: 'The server is misconfigured: its database key (SUPABASE_ANON_KEY on Netlify) is not this project\'s. Nothing is wrong with your sign-in.', detail });
    }
    // 401: PostgREST did not accept the token (expired or revoked); 403: it accepted it and refused
    // the read. Both go to the page as 401, which refreshes the session once and retries; the
    // detail says which it was.
    throw new HttpError(401, 'Sign in again.', { error: res.status === 401 ? 'Your sign-in has expired. Sign in again.' : 'The database refused this for your account. Sign in again.', detail });
  }
  if (!res.ok) throw new HttpError(502, 'The database answered an error.', { error: `The database answered ${res.status}.` });
  const rows: unknown = await res.json().catch(() => []);
  const id = Array.isArray(rows) && rows[0] && typeof rows[0] === 'object' ? Number((rows[0] as { id?: unknown }).id) : NaN;
  if (!Number.isFinite(id)) throw new HttpError(403, 'Unknown repository.', { error: 'Add this repository in udebt first.' });
  if (cache.size >= MAX_CACHED) cache.delete(cache.keys().next().value as string);
  cache.set(key, { repoId: id, until: now + TTL_MS });
  return id;
}
