/**
 * The preview's sign-in: a fake of the part of supabase-js the sign-in panel (lib/auth.ts)
 * uses, so `npm run dev`, `vite preview` and the Playwright walkthrough run the real panel
 * without Supabase. "Continue with Google" and an emailed link both sign in
 * preview@example.com at once; the state lives in sessionStorage so a reload keeps it.
 */
import type { Session, User } from '@supabase/supabase-js';
import { PREVIEW_TOKEN } from '../core/preview';

type Listener = (event: string, session: Session | null) => void;

export const PREVIEW_USER = { id: '00000000-0000-4000-8000-000000000001', email: 'preview@example.com' };
const KEY = 'udebt.previewSignedIn';

export class FakeAuth {
  private listeners: Listener[] = [];
  private signedIn: boolean;

  constructor(private readonly storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null = null) {
    let v: string | null = null;
    try {
      v = storage?.getItem(KEY) ?? null;
    } catch {
      v = null;
    }
    this.signedIn = v === '1';
  }

  session(): Session | null {
    if (!this.signedIn) return null;
    const user = { id: PREVIEW_USER.id, email: PREVIEW_USER.email, aud: 'authenticated', app_metadata: {}, user_metadata: {}, created_at: '2026-11-15T00:00:00Z' } as User;
    return { access_token: PREVIEW_TOKEN, refresh_token: 'preview', expires_in: 3600, token_type: 'bearer', user } as Session;
  }

  private set(on: boolean): void {
    this.signedIn = on;
    try {
      if (on) this.storage?.setItem(KEY, '1');
      else this.storage?.removeItem(KEY);
    } catch {
      /* not kept */
    }
    const s = this.session();
    for (const l of [...this.listeners]) l(on ? 'SIGNED_IN' : 'SIGNED_OUT', s);
  }

  readonly auth = {
    getSession: async () => ({ data: { session: this.session() }, error: null }),
    onAuthStateChange: (cb: Listener) => {
      this.listeners.push(cb);
      return { data: { subscription: { unsubscribe: () => void (this.listeners = this.listeners.filter((l) => l !== cb)) } } };
    },
    signInWithOAuth: async () => {
      this.set(true);
      return { data: { provider: 'google', url: null }, error: null };
    },
    verifyOtp: async () => {
      this.set(true);
      return { data: { session: this.session(), user: this.session()?.user ?? null }, error: null };
    },
    signOut: async () => {
      this.set(false);
      return { error: null };
    },
  };

  /** The preview token while signed in. */
  async token(): Promise<string | null> {
    return this.signedIn ? PREVIEW_TOKEN : null;
  }
}
