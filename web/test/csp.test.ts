/**
 * The page's fixed parts: the HintWise footer embedded byte for byte (docs/new-app-guide.md
 * section 6, the sha256 the Plabs session gave for the slug udebt), the HintWise favicon, and
 * netlify.toml's Content-Security-Policy matching dev/csp.ts (which vite preview sends).
 */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { FOOTER_STYLE_CSP, contentSecurityPolicy, googleStyleAttributes, sha256 } from '../dev/csp';
import { SIGN_IN_CSS } from '../src/lib/signin-styles';

const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const toml = readFileSync(new URL('../../netlify.toml', import.meta.url), 'utf8');

describe('the footer, the favicon and the CSP', () => {
  it("embeds footerHtml('udebt') exactly once, before </body> and outside #app", () => {
    const start = html.indexOf('<site-footer data-site-footer>');
    const end = html.indexOf('</site-footer>') + '</site-footer>'.length;
    const footer = html.slice(start, end);
    expect(html.split('data-site-footer').length - 1).toBe(1);
    expect(Buffer.byteLength(footer)).toBe(2231);
    expect(createHash('sha256').update(footer).digest('hex')).toBe('dc713ce9950e2cb7cabc6a20f439d96e2c1e39666b483c3e9acb65f6cd4685c8');
    expect(footer).toContain('subject=Project%20idea%20(via%20udebt)');
    expect(html.indexOf('<div id="app"></div>')).toBeLessThan(start);
    expect(html.slice(end).trim()).toBe('</body>\n</html>');
  });

  it("has the footer's style hash right (FOOTER_STYLE_CSP)", () => {
    const style = /<style>([\s\S]*?)<\/style>/.exec(html.slice(html.indexOf('<site-footer')))?.[1] ?? '';
    expect(sha256(style)).toBe(FOOTER_STYLE_CSP);
  });

  it('uses the HintWise favicon tag', () => {
    expect(html).toMatch(/<link rel="icon" href="data:image\/svg\+xml,%3Csvg[^>]+%23312E81/);
  });

  it('sends the same policy from netlify.toml as vite preview, with no unsafe-inline', () => {
    const csp = /Content-Security-Policy = "([^"]+)"/.exec(toml)?.[1];
    expect(csp).toBe(contentSecurityPolicy());
    expect(csp).not.toContain("'unsafe-inline'");
    expect(csp).toContain(FOOTER_STYLE_CSP);
    expect(csp).toContain(sha256(SIGN_IN_CSS));
    expect(googleStyleAttributes()).toHaveLength(2);
  });

  it('builds the site from web/ with the worker URL and the functions directory', () => {
    expect(toml).toContain('base = "web"');
    expect(toml).toContain('VITE_UDEBT_WORKER_URL = "https://udebt-api.hintwise.org"');
    expect(toml).toContain('directory = "netlify/functions"');
  });
});
