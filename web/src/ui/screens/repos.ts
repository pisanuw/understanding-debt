/** The repository list: the samples and the person's own, each with their debt index. */
import { parseGitHubUrl } from '../../core/specs';
import { repoHref, type App } from '../app';
import { badge, fmt, h } from '../dom';
import { queue } from './review';

export async function reposScreen(app: App): Promise<HTMLElement> {
  const { repos } = await app.deps.store.repositories();
  const grid = h('div', { class: 'cards' });
  for (const r of repos) {
    const view = await app.repoView(r.id);
    const g = parseGitHubUrl(r.url);
    const a = view?.analysis;
    const units = a ? a.specs.units.filter((u) => !u.parent).length : 0;
    const due = view ? queue(view).due.length : 0;
    const card = h(
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
    grid.append(card);
  }
  grid.append(h('a', { class: 'card repo-card add-card', href: '#/add' }, h('strong', null, '+ Add a repository'), h('div', { class: 'muted small' }, 'Any public GitHub repository: shared if someone already analysed it, else analysed with your own key.')));
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
