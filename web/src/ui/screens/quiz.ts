/**
 * The quiz ladder in the browser (udebt serve's Quiz tab): each unit at the person's level,
 * one fact per question, the orientation before a level 1 question until level 1 is passed,
 * "I don't know" with no call (at levels 1-2 the answer is shown at once), a stored variant of
 * a question just missed before a new one, and the sitting graded in one call at its end,
 * then recorded (each record with its spaced-review state) and the debt recomputed. The Review
 * tab (review.ts) asks its items through the same flow.
 *
 * The questions written in advance (core/advance.ts) go through the self-check flow: the learner
 * answers, reads the reference answer and marks their own answer (correct, partly right, wrong,
 * didn't know), recorded as `self-checked` with no call. Everybody gets them where a repository
 * has them (owner, 2026-11-15): with no model to hand (a visitor without a key of their own, or
 * model features off) they are the only quiz; with a model in use the tab offers both kinds, the
 * advance quiz first (model/prefs.ts DEFAULT_QUIZ_KIND), the choice kept per browser.
 */
import type { QuestionUnit } from '../../core/prompts';
import { advancePicks, type AdvanceFile } from '../../core/advance';
import { label, levelOf } from '../../core/ladder';
import { parseRef, refsForUnit, type ExcerptRef } from '../../core/excerpts';
import { SELF_CHECKED, SELF_FEEDBACK, UNKNOWN_FEEDBACK, markRevealed, mergeGrades, orientationWanted, pickUnits, revealText, revealedOn, revealedVariant, reveals, levelsFor, sittingTs, type Answered, type Graded, type Question } from '../../core/quiz';
import { sittingRows, stateAt } from '../../core/review';
import { githubLink, orientationOf, orientationText, unitById, type Orientation, type SpecUnit } from '../../core/specs';
import { ApiError, CapError, capMessage, capNotice, type Allowance } from '../../model/client';
import { getQuizKind, setQuizKind, type QuizKind } from '../../model/prefs';
import { repoHref, unitHref, type App, type RepoTab, type RepoView } from '../app';
import { badge, busy, button, clear, day, extLink, fmt, h } from '../dom';
import { queue, showCount } from './review';

export interface Served extends Question {
  id: string;
  unit_name: string;
  orientation: Orientation | null;
}

/** What to say for an error from writing or grading. */
export function errorText(e: unknown, anonymous = false): string {
  if (e instanceof CapError || (e instanceof Error && e.name === 'CapReached' && 'scope' in e)) return capMessage(e as CapError, new Date(), anonymous);
  if (e instanceof ApiError && e.detail) return `${e.message} (${e.detail})`;
  return e instanceof Error ? e.message : String(e);
}

function confPicker(name: string): { el: HTMLElement; value: () => number } {
  const el = h('span', { class: 'conf' }, h('span', { class: 'small muted' }, 'Confidence'));
  for (const [v, t] of [
    ['1', 'guess'],
    ['2', 'fairly sure'],
    ['3', 'certain'],
  ]) {
    const inp = h('input', { type: 'radio', name, value: v, checked: v === '2' });
    el.append(h('label', null, inp, t));
  }
  return { el, value: () => Number((el.querySelector('input:checked') as HTMLInputElement | null)?.value ?? 2) };
}

function orientationBox(o: Orientation): HTMLElement {
  const dl = h('dl', { class: 'kv' }, h('dt', null, 'What it is for'), h('dd', null, o.purpose), h('dt', null, 'The decision'), h('dd', null, o.decision), h('dt', null, 'Who shaped it'), h('dd', null, o.shaped_by));
  return h('details', { open: true, class: 'orientation' }, h('summary', null, 'Orientation: read this first'), dl);
}

/** What to say when no model is in use. */
export function offText(app: App): string {
  return app.anonymous ? 'No model is in use: add your own Anthropic key in Settings, or sign in.' : 'Model features are off. Choose the HintWise key or your own key in Settings.';
}

/** Questions for a sitting: a stored variant where one is due (no call), the rest written in one call. */
export async function prepare(app: App, v: RepoView, n: number, only: string | null): Promise<Served[]> {
  const a = v.analysis;
  if (!a || !v.scores) throw new Error('This repository has no analysis yet.');
  const units = a.specs.units;
  const picked = pickUnits(units, v.scores.scores, n, only);
  if (!picked.length) throw new Error('Nothing to quiz: every unit in view is blackbox.');
  const levels = levelsFor(picked, v.records);
  const chosen = new Map<string, Question>();
  const toWrite: (QuestionUnit & { refs: ExcerptRef[] })[] = [];
  const shown = new Map<string, Orientation>();
  for (const u of picked) {
    const lv = levels.get(u.id) ?? 1;
    const on = v.records.filter((r) => r.unit_id === u.id);
    if (orientationWanted(on, lv)) shown.set(u.id, orientationOf(u));
    const again = revealedVariant(u.id, lv, on);
    if (again) {
      chosen.set(u.id, again);
      continue;
    }
    const parent = u.parent ? unitById(units, u.parent) : undefined;
    const o = shown.get(u.id);
    toWrite.push({
      id: u.id,
      name: u.name,
      parent_name: parent?.name,
      level: lv,
      summary: u.summary,
      decision: u.decision,
      alternatives: u.alternatives,
      why: u.why,
      paths: u.paths,
      history: (u.history ?? []).slice(0, 5),
      shown_orientation: o ? orientationText(o) : undefined,
      revealed: revealedOn(on, lv),
      refs: refsForUnit(u, a.specs.walkthroughs?.[u.id]),
    });
  }
  if (app.trees && toWrite.some((w) => !w.refs.length)) {
    // Units that name only directories: the top files under them at the sha (model/tree.ts).
    const found = await app.trees.refsFor(v.repo.url, a.sha, toWrite.map((w) => ({ unit: unitById(units, w.id) as SpecUnit, walk: a.specs.walkthroughs?.[w.id] })));
    toWrite.forEach((w, i) => (w.refs = found[i]));
  }
  if (toWrite.length) {
    const client = app.quiz();
    if (!client) throw new Error(offText(app));
    for (const q of await client.writeQuestions({ repo: v.repo.url, sha: a.sha, units: toWrite, per_unit: 1 })) if (!chosen.has(q.unit_id)) chosen.set(q.unit_id, q);
  }
  const out: Served[] = [];
  picked.forEach((u: SpecUnit, i) => {
    const q = chosen.get(u.id);
    if (q) out.push({ ...q, id: `q${i}`, unit_name: u.name, orientation: shown.get(u.id) ?? null });
  });
  return out;
}

/** A link to a tab that draws it again even when it is the current address (a new sitting). */
function tabLink(app: App, v: RepoView, tab: RepoTab, text: string): HTMLElement {
  const href = repoHref(v.repo.id, tab);
  const a = h('a', { href }, text);
  a.addEventListener('click', (ev) => {
    ev.preventDefault();
    app.go(href);
  });
  return a;
}

/**
 * The files a question rests on (its `evidence`), linked on GitHub at the analysis's sha:
 * shown with the grade, never before the answer, as the CLI shows the evidence beside the
 * reference answer.
 */
export function evidenceLine(repoUrl: string, sha: string, evidence: readonly string[]): HTMLElement | null {
  const links: HTMLElement[] = [];
  for (const e of evidence.slice(0, 4)) {
    const ref = parseRef(e);
    if (!ref) continue;
    const ranged = e.trim().replace(/^\.\//, '') !== ref.path;
    links.push(extLink(githubLink(repoUrl, sha, ref.path, ranged ? ref.start : undefined, ranged ? ref.end : undefined), ranged ? `${ref.path}:${ref.start}-${ref.end}` : ref.path, 'mono'));
  }
  if (!links.length) return null;
  const line = h('p', { class: 'small muted', 'data-testid': 'evidence' }, 'In the code: ');
  links.forEach((a, i) => line.append(...(i ? [', ', a] : [a])));
  return line;
}

function resultCard(v: RepoView, r: Graded, next: number, self = false): HTMLElement {
  const card = h('div', { class: 'card result' });
  card.append(
    h('p', { class: 'small muted' }, h('a', { href: unitHref(v.repo.id, r.unit_id) }, unitById(v.analysis?.specs.units ?? [], r.unit_id)?.name ?? r.unit_id), ` · level ${levelOf(r)}`),
    h('p', null, r.question),
    h('p', { class: 'verdict' }, badge(r.verdict, r.verdict), self ? ' ' : '', self ? selfBadge() : null, ` ${r.feedback}`),
  );
  if (r.revealed && revealText(r)) card.append(h('div', { class: 'answer' }, h('strong', null, 'The answer'), revealText(r)));
  const code = v.analysis ? evidenceLine(v.repo.url, v.analysis.sha, r.evidence ?? []) : null;
  if (code) card.append(code);
  const again = stateAt(v.records, r.unit_id, levelOf(r));
  card.append(h('p', { class: 'small muted' }, `Next on this unit: level ${next}`, again ? '; back in review on ' : '', again ? h('span', { class: 'nb' }, day(again.due)) : null, '.'));
  return card;
}

/**
 * Asks the questions one by one, then grades and records the sitting (`source`: a quiz or a review
 * sitting); `onDone` runs once the sitting is recorded (or nothing was answered).
 */
export function askFlow(app: App, view: RepoView, box: HTMLElement, questions: Served[], sessionId: number | null, source: 'quiz' | 'review' = 'quiz', onDone?: () => void): void {
  let i = 0;
  const answers = new Map<string, Answered>();
  const order: string[] = [];
  const keep = (q: Served, a: Answered) => {
    if (!answers.has(q.id)) order.push(q.id);
    answers.set(q.id, a);
  };

  async function finish(btn: HTMLButtonElement): Promise<void> {
    const given = order.map((id) => answers.get(id) as Answered);
    if (!given.length) {
      clear(box).append(h('div', { class: 'card' }, 'Nothing answered, nothing recorded.'));
      onDone?.();
      return;
    }
    const typed = given.filter((a) => a.answer.trim()).length;
    const done = busy(btn, typed ? `Grading ${given.length} answer(s) in one call` : 'Recording');
    const v = (await app.repoView(view.repo.id)) ?? view;
    const a = v.analysis;
    if (!a) return;
    const before = v.scores?.index ?? 100;
    const ts = sittingTs(v.records);
    try {
      const client = app.quiz();
      let outcome: { grades: { index: number; verdict: Graded['verdict']; feedback: string }[]; recorded: unknown[] | null } = { grades: [], recorded: null };
      if (typed) {
        if (!client) throw new Error(offText(app));
        outcome = await client.grade(
          {
            repo: v.repo.url,
            sha: a.sha,
            items: given.map((x) => ({ unit_id: x.unit_id, level: x.level, question: x.question, reference_answer: x.reference_answer, evidence: x.evidence, answer: x.answer, variants: x.variants, revealed: !!x.revealed, confidence: x.confidence })),
          },
          { repo_id: v.repo.id, ts, source },
        );
      }
      const results = markRevealed(mergeGrades(given, outcome.grades));
      if (outcome.recorded) await app.store.records(v.repo.id).then((all) => app.refresh(v, all));
      else app.refresh(v, [...v.records, ...(await app.store.addRecords(sittingRows(results, v.records, { repoId: v.repo.id, sha: a.sha, ts, source })))]);
      if (sessionId !== null) await app.store.finishSession(sessionId).catch(() => undefined);
      const after = (await app.repoView(v.repo.id)) ?? v;
      clear(box);
      const counts = new Map<string, number>();
      for (const r of results) counts.set(r.verdict, (counts.get(r.verdict) ?? 0) + 1);
      const index = after.scores?.index ?? before;
      showHeader(box.ownerDocument, after);
      box.append(
        h(
          'div',
          { class: 'card summary' },
          h('h3', null, 'Graded'),
          h('p', null, `${[...counts].map(([k, n]) => `${n} ${k}`).join(', ')}${typed ? ' (one grading call).' : ' (no call: every answer was blank).'}`),
          h('p', { class: 'delta', 'data-testid': 'debt-delta' }, 'Debt index ', h('strong', null, fmt(before, 1)), ' → ', h('strong', null, fmt(index, 1)), index < before ? ' ↓' : ''),
          h('p', { class: 'row' }, h('a', { href: repoHref(v.repo.id, 'debt') }, 'See your debt per unit'), source === 'review' ? tabLink(app, v, 'review', 'Back to Review') : tabLink(app, v, 'quiz', 'Another sitting')),
        ),
      );
      for (const r of results) box.append(resultCard(after, r, levelsFor([{ id: r.unit_id }], after.records).get(r.unit_id) ?? 1));
      onDone?.();
    } catch (e) {
      done();
      box.append(h('p', { class: 'error' }, errorText(e, app.anonymous)));
    }
  }

  function next(): void {
    clear(box);
    if (i >= questions.length) {
      const go = button('Grade my answers', () => void finish(go), 'primary');
      box.append(h('div', { class: 'card' }, h('h3', null, 'All answered'), h('p', null, `${order.length} answer(s) to grade together.`), go));
      go.focus();
      return;
    }
    const q = questions[i];
    const card = h('div', { class: 'card question' });
    card.append(h('p', { class: 'small muted' }, `[${i + 1}/${questions.length}] ${q.unit_name} · ${label(levelOf(q))}`));
    if (q.orientation) card.append(orientationBox(q.orientation));
    card.append(h('p', { class: 'verdict q-text' }, q.question));
    const ta = h('textarea', { 'aria-label': 'Your answer', placeholder: 'One or two sentences, in plain language.' });
    const conf = confPicker(`conf-${q.id}`);
    const last = i + 1 >= questions.length;
    const send = button(last ? 'Check Answer and Grade' : 'Next', () => {
      if (!ta.value.trim()) {
        ta.focus();
        return;
      }
      keep(q, { ...q, answer: ta.value, confidence: conf.value() });
      i += 1;
      if (last) void finish(send);
      else next();
    }, 'primary');
    const out = h('div');
    const dunno = button("I don't know", () => {
      keep(q, { ...q, answer: '', confidence: 1, revealed: reveals(q, 'unknown') && !!revealText(q) });
      send.hidden = true;
      dunno.hidden = true;
      stop.hidden = true;
      ta.disabled = true;
      out.append(h('p', { class: 'verdict gap-top' }, badge('unknown', 'unknown'), ' A known unknown: no penalty.'));
      if (reveals(q, 'unknown') && revealText(q)) out.append(h('div', { class: 'answer' }, h('strong', null, 'The answer'), revealText(q)));
      const nb = button(last ? 'Grade my answers' : 'Next question', () => {
        i += 1;
        if (last) void finish(nb);
        else next();
      }, 'primary');
      out.append(nb);
      nb.focus();
    });
    const stop = button('Finish now', () => void finish(stop));
    stop.hidden = order.length === 0;
    card.append(ta, h('div', { class: 'row gap-top' }, conf.el, send, dunno, stop), out);
    box.append(card);
    ta.focus();
  }
  next();
}

/** The "self-checked" label: the learner marked the answer against the reference, no model graded it. */
export function selfBadge(): HTMLElement {
  return h('span', { class: 'badge self-checked', title: 'You marked this answer yourself against the reference answer; no model graded it.' }, 'self-checked');
}

/** Updates the repository header after a sitting: the debt index, its self-checked label, the Review count. */
export function showHeader(doc: Document, v: RepoView): void {
  const stat = doc.querySelector('[data-testid="debt-index"]');
  if (stat) stat.textContent = fmt(v.scores?.index ?? 100, 1);
  const self = doc.querySelector<HTMLElement>('[data-testid="self-checked-index"]');
  if (self) self.hidden = !v.scores?.selfCheckedIndex;
  showCount(doc, queue(v).due.length);
}

const MARKS: [Graded['verdict'], string][] = [
  ['correct', 'Correct'],
  ['partial', 'Partly right'],
  ['wrong', 'Wrong'],
  ['unknown', "I didn't know"],
];

/**
 * Asks questions that carry their reference answer (written in advance, or a review item's
 * kept reference) and lets the learner mark their own answers: answer, show the reference,
 * mark; "I don't know" marks a known unknown at once. Recorded at the end as `self-checked`
 * (`source` advance or review), each row with its review state, with no model call.
 */
export function selfCheckFlow(app: App, view: RepoView, box: HTMLElement, questions: Served[], source: 'advance' | 'review' = 'advance', onDone?: () => void): void {
  let i = 0;
  const results: Graded[] = [];

  async function finish(btn: HTMLButtonElement | null): Promise<void> {
    if (!results.length) {
      clear(box).append(h('div', { class: 'card' }, 'Nothing answered, nothing recorded.'));
      onDone?.();
      return;
    }
    const done = btn ? busy(btn, 'Recording') : () => undefined;
    const v = (await app.repoView(view.repo.id)) ?? view;
    const a = v.analysis;
    if (!a) return;
    const before = v.scores?.index ?? 100;
    try {
      const rows = sittingRows(results, v.records, { repoId: v.repo.id, sha: a.sha, ts: sittingTs(v.records), source, type: SELF_CHECKED });
      app.refresh(v, [...v.records, ...(await app.store.addRecords(rows))]);
      const after = (await app.repoView(v.repo.id)) ?? v;
      const index = after.scores?.index ?? before;
      showHeader(box.ownerDocument, after);
      const counts = new Map<string, number>();
      for (const r of results) counts.set(r.verdict, (counts.get(r.verdict) ?? 0) + 1);
      clear(box).append(
        h(
          'div',
          { class: 'card summary' },
          h('h3', null, 'Recorded'),
          h('p', null, `${[...counts].map(([k, n]) => `${n} ${k}`).join(', ')}, `, selfBadge(), ' (no model call).'),
          h('p', { class: 'delta', 'data-testid': 'debt-delta' }, 'Debt index ', h('strong', null, fmt(before, 1)), ' → ', h('strong', null, fmt(index, 1)), index < before ? ' ↓' : ''),
          h('p', { class: 'row' }, h('a', { href: repoHref(v.repo.id, 'debt') }, 'See your debt per unit'), source === 'review' ? tabLink(app, v, 'review', 'Back to Review') : tabLink(app, v, 'quiz', 'Another sitting')),
        ),
      );
      for (const r of results) box.append(resultCard(after, r, levelsFor([{ id: r.unit_id }], after.records).get(r.unit_id) ?? 1, true));
      onDone?.();
    } catch (e) {
      done();
      box.append(h('p', { class: 'error' }, errorText(e, app.anonymous)));
    }
  }

  function next(): void {
    clear(box);
    if (i >= questions.length) {
      void finish(null);
      return;
    }
    const q = questions[i];
    const last = i + 1 >= questions.length;
    const card = h('div', { class: 'card question self-check' });
    card.append(h('p', { class: 'small muted' }, `[${i + 1}/${questions.length}] ${q.unit_name} · ${label(levelOf(q))} · `, selfBadge()));
    if (q.orientation) card.append(orientationBox(q.orientation));
    card.append(h('p', { class: 'verdict q-text' }, q.question));
    const ta = h('textarea', { 'aria-label': 'Your answer', placeholder: 'One or two sentences, in plain language. It stays on this page: never sent, never stored.' });
    const conf = confPicker(`conf-${q.id}`);
    const out = h('div');
    const answerBox = () => {
      out.append(h('div', { class: 'answer', 'data-testid': 'reference' }, h('strong', null, 'The answer'), revealText(q)));
      const code = view.analysis ? evidenceLine(view.repo.url, view.analysis.sha, q.evidence ?? []) : null;
      if (code) out.append(code);
    };
    const keep = (r: Graded) => {
      results.push(r);
      i += 1;
      if (last) void finish(null);
      else next();
    };
    const show = button(
      'Check Answer',
      () => {
        if (!ta.value.trim()) {
          ta.focus();
          return;
        }
        ta.disabled = true;
        show.hidden = true;
        dunno.hidden = true;
        stop.hidden = true;
        // The confidence was stated before the reference was seen: it stays as it was.
        const said = conf.value();
        for (const r of conf.el.querySelectorAll('input')) r.disabled = true;
        answerBox();
        const marks = h('div', { class: 'row marks', role: 'group', 'aria-label': 'How did your answer compare?' });
        // No mark is highlighted, so none is suggested.
        for (const [verdict, text] of MARKS) marks.append(button(text, () => keep({ ...q, answer: ta.value, confidence: said, revealed: true, verdict, feedback: SELF_FEEDBACK })));
        out.append(h('p', { class: 'gap-top' }, h('strong', null, 'How did your answer compare?'), ' Mark it honestly: the debt counts it as you mark it.'), marks);
        (marks.querySelector('button') as HTMLButtonElement).focus();
      },
      'primary',
    );
    const dunno = button("I don't know", () => {
      ta.disabled = true;
      show.hidden = true;
      dunno.hidden = true;
      stop.hidden = true;
      out.append(h('p', { class: 'verdict gap-top' }, badge('unknown', 'unknown'), ' A known unknown: no penalty.'));
      answerBox();
      const nb = button(last ? 'Record my answers' : 'Next question', () => keep({ ...q, answer: '', confidence: 1, revealed: true, verdict: 'unknown', feedback: UNKNOWN_FEEDBACK }), 'primary');
      out.append(nb);
      nb.focus();
    });
    const stop = button('Finish now', () => void finish(stop));
    stop.hidden = results.length === 0;
    card.append(ta, h('div', { class: 'row gap-top' }, conf.el, show, dunno, stop), out);
    box.append(card);
    ta.focus();
  }
  next();
}

/**
 * What the tab of each kind needs when a person has both: the choice (moved into the tab drawn),
 * and the lock that keeps it from discarding a sitting under way.
 */
interface Both {
  choice: HTMLFieldSetElement;
  lock: (on: boolean) => void;
}

/** The start card's selects: how many questions (2 by default), and which unit (`offered`: the units listed). */
function startSelects(units: SpecUnit[], unit: string | null, offered: (u: SpecUnit) => boolean = () => true): { n: HTMLSelectElement; u: HTMLSelectElement } {
  const n = h('select', { id: 'quiz-n', 'aria-label': 'Questions' });
  for (const k of [1, 2, 3, 4, 5]) n.append(h('option', { value: String(k), selected: k === 2 }, String(k)));
  const u = h('select', { id: 'quiz-unit', class: 'grow', 'aria-label': 'Unit' }, h('option', { value: '' }, 'Most debt first'));
  for (const x of units) {
    if ((x.scope ?? 'architectural') !== 'architectural' || !offered(x)) continue;
    u.append(h('option', { value: x.id, selected: unit === x.id }, `${x.parent ? ' ' : ''}${x.name}`));
  }
  return { n, u };
}

/** What a sitting of the model's quiz costs in the person's mode. */
function modelCost(app: App): string {
  if (app.mode() === 'byok') return 'with your own Anthropic key, straight from this browser';
  return app.anonymous ? 'through the HintWise key: each sitting counts against one daily allowance shared by everyone who is not signed in' : 'through the HintWise key: each sitting counts against your daily allowance';
}

/** The quiz-kind choice for a person who has both kinds; `pick` runs on a change. */
function kindChoice(app: App, current: QuizKind, pick: (k: QuizKind) => void, modelBlocked: string | null = null): HTMLFieldSetElement {
  const kinds: [QuizKind, string, string][] = [
    ['advance', 'Written in advance, self-checked (no model call)', 'The same for everyone, at levels 1-3: you read the reference answer and mark your own answer.'],
    ['model', 'Written and graded by the model (one call each way)', `Written for you at any level and graded by the model, ${modelCost(app)}.`],
  ];
  const fs = h('fieldset', { class: 'modes kinds', 'data-testid': 'quiz-kind' }, h('legend', null, 'Which quiz'));
  for (const [k, title, text] of kinds) {
    const blocked = k === 'model' && modelBlocked !== null;
    const inp = h('input', { type: 'radio', name: 'quiz-kind', value: k, checked: k === current, disabled: blocked });
    inp.addEventListener('change', () => pick(k));
    fs.append(h('label', { class: 'mode' }, inp, h('span', null, h('strong', null, title), h('span', { class: blocked ? 'small warn' : 'small muted' }, blocked ? modelBlocked : text))));
  }
  return fs;
}

/** The questions written in advance, self-checked: the only quiz with no model in use, or the advance kind of the choice. */
function advanceTab(app: App, v: RepoView, file: AdvanceFile, unit: string | null, both: Both | null): HTMLElement {
  const a = v.analysis as NonNullable<RepoView['analysis']>;
  const flow = h('div', { id: 'quiz-flow' });
  const asked = new Set(file.questions.map((q) => q.unit_id));
  const sel = startSelects(a.specs.units, unit, (u) => asked.has(u.id));
  const elsewhere = both
    ? 'Choose “Written and graded by the model” above for questions written and graded for you.'
    : app.anonymous
      ? 'Add your own Anthropic key in Settings, or sign in, for questions written and graded for you.'
      : 'Choose the HintWise key or your own key in Settings for questions written and graded for you.';
  const go = button(
    'Start',
    () =>
      // The view as it is now: a sitting just recorded on this tab has moved the ladder on.
      void app.repoView(v.repo.id).then((now) => {
        const cur = now ?? v;
        const { picks, skipped } = advancePicks(file, a.specs.units, cur.scores?.scores ?? [], cur.records, Number(sel.n.value), sel.u.value || null);
        if (!picks.length) {
          const past = skipped.some((x) => x.level > 3);
          clear(flow).append(h('p', { class: 'warn' }, past ? 'Levels 4 and 5 need code reading and a real grader, so no questions are written for them in advance. ' : 'No question was written in advance for this unit at your level. ', elsewhere));
          return;
        }
        const served: Served[] = picks.map((p, k) => ({ ...p.question, id: `a${k}`, unit_name: p.unit.name, orientation: orientationWanted(cur.records.filter((r) => r.unit_id === p.unit.id), p.level) ? (p.question.orientation ?? orientationOf(p.unit)) : null }));
        both?.lock(true);
        selfCheckFlow(app, cur, flow, served, 'advance', () => both?.lock(false));
      }),
    'primary',
  );
  const where = app.anonymous ? ' in this browser' : both ? ' in your account' : '';
  return h(
    'div',
    null,
    h('p', { class: 'sub' }, 'Questions written in advance for this repository, each unit at your level on the ladder (levels 1-3). Answer, then read the reference answer and mark your own answer: no model call, and your answer text is never sent or stored.'),
    h(
      'div',
      { class: 'card' },
      both?.choice,
      h('div', { class: 'row' }, h('label', { for: 'quiz-n' }, 'Questions'), sel.n, h('label', { for: 'quiz-unit' }, 'Unit'), sel.u, go),
      both
        ? h('p', { class: 'muted small' }, 'Recorded as ', selfBadge(), `${where}, with no model call.`)
        : h(
            'p',
            { class: 'muted small' },
            'Recorded as ',
            selfBadge(),
            app.anonymous ? `${where}. For questions written for you and graded by a model, add your own Anthropic key in ` : '. For questions written for you and graded by a model, choose the HintWise key or your own key in ',
            h('a', { href: '#/settings' }, 'Settings'),
            app.anonymous ? ' or ' : '.',
            app.anonymous ? h('a', { href: '#/signin' }, 'sign in') : null,
            app.anonymous ? '.' : null,
          ),
    ),
    flow,
  );
}

/** The quiz the model writes and grades (or, with no model in use and nothing written in advance, how to get one). */
function modelTab(app: App, v: RepoView, unit: string | null, both: Both | null, blocked = false): HTMLElement {
  const a = v.analysis as NonNullable<RepoView['analysis']>;
  const mode = app.mode();
  const flow = h('div', { id: 'quiz-flow' });
  const sel = startSelects(a.specs.units, unit);
  const note =
    mode === 'gateway'
      ? `Questions are written in one call and your answers graded in one more, through the HintWise key (they count against ${app.anonymous ? 'one daily allowance shared by everyone who is not signed in' : 'your daily allowance'}). A question you missed comes back in other words without a call.`
      : mode === 'byok'
        ? 'Questions are written and graded with your own Anthropic key, straight from this browser: two calls per sitting.'
        : null;
  const go = button('Start', () => {
    const done = busy(go, 'Writing questions');
    both?.lock(true);
    let cur = v;
    // The view as it is now: a sitting just recorded on this tab has moved the ladder on.
    void app
      .repoView(v.repo.id)
      .then((now) => prepare(app, (cur = now ?? v), Number(sel.n.value), sel.u.value || null))
      .then(async (qs) => {
        done();
        const id = await app.store.startSession(v.repo.id, a.sha, qs.map((q) => ({ unit_id: q.unit_id, level: q.level, question: q.question }))).catch(() => null);
        askFlow(app, cur, flow, qs, id, 'quiz', () => both?.lock(false));
      })
      .catch((e: unknown) => {
        done();
        both?.lock(false);
        clear(flow).append(h('p', { class: 'error' }, errorText(e, app.anonymous)));
      });
  }, 'primary');
  go.disabled = mode === 'off' || blocked;
  return h(
    'div',
    null,
    h('p', { class: 'sub' }, 'Questions written and graded by the model, each unit at your level on the question ladder, one fact per question. At levels 1 and 2 a miss shows the answer; it counts as the miss it was.'),
    h(
      'div',
      { class: 'card' },
      both?.choice,
      h('div', { class: 'row' }, h('label', { for: 'quiz-n' }, 'Questions'), sel.n, h('label', { for: 'quiz-unit' }, 'Unit'), sel.u, go),
      note
        ? h('p', { class: 'muted small' }, note)
        : app.anonymous
          ? h('p', { class: 'warn small' }, 'No questions were written in advance for this repository yet. ', h('a', { href: '#/settings' }, 'Add your own Anthropic key'), ' or ', h('a', { href: '#/signin' }, 'sign in'), ' to take a quiz.')
          : h('p', { class: 'warn small' }, 'Model features are off. ', h('a', { href: '#/settings' }, 'Choose the HintWise key or your own key'), ' to take a quiz.'),
    ),
    flow,
  );
}

/**
 * The Quiz tab. Questions written in advance for the analysis (a sample at its pin) and a model in
 * use: both kinds, the choice on top (the kept choice, else DEFAULT_QUIZ_KIND), the other kind one
 * click away. Only one of them: that one, with no choice.
 */
export async function quizTab(app: App, v: RepoView): Promise<HTMLElement> {
  if (!v.analysis) return h('p', null, 'This repository has no analysis yet.');
  const [got, allowance] = await Promise.all([app.advanceFor(v), app.allowance()]);
  const file = got?.questions.length ? got : null;
  const unit = app.quizUnit;
  app.quizUnit = null;
  // The HintWise key's allowance used up, or the session refused (ended elsewhere): said on top,
  // the model's quiz out of reach until it resets or the person signs in again.
  const signedOut = allowance === 'signed-out';
  const usedUp = signedOut || (allowance !== null && !allowance.allowed);
  const notice = signedOut ? sessionBanner(app, file !== null) : allowance !== null && usedUp ? capBanner(allowance as Allowance, file !== null, app.anonymous) : null;
  const withNotice = (el: HTMLElement): HTMLElement => (notice ? h('div', null, notice, el) : el);
  if (!file) return withNotice(modelTab(app, v, unit, null, usedUp));
  if (app.mode() === 'off') return advanceTab(app, v, file, unit, null);
  const storage = app.deps.storage;
  const slot = h('div', { class: 'quiz-kind-slot' });
  const draw = (k: QuizKind, u: string | null) => slot.replaceChildren(k === 'advance' ? advanceTab(app, v, file, u, both) : modelTab(app, v, u, both));
  // On the HintWise key the model's quiz is the default (owner, 2026-11-15), the advance one while it is used up.
  const kept = getQuizKind(storage, app.mode() === 'gateway' ? 'model' : undefined);
  const first: QuizKind = usedUp ? 'advance' : kept;
  const choice = kindChoice(
    app,
    first,
    (k) => {
      setQuizKind(k, storage);
      // The unit chosen stays chosen where the other kind lists it.
      draw(k, slot.querySelector<HTMLSelectElement>('#quiz-unit')?.value || null);
      choice.querySelector<HTMLInputElement>('input:checked')?.focus();
    },
    signedOut ? 'Not available until you sign in again (see above).' : usedUp ? `Not available until ${app.anonymous ? 'the shared' : 'your'} HintWise allowance resets (see above).` : null,
  );
  const both: Both = {
    choice,
    lock: (on) => {
      choice.disabled = on;
      choice.title = on ? 'Finish this sitting to change the kind of quiz.' : '';
    },
  };
  draw(first, unit);
  return withNotice(slot);
}

/** The notice on top of the Quiz tab when the gateway refused the session even after a refresh. */
function sessionBanner(app: App, hasAdvance: boolean): HTMLElement {
  return h(
    'div',
    { class: 'warn cap-notice', role: 'status', 'data-testid': 'session-notice' },
    h('p', null, 'Your sign-in has ended, perhaps by signing out in another HintWise app or tab, so the HintWise key cannot be used. ', hasAdvance ? 'The questions written in advance still work. ' : '', 'Sign in again to use it.'),
    button('Sign out and sign in again', () => void app.signInAgain()),
  );
}

/** The notice on top of the Quiz tab while the person's HintWise allowance is used up. */
function capBanner(a: Allowance, hasAdvance: boolean, anonymous: boolean): HTMLElement {
  return h('p', { class: 'warn cap-notice', role: 'status', 'data-testid': 'cap-notice' }, capNotice(a, hasAdvance, new Date(), anonymous));
}
