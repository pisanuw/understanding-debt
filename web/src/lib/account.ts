/**
 * "Delete my account" for a signed-in visitor (docs/platform.md, "Account
 * deletion"). The platform's delete-account function deletes the visitor's
 * data in every HintWise app, then their sign-in, unless another app on the
 * shared sign-in service still uses it. It needs a sign-in from the last 15
 * minutes and the word DELETE typed out.
 *
 * Copied unchanged into each app's src/lib/ from platform/templates/app;
 * the sign-in panel (auth.ts) shows deleteAccountControl under "Sign out".
 */
import type { SupabaseClient } from '@supabase/supabase-js';
import { getSupabase, supabaseEnv } from './supabase';

export type AccountClient = Pick<SupabaseClient, 'auth'>;

/** The word the visitor types; the function checks the same word. */
export const CONFIRM_WORD = 'DELETE';

export type DeleteResult =
  | { status: 'deleted' }
  | { status: 'data-deleted'; message: string }
  | { status: 'reauth'; message: string }
  | { status: 'error'; message: string };

export const DELETED_MESSAGE = 'Your account and your data in every HintWise app are deleted.';
/** Shown on the signed-out panel after "Sign in again". */
export const SIGN_IN_AGAIN = 'Sign in again, then open "Delete my account" within 15 minutes.';

/** The function's URL for the configured project, or null without Supabase env. */
export function deleteAccountUrl(env: Record<string, unknown> = import.meta.env): string | null {
  const e = supabaseEnv(env);
  return e ? `${e.url.replace(/\/+$/, '')}/functions/v1/delete-account` : null;
}

/** Reads the function's answer; anything unexpected becomes an error with a message to show. */
export function toDeleteResult(httpStatus: number, data: unknown): DeleteResult {
  const d = typeof data === 'object' && data !== null ? (data as Record<string, unknown>) : {};
  const message = typeof d.message === 'string' && d.message ? d.message : '';
  if (d.status === 'deleted') return { status: 'deleted' };
  if (d.status === 'data-deleted') return { status: 'data-deleted', message: message || DELETED_MESSAGE };
  if (d.status === 'reauth') return { status: 'reauth', message: message || 'Sign in again, then delete your account.' };
  return { status: 'error', message: message || `The account service answered ${httpStatus}.` };
}

export interface DeleteOptions {
  client?: AccountClient;
  env?: Record<string, unknown>;
  fetch?: typeof fetch;
}

/**
 * Asks the platform to delete the signed-in visitor's account. Never throws.
 * After deleted, this browser's session is ended (the sign-in is gone, so
 * every other session is too). After data-deleted, every session of the
 * sign-in is ended (scope global), so a forgotten or stolen one stops too.
 */
export async function deleteAccount(confirm: string, opts: DeleteOptions = {}): Promise<DeleteResult> {
  const env = opts.env ?? import.meta.env;
  const e = supabaseEnv(env);
  const url = deleteAccountUrl(env);
  if (!e || !url) return { status: 'error', message: 'Account deletion is not set up for this app.' };
  const client = opts.client ?? getSupabase();
  const { data } = await client.auth.getSession();
  const token = data.session?.access_token;
  if (!token) return { status: 'error', message: 'Sign in first.' };
  let res: Response;
  try {
    res = await (opts.fetch ?? fetch)(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json', apikey: e.anonKey, authorization: `Bearer ${token}` },
      body: JSON.stringify({ confirm }),
    });
  } catch {
    return { status: 'error', message: 'Could not reach the account service. Check your connection and try again.' };
  }
  const result = toDeleteResult(res.status, await res.json().catch(() => null));
  if (result.status === 'deleted' || result.status === 'data-deleted') {
    await client.auth.signOut({ scope: result.status === 'deleted' ? 'local' : 'global' }).catch(() => undefined);
  }
  return result;
}

/**
 * The control: a closed "Delete my account" section with what it does, a
 * box for the word DELETE and the button. `onDone` gets the message to show
 * on the signed-out panel: after the account is gone, or after "Sign in
 * again" when the sign-in was too old.
 */
export function deleteAccountControl(opts: DeleteOptions & { onDone?: (message: string) => void } = {}): HTMLElement {
  const box = document.createElement('details');
  box.className = 'hw-account-delete';
  const summary = document.createElement('summary');
  summary.textContent = 'Delete my account';
  const what = document.createElement('p');
  what.textContent =
    'This deletes your sign-in and what every HintWise app keeps for you. It cannot be undone. ' +
    'If you signed in more than 15 minutes ago, you will be asked to sign in again first.';
  const form = document.createElement('form');
  const label = document.createElement('label');
  label.textContent = `Type ${CONFIRM_WORD} to confirm `;
  const input = document.createElement('input');
  input.autocomplete = 'off';
  input.spellcheck = false;
  label.append(input);
  const go = document.createElement('button');
  go.type = 'submit';
  go.textContent = 'Delete my account';
  go.disabled = true;
  const status = document.createElement('p');
  status.setAttribute('role', 'status');
  const again = document.createElement('button');
  again.type = 'button';
  again.textContent = 'Sign in again';
  again.addEventListener('click', () => {
    opts.onDone?.(SIGN_IN_AGAIN);
    void (opts.client ?? getSupabase()).auth.signOut({ scope: 'local' }).catch(() => undefined);
  });
  input.addEventListener('input', () => {
    go.disabled = input.value.trim() !== CONFIRM_WORD;
  });
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    if (input.value.trim() !== CONFIRM_WORD) return;
    go.disabled = true;
    status.textContent = 'Deleting...';
    void deleteAccount(CONFIRM_WORD, opts).then((r) => {
      if (r.status === 'deleted') opts.onDone?.(DELETED_MESSAGE);
      else if (r.status === 'data-deleted') opts.onDone?.(r.message);
      status.textContent = r.status === 'deleted' ? DELETED_MESSAGE : r.message;
      go.disabled = r.status === 'deleted' || r.status === 'data-deleted';
      if (r.status === 'reauth' && !again.isConnected) box.append(again);
    });
  });
  form.append(label, go);
  box.append(summary, what, form, status);
  return box;
}
