// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Session, User } from '@supabase/supabase-js';
import asset from '../src/lib/google-g-light.svg?raw';
import { ensureSignInStyles, SIGN_IN_CSS as css, SIGN_IN_STYLE_ID } from '../src/lib/signin-styles';
import {
  BROWSER_ID_KEY,
  browserId,
  completeSignInFromUrl,
  currentToken,
  defaultRedirect,
  ensureProfile,
  EXPIRED_LINK,
  formatRetryAt,
  GOOGLE_BUTTON_TEXT,
  googleButton,
  googleLogoSvg,
  loadTurnstile,
  maskEmail,
  validHint,
  MAX_FAILED_CHECKS,
  mountSignIn,
  onAuth,
  resetTurnstileLoader,
  sendMagicLink,
  signInLinkUrl,
  signInWithGoogle,
  signOut,
  SUPPRESSED_MESSAGE,
  toLinkResult,
  TURNSTILE_SCRIPT,
  TURNSTILE_SITE_KEY,
  type AuthClient,
  type ProfileClient,
  type SignInLinkResult,
  type TurnstileApi,
} from '../src/lib/auth';

type Listener = (event: string, session: Session | null) => void;

/** A fake of the supabase-js auth surface the sign-in code uses. */
function fakeAuth(session: Session | null = null) {
  let listener: Listener | null = null;
  const unsubscribe = vi.fn();
  const auth = {
    signInWithOAuth: vi.fn(async () => ({ data: { provider: 'google', url: 'https://accounts.google.com/o' }, error: null })),
    verifyOtp: vi.fn(async () => ({ data: { user: null, session: null }, error: null as { message: string } | null })),
    signOut: vi.fn(async () => ({ error: null })),
    getSession: vi.fn(async () => ({ data: { session }, error: null })),
    onAuthStateChange: vi.fn((cb: Listener) => {
      listener = cb;
      return { data: { subscription: { id: 's', callback: cb, unsubscribe } } };
    }),
  };
  const client = { auth } as unknown as AuthClient;
  return { client, auth, unsubscribe, emit: (s: Session | null) => listener?.('SIGNED_IN', s) };
}

const user = { id: 'u1', email: 'ann@example.com' } as User;
const session = { access_token: 'tok-1', user } as Session;
const flush = () => new Promise((r) => setTimeout(r, 0));
const ENV = { VITE_SUPABASE_URL: 'https://auth.hintwise.org/', VITE_SUPABASE_ANON_KEY: 'anon-key' };
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

/** A Storage-like map. */
function store(initial: Record<string, string> = {}) {
  const m = new Map(Object.entries(initial));
  return { getItem: vi.fn((k: string) => m.get(k) ?? null), setItem: vi.fn((k: string, v: string) => void m.set(k, v)), m };
}

let root: HTMLElement;
beforeEach(() => {
  document.body.innerHTML = '<div id="panel"></div>';
  root = document.getElementById('panel')!;
});
afterEach(() => {
  resetTurnstileLoader();
  delete (window as { turnstile?: unknown }).turnstile;
  document.head.innerHTML = '';
});

describe('auth functions', () => {
  it('start Google sign-in with the redirect, and sign out of this app only', async () => {
    const { client, auth } = fakeAuth();
    await signInWithGoogle('https://x.hintwise.org/', client);
    expect(auth.signInWithOAuth).toHaveBeenCalledWith({ provider: 'google', options: { redirectTo: 'https://x.hintwise.org/', queryParams: { prompt: 'select_account' } } });
    await signOut(client);
    expect(auth.signOut).toHaveBeenCalledWith({ scope: 'local' });
  });
  it('reads the session token for the gateway', async () => {
    expect(await currentToken(fakeAuth(null).client)).toBeNull();
    expect(await currentToken(fakeAuth(session).client)).toBe('tok-1');
  });
  it('calls back now and on every change, until unsubscribed', async () => {
    const { client, emit, unsubscribe } = fakeAuth(session);
    const cb = vi.fn();
    const off = onAuth(cb, client);
    await flush();
    expect(cb).toHaveBeenCalledWith(user, session);
    emit(null);
    expect(cb).toHaveBeenLastCalledWith(null, null);
    off();
    expect(unsubscribe).toHaveBeenCalled();
  });
  it('redirects back to this page without query or hash', () => {
    expect(defaultRedirect()).toBe(location.origin + location.pathname);
  });
  it('uses the app client by default, which needs the Supabase env', () => {
    expect(() => mountSignIn(root)).toThrow(/Supabase is not configured/);
  });
});

describe('browserId', () => {
  it('creates one id with crypto.randomUUID, keeps it in localStorage and reuses it', () => {
    localStorage.clear();
    const first = browserId();
    expect(first).toMatch(/^[0-9a-f-]{36}$/);
    expect(localStorage.getItem(BROWSER_ID_KEY)).toBe(first);
    expect(browserId()).toBe(first);
  });
  it('replaces a stored value that is not an id, and copes with blocked storage', () => {
    const s = store({ [BROWSER_ID_KEY]: 'bad id!' });
    expect(browserId(s, () => 'fresh-id-123')).toBe('fresh-id-123');
    expect(s.m.get(BROWSER_ID_KEY)).toBe('fresh-id-123');
    const blocked = { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('denied'); } };
    expect(browserId(blocked, () => 'another-id-1')).toBe('another-id-1');
    expect(browserId(null, () => 'no-storage-1')).toBe('no-storage-1');
  });
});

describe('sendMagicLink', () => {
  it('posts to the sign-in-link function with the anon key as apikey', async () => {
    const f = vi.fn(async () => json(200, { status: 'sent' }));
    const s = store({ [BROWSER_ID_KEY]: 'browser-0123456789' });
    expect(await sendMagicLink('ann@example.com', 'https://x.hintwise.org/app', { fetch: f as unknown as typeof fetch, env: ENV, storage: s })).toEqual({ status: 'sent' });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://auth.hintwise.org/functions/v1/sign-in-link');
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({ 'content-type': 'application/json', apikey: 'anon-key' });
    expect(JSON.parse(String(init.body))).toEqual({ email: 'ann@example.com', redirectTo: 'https://x.hintwise.org/app', app: 'udebt', browserId: 'browser-0123456789' });
  });
  it('sends the Turnstile token and another app slug when given', async () => {
    const f = vi.fn(async () => json(200, { status: 'sent' }));
    await sendMagicLink('a@b.org', 'https://x.hintwise.org/', { fetch: f as unknown as typeof fetch, env: ENV, storage: null, randomId: () => 'r-12345678', turnstileToken: 'tok', app: 'other' });
    expect(JSON.parse(String((f.mock.calls[0] as unknown as [string, RequestInit])[1].body))).toMatchObject({ app: 'other', browserId: 'r-12345678', turnstileToken: 'tok' });
  });
  it('reads wait, challenge and error answers', async () => {
    const run = (status: number, body: unknown) => sendMagicLink('a@b.org', 'https://x.hintwise.org/', { fetch: (async () => json(status, body)) as unknown as typeof fetch, env: ENV, storage: null });
    expect(await run(429, { status: 'wait', retryAt: '2026-11-10T12:02:00.000Z' })).toEqual({ status: 'wait', retryAt: '2026-11-10T12:02:00.000Z' });
    expect(await run(403, { status: 'challenge', siteKey: 'k' })).toEqual({ status: 'challenge', siteKey: 'k' });
    expect(await run(400, { status: 'error', message: 'Enter a valid email address.' })).toEqual({ status: 'error', message: 'Enter a valid email address.' });
    const notJson = await sendMagicLink('a@b.org', 'https://x/', { fetch: (async () => new Response('<html>', { status: 502 })) as unknown as typeof fetch, env: ENV, storage: null });
    expect(notJson).toEqual({ status: 'error', message: 'The sign-in service answered 502.' });
  });
  it('reports a network failure and a missing configuration without throwing', async () => {
    const offline = await sendMagicLink('a@b.org', 'https://x/', { fetch: (async () => { throw new TypeError('Failed to fetch'); }) as unknown as typeof fetch, env: ENV, storage: null });
    expect(offline).toMatchObject({ status: 'error', message: expect.stringContaining('Could not reach') });
    expect(await sendMagicLink('a@b.org', 'https://x/', { env: {} })).toEqual({ status: 'error', message: 'Email sign-in is not set up for this app.' });
    expect(await sendMagicLink('a@b.org', 'https://x/')).toMatchObject({ status: 'error' }); // no env in tests
  });
  it('builds the function URL from the env', () => {
    expect(signInLinkUrl(ENV)).toBe('https://auth.hintwise.org/functions/v1/sign-in-link');
    expect(signInLinkUrl({})).toBeNull();
    expect(signInLinkUrl()).toBeNull();
  });
});

describe('toLinkResult', () => {
  it('falls back to the public site key and keeps failed', () => {
    expect(toLinkResult(403, { status: 'challenge' })).toEqual({ status: 'challenge', siteKey: TURNSTILE_SITE_KEY });
    expect(toLinkResult(403, { status: 'challenge', siteKey: '', failed: true })).toEqual({ status: 'challenge', siteKey: '0x4AAAAAAF7YqIR2_9E_hu1x', failed: true });
  });
  it('reads suppressed with the service\'s message, or the standard one', () => {
    const message = 'This address asked HintWise not to send it email. Sign in with Google instead, or write to support@hintwise.org from this address to turn email back on.';
    expect(toLinkResult(200, { status: 'suppressed', message })).toEqual({ status: 'suppressed', message });
    expect(toLinkResult(200, { status: 'suppressed' })).toEqual({ status: 'suppressed', message: SUPPRESSED_MESSAGE });
    expect(toLinkResult(200, { status: 'suppressed', message: '' })).toEqual({ status: 'suppressed', message: SUPPRESSED_MESSAGE });
    expect(SUPPRESSED_MESSAGE).toBe(message);
  });
  it('turns anything else into an error', () => {
    expect(toLinkResult(429, { status: 'wait' })).toEqual({ status: 'error', message: 'The sign-in service answered 429.' });
    expect(toLinkResult(500, null)).toEqual({ status: 'error', message: 'The sign-in service answered 500.' });
    expect(toLinkResult(500, { message: '' })).toEqual({ status: 'error', message: 'The sign-in service answered 500.' });
  });
});

describe('completeSignInFromUrl', () => {
  const win = (href: string) => ({ location: { href }, history: { state: { s: 1 }, replaceState: vi.fn() } });
  it('does nothing without token_hash and type', async () => {
    const { client, auth } = fakeAuth();
    expect(await completeSignInFromUrl(client, win('https://x.hintwise.org/?type=email'))).toEqual({ status: 'none' });
    expect(await completeSignInFromUrl(client, win('not a url'))).toEqual({ status: 'none' });
    expect(auth.verifyOtp).not.toHaveBeenCalled();
  });
  it('strips the parameters first, then waits for the click before verifying the token hash as type email', async () => {
    const { client, auth } = fakeAuth();
    const w = win('https://x.hintwise.org/app?x=1&token_hash=abc&type=email&email_hint=a***@example.com#notes');
    const r = await completeSignInFromUrl(client, w);
    expect(w.history.replaceState).toHaveBeenCalledWith({ s: 1 }, '', '/app?x=1#notes');
    // A mail scanner that opens the page must not spend the single-use link.
    expect(auth.verifyOtp).not.toHaveBeenCalled();
    expect(r).toMatchObject({ status: 'finish', hint: 'a***@example.com' });
    if (r.status !== 'finish') throw new Error('expected finish');
    expect(await r.accept()).toEqual({ status: 'signed-in' });
    expect(auth.verifyOtp).toHaveBeenCalledWith({ token_hash: 'abc', type: 'email' });
  });
  it('reports a spent or expired link when the visitor finishes it', async () => {
    const { client, auth } = fakeAuth();
    auth.verifyOtp.mockResolvedValueOnce({ data: { user: null, session: null }, error: { message: 'Token has expired or is invalid' } });
    const r = await completeSignInFromUrl(client, win('https://x.hintwise.org/?token_hash=a&type=email'));
    expect(r).toMatchObject({ status: 'finish', hint: '' });
    if (r.status !== 'finish') throw new Error('expected finish');
    expect(await r.accept()).toEqual({ status: 'error', message: EXPIRED_LINK });
  });
  it('keeps the session when the mask matches the account signed in, and still offers the link', async () => {
    const { client, auth } = fakeAuth(session); // ann@example.com
    const w = win('https://x.hintwise.org/app?token_hash=abc&type=email&email_hint=A***@Example.com');
    const r = await completeSignInFromUrl(client, w);
    expect(r.status).toBe('kept');
    if (r.status !== 'kept') return;
    expect(r.current).toBe('a***@example.com');
    expect(w.history.replaceState).toHaveBeenCalledWith({ s: 1 }, '', '/app');
    expect(auth.verifyOtp).not.toHaveBeenCalled();
    expect(await r.accept()).toEqual({ status: 'signed-in' }); // masks collide: the visitor may still use the link
    expect(auth.verifyOtp).toHaveBeenCalledWith({ token_hash: 'abc', type: 'email' });
  });
  it('ignores an email_hint of the wrong shape or length', async () => {
    expect(validHint('a***@example.com')).toBe('a***@example.com');
    for (const bad of ['', 'ann@example.com', 'a**@example.com', 'ab***@example.com', 'a***@exa mple.com', 'a***@<b>.com', ' ***@x.org', `a***@${'b'.repeat(254)}`, `a***@${'b'.repeat(253)}.org`, null]) {
      expect(validHint(bad), String(bad)).toBe('');
    }
    const { client } = fakeAuth(session);
    const r = await completeSignInFromUrl(client, win('https://x.hintwise.org/?token_hash=abc&type=email&email_hint=Click%20here%20to%20win'));
    expect(r).toMatchObject({ status: 'confirm', hint: 'another account' });
  });
  it('asks before switching when another account is signed in, and verifies only on yes', async () => {
    const { client, auth } = fakeAuth(session);
    const r = await completeSignInFromUrl(client, win('https://x.hintwise.org/?token_hash=abc&type=email&email_hint=b***@example.org'));
    expect(r.status).toBe('confirm');
    if (r.status !== 'confirm') return;
    expect(r.hint).toBe('b***@example.org');
    expect(auth.verifyOtp).not.toHaveBeenCalled();
    expect(await r.accept()).toEqual({ status: 'signed-in' });
    expect(auth.verifyOtp).toHaveBeenCalledWith({ token_hash: 'abc', type: 'email' });
    const noHint = await completeSignInFromUrl(client, win('https://x.hintwise.org/?token_hash=abc&type=email'));
    expect(noHint).toMatchObject({ status: 'confirm', hint: 'another account' });
  });
  it('masks addresses the way sign-in-link does', () => {
    expect(maskEmail('Ann@Example.com')).toBe('a***@example.com');
    expect(maskEmail('x')).toBe('***');
    expect(maskEmail(null)).toBe('***');
  });
  it('uses window by default', async () => {
    const { client } = fakeAuth();
    expect(await completeSignInFromUrl(client)).toEqual({ status: 'none' });
  });
});

describe('loadTurnstile', () => {
  const fakeApi = (): TurnstileApi => ({ render: vi.fn(() => 'w1'), reset: vi.fn(), remove: vi.fn() });
  it('uses window.turnstile when it is already there', async () => {
    const api = fakeApi();
    (window as { turnstile?: TurnstileApi }).turnstile = api;
    expect(await loadTurnstile()).toBe(api);
    expect(document.head.querySelector('script')).toBeNull();
  });
  it('adds the explicit-render script once and resolves when it loads', async () => {
    const a = loadTurnstile();
    const b = loadTurnstile();
    expect(a).toBe(b);
    const scripts = document.head.querySelectorAll('script');
    expect(scripts).toHaveLength(1);
    expect(scripts[0].src).toBe(TURNSTILE_SCRIPT);
    expect(TURNSTILE_SCRIPT).toBe('https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit');
    const api = fakeApi();
    (window as { turnstile?: TurnstileApi }).turnstile = api;
    scripts[0].dispatchEvent(new Event('load'));
    expect(await a).toBe(api);
  });
  it('rejects when the script fails or does not start, and may be tried again', async () => {
    const a = loadTurnstile();
    document.head.querySelector('script')!.dispatchEvent(new Event('error'));
    await expect(a).rejects.toThrow('Turnstile did not load.');
    await flush();
    const b = loadTurnstile();
    expect(b).not.toBe(a);
    document.head.querySelectorAll('script')[1].dispatchEvent(new Event('load'));
    await expect(b).rejects.toThrow('Turnstile did not start.');
  });
});

describe('the Google button (branding guidelines)', () => {
  it('says "Continue with Google" next to Google\'s G', () => {
    const onClick = vi.fn();
    const b = googleButton(onClick);
    expect(GOOGLE_BUTTON_TEXT).toBe('Continue with Google');
    expect(b.textContent?.trim()).toBe('Continue with Google');
    expect(b.type).toBe('button');
    const svg = b.querySelector('.hw-signin-google-logo svg')!;
    expect(svg).toBeTruthy();
    expect(svg.getAttribute('viewBox')).toBe('10 10 20 20');
    expect(svg.getAttribute('width')).toBe('20');
    // The G's own outline from Google's asset, and its colours.
    expect(svg.querySelector('mask path')!.getAttribute('d')!.startsWith('M29.3987 18.1814H19.9849V22.0445H25.3598')).toBe(true);
    expect(svg.innerHTML).toContain('#3186FF');
    expect(svg.innerHTML).toContain('#FF4641');
    b.click();
    expect(onClick).toHaveBeenCalled();
  });
  it('ships Google\'s light G asset byte for byte, and only moves its viewport', async () => {
    // sha256 of "Theme=Light, Show text=No, Shape=Square, Platform=Android+Web.svg" from signin-assets.zip (10 Nov 2026).
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(asset));
    expect([...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('')).toBe('6587b180203a99da15c0a041018e15c94ffd9556c4b9855bdc69806081a93791');
    const svg = googleLogoSvg();
    expect(svg.startsWith('<svg width="20" height="20" viewBox="10 10 20 20" focusable="false" aria-hidden="true" fill="none"')).toBe(true);
    expect(svg.slice(svg.indexOf('>'))).toBe(asset.slice(asset.indexOf('>')));
  });
  it('keeps its border and the light theme, and the email button has the same size and weight', () => {
    const rule = (selector: string) => {
      const m = css.match(new RegExp(`(^|\\n)${selector.replace(/[.[\]']/g, '\\$&')} \\{([^}]*)\\}`));
      return m ? m[2] : '';
    };
    const button = rule('.hw-signin-button');
    expect(button).toContain('border: 1px solid #747775;');
    expect(button).toContain('background: #FFFFFF;');
    expect(button).toContain('color: #1F1F1F;');
    expect(button).toContain("font-family: 'Google Sans', Roboto, Arial, sans-serif;");
    expect(button).toContain('font-weight: 500;');
    expect(button).toContain('font-size: 14px;');
    expect(button).toContain('line-height: 20px;');
    expect(button).toContain('padding: 0 12px;');
    expect(button).toContain('height: 40px;');
    expect(button).toMatch(/border-radius: 20px;/);
    expect(rule('.hw-signin-google-logo')).toContain('margin-right: 10px;');
    expect(css).not.toMatch(/\.hw-signin-google[^-][^{]*\{[^}]*border: *(none|0)/);
    const { client } = fakeAuth();
    mountSignIn(root, { client, redirectTo: 'https://x/', completeFromUrl: false });
    return flush().then(() => {
      const google = root.querySelector('.hw-signin-google')!;
      const email = root.querySelector('.hw-signin-email')!;
      expect(google.classList.contains('hw-signin-button')).toBe(true);
      expect(email.classList.contains('hw-signin-button')).toBe(true);
      // The block is on the page, once, first in <head>.
      expect(document.head.firstElementChild?.id).toBe(SIGN_IN_STYLE_ID);
      expect(document.getElementById(SIGN_IN_STYLE_ID)!.textContent).toBe(css);
      ensureSignInStyles();
      expect(document.querySelectorAll(`#${SIGN_IN_STYLE_ID}`)).toHaveLength(1);
    });
  });
});

describe('formatRetryAt', () => {
  it('shows a time today and a weekday with the time otherwise', () => {
    const now = new Date('2026-11-10T12:00:00');
    expect(formatRetryAt('2026-11-10T12:05:00', now)).toMatch(/12:05/);
    expect(formatRetryAt('2026-11-11T09:12:00', now)).toMatch(/^Wednesday .*9:12/);
    expect(formatRetryAt('garbage', now)).toBe('garbage');
    expect(typeof formatRetryAt(new Date().toISOString())).toBe('string');
  });
});

describe('mountSignIn', () => {
  const button = (label: string) => [...root.querySelectorAll('button')].find((b) => b.textContent?.trim() === label)!;
  const statusText = () => root.querySelector('.hw-signin-status')!.textContent;
  const submit = (email: string) => {
    root.querySelector('input')!.value = email;
    root.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
  };
  const mount = (sendLink: (e: string, r: string, o: { turnstileToken?: string }) => Promise<SignInLinkResult>, extra: Record<string, unknown> = {}) => {
    const f = fakeAuth();
    mountSignIn(root, { client: f.client, redirectTo: 'https://x.hintwise.org/app', sendLink, completeFromUrl: false, formatTime: (iso) => `[${iso}]`, ...extra });
    return f;
  };

  it('renders the signed-out panel: Google button and an email form', async () => {
    const { client, auth } = fakeAuth();
    mountSignIn(root, { client, redirectTo: 'https://x.hintwise.org/app/' });
    await flush();
    expect(root.classList.contains('hw-signin')).toBe(true);
    expect(button('Continue with Google')).toBeTruthy();
    expect(root.querySelector('input[type=email]')).toBeTruthy();
    expect(button('Email me a sign-in link').type).toBe('submit');
    button('Continue with Google').click();
    expect(auth.signInWithOAuth).toHaveBeenCalledWith({ provider: 'google', options: { redirectTo: 'https://x.hintwise.org/app/', queryParams: { prompt: 'select_account' } } });
  });
  it('sends the link and says where to look; ignores an empty email', async () => {
    const sendLink = vi.fn(async () => ({ status: 'sent' }) as SignInLinkResult);
    mount(sendLink);
    await flush();
    root.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
    expect(sendLink).not.toHaveBeenCalled();
    submit(' ann@example.com ');
    expect(sendLink).toHaveBeenCalledWith('ann@example.com', 'https://x.hintwise.org/app', {});
    expect(statusText()).toBe('Sending...');
    expect(button('Email me a sign-in link').disabled).toBe(true);
    await flush();
    expect(statusText()).toBe('Check ann@example.com for a sign-in link.');
    expect(button('Email me a sign-in link').disabled).toBe(false);
  });
  it('says when another link may be asked for, in local time', async () => {
    mount(async () => ({ status: 'wait', retryAt: '2026-11-10T12:02:00.000Z' }));
    await flush();
    submit('ann@example.com');
    await flush();
    expect(statusText()).toBe('You can ask for another link at [2026-11-10T12:02:00.000Z].');
  });
  it('formats the time with the default formatter', async () => {
    const f = fakeAuth();
    mountSignIn(root, { client: f.client, redirectTo: 'https://x/', completeFromUrl: false, sendLink: async () => ({ status: 'wait', retryAt: 'not-a-time' }) });
    await flush();
    submit('a@b.org');
    await flush();
    expect(statusText()).toBe('You can ask for another link at not-a-time.');
  });
  it('says why no link comes when the address turned HintWise email off, and keeps Google', async () => {
    mount(async () => ({ status: 'suppressed', message: 'This address asked HintWise not to send it email.' }));
    await flush();
    submit('ann@example.com');
    await flush();
    expect(statusText()).toBe('This address asked HintWise not to send it email.');
    expect(button('Continue with Google')).toBeTruthy();
    expect(button('Email me a sign-in link').disabled).toBe(false);
  });
  it('shows the error when the link cannot be sent', async () => {
    mount(async () => ({ status: 'error', message: 'Enter a valid email address.' }));
    await flush();
    submit('bob');
    await flush();
    expect(statusText()).toBe('Could not send the link: Enter a valid email address.');
  });

  describe('the Turnstile challenge', () => {
    function turnstile() {
      let callback: ((token: string) => void) | undefined;
      let errorCallback: (() => void) | undefined;
      const api: TurnstileApi = {
        render: vi.fn((_el: HTMLElement, o: { callback: (t: string) => void; 'error-callback'?: () => void }) => {
          callback = o.callback;
          errorCallback = o['error-callback'];
          return 'widget-1';
        }),
        reset: vi.fn(),
        remove: vi.fn(),
      };
      return { api, solve: (t: string) => callback?.(t), fail: () => errorCallback?.() };
    }

    it('renders the widget explicitly with the site key and action, resends with the token, and resets after each request', async () => {
      const t = turnstile();
      const answers: SignInLinkResult[] = [{ status: 'challenge', siteKey: 'site-k' }, { status: 'sent' }];
      const sendLink = vi.fn(async () => answers.shift()!);
      mount(sendLink, { loadTurnstile: async () => t.api });
      await flush();
      submit('ann@example.com');
      await flush();
      await flush();
      expect(statusText()).toBe('One more step: complete the check below to get your link.');
      expect(t.api.render).toHaveBeenCalledWith(expect.any(HTMLElement), expect.objectContaining({ sitekey: 'site-k', action: 'sign-in' }));
      expect(root.querySelector('.hw-signin-challenge')!.children).toHaveLength(1);
      t.solve('tok-1');
      await flush();
      expect(sendLink).toHaveBeenLastCalledWith('ann@example.com', 'https://x.hintwise.org/app', { turnstileToken: 'tok-1' });
      expect(t.api.reset).toHaveBeenCalledWith('widget-1');
      expect(t.api.remove).toHaveBeenCalledWith('widget-1');
      expect(statusText()).toBe('Check ann@example.com for a sign-in link.');
      expect(root.querySelector('.hw-signin-challenge')!.children).toHaveLength(0);
    });
    it('drops the widget when the request after a passed check fails, so it cannot resubmit on its own', async () => {
      const t = turnstile();
      const answers: SignInLinkResult[] = [{ status: 'challenge', siteKey: 'k' }, { status: 'error', message: 'Could not send the email. Try again in a minute.' }];
      const sendLink = vi.fn(async () => answers.shift()!);
      mount(sendLink, { loadTurnstile: async () => t.api });
      await flush();
      submit('ann@example.com');
      await flush();
      await flush();
      t.solve('tok-1');
      await flush();
      expect(t.api.remove).toHaveBeenCalledWith('widget-1');
      expect(statusText()).toBe('Could not send the link: Could not send the email. Try again in a minute.');
      t.solve('tok-2'); // a late callback from the dropped widget is ignored
      t.fail();
      await flush();
      expect(sendLink).toHaveBeenCalledTimes(2);
      expect(statusText()).toBe('Could not send the link: Could not send the email. Try again in a minute.');
    });
    it('renders one widget even when the visitor submits twice while the script loads', async () => {
      const t = turnstile();
      let finishLoad: (api: TurnstileApi) => void = () => undefined;
      mount(async () => ({ status: 'challenge', siteKey: 'k' }), { loadTurnstile: () => new Promise<TurnstileApi>((r) => (finishLoad = r)) });
      await flush();
      submit('a@b.org');
      await flush();
      submit('a@b.org');
      await flush();
      finishLoad(t.api);
      await flush();
      expect(t.api.render).toHaveBeenCalledTimes(1);
    });
    it('asks again after a failed check, with the reset widget, and stops after three failures', async () => {
      const t = turnstile();
      const sendLink = vi.fn(async (_e: string, _r: string, o: { turnstileToken?: string }) => (o.turnstileToken ? { status: 'challenge', siteKey: 'k', failed: true } : { status: 'challenge', siteKey: 'k' }) as SignInLinkResult);
      mount(sendLink, { loadTurnstile: async () => t.api });
      await flush();
      submit('ann@example.com');
      await flush();
      await flush();
      t.solve('tok-1');
      await flush();
      expect(statusText()).toBe('The check did not pass. Try it once more.');
      expect(t.api.reset).toHaveBeenCalledTimes(1);
      expect(t.api.render).toHaveBeenCalledTimes(1); // the same widget, reset
      for (let i = 2; i <= MAX_FAILED_CHECKS; i += 1) {
        t.solve(`tok-${i}`);
        await flush();
      }
      expect(statusText()).toBe('The check did not pass. Try again later, or continue with Google.');
      expect(t.api.remove).toHaveBeenCalled();
      submit('ann@example.com'); // the visitor asks again: a fresh start
      await flush();
      await flush();
      expect(t.api.render).toHaveBeenCalledTimes(2);
    });
    it('falls back to the public site key and says when the check cannot load or run', async () => {
      const t = turnstile();
      mount(async () => ({ status: 'challenge', siteKey: '' }), { loadTurnstile: async () => t.api });
      await flush();
      submit('a@b.org');
      await flush();
      await flush();
      expect(t.api.render).toHaveBeenCalledWith(expect.any(HTMLElement), expect.objectContaining({ sitekey: TURNSTILE_SITE_KEY }));
      t.fail();
      expect(statusText()).toBe('The check could not run. Reload the page and try again.');
      root.replaceChildren();
      mount(async () => ({ status: 'challenge', siteKey: 'k' }), { loadTurnstile: async () => { throw new Error('blocked'); } });
      await flush();
      submit('a@b.org');
      await flush();
      await flush();
      expect(statusText()).toBe('The check could not load. Reload the page and try again.');
    });
    it('drops a widget when the panel re-renders, and ignores a load that finishes after it', async () => {
      const t = turnstile();
      let finishLoad: (api: TurnstileApi) => void = () => undefined;
      const f = mount(async () => ({ status: 'challenge', siteKey: 'k' }), { loadTurnstile: () => new Promise<TurnstileApi>((r) => (finishLoad = r)) });
      await flush();
      submit('a@b.org');
      await flush();
      f.emit(null); // re-render while the script loads
      finishLoad(t.api);
      await flush();
      expect(t.api.render).not.toHaveBeenCalled();
      expect(statusText()).toBe('One more step: complete the check below to get your link.'); // the notice survives the re-render
    });
    it('uses the real loader and sendMagicLink by default', async () => {
      const f = fakeAuth();
      mountSignIn(root, { client: f.client, redirectTo: 'https://x/', completeFromUrl: false });
      await flush();
      submit('a@b.org');
      await flush();
      expect(statusText()).toBe('Could not send the link: Email sign-in is not set up for this app.');
    });
  });

  it('offers "Finish signing in" for a link in the URL on mount, verifies only on the click, and reports a spent one', async () => {
    const f = fakeAuth();
    f.auth.verifyOtp.mockResolvedValueOnce({ data: { user: null, session: null }, error: { message: 'expired' } });
    const w = { location: { href: 'https://x.hintwise.org/?token_hash=h&type=email' }, history: { state: null, replaceState: vi.fn() } };
    mountSignIn(root, { client: f.client, redirectTo: 'https://x/', window: w });
    await flush();
    await flush();
    expect(f.auth.verifyOtp).not.toHaveBeenCalled();
    expect(root.querySelector('.hw-signin-note')!.textContent).toBe('Your sign-in link is ready.');
    expect(button('Continue with Google')).toBeTruthy(); // the other ways stay on the page
    button('Finish signing in').click();
    expect(button('Finish signing in').disabled).toBe(true);
    await flush();
    expect(f.auth.verifyOtp).toHaveBeenCalledWith({ token_hash: 'h', type: 'email' });
    expect(statusText()).toBe(EXPIRED_LINK);
    expect(root.querySelector('.hw-signin-finish')).toBeNull(); // the spent link is not offered again
  });
  it('names the masked address on the button and signs in on the click', async () => {
    const f = fakeAuth();
    f.auth.verifyOtp.mockImplementationOnce(async () => {
      f.emit(session);
      return { data: { user: null, session: null }, error: null };
    });
    const w = { location: { href: 'https://x.hintwise.org/?token_hash=h&type=email&email_hint=a***@example.com' }, history: { state: null, replaceState: vi.fn() } };
    mountSignIn(root, { client: f.client, redirectTo: 'https://x/', window: w, profileClient: false });
    await flush();
    await flush();
    button('Finish signing in as a***@example.com').click();
    await flush();
    expect(root.querySelector('.hw-signin-user')!.textContent).toBe('Signed in as ann@example.com');
    expect(root.querySelector('.hw-signin-finish')).toBeNull();
    f.emit(null); // signing out later does not bring the spent link back
    expect(root.querySelector('.hw-signin-finish')).toBeNull();
  });
  it('offers to switch accounts when a link for another account arrives while signed in', async () => {
    const f = fakeAuth(session);
    f.auth.verifyOtp.mockResolvedValueOnce({ data: { user: null, session: null }, error: { message: 'expired' } });
    const w = { location: { href: 'https://x.hintwise.org/?token_hash=h&type=email&email_hint=b***@example.org' }, history: { state: null, replaceState: vi.fn() } };
    mountSignIn(root, { client: f.client, redirectTo: 'https://x/', window: w });
    await flush();
    await flush();
    expect(root.querySelector('.hw-signin-user')!.textContent).toBe('Signed in as ann@example.com');
    const sw = button('Switch to b***@example.org?');
    expect(sw).toBeTruthy();
    expect(f.auth.verifyOtp).not.toHaveBeenCalled();
    sw.click();
    await flush();
    expect(f.auth.verifyOtp).toHaveBeenCalledWith({ token_hash: 'h', type: 'email' });
    expect(statusText()).toBe(EXPIRED_LINK);
    expect(root.querySelector('.hw-signin-switch')).toBeNull();
  });
  it('says who is signed in and offers "Use the link instead" when the masks match', async () => {
    const f = fakeAuth(session);
    const w = { location: { href: 'https://x.hintwise.org/?token_hash=h&type=email&email_hint=a***@example.com' }, history: { state: null, replaceState: vi.fn() } };
    mountSignIn(root, { client: f.client, redirectTo: 'https://x/', window: w });
    await flush();
    await flush();
    expect(root.querySelector('.hw-signin-note')!.textContent).toBe('Already signed in as a***@example.com');
    expect(f.auth.verifyOtp).not.toHaveBeenCalled();
    button('Use the link instead').click();
    await flush();
    expect(f.auth.verifyOtp).toHaveBeenCalledWith({ token_hash: 'h', type: 'email' });
    expect(root.querySelector('.hw-signin-note')).toBeNull();
  });
  it('drops a switch offer when another tab changes the account', async () => {
    const f = fakeAuth(session);
    const w = { location: { href: 'https://x.hintwise.org/?token_hash=h&type=email&email_hint=b***@example.org' }, history: { state: null, replaceState: vi.fn() } };
    mountSignIn(root, { client: f.client, redirectTo: 'https://x/', window: w });
    await flush();
    await flush();
    expect(button('Switch to b***@example.org?')).toBeTruthy();
    f.emit({ access_token: 't2', user: { id: 'u3', email: 'cy@example.com' } as User } as Session);
    await flush();
    expect(root.querySelector('.hw-signin-user')!.textContent).toBe('Signed in as cy@example.com');
    expect(root.querySelector('.hw-signin-switch')).toBeNull();
  });
  it('shows the offer once the signed-in view is drawn, even if the check finishes first', async () => {
    const f = fakeAuth(session);
    let release: () => void = () => undefined;
    f.auth.getSession.mockImplementationOnce(async () => ({ data: { session }, error: null })); // completeSignInFromUrl
    f.auth.getSession.mockImplementationOnce(() => new Promise((r) => (release = () => r({ data: { session }, error: null })))); // onAuth, late
    const w = { location: { href: 'https://x.hintwise.org/?token_hash=h&type=email&email_hint=b***@example.org' }, history: { state: null, replaceState: vi.fn() } };
    mountSignIn(root, { client: f.client, redirectTo: 'https://x/', window: w });
    await flush();
    expect(root.querySelector('.hw-signin-switch')).toBeNull();
    release();
    await flush();
    expect(button('Switch to b***@example.org?')).toBeTruthy();
  });
  it('switches to the signed-in state and back, with sign-out', async () => {
    const { client, auth, emit, unsubscribe } = fakeAuth(session);
    const off = mountSignIn(root, { client, redirectTo: 'https://x/' });
    await flush();
    expect(root.querySelector('.hw-signin-user')!.textContent).toBe('Signed in as ann@example.com');
    expect(root.querySelector('input[type="email"]')).toBeNull();
    expect(root.querySelector('.hw-account-delete summary')!.textContent).toBe('Delete my account');
    button('Sign out').click();
    expect(auth.signOut).toHaveBeenCalled();
    emit(null);
    expect(root.querySelector('form')).toBeTruthy();
    emit({ ...session, user: { id: 'u2' } as User });
    expect(root.querySelector('.hw-signin-user')!.textContent).toBe('Signed in as u2');
    off();
    expect(unsubscribe).toHaveBeenCalled();
  });
  it('shows the farewell on the signed-out panel after the account is deleted', async () => {
    const { client, emit } = fakeAuth(session);
    const env = { VITE_SUPABASE_URL: 'https://auth.hintwise.org', VITE_SUPABASE_ANON_KEY: 'anon' };
    const f = (async () => new Response(JSON.stringify({ status: 'data-deleted', message: 'Data gone, sign-in kept.' }), { status: 200 })) as unknown as typeof fetch;
    mountSignIn(root, { client, redirectTo: 'https://x/', deleteOptions: { env, fetch: f } });
    await flush();
    const box = root.querySelector('.hw-account-delete')!;
    const input = box.querySelector('input')!;
    input.value = 'DELETE';
    input.dispatchEvent(new Event('input'));
    box.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
    await flush();
    await flush();
    emit(null);
    expect(root.querySelector('.hw-signin-status')!.textContent).toBe('Data gone, sign-in kept.');
    emit(null);
  });
  it('defaults the redirect to this page', async () => {
    const { client, auth } = fakeAuth();
    mountSignIn(root, { client });
    await flush();
    button('Continue with Google').click();
    expect(auth.signInWithOAuth).toHaveBeenCalledWith({ provider: 'google', options: { redirectTo: location.origin + location.pathname, queryParams: { prompt: 'select_account' } } });
  });
});

describe('ensureProfile', () => {
  it('calls platform.ensure_profile and returns null on success', async () => {
    const rpc = vi.fn().mockResolvedValue({ error: null });
    const schema = vi.fn().mockReturnValue({ rpc });
    expect(await ensureProfile({ schema } as unknown as ProfileClient)).toBeNull();
    expect(schema).toHaveBeenCalledWith('platform');
    expect(rpc).toHaveBeenCalledWith('ensure_profile');
  });

  it('returns the error message instead of throwing', async () => {
    const rpc = vi.fn().mockResolvedValue({ error: { message: 'not signed in' } });
    expect(await ensureProfile({ schema: () => ({ rpc }) } as unknown as ProfileClient)).toBe('not signed in');
  });
});

describe('mountSignIn profiles', () => {
  function authFake(u: { id: string; email: string } | null) {
    const s = u ? ({ user: u, access_token: 't' } as unknown as Session) : null;
    let listener: ((e: string, s: Session | null) => void) | undefined;
    const auth = {
      getSession: vi.fn().mockResolvedValue({ data: { session: s } }),
      onAuthStateChange: vi.fn((cb: (e: string, s: Session | null) => void) => {
        listener = cb;
        return { data: { subscription: { unsubscribe: vi.fn() } } };
      }),
      signOut: vi.fn(),
      signInWithOAuth: vi.fn(),
      verifyOtp: vi.fn(),
    };
    return { auth, emit: (x: Session | null) => listener?.('SIGNED_IN', x), session: s };
  }

  it('creates the profile once per signed-in user when the client can', async () => {
    const f = authFake({ id: 'u1', email: 'a@b.c' });
    const rpc = vi.fn().mockResolvedValue({ error: null });
    const schema = vi.fn().mockReturnValue({ rpc });
    mountSignIn(document.createElement('div'), { client: { auth: f.auth, schema } as unknown as AuthClient, redirectTo: 'https://x/' });
    await vi.waitFor(() => expect(rpc).toHaveBeenCalledTimes(1));
    f.emit(f.session);
    await Promise.resolve();
    expect(rpc).toHaveBeenCalledTimes(1);
    expect(schema).toHaveBeenCalledWith('platform');
  });

  it('skips profiles when told to, or when the client has no schema()', async () => {
    const f = authFake({ id: 'u2', email: 'a@b.c' });
    const rpc = vi.fn();
    mountSignIn(document.createElement('div'), { client: { auth: f.auth, schema: () => ({ rpc }) } as unknown as AuthClient, redirectTo: 'https://x/', profileClient: false });
    mountSignIn(document.createElement('div'), { client: { auth: f.auth } as unknown as AuthClient, redirectTo: 'https://x/' });
    await new Promise((r) => setTimeout(r, 0));
    expect(rpc).not.toHaveBeenCalled();
  });

  it('uses an explicit profile client', async () => {
    const f = authFake({ id: 'u3', email: 'a@b.c' });
    const rpc = vi.fn().mockResolvedValue({ error: null });
    mountSignIn(document.createElement('div'), { client: { auth: f.auth } as unknown as AuthClient, redirectTo: 'https://x/', profileClient: { schema: () => ({ rpc }) } });
    await vi.waitFor(() => expect(rpc).toHaveBeenCalledTimes(1));
  });
});
