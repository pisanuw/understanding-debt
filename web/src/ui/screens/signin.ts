/** The sign-in page: what udebt is, the HintWise sign-in panel, and what is stored. */
import { mountSignIn } from '../../lib/auth';
import type { App } from '../app';
import { h } from '../dom';
import { LEAD } from './home';

export function signinScreen(app: App): HTMLElement {
  const panel = h('div', { id: 'signin', 'aria-label': 'Sign in' });
  app.onLeave(mountSignIn(panel, app.deps.signIn));
  const s = h(
    'section',
    { class: 'hero' },
    h('h1', null, 'Understand the code an AI wrote'),
    h('p', { class: 'lead' }, LEAD),
    h('div', { class: 'card signin-card' }, h('h2', null, 'Sign in'), panel),
    h(
      'p',
      { class: 'muted small' },
      app.deps.anon
        ? 'The samples need no account: '
        : 'Four public repositories are ready to start with: huggingface_hub, sentry-javascript, mlflow and udebt itself. ',
      app.deps.anon ? h('a', { href: '#/' }, 'start with one') : null,
      app.deps.anon ? '. ' : null,
      'Signed in, udebt keeps the verdicts of your answers (never your answer text) in HintWise’s database (Supabase, US East); "Delete my account" in Settings removes them.',
    ),
  );
  s.dataset.title = 'Sign in';
  return s;
}
