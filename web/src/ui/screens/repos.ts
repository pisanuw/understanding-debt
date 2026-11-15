/** The repository list: the samples and the person's own, each with their debt index. */
import type { Repository } from '../../core/specs';
import { parseGitHubUrl } from '../../core/specs';
import { repoHref, type App } from '../app';
import { badge, fmt, h } from '../dom';
import { queue } from './review';

/** A repository's card: name, licence, debt index, units measured, the pinned commit, items due. */
export async function repoCard(app: App, r: Repository): Promise<HTMLElement> {
  const view = await app.repoView(r.id);
  const g = parseGitHubUrl(r.url);
  const a = view?.analysis;
  const units = a ? a.specs.units.filter((u) => !u.parent).length : 0;
  const due = view ? queue(view).due.length : 0;
  return h(
    'a',
    { class: 'card repo-card', href: repoHref(r.id) },
    h('div', { class: 'repo-head' }, h('strong', null, r.name), r.is_sample ? badge('sample', 'sample') : badge('yours')),
    h('div', { class: 'muted small' }, g ? `${g.owner}/${g.repo}` : r.url, r.license ? ` · ${r.license}` : ''),
    a
      ? h(
          'div',
          { class: 'repo-stats' },
          h('span', null, h('span', { class: 'big' }, fmt(view?.scores?.index ?? 100, 1)), ' debt index'),
          h('span', { class: 'muted small' }, `${units} units · ${view?.scores?.basis.measured ?? 0} measured · at ${a.sha.slice(0, 8)}`),
          due ? h('span', { class: 'small due', 'data-testid': 'repo-review-due' }, `${due} to review`) : null,
        )
      : h('div', { class: 'muted small' }, 'No analysis yet.'),
  );
}

/**
 * Fills `grid` with the repositories' cards once they are read, all of them side by side (each
 * its own analysis and records), so a screen draws its text at once and the cards follow.
 * `empty` stands in when there is no repository; `tail` goes after the cards.
 */
export function fillCards(app: App, grid: HTMLElement, empty: () => HTMLElement | null, tail: HTMLElement | null = null): Promise<void> {
  grid.setAttribute('aria-busy', 'true');
  return app.store
    .repositories()
    .then(({ repos }) => Promise.all(repos.map((r) => repoCard(app, r))))
    .then((cards) => {
      const none = cards.length ? null : empty();
      grid.replaceChildren(...cards, ...(none ? [none] : []), ...(tail ? [tail] : []));
    })
    .catch((e: unknown) => {
      const why = e instanceof Error ? e.message : String(e);
      grid.replaceChildren(h('p', { class: 'error', role: 'alert' }, `The repositories could not be loaded (${why}). Try again in a moment.`), ...(tail ? [tail] : []));
    })
    .finally(() => grid.removeAttribute('aria-busy'));
}

export async function reposScreen(app: App): Promise<HTMLElement> {
  const grid = h('div', { class: 'cards' }, h('p', { class: 'muted small loading' }, 'Loading the repositories…'));
  const add = h('a', { class: 'card repo-card add-card', href: '#/add' }, h('strong', null, '+ Add a repository'), h('div', { class: 'muted small' }, 'Any public GitHub repository: shared if someone already analysed it, else analysed with your own key.'));
  void fillCards(app, grid, () => null, add);
  const s = h(
    'section',
    null,
    h('h1', null, 'Repositories'),
    h('p', { class: 'sub' }, 'Pick one to read its specs and climb the quiz ladder. Debt starts at 100 for a repository you have not answered on and goes down as you show what you understand.'),
    grid,
  );
  s.dataset.title = 'Repositories';
  return s;
}
