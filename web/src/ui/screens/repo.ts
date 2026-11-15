/**
 * One repository: the header (name, licence, pinned commit, debt index), the tabs, and the
 * specs view (the tour, the units and sub-units) or one unit's page (orientation, decision,
 * alternatives and why, the commits that shaped it, connections, the walkthrough with its
 * code fetched from GitHub at the pinned sha, and the person's answers on it).
 */
import { label, nextLevel } from '../../core/ladder';
import { decayNote, debtOf } from '../../core/scoring';
import { childrenOf, connections, githubLink, orientationOf, parseGitHubUrl, topLevel, unitById, type SpecUnit } from '../../core/specs';
import { repoHref, unitHref, type App, type RepoView, type Route } from '../app';
import { add, badge, button, day, extLink, fmt, h } from '../dom';
import { renderMarkdown } from '../markdown';
import { debtTab } from './debt';
import { quizTab } from './quiz';
import { walkthroughCard } from './walkthrough';

function header(app: App, v: RepoView, tab: string): HTMLElement {
  const g = parseGitHubUrl(v.repo.url);
  const a = v.analysis;
  const tabs = h('nav', { class: 'tabs', 'aria-label': 'Repository sections' });
  for (const [key, text] of [
    ['specs', 'Specs'],
    ['quiz', 'Quiz'],
    ['debt', 'Your debt'],
  ] as const) {
    tabs.append(h('a', { href: repoHref(v.repo.id, key), 'aria-current': tab === key ? 'page' : undefined }, text));
  }
  return h(
    'div',
    { class: 'repo-top' },
    h(
      'div',
      { class: 'repo-title' },
      h('h1', null, v.repo.name),
      v.scores ? h('span', { class: 'stat' }, 'debt index ', h('strong', { 'data-testid': 'debt-index' }, fmt(v.scores.index, 1))) : null,
    ),
    h(
      'p',
      { class: 'muted small' },
      extLink(g?.url ?? null, g ? `${g.owner}/${g.repo}` : v.repo.url),
      v.repo.license ? ` · ${v.repo.license} licence` : '',
      a ? ' · at ' : '',
      a ? extLink(githubLink(v.repo.url, a.sha), a.sha.slice(0, 8), 'mono') : null,
      a?.manifest.generated_at ? ` · analysed ${day(a.manifest.generated_at)}` : '',
      a && a.user_id === null ? ' · prepared by HintWise' : a && a.user_id === app.user?.id ? ' · your analysis' : a ? ' · shared by another person' : '',
    ),
    tabs,
  );
}

function unitRow(v: RepoView, u: SpecUnit, child = false): HTMLElement {
  const s = v.scores?.byId.get(u.id);
  return h(
    'li',
    { class: child ? 'unit-row child' : 'unit-row' },
    h('div', { class: 'unit-name' }, h('a', { href: unitHref(v.repo.id, u.id) }, u.name), u.kind ? badge(u.kind) : null, (u.scope ?? 'architectural') === 'blackbox' ? badge('blackbox') : null),
    u.summary ? h('div', { class: 'small' }, u.summary) : null,
    s && (u.scope ?? 'architectural') === 'architectural'
      ? h('div', { class: 'meta' }, `understanding ${fmt(s.understanding)} · depth ${s.depth ?? 0}/5 · next level ${nextLevel(v.records.filter((r) => r.unit_id === u.id))} · debt ${fmt(debtOf(s))}`)
      : null,
  );
}

function specsTab(v: RepoView): HTMLElement {
  const a = v.analysis;
  if (!a) return h('p', null, 'This repository has no analysis yet.');
  const box = h('div');
  const tour = a.specs.tour;
  if (tour?.markdown) {
    const body = h('div', { class: 'prose' });
    body.append(renderMarkdown(tour.markdown.replace(/^#\s[^\n]*\n+/, '')));
    box.append(h('details', { class: 'card tour' }, h('summary', null, h('strong', null, 'Architecture tour'), ` · the whole system in one read, about ${tour.minutes} minutes${v.records.length ? '' : ': start here'}`), body));
  }
  const list = h('ul', { class: 'list units' });
  const order = a.specs.order ?? [];
  const tops = [...topLevel(a.specs.units)].sort((x, y) => {
    const i = order.indexOf(x.id);
    const j = order.indexOf(y.id);
    return (i < 0 ? 1e9 : i) - (j < 0 ? 1e9 : j);
  });
  for (const u of tops) {
    list.append(unitRow(v, u));
    for (const c of childrenOf(a.specs.units, u.id)) list.append(unitRow(v, c, true));
  }
  box.append(h('div', { class: 'card' }, h('h2', null, 'Units'), h('p', { class: 'sub' }, order.length ? 'In the order to learn them: each after the units it uses.' : 'The architectural units and their sub-units.'), list));
  if (v.repo.is_sample) {
    const g = parseGitHubUrl(v.repo.url);
    box.append(
      h('p', { class: 'muted small' }, `Sample: ${g ? `${g.owner}/${g.repo}` : v.repo.name}${v.repo.license ? `, ${v.repo.license} licence` : ''}. The analysis references its code at the pinned commit; the code itself stays on GitHub.`),
    );
  }
  return box;
}

function kv(rows: [string, Node | string | null | undefined][]): HTMLElement {
  const dl = h('dl', { class: 'kv' });
  for (const [k, v] of rows) if (v) dl.append(h('dt', null, k), h('dd', null, v));
  return dl;
}

function unitPage(app: App, v: RepoView, unitId: string): HTMLElement {
  const a = v.analysis;
  const u = a ? unitById(a.specs.units, unitId) : undefined;
  if (!a || !u) return h('p', { class: 'error' }, `Unknown unit: ${unitId}`);
  const s = v.scores?.byId.get(u.id);
  const recs = v.records.filter((r) => r.unit_id === u.id);
  const lv = nextLevel(recs);
  const parent = u.parent ? unitById(a.specs.units, u.parent) : undefined;
  const box = h('div', { class: 'unit-page' });
  add(
    box,
    h('p', { class: 'crumbs small' }, h('a', { href: repoHref(v.repo.id) }, 'Specs'), parent ? ' / ' : '', parent ? h('a', { href: unitHref(v.repo.id, parent.id) }, parent.name) : null),
    h('h2', null, u.name),
    u.summary ? h('p', { class: 'sub' }, u.summary) : null,
  );
  if (s && (u.scope ?? 'architectural') === 'architectural') {
    const note = decayNote(s);
    box.append(
      h(
        'div',
        { class: 'tiles' },
        h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Understanding'), h('div', { class: 'value' }, fmt(s.understanding)), badge(s.source === 'none' ? 'none' : s.source === 'parent' ? 'estimated' : 'measured', s.source === 'none' ? '' : s.source === 'parent' ? 'estimated' : 'measured'), note ? h('div', { class: 'note' }, note) : null),
        h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Depth'), h('div', { class: 'value' }, `${s.depth ?? 0}/5`), h('div', { class: 'note' }, 'ladder levels passed')),
        h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Next'), h('div', { class: 'value' }, `level ${lv}`), h('div', { class: 'note' }, label(lv).replace(/^level \d of 5: /, ''))),
        h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Debt'), h('div', { class: 'value' }, fmt(debtOf(s))), h('div', { class: 'note' }, `importance ${fmt(s.importance)}`)),
      ),
    );
    box.append(
      h(
        'div',
        { class: 'row gap-top' },
        button(
          'Quiz this unit',
          () => {
            app.quizUnit = u.id;
            app.go(repoHref(v.repo.id, 'quiz'));
          },
          'primary',
        ),
      ),
    );
  }
  const o = orientationOf(u);
  const where = h('div');
  for (const w of o.where) where.append(h('div', null, extLink(githubLink(v.repo.url, a.sha, w.path), w.path, 'mono'), w.what ? `: ${w.what}` : ''));
  const c = connections(a.specs, u.parent ?? u.id);
  const name = (id: string) => unitById(a.specs.units, id)?.name ?? id;
  const conn = [c.uses.length ? `uses ${c.uses.map(name).join(', ')}` : '', c.used_by.length ? `used by ${c.used_by.map(name).join(', ')}` : '', c.changes_with.length ? `changes with ${c.changes_with.map(name).join(', ')}` : '']
    .filter(Boolean)
    .join('; ');
  box.append(
    h(
      'div',
      { class: 'card' },
      h('h3', null, 'Orientation'),
      kv([
        ['What it is for', o.purpose],
        ['The decision', o.decision],
        ['Where it lives', where],
        ['Who shaped it', o.shaped_by],
        [u.parent ? 'Connections (of its unit)' : 'Connections', conn || 'none found'],
      ]),
      o.fallback ? h('p', { class: 'muted small' }, 'Made from the unit’s own description: no orientation was written for it.') : null,
    ),
    h(
      'div',
      { class: 'card' },
      h('h3', null, 'The decision'),
      kv([
        ['Chosen', u.decision ?? ''],
        ['Alternatives', u.alternatives ?? ''],
        ['Why', u.why ?? ''],
        ['Paths', u.paths?.length ? h('span', { class: 'mono small' }, u.paths.join(', ')) : null],
      ]),
    ),
  );
  const hist = u.history ?? [];
  const hl = h('ul', { class: 'list' });
  for (const e of hist) {
    hl.append(h('li', null, h('div', { class: 'small muted' }, extLink(githubLink(v.repo.url, e.sha), e.sha.slice(0, 8), 'mono'), ` · ${e.date ?? ''} · `, badge(e.klass ?? '?', `klass-${e.klass ?? 'unknown'}`), e.author ? ` · ${e.author}` : ''), h('div', null, e.subject ?? '')));
  }
  box.append(h('div', { class: 'card' }, h('h3', null, 'The commits that shaped it'), hist.length ? hl : h('p', { class: 'muted' }, 'None found in the history.')));
  box.append(walkthroughCard(app, v, u));
  const al = h('ul', { class: 'list' });
  for (const r of [...recs].reverse()) {
    al.append(h('li', null, h('div', { class: 'small muted' }, `${day(r.ts)} · level ${r.level} · `, badge(r.verdict, r.verdict), r.revealed ? ' · answer shown' : ''), h('div', null, r.question)));
  }
  box.append(h('div', { class: 'card' }, h('h3', null, 'Your answers on this unit'), recs.length ? al : h('p', { class: 'muted' }, 'None yet.')));
  return box;
}

export async function repoScreen(app: App, route: Extract<Route, { name: 'repo' | 'unit' }>): Promise<HTMLElement> {
  const v = await app.repoView(route.id);
  if (!v) {
    const s = h('section', null, h('h1', null, 'Not found'), h('p', null, 'This repository is not in your list. ', h('a', { href: '#/' }, 'Back to the repositories')));
    s.dataset.title = 'Not found';
    return s;
  }
  const tab = route.name === 'unit' ? 'specs' : route.tab;
  const s = h('section', { class: 'repo' }, header(app, v, tab));
  if (route.name === 'unit') s.append(unitPage(app, v, route.unit));
  else if (tab === 'quiz') s.append(quizTab(app, v));
  else if (tab === 'debt') s.append(debtTab(app, v));
  else s.append(specsTab(v));
  const u = route.name === 'unit' && v.analysis ? unitById(v.analysis.specs.units, route.unit) : undefined;
  s.dataset.title = u ? `${u.name} · ${v.repo.name}` : `${v.repo.name}${tab === 'specs' ? '' : tab === 'quiz' ? ' quiz' : ' debt'}`;
  return s;
}
