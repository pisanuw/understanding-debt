/**
 * The functions' settings, from the Netlify site's environment. Public values only: the
 * Supabase URL and anon key (the same ones the page has). There is no secret here: the
 * functions act with the signed-in person's token, never a service-role key, and call the
 * model only through the HintWise gateway with that token.
 *
 * UDEBT_FAKES=1 turns on the fakes (sign-in token, gateway, GitHub) for `npm run dev`,
 * `vite preview` and tests; it is ignored on Netlify (where AWS_LAMBDA_FUNCTION_NAME or
 * NETLIFY is set), so a stray setting can never fake a live site.
 */
export interface FnEnv {
  supabaseUrl: string | null;
  anonKey: string | null;
  fakes: boolean;
  /** "owner/repo" (or "*") -> a local clone the fake GitHub reads with `git show`. */
  fakeRepos: Record<string, string>;
  /** With the fakes: the person's daily allowance is used up (UDEBT_FAKE_CAP=1), for the walkthrough of the cap notice. */
  fakeCap?: boolean;
  /**
   * The HintWise key for visitors who are not signed in (owner, 2026-11-15: "All unsigned users
   * are considered "one user" in terms of quota"), on the samples at their pins only:
   * VITE_UDEBT_ANON_MODEL=1, set once on Netlify for builds (the page) and functions (here).
   * Off until the gateway's anonymous mode exists.
   */
  anonModel?: boolean;
}

export { PREVIEW_TOKEN as FAKE_TOKEN } from '../../../src/core/preview';

function parseRepos(v: string | undefined): Record<string, string> {
  if (!v) return {};
  try {
    const o: unknown = JSON.parse(v);
    if (o && typeof o === 'object' && !Array.isArray(o)) return Object.fromEntries(Object.entries(o).filter(([, p]) => typeof p === 'string')) as Record<string, string>;
  } catch {
    return { '*': v };
  }
  return {};
}

export function readEnv(env: Record<string, string | undefined> = process.env): FnEnv {
  const onNetlify = Boolean(env.AWS_LAMBDA_FUNCTION_NAME || env.NETLIFY);
  const url = env.SUPABASE_URL || env.VITE_SUPABASE_URL || null;
  const key = env.SUPABASE_ANON_KEY || env.VITE_SUPABASE_ANON_KEY || null;
  return {
    supabaseUrl: url ? url.replace(/\/+$/, '') : null,
    anonKey: key,
    fakes: env.UDEBT_FAKES === '1' && !onNetlify,
    fakeRepos: parseRepos(env.UDEBT_FAKE_REPOS),
    fakeCap: env.UDEBT_FAKES === '1' && !onNetlify && env.UDEBT_FAKE_CAP === '1',
    anonModel: env.VITE_UDEBT_ANON_MODEL === '1',
  };
}
