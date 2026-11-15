// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import {
  CONFIRM_WORD,
  DELETED_MESSAGE,
  SIGN_IN_AGAIN,
  deleteAccount,
  deleteAccountControl,
  deleteAccountUrl,
  toDeleteResult,
  type AccountClient,
} from '../src/lib/account';

const env = { VITE_SUPABASE_URL: 'https://auth.hintwise.org/', VITE_SUPABASE_ANON_KEY: 'anon' };

function client(token: string | null = 'jwt') {
  const signOut = vi.fn(async () => ({ error: null }));
  const c = {
    auth: {
      getSession: async () => ({ data: { session: token ? { access_token: token } : null }, error: null }),
      signOut,
    },
  } as unknown as AccountClient;
  return { c, signOut };
}

function answering(status: number, body: unknown) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;
}

describe('deleteAccount', () => {
  it('posts the confirm word with the session token and signs out locally when deleted', async () => {
    const { c, signOut } = client();
    const f = answering(200, { status: 'deleted' });
    expect(await deleteAccount(CONFIRM_WORD, { client: c, env, fetch: f })).toEqual({ status: 'deleted' });
    const [url, init] = (f as unknown as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(url).toBe('https://auth.hintwise.org/functions/v1/delete-account');
    expect(init.headers).toMatchObject({ authorization: 'Bearer jwt', apikey: 'anon' });
    expect(JSON.parse(String(init.body))).toEqual({ confirm: 'DELETE' });
    expect(signOut).toHaveBeenCalledWith({ scope: 'local' });
  });

  it('signs out when the data is deleted and the sign-in kept', async () => {
    const { c, signOut } = client();
    const r = await deleteAccount(CONFIRM_WORD, { client: c, env, fetch: answering(200, { status: 'data-deleted', message: 'kept' }) });
    expect(r).toEqual({ status: 'data-deleted', message: 'kept' });
    expect(signOut).toHaveBeenCalledWith({ scope: 'global' });
  });

  it('keeps the session for reauth and errors', async () => {
    const { c, signOut } = client();
    expect(await deleteAccount(CONFIRM_WORD, { client: c, env, fetch: answering(403, { status: 'reauth', message: 'again' }) })).toEqual({ status: 'reauth', message: 'again' });
    expect(await deleteAccount(CONFIRM_WORD, { client: c, env, fetch: answering(500, 'nope') })).toEqual({ status: 'error', message: 'The account service answered 500.' });
    expect(signOut).not.toHaveBeenCalled();
  });

  it('needs the env, a session and a reachable service', async () => {
    expect((await deleteAccount(CONFIRM_WORD, { client: client().c, env: {} })).status).toBe('error');
    expect(await deleteAccount(CONFIRM_WORD, { client: client(null).c, env, fetch: answering(200, {}) })).toEqual({ status: 'error', message: 'Sign in first.' });
    const down = vi.fn(async () => { throw new Error('offline'); }) as unknown as typeof fetch;
    expect(await deleteAccount(CONFIRM_WORD, { client: client().c, env, fetch: down })).toMatchObject({ status: 'error', message: expect.stringMatching(/Could not reach/) });
  });
});

describe('toDeleteResult and deleteAccountUrl', () => {
  it('fills in messages and rejects odd answers', () => {
    expect(toDeleteResult(200, { status: 'data-deleted' })).toEqual({ status: 'data-deleted', message: DELETED_MESSAGE });
    expect(toDeleteResult(403, { status: 'reauth' }).status).toBe('reauth');
    expect(toDeleteResult(400, { status: 'error', message: 'Type DELETE' })).toEqual({ status: 'error', message: 'Type DELETE' });
    expect(toDeleteResult(502, null)).toEqual({ status: 'error', message: 'The account service answered 502.' });
    expect(deleteAccountUrl({})).toBeNull();
  });
});

describe('deleteAccountControl', () => {
  const flush = () => new Promise((r) => setTimeout(r, 0));

  it('enables the button only for the exact word, then reports and hands the message on', async () => {
    const { c } = client();
    const onDone = vi.fn();
    const el = deleteAccountControl({ client: c, env, fetch: answering(200, { status: 'deleted' }), onDone });
    document.body.append(el);
    const input = el.querySelector('input')!;
    const go = el.querySelector('button')!;
    expect(el.querySelector('summary')!.textContent).toBe('Delete my account');
    expect(go.disabled).toBe(true);
    input.value = 'delete';
    input.dispatchEvent(new Event('input'));
    expect(go.disabled).toBe(true);
    el.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
    await flush();
    expect(onDone).not.toHaveBeenCalled();
    input.value = ' DELETE ';
    input.dispatchEvent(new Event('input'));
    expect(go.disabled).toBe(false);
    el.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
    await flush();
    await flush();
    expect(onDone).toHaveBeenCalledWith(DELETED_MESSAGE);
    expect(el.querySelector('[role="status"]')!.textContent).toBe(DELETED_MESSAGE);
    expect(go.disabled).toBe(true);
  });

  it('shows the reason and lets the visitor try again after reauth', async () => {
    const onDone = vi.fn();
    const { c, signOut } = client();
    const el = deleteAccountControl({ client: c, env, fetch: answering(403, { status: 'reauth', message: 'Sign in again.' }), onDone });
    const input = el.querySelector('input')!;
    input.value = CONFIRM_WORD;
    input.dispatchEvent(new Event('input'));
    el.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
    await flush();
    await flush();
    expect(el.querySelector('[role="status"]')!.textContent).toBe('Sign in again.');
    expect(el.querySelector('button')!.disabled).toBe(false);
    expect(onDone).not.toHaveBeenCalled();
    const again = [...el.querySelectorAll('button')].find((b) => b.textContent === 'Sign in again')!;
    again.click();
    expect(onDone).toHaveBeenCalledWith(SIGN_IN_AGAIN);
    expect(signOut).toHaveBeenCalledWith({ scope: 'local' });
  });

  it('passes the kept-sign-in message on', async () => {
    const onDone = vi.fn();
    const el = deleteAccountControl({ client: client().c, env, fetch: answering(200, { status: 'data-deleted', message: 'kept' }), onDone });
    const input = el.querySelector('input')!;
    input.value = CONFIRM_WORD;
    input.dispatchEvent(new Event('input'));
    el.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
    await flush();
    await flush();
    expect(onDone).toHaveBeenCalledWith('kept');
  });
});
