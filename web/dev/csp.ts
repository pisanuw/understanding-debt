/**
 * The page's Content-Security-Policy, one source for netlify.toml (checked by
 * test/csp.test.ts) and `vite preview` (vite.config.ts), so the Playwright walkthrough runs
 * under the policy the live site sends.
 *
 * - script-src: the app's own bundle, and Cloudflare Turnstile, which the sign-in panel loads
 *   when the sign-in-link function asks for a check.
 * - style-src: the app's stylesheet, the sign-in panel's one <style> block (lib/signin-styles.ts)
 *   and the HintWise footer's (FOOTER_STYLE_CSP), each by hash; no 'unsafe-inline'.
 * - style-src-attr: only the two style attributes inside Google's unmodified G
 *   (lib/google-g-light.svg), by hash.
 * - connect-src: this site's /api functions, Supabase at auth.hintwise.org (data, sign-in,
 *   the gateway, account deletion), api.anthropic.com for the person's own key, and the
 *   analysis service at udebt-api.hintwise.org.
 */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { SIGN_IN_CSS } from '../src/lib/signin-styles';

/** scripts/site-footer.mjs FOOTER_STYLE_CSP in claude-built-apps (docs/new-app-guide.md section 6). */
export const FOOTER_STYLE_CSP = "'sha256-4VDViIffTKl8oDDJg58VHCVBOHSQm7GPM7eZ1fAKuzc='";
export const AUTH_ORIGIN = 'https://auth.hintwise.org';
export const WORKER_ORIGIN = 'https://udebt-api.hintwise.org';
export const TURNSTILE_ORIGIN = 'https://challenges.cloudflare.com';

export function sha256(text: string): string {
  return `'sha256-${createHash('sha256').update(text, 'utf8').digest('base64')}'`;
}

/** The style attribute values of Google's G, exactly as written in the SVG. */
export function googleStyleAttributes(svg: string = readFileSync(fileURLToPath(new URL('../src/lib/google-g-light.svg', import.meta.url)), 'utf8')): string[] {
  return [...svg.matchAll(/\sstyle="([^"]*)"/g)].map((m) => m[1]);
}

export function contentSecurityPolicy(svg?: string): string {
  const attrs = googleStyleAttributes(svg).map(sha256);
  return [
    "default-src 'self'",
    `script-src 'self' ${TURNSTILE_ORIGIN}`,
    `style-src 'self' ${sha256(SIGN_IN_CSS)} ${FOOTER_STYLE_CSP}`,
    `style-src-attr 'unsafe-hashes' ${attrs.join(' ')}`,
    "img-src 'self' data:",
    "font-src 'self'",
    `connect-src 'self' ${AUTH_ORIGIN} https://api.anthropic.com ${WORKER_ORIGIN}`,
    `frame-src ${TURNSTILE_ORIGIN}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join('; ');
}

export const SECURITY_HEADERS: Record<string, string> = {
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
};
