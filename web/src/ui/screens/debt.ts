/** The person's debt on one repository: the index, the basis counts, every unit's numbers. */
import { nextLevel } from '../../core/ladder';
import { KNOWN, basisOf, decayNote, debtOf } from '../../core/scoring';
import { childrenOf, topLevel, type SpecUnit } from '../../core/specs';
import { repoHref, unitHref, type App, type RepoView } from '../app';
import { badge, fmt, h } from '../dom';

export function debtTab(_app: App, v: RepoView): HTMLElement {
  const a = v.analysis;
  const sc = v.scores;
  if (!a || !sc) return h('p', null, 'This repository has no analysis yet.');
  const box = h('div');
  const b = sc.basis;
  box.append(
    h(
      'div',
      { class: 'tiles' },
      h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Debt index'), h('div', { class: 'value', 'data-testid': 'debt-tile' }, fmt(sc.index, 1)), h('div', { class: 'note' }, '100 = nothing shown yet; 0 = every unit understood')),
      h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Measured'), h('div', { class: 'value' }, String(b.measured)), h('div', { class: 'note' }, 'units with your answers')),
      h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Estimated'), h('div', { class: 'value' }, String(b.estimated)), h('div', { class: 'note' }, 'from a sub-unit’s parent')),
      h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Known'), h('div', { class: 'value' }, String(sc.scores.filter((s) => !s.parent && s.scope === 'architectural' && s.understanding >= KNOWN).length)), h('div', { class: 'note' }, `understanding ${KNOWN} or more`)),
    ),
  );
  const table = h('table', { class: 'debt' });
  table.append(h('thead', null, h('tr', null, h('th', null, 'Unit'), h('th', null, 'Importance'), h('th', { title: 'Understanding' }, h('span', { class: 'long' }, 'Understanding'), h('span', { class: 'short' }, 'Und.')), h('th', null, 'Depth'), h('th', null, 'Next'), h('th', null, 'Debt'))));
  const body = h('tbody');
  const row = (u: SpecUnit, child: boolean) => {
    const s = sc.byId.get(u.id);
    if (!s) return;
    const arch = s.scope === 'architectural';
    const note = decayNote(s);
    body.append(
      h(
        'tr',
        { class: child ? 'child' : undefined },
        h('td', null, h('a', { href: unitHref(v.repo.id, u.id) }, u.name), ' ', arch ? badge(basisOf(s), basisOf(s)) : badge('blackbox'), note ? h('div', { class: 'note' }, note) : null),
        h('td', { class: 'num' }, fmt(s.importance)),
        h('td', { class: 'num' }, arch ? fmt(s.understanding) : '-'),
        h('td', { class: 'num' }, arch ? `${s.depth ?? 0}/5` : '-'),
        h('td', { class: 'num' }, arch ? String(nextLevel(v.records.filter((r) => r.unit_id === u.id))) : '-'),
        h('td', { class: 'num' }, arch ? fmt(debtOf(s)) : '-'),
      ),
    );
  };
  for (const u of topLevel(a.specs.units)) {
    row(u, false);
    for (const c of childrenOf(a.specs.units, u.id)) row(c, true);
  }
  table.append(body);
  box.append(h('div', { class: 'card table-wrap' }, table));
  box.append(
    h(
      'p',
      { class: 'muted small' },
      'Understanding per unit: a third for each of ladder levels 1-3 (purpose, decision, consequence) by your latest answers there, plus 0.1 each for levels 4-5; each level fades as the unit changes after your answer (k 0.2 at levels 1-3, 1.0 at 4-5). Debt is importance times what is not understood; the index sums it over the top-level units. ',
      h('a', { href: repoHref(v.repo.id, 'quiz') }, 'Take a quiz'),
      ' to lower it.',
    ),
  );
  if (sc.unknownShare.length) box.append(h('p', { class: 'muted small' }, `Some answers were given at a commit this analysis has no change figures for (${sc.unknownShare.join(', ')}); they are not faded.`));
  return box;
}
