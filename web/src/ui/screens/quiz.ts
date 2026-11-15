/**
 * The quiz ladder in the browser (udebt serve's Quiz tab): each unit at the person's level,
 * one fact per question, the orientation before a level 1 question until level 1 is passed,
 * "I don't know" with no call (at levels 1-2 the answer is shown at once), a stored variant of
 * a question just missed before a new one, and the sitting graded in one call at its end,
 * then recorded and the debt recomputed.
 */
import type { QuestionUnit } from '../../core/prompts';
import { label, levelOf } from '../../core/ladder';
import { refsForUnit } from '../../core/excerpts';
import { markRevealed, mergeGrades, orientationWanted, pickUnits, recordRows, revealText, revealedOn, revealedVariant, reveals, levelsFor, sittingTs, type Answered, type Graded, type Question } from '../../core/quiz';
import { orientationOf, orientationText, unitById, type Orientation, type SpecUnit } from '../../core/specs';
import { CapError, capMessage } from '../../model/client';
import { getMode } from '../../model/prefs';
import { repoHref, unitHref, type App, type RepoView } from '../app';
import { badge, busy, button, clear, fmt, h } from '../dom';

interface Served extends Question {
  id: string;
  unit_name: string;
  orientation: Orientation | null;
}

/** What to say for an error from writing or grading. */
export function errorText(e: unknown): string {
  if (e instanceof CapError || (e instanceof Error && e.name === 'CapReached' && 'scope' in e)) return capMessage(e as CapError);
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

/** Questions for a sitting: a stored variant where one is due (no call), the rest written in one call. */
export async function prepare(app: App, v: RepoView, n: number, only: string | null): Promise<Served[]> {
  const a = v.analysis;
  if (!a || !v.scores) throw new Error('This repository has no analysis yet.');
  const units = a.specs.units;
  const picked = pickUnits(units, v.scores.scores, n, only);
  if (!picked.length) throw new Error('Nothing to quiz: every unit in view is blackbox.');
  const levels = levelsFor(picked, v.records);
  const chosen = new Map<string, Question>();
  const toWrite: (QuestionUnit & { refs: ReturnType<typeof refsForUnit> })[] = [];
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
  if (toWrite.length) {
    const client = app.deps.quiz();
    if (!client) throw new Error('Model features are off. Choose the HintWise key or your own key in Settings.');
    for (const q of await client.writeQuestions({ repo: v.repo.url, sha: a.sha, units: toWrite, per_unit: 1 })) if (!chosen.has(q.unit_id)) chosen.set(q.unit_id, q);
  }
  const out: Served[] = [];
  picked.forEach((u: SpecUnit, i) => {
    const q = chosen.get(u.id);
    if (q) out.push({ ...q, id: `q${i}`, unit_name: u.name, orientation: shown.get(u.id) ?? null });
  });
  return out;
}

function resultCard(v: RepoView, r: Graded, next: number): HTMLElement {
  const card = h('div', { class: 'card result' });
  card.append(
    h('p', { class: 'small muted' }, h('a', { href: unitHref(v.repo.id, r.unit_id) }, unitById(v.analysis?.specs.units ?? [], r.unit_id)?.name ?? r.unit_id), ` · level ${levelOf(r)}`),
    h('p', null, r.question),
    h('p', { class: 'verdict' }, badge(r.verdict, r.verdict), ` ${r.feedback}`),
  );
  if (r.revealed && revealText(r)) card.append(h('div', { class: 'answer' }, h('strong', null, 'The answer'), revealText(r)));
  card.append(h('p', { class: 'small muted' }, `Next on this unit: level ${next}.`));
  return card;
}

/** Asks the questions one by one, then grades and records the sitting. */
export function askFlow(app: App, view: RepoView, box: HTMLElement, questions: Served[], sessionId: number | null): void {
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
      const client = app.deps.quiz();
      let outcome: { grades: { index: number; verdict: Graded['verdict']; feedback: string }[]; recorded: unknown[] | null } = { grades: [], recorded: null };
      if (typed) {
        if (!client) throw new Error('Model features are off. Choose the HintWise key or your own key in Settings.');
        outcome = await client.grade(
          {
            repo: v.repo.url,
            sha: a.sha,
            items: given.map((x) => ({ unit_id: x.unit_id, level: x.level, question: x.question, reference_answer: x.reference_answer, evidence: x.evidence, answer: x.answer, variants: x.variants, revealed: !!x.revealed, confidence: x.confidence })),
          },
          { repo_id: v.repo.id, ts },
        );
      }
      const results = markRevealed(mergeGrades(given, outcome.grades));
      if (outcome.recorded) await app.deps.store.records(v.repo.id).then((all) => app.refresh(v, all));
      else app.refresh(v, [...v.records, ...(await app.deps.store.addRecords(recordRows(results, { repoId: v.repo.id, sha: a.sha, ts })))]);
      if (sessionId !== null) await app.deps.store.finishSession(sessionId).catch(() => undefined);
      const after = (await app.repoView(v.repo.id)) ?? v;
      clear(box);
      const counts = new Map<string, number>();
      for (const r of results) counts.set(r.verdict, (counts.get(r.verdict) ?? 0) + 1);
      const index = after.scores?.index ?? before;
      const stat = box.ownerDocument.querySelector('[data-testid="debt-index"]');
      if (stat) stat.textContent = fmt(index, 1);
      box.append(
        h(
          'div',
          { class: 'card summary' },
          h('h3', null, 'Graded'),
          h('p', null, `${[...counts].map(([k, n]) => `${n} ${k}`).join(', ')}${typed ? ' (one grading call).' : ' (no call: every answer was blank).'}`),
          h('p', { class: 'delta', 'data-testid': 'debt-delta' }, 'Debt index ', h('strong', null, fmt(before, 1)), ' → ', h('strong', null, fmt(index, 1)), index < before ? ' ↓' : ''),
          h('p', { class: 'row' }, h('a', { href: repoHref(v.repo.id, 'debt') }, 'See your debt per unit'), h('a', { href: repoHref(v.repo.id, 'quiz') }, 'Another sitting')),
        ),
      );
      for (const r of results) box.append(resultCard(after, r, levelsFor([{ id: r.unit_id }], after.records).get(r.unit_id) ?? 1));
    } catch (e) {
      done();
      box.append(h('p', { class: 'error' }, errorText(e)));
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
    const send = button(last ? 'Answer and grade' : 'Next', () => {
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

export function quizTab(app: App, v: RepoView): HTMLElement {
  const a = v.analysis;
  if (!a) return h('p', null, 'This repository has no analysis yet.');
  const flow = h('div', { id: 'quiz-flow' });
  const nSel = h('select', { id: 'quiz-n', 'aria-label': 'Questions' });
  for (const k of [1, 2, 3, 4, 5]) nSel.append(h('option', { value: String(k), selected: k === 2 }, String(k)));
  const uSel = h('select', { id: 'quiz-unit', class: 'grow', 'aria-label': 'Unit' }, h('option', { value: '' }, 'Most debt first'));
  for (const u of a.specs.units) {
    if ((u.scope ?? 'architectural') !== 'architectural') continue;
    uSel.append(h('option', { value: u.id, selected: app.quizUnit === u.id }, `${u.parent ? ' ' : ''}${u.name}`));
  }
  app.quizUnit = null;
  const mode = getMode(app.deps.storage);
  const note =
    mode === 'gateway'
      ? 'Questions are written in one call and your answers graded in one more, through the HintWise key (they count against your daily allowance). A question you missed comes back in other words without a call.'
      : mode === 'byok'
        ? 'Questions are written and graded with your own Anthropic key, straight from this browser: two calls per sitting.'
        : null;
  const go = button('Start', () => {
    const done = busy(go, 'Writing questions');
    void prepare(app, v, Number(nSel.value), uSel.value || null)
      .then(async (qs) => {
        done();
        const id = await app.deps.store.startSession(v.repo.id, a.sha, qs.map((q) => ({ unit_id: q.unit_id, level: q.level, question: q.question }))).catch(() => null);
        askFlow(app, v, flow, qs, id);
      })
      .catch((e: unknown) => {
        done();
        clear(flow).append(h('p', { class: 'error' }, errorText(e)));
      });
  }, 'primary');
  go.disabled = mode === 'off';
  return h(
    'div',
    null,
    h('p', { class: 'sub' }, 'Each unit at your level on the question ladder, one fact per question. At levels 1 and 2 a miss shows the answer; it counts as the miss it was.'),
    h(
      'div',
      { class: 'card' },
      h('div', { class: 'row' }, h('label', { for: 'quiz-n' }, 'Questions'), nSel, h('label', { for: 'quiz-unit' }, 'Unit'), uSel, go),
      note ? h('p', { class: 'muted small' }, note) : h('p', { class: 'warn small' }, 'Model features are off. ', h('a', { href: '#/settings' }, 'Choose the HintWise key or your own key'), ' to take a quiz.'),
    ),
    flow,
  );
}
