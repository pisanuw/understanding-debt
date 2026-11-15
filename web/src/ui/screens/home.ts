/**
 * The front page for a visitor who is not signed in (HOSTING.md decision 10): what udebt is, the
 * samples, ready without an account, and the HintWise sign-in panel for everything else (adding a
 * repository, the HintWise key, progress kept in an account).
 */
import { mountSignIn } from '../../lib/auth';
import type { App } from '../app';
import { h } from '../dom';
import { repoCard } from './repos';

export const LEAD =
  'udebt maps a repository into the architectural units and the decisions that shaped them, then asks you one question at a time, from what each part is for down to how it works. Your understanding debt goes down as you answer.';

export async function homeScreen(app: App): Promise<HTMLElement> {
  const { repos } = await app.store.repositories();
  const grid = h('div', { class: 'cards' });
  for (const r of repos) grid.append(await repoCard(app, r));
  const panel = h('div', { id: 'signin', 'aria-label': 'Sign in' });
  app.onLeave(mountSignIn(panel, app.deps.signIn));
  const s = h(
    'section',
    { class: 'hero home' },
    h('h1', null, 'Understand the code an AI wrote'),
    h('p', { class: 'lead' }, LEAD),
    h('h2', null, 'Start with a sample: no account needed'),
    h(
      'p',
      { class: 'sub' },
      'Read a sample’s architecture, then answer questions written in advance and check yourself against the reference answer. Your answers stay in this browser.',
    ),
    repos.length ? grid : h('p', { class: 'muted' }, 'The samples could not be loaded. Try again in a moment.'),
    h(
      'div',
      { class: 'card signin-card' },
      h('h2', null, 'Sign in'),
      h('p', { class: 'small muted' }, 'To add your own repositories, have questions written and graded on the HintWise key, and keep your progress in your account.'),
      panel,
    ),
    h(
      'p',
      { class: 'muted small' },
      'Without an account, udebt keeps the verdicts of your answers on the samples in this browser only (never your answer text). Signed in, it keeps them in HintWise’s database (Supabase, US East); "Delete my account" in Settings removes them.',
    ),
  );
  s.dataset.title = 'Samples';
  return s;
}
