/** Signed out: what udebt is, the HintWise sign-in panel, and what is stored. */
import { mountSignIn } from '../../lib/auth';
import type { App } from '../app';
import { h } from '../dom';

export function signinScreen(app: App): HTMLElement {
  const panel = h('div', { id: 'signin', 'aria-label': 'Sign in' });
  mountSignIn(panel, app.deps.signIn);
  const s = h(
    'section',
    { class: 'hero' },
    h('h1', null, 'Understand the code an AI wrote'),
    h(
      'p',
      { class: 'lead' },
      'udebt maps a repository into the architectural units and the decisions that shaped them, then asks you one question at a time, from what each part is for down to how it works. Your understanding debt goes down as you answer.',
    ),
    h('div', { class: 'card signin-card' }, h('h2', null, 'Sign in'), panel),
    h(
      'p',
      { class: 'muted small' },
      'Three public repositories are ready to start with: huggingface_hub, sentry-javascript and mlflow. udebt keeps the verdicts of your answers (never your answer text) in HintWise’s database (Supabase, US East); "Delete my account" in Settings removes them.',
    ),
  );
  s.dataset.title = 'Sign in';
  return s;
}
