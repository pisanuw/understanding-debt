/**
 * The spaced review queue (`udebt review`, udebt serve's Review tab): the questions the person
 * missed come back at growing intervals, in other words, until they stick. The tab lists what
 * is due (unit, level, due since) and what comes up next; a sitting asks up to N due items,
 * most overdue first, each in a stored phrasing not asked last time with the reference kept
 * from the miss, through the quiz's answer box ("I don't know" with no call, the answer shown
 * at levels 1-2 on a miss), graded in one call at its end and recorded with `source: review`.
 * Self-checked items go through the self-check flow instead: each item's kept reference is shown
 * after the answer and the learner marks it (`self-checked`, no call). Which items (`checkedBy`):
 * with no model in use (a visitor without a key of their own, or model features off), every item
 * with a reference to check against, the others waiting for a model; with a model in use, the
 * items whose latest miss was self-checked (in practice the questions written in advance), the
 * others graded by the model. A sitting is of one kind; with both due, one button for each.
 */
import { label } from '../../core/ladder';
import { SITTING, items as reviewItems, lastMiss, parseTs, type ReviewItem } from '../../core/review';
import { isSelfChecked, revealText, type EvidenceRow } from '../../core/quiz';
import { unitById } from '../../core/specs';
import { unitHref, type App, type RepoView } from '../app';
import { button, day, h } from '../dom';
import { askFlow, selfBadge, selfCheckFlow, type Served } from './quiz';

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

/**
 * How each due item is checked (BRIEFING, "Everybody gets advance quizzes"). With no model in use,
 * an item with a reference answer to compare against is self-checked and any other waits for a
 * model. With a model in use, an item is checked the way its latest miss was (the miss its
 * question and reference come from): self-checked when the person marked that answer themselves
 * (a question written in advance, or a review with no model in use), else by the model.
 */
export function checkedBy(due: ReviewItem[], records: EvidenceRow[], modelInUse: boolean): { self: ReviewItem[]; model: ReviewItem[]; waiting: ReviewItem[] } {
  const out = { self: [] as ReviewItem[], model: [] as ReviewItem[], waiting: [] as ReviewItem[] };
  for (const it of due) {
    const checkable = !!revealText(it.question);
    if (!modelInUse) (checkable ? out.self : out.waiting).push(it);
    else (checkable && isSelfChecked(lastMiss(records, it.unit, it.level) ?? {}) ? out.self : out.model).push(it);
  }
  return out;
}

function itemList(v: RepoView, list: ReviewItem[], now: Date, testId: string, marked: ReviewItem[] = []): HTMLElement {
  const ul = h('ul', { class: 'list review-items', 'data-testid': testId });
  for (const it of list) {
    const name = unitById(v.analysis?.specs.units ?? [], it.unit)?.name ?? it.unit;
    const self = marked.includes(it);
    ul.append(h('li', null, h('div', { class: 'unit-name' }, h('a', { href: unitHref(v.repo.id, it.unit) }, name)), h('div', { class: 'meta' }, `${label(it.level)} · `, h('span', { class: 'nb' }, dueText(it, now)), self ? ' · ' : null, self ? selfBadge() : null)));
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
    const mode = app.mode();
    const by = checkedBy(due, v.records, mode !== 'off');
    const run = (self: boolean) => {
      for (const c of cards) c.hidden = true;
      if (self) selfCheckFlow(app, v, flow, served(v, by.self, Number(nSel.value)), 'review');
      else askFlow(app, v, flow, served(v, by.model, Number(nSel.value)), null, 'review');
    };
    const mixed = by.self.length > 0 && by.model.length > 0;
    const buttons: HTMLButtonElement[] = [];
    if (!by.model.length || by.self.length) buttons.push(button(mixed ? 'Review the self-checked ones' : 'Review what is due', () => run(true), 'primary'));
    if (by.model.length) buttons.push(button(mixed ? 'Review the model-graded ones' : 'Review what is due', () => run(false), mixed ? '' : 'primary'));
    // With no model in use and no reference to check any item against, everything waits.
    buttons[0].disabled = !by.self.length && !by.model.length;
    const notes: HTMLElement[] = [];
    if (mode === 'off') {
      notes.push(
        h(
          'p',
          { class: by.self.length ? 'muted small' : 'warn small' },
          'No model is in use: you compare each answer with the one kept from the miss and mark it yourself, recorded as ',
          selfBadge(),
          ' with no call.',
          by.waiting.length ? ` ${by.waiting.length} item(s) have no reference answer to check against and wait for a model: ` : ' ',
          h('a', { href: '#/settings' }, app.anonymous ? 'add your own key' : 'choose the HintWise key or your own key'),
          ' for a grader.',
        ),
      );
    } else {
      if (by.self.length) notes.push(h('p', { class: 'muted small', 'data-testid': 'review-self-note' }, mixed ? `${by.self.length} item(s) marked ` : 'Marked ', selfBadge(), ': you checked these yourself when you missed them (questions written in advance), so you check them again against the reference answer, with no model call.'));
      const cost = mode === 'gateway' ? `One grading call per sitting through the HintWise key (it counts against ${app.anonymous ? 'one daily allowance shared by everyone who is not signed in' : 'your daily allowance'}); no call writes questions, and "I don't know" costs none.` : 'One grading call per sitting with your own Anthropic key, straight from this browser; no call writes questions.';
      if (by.model.length) notes.push(h('p', { class: 'muted small' }, mixed ? `The other ${by.model.length} were graded by the model when you missed them, and are again: ${cost.charAt(0).toLowerCase()}${cost.slice(1)}` : cost));
    }
    start.append(itemList(v, due, now, 'review-due', mode === 'off' ? [] : by.self), h('div', { class: 'row gap-top' }, h('label', { for: 'review-n' }, 'Items'), nSel, ...buttons), ...notes);
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
    h('p', { class: 'sub' }, 'The questions you missed come back at growing intervals (1 day, 3 days, then longer), in other words, until they stick. Each is asked with the answer kept from the miss, so no call writes questions: a sitting costs one grading call, or none when you check your answers yourself; a correct answer counts on the ladder like any other.'),
    ...cards,
    flow,
  );
}
