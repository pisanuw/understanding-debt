/**
 * A unit's walkthrough (`udebt dig`): one real request traced through its code in three to
 * six steps. The analysis keeps each step's file and line range at the pinned sha, never the
 * code; the lines are fetched from GitHub through /api/excerpt when the step is shown.
 */
import type { Excerpt } from '../../core/excerpts';
import { githubLink, type SpecUnit } from '../../core/specs';
import { repoHref, type App, type RepoView } from '../app';
import { button, extLink, h } from '../dom';

export function codeBlock(x: Pick<Excerpt, 'path' | 'start' | 'end' | 'lines' | 'cut'>): HTMLElement {
  const pre = h('pre', { class: 'code', tabindex: '0', 'aria-label': `${x.path} lines ${x.start} to ${x.end}` });
  x.lines.forEach((ln, i) => pre.append(h('span', { class: 'l' }, h('span', { class: 'n' }, String(x.start + i)), ln)));
  if (x.cut) pre.append(h('span', { class: 'l muted' }, `      ... ${x.cut} more line(s) in the range`));
  return pre;
}

export function walkthroughCard(app: App, v: RepoView, u: SpecUnit): HTMLElement {
  const a = v.analysis;
  const w = a?.specs.walkthroughs?.[u.id];
  const card = h('div', { class: 'card walkthrough' }, h('h3', null, 'Walkthrough: one request through the code'));
  if (!a || !w || !w.steps?.length) {
    card.append(h('p', { class: 'muted' }, 'No walkthrough was written for this unit. The analysis has walkthroughs for its most important units.'));
    return card;
  }
  if (w.flow) card.append(h('p', null, w.flow));
  w.steps.forEach((st, i) => {
    const box = h('div', { class: 'step' });
    box.append(
      h('div', { class: 'where' }, `${i + 1}. `, extLink(githubLink(v.repo.url, a.sha, st.file, st.start_line, st.end_line), `${st.file}:${st.start_line}-${st.end_line}`, 'mono')),
      h('p', null, st.narration),
    );
    const slot = h('div', { class: 'excerpt' }, h('p', { class: 'muted small' }, 'Loading the code from GitHub...'));
    box.append(slot);
    card.append(box);
    if (st.lines?.length) {
      slot.replaceChildren(codeBlock({ path: st.file, start: st.start_line, end: st.start_line + st.lines.length - 1, lines: st.lines, cut: 0 }));
    } else {
      void app.deps
        .excerpt(v.repo.url, a.sha, { path: st.file, start: st.start_line, end: st.end_line })
        .then((x) => slot.replaceChildren(codeBlock(x)))
        .catch((e: unknown) => slot.replaceChildren(h('p', { class: 'error small' }, e instanceof Error ? e.message : String(e))));
    }
  });
  if (w.trap) card.append(h('div', { class: 'answer' }, h('strong', null, 'The trap'), w.trap));
  card.append(
    h('p', { class: 'muted small' }, `Traced at ${(w.head ?? a.sha).slice(0, 8)}; the code above is fetched from GitHub at ${a.sha.slice(0, 8)} each time it is shown.`),
    h(
      'div',
      { class: 'row' },
      button('Quiz this unit', () => {
        app.quizUnit = u.parent ?? u.id;
        app.go(repoHref(v.repo.id, 'quiz'));
      }),
    ),
  );
  return card;
}
