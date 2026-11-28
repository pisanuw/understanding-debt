// @vitest-environment jsdom
/**
 * The light and dark mode switch (claude-built-apps platform/src/theme/README.md): its two files,
 * copied unchanged, the switch in the header, the colours in both modes, and print.
 */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';
import fixture from '../fixtures/digitalme.json';
import { contentSecurityPolicy } from '../dev/csp';
import type { Fixture } from '../src/data/store';
import { memoryStorage } from '../src/lib/llm';
import { App } from '../src/ui/app';
import { previewDeps } from '../src/ui/boot';

const sha = (s: string | Uint8Array, enc: 'hex' | 'base64'): string => createHash('sha256').update(s).digest(enc);

// From claude-built-apps platform/src/theme/README.md, "The values" (main at 1975ac1). This repository
// cannot see the source, so the values are pasted: a newer copy there means updating the files and these.
const HW_THEME_SHA256 = '8e78682bb6c0f0a30c20687122363ec3a5bf020df14faaa5235a99d065d71d9c';
const THEME_INIT_CSP = "'sha256-GhyjaPQKUCt6Zif2GMbTG7M6cUY6iW77/obk7CBvquk='";

// Paths from web/, where vitest runs (import.meta.url is not a file URL under jsdom).
const file = (p: string): string => readFileSync(p, 'utf8');
const html = file('index.html');
const css = file('src/styles.css').replace(/\/\*[\s\S]*?\*\//g, '');
const main = file('src/main.ts');

describe('the copies of the shared switch', () => {
  it('hw-theme.js is the platform copy, unchanged', () => {
    expect(sha(readFileSync('src/lib/hw-theme.js'), 'hex')).toBe(HW_THEME_SHA256);
  });

  it('hw-theme-init.js is the platform copy, unchanged, served from the site root', () => {
    expect(`'sha256-${sha(readFileSync('public/hw-theme-init.js'), 'base64')}'`).toBe(THEME_INIT_CSP);
  });

  it('loads hw-theme-init.js in the head as a plain blocking script, before any stylesheet or other script', () => {
    const head = html.slice(0, html.indexOf('</head>'));
    const tag = '<script src="/hw-theme-init.js"></script>';
    expect(head.split(tag)).toHaveLength(2);
    const at = head.indexOf(tag);
    for (const later of ['<link rel="stylesheet"', '<style', '/hw-events.js', '<script type="module"']) {
      const i = head.indexOf(later);
      expect(i === -1 || at < i, later).toBe(true);
    }
  });

  it("keeps the Content-Security-Policy strict: no inline script on the page, and script-src 'self' as before", () => {
    expect([...html.matchAll(/<script\b([^>]*)>/g)].every(([, attrs]) => /\ssrc="/.test(attrs))).toBe(true);
    // dev/csp.ts finds the G by import.meta.url, which jsdom does not give as a file URL: hand it the file.
    const policy = contentSecurityPolicy(file('src/lib/google-g-light.svg'));
    expect(policy).toContain("script-src 'self' https://challenges.cloudflare.com;");
    expect(policy).not.toContain("'unsafe-inline'");
  });

  it('main.ts loads hw-theme.js first, before the stylesheet and the app', () => {
    const at = main.indexOf("import './lib/hw-theme.js';");
    expect(at).toBeGreaterThan(-1);
    expect(at).toBeLessThan(main.indexOf("import './styles.css';"));
    expect(at).toBeLessThan(main.indexOf("from './ui/boot'"));
  });
});

const rgb = (hex: string): number[] => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const lum = (c: number[]): number => {
  const [r, g, b] = c.map((v) => v / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const ratio = (a: number[], b: number[]): number => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
const mix = (a: number[], b: number[], share: number): number[] => a.map((v, i) => share * v + (1 - share) * b[i]);
const PRINT = /@media\s+print\s*\{((?:[^{}]*\{[^{}]*\})*[^{}]*)\}/g;
const SCREEN_ATTR = /@media\s+screen\s*\{\s*:root\[data-theme="dark"\]\s*\{([^}]*)\}\s*\}/;
const SCREEN_DEVICE = /@media\s+screen\s+and\s+\(prefers-color-scheme:\s*dark\)\s*\{\s*:root:not\(\[data-theme="light"\]\)\s*\{([^}]*)\}\s*\}/;
const rules = (sheet: string, selector: string): string =>
  [...sheet.matchAll(/([^{}@]+)\{([^{}]*)\}/g)].filter(([, s]) => s.split(',').some((x) => x.trim() === selector)).map((m) => m[2]).join(';');

describe('the palette', () => {
  const block = (re: RegExp): string => (css.match(re)?.[1] ?? '').replace(/\s+/g, ' ').trim();
  const tokens = (body: string): Record<string, string> =>
    Object.fromEntries([...body.matchAll(/(--[\w-]+):\s*(#[0-9a-f]{6})\b/gi)].map((m) => [m[1], m[2].toLowerCase()]));
  const light = tokens(css.match(/:root\s*\{([^}]*)\}/)![1]);
  const dark = { ...light, ...tokens(block(SCREEN_ATTR)) };

  it('has the dark set twice and the same, for screens only, and nothing else follows the theme or the device', () => {
    expect(block(SCREEN_ATTR)).toContain('color-scheme: dark;');
    expect(block(SCREEN_DEVICE)).toBe(block(SCREEN_ATTR));
    expect(css.match(/:root\s*\{([^}]*)\}/)![1]).toMatch(/color-scheme:\s*light;/);
    expect(css.replace(new RegExp(SCREEN_ATTR.source, 'g'), '').replace(new RegExp(SCREEN_DEVICE.source, 'g'), '')).not.toMatch(/data-theme|prefers-color-scheme/);
  });

  it('keeps the text at 4.5:1 and the switch outline at 3:1 against the header, in both modes', () => {
    const share = Number(file('src/lib/hw-theme.js').match(/currentColor (\d+)%/)![1]) / 100;
    expect(rules(css, 'header.top')).toMatch(/background:\s*var\(--surface\)/);
    for (const [theme, t] of [['light', light], ['dark', dark]] as const) {
      for (const [fg, bg] of [['--ink', '--page'], ['--ink', '--surface'], ['--ink-2', '--surface'], ['--muted', '--page'], ['--accent', '--page'], ['--accent', '--surface'], ['--accent-ink', '--accent']]) {
        expect(ratio(rgb(t[fg]), rgb(t[bg])), `${theme}: ${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
      }
      expect(ratio(mix(rgb(t['--ink']), rgb(t['--surface']), share), rgb(t['--surface'])), theme).toBeGreaterThanOrEqual(3);
    }
  });

  it("leaves the switch off paper, and prints the primary button's label in the ink", () => {
    const blocks = [...css.matchAll(PRINT)];
    expect(blocks).toHaveLength(1);
    expect(css.slice(blocks[0].index! + blocks[0][0].length).trim()).toBe('');
    expect(blocks[0][1]).toMatch(/(?:^|[\s,}])hw-theme-toggle\s*\{\s*display:\s*none\s*!important;?\s*\}/);
    expect(rules(css, 'button.primary')).toMatch(/background:\s*var\(--accent\)/);
    expect(rules(blocks[0][1], 'button.primary')).toMatch(/color:\s*var\(--ink\)/);
  });
});

describe('the switch in the header', () => {
  let root: HTMLElement;
  beforeAll(async () => {
    await import('../src/lib/hw-theme.js');
  });
  beforeEach(() => {
    window.hintwise!.theme!.set(null);
    localStorage.clear();
    root = document.createElement('div');
    root.id = 'app';
    document.body.replaceChildren(root);
    new App(root, previewDeps(fixture as unknown as Fixture, { doFetch: fetch, session: memoryStorage(), local: memoryStorage() }));
  });
  const button = (): HTMLButtonElement => root.querySelector('header.top hw-theme-toggle')!.shadowRoot!.querySelector('button')!;

  it('is last in the header, after the account links, in a group that keeps it off a line of its own', () => {
    expect(root.querySelectorAll('hw-theme-toggle')).toHaveLength(1);
    const toggle = root.querySelector('header.top hw-theme-toggle')!;
    const end = toggle.parentElement!;
    expect(end.classList.contains('top-end')).toBe(true);
    expect(end.lastElementChild).toBe(toggle);
    expect(toggle.previousElementSibling?.matches('nav.who')).toBe(true);
    expect(rules(css, '.top-end')).toMatch(/display:\s*flex/);
    expect(rules(css, '.top-end')).not.toMatch(/flex-wrap:\s*wrap/);
    expect(rules(css, 'hw-theme-toggle:not([hidden])')).toMatch(/flex:\s*none/);
    expect(rules(css, 'hw-theme-toggle:not([hidden])')).toMatch(/width:\s*var\(--hw-theme-toggle-size, 44px\)/);
  });

  it('says what it will do, switches the page at once and remembers the choice', () => {
    expect(button().getAttribute('aria-label')).toBe('Switch to dark mode');
    button().click();
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(localStorage.getItem('hw-theme')).toBe('dark');
    expect(button().getAttribute('aria-label')).toBe('Switch to light mode');
    button().click();
    expect(document.documentElement.dataset.theme).toBe('light');
  });
});
