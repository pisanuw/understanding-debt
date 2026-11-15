/**
 * The spaced review queue (`udebt review`, udebt serve's Review tab): the questions the person
 * missed come back at growing intervals, in other words, until they stick. The tab lists what
 * is due (unit, level, due since) and what comes up next; a sitting asks up to N due items,
 * most overdue first, each in a stored phrasing not asked last time with the reference kept
 * from the miss, through the quiz's answer box ("I don't know" with no call, the answer shown
 * at levels 1-2 on a miss), graded in one call at its end and recorded with `source: review`.
 */
import { label } from '../../core/ladder';
import { SITTING, items as reviewItems, parseTs, type ReviewItem } from '../../core/review';
import { unitById } from '../../core/specs';
import { getMode } from '../../model/prefs';
import { unitHref, type App, type RepoView } from '../app';
import { button, day, h } from '../dom';
import { askFlow, type Served } from './quiz';

/** The analysis's unit ids: an item on a unit it no longer has is left out (as `udebt serve`). */
export function unitIds(v: RepoView): Set<string> {
  return new Set((v.analysis?.specs.units ?? []).map((u) => u.id));
}

/** The items on this repository's units, and those due by `now`. */
export function queue(v: RepoView, now: Date = new Date()): { all: ReviewItem[]; due: ReviewItem[] } {
  const all = v.analysis ? reviewItems(v.records, unitIds(v)) : [];
  return { all, due: all.filter((it) => parseTs(it.state.due) <= now.getTime()) };
}

/** The count badge on the Review tab: the number due, hidden when nothing is. */
export function countBadge(n: number): HTMLElement {
  return h('span', { class: 'count', 'data-testid': 'review-count', hidden: n === 0, title: `${n} due for review` }, String(n));
}

/** Updates the badge after a sitting without redrawing the screen. */
export function showCount(doc: Document, n: number): void {
  const el = doc.querySelector<HTMLElement>('[data-testid="review-count"]');
  if (!el) return;
  el.textContent = String(n);
  el.title = `${n} due for review`;
  el.hidden = n === 0;
}

/** "due since 16 Nov 2026" for an item due by `now`, else "due 19 Nov 2026". */
export function dueText(it: ReviewItem, now: Date = new Date()): string {
  return `${parseTs(it.state.due) <= now.getTime() ? 'due since' : 'due'} ${day(it.state.due)}`;
}

function itemList(v: RepoView, list: ReviewItem[], now: Date, testId: string): HTMLElement {
  const ul = h('ul', { class: 'list review-items', 'data-testid': testId });
  for (const it of list) {
    const name = unitById(v.analysis?.specs.units ?? [], it.unit)?.name ?? it.unit;
    ul.append(h('li', null, h('div', { class: 'unit-name' }, h('a', { href: unitHref(v.repo.id, it.unit) }, name)), h('div', { class: 'meta' }, `${label(it.level)} · `, h('span', { class: 'nb' }, dueText(it, now)))));
  }
  return ul;
}

/** The questions for a sitting: the first `n` due items, each in its stored phrasing (no call). */
export function served(v: RepoView, due: ReviewItem[], n: number): Served[] {
  return due.slice(0, n).map((it, i) => ({ ...it.question, id: `r${i}`, unit_name: unitById(v.analysis?.specs.units ?? [], it.unit)?.name ?? it.unit, orientation: null }));
}

export function reviewTab(app: App, v: RepoView): HTMLElement {
  if (!v.analysis) return h('p', null, 'This repository has no analysis yet.');
  const now = new Date();
  const { all, due } = queue(v, now);
  const later = all.filter((it) => !due.includes(it));
  const flow = h('div', { id: 'review-flow' });
  const start = h('div', { class: 'card' }, h('h2', null, due.length ? `Due now: ${due.length}` : 'Nothing due now'));
  const cards: HTMLElement[] = [start];
  if (due.length) {
    const nSel = h('select', { id: 'review-n', 'aria-label': 'Items' });
    for (const k of [1, 2, 3, 5, 10]) nSel.append(h('option', { value: String(k), selected: k === SITTING }, String(k)));
    const mode = getMode(app.deps.storage);
    const go = button(
      'Review what is due',
      () => {
        for (const c of cards) c.hidden = true;
        askFlow(app, v, flow, served(v, due, Number(nSel.value)), null, 'review');
      },
      'primary',
    );
    go.disabled = mode === 'off';
    start.append(
      itemList(v, due, now, 'review-due'),
      h('div', { class: 'row gap-top' }, h('label', { for: 'review-n' }, 'Items'), nSel, go),
      mode === 'off'
        ? h('p', { class: 'warn small' }, 'Model features are off. ', h('a', { href: '#/settings' }, 'Choose the HintWise key or your own key'), ' to review.')
        : h('p', { class: 'muted small' }, mode === 'gateway' ? 'One grading call per sitting through the HintWise key (it counts against your daily allowance); no call writes questions, and "I don\'t know" costs none.' : 'One grading call per sitting with your own Anthropic key, straight from this browser; no call writes questions.'),
    );
  } else {
    start.append(
      h(
        'p',
        { class: 'muted' },
        later.length ? `The next item is due on ${day(later[0].state.due)} (${later.length} in review).` : 'Nothing to review yet: a question you miss in a quiz comes back here the next day.',
      ),
    );
  }
  if (later.length) {
    const soon = h('div', { class: 'card' }, h('h3', null, 'Coming up'), itemList(v, later, now, 'review-later'));
    cards.push(soon);
  }
  return h(
    'div',
    null,
    h('p', { class: 'sub' }, 'The questions you missed come back at growing intervals (1 day, 3 days, then longer), in other words, until they stick. Each is asked with the answer kept from the miss, so a sitting costs one grading call; a correct answer counts on the ladder like any other.'),
    ...cards,
    flow,
  );
}
