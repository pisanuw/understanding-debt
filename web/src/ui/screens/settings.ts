/**
 * Settings: how udebt calls the model (the HintWise key through the gateway, the person's
 * own Anthropic key, or off), the account (sign out, "Delete my account"), and what is stored.
 * Not signed in, the HintWise key is not offered (the gateway is for signed-in people), the
 * account panel is the sign-in panel, and the answers kept in this browser can be deleted.
 */
import { mountSignIn } from '../../lib/auth';
import { getByokKey, getMode, setByokKey, setMode, type LlmMode } from '../../model/prefs';
import type { App } from '../app';
import { button, h, toast } from '../dom';

/**
 * What HintWise's analytics script (public/hw-events.js) and the app's "screen" event send,
 * field by field, so the list stays true: keep it in step with the script and app.ts.
 */
export const VISITS =
  'Visits are counted for HintWise’s own analytics, with no cookie and nothing kept in this browser: the page’s path (never what follows the #, so never a repository or a unit), which screen and tab you open, the site you came from (its name only), how long the page was open, the site of a link you follow out, and the first three script error messages on each page you view. Apart from what an error message may contain, never what you type. Each error message is cut to 100 characters, and the script removes the quoted text, email addresses and long numbers it can detect, but a message can still contain words a browser quotes from what you typed. HintWise’s server also keeps the kind of device (phone, tablet or computer), worked out from the browser’s user agent. It counts a visitor with a code made from the IP address, the browser’s user agent and a random value; the IP address and user agent are not stored. The random value changes each UTC day and serves every HintWise app; it is deleted at the first visit any HintWise app counts on a later UTC day, so visits on different UTC days cannot be linked. Nothing is sent when your browser asks not to be tracked (Global Privacy Control or Do Not Track).';

export function settingsScreen(app: App): HTMLElement {
  const storage = app.deps.storage;
  // Not chosen yet: the HintWise key when it serves this visitor (signed in, or the shared
  // allowance for visitors who are not); else the stored choice.
  const anonymous = app.anonymous;
  const shared = anonymous && app.anonModel;
  const current = anonymous ? getMode(storage, shared ? 'gateway' : 'off') : app.mode();
  const gatewayText = !anonymous
    ? 'The default: questions are written and graded through HintWise, within a daily allowance per person. Nothing to set up.'
    : shared
      ? 'The default: questions are written and graded through HintWise, from one daily allowance shared by everyone who is not signed in. Sign in for an allowance of your own.'
      : 'Sign in to use it: questions are written and graded through HintWise, within a daily allowance per person.';
  const modes: [LlmMode, string, string][] = [
    ['gateway', 'The HintWise key', gatewayText],
    ['byok', 'My own Anthropic key', 'Your key stays in this browser and goes only to api.anthropic.com, except when you ask udebt to analyse a new repository: then it is sent once to udebt’s analysis service, used for that job only and never stored. No allowance applies. No account needed.'],
    ['off', 'Off', 'No model call: read the specs and walkthroughs, and answer the questions written in advance for the samples, checking yourself against the reference answer.'],
  ];
  const keyBox = h('div', { class: 'byok', hidden: current !== 'byok' });
  const keyInput = h('input', { type: 'password', autocomplete: 'off', spellcheck: 'false', placeholder: getByokKey(storage) ? 'A key is saved in this browser' : 'sk-ant-...', 'aria-label': 'Your Anthropic API key', class: 'grow' });
  keyBox.append(
    h('div', { class: 'row' }, keyInput, button('Save the key', () => {
      setByokKey(keyInput.value, storage);
      keyInput.value = '';
      keyInput.placeholder = getByokKey(storage) ? 'A key is saved in this browser' : 'sk-ant-...';
      toast(getByokKey(storage) ? 'Your key is saved in this browser.' : 'No key saved.');
    }), button('Forget the key', () => {
      setByokKey(null, storage);
      keyInput.placeholder = 'sk-ant-...';
      toast('Your key is removed from this browser.');
    })),
  );
  const fs = h('fieldset', { class: 'modes' }, h('legend', null, 'How udebt calls the model'));
  for (const [m, title, text] of modes) {
    const inp = h('input', { type: 'radio', name: 'llm-mode', value: m, checked: m === current, disabled: anonymous && !shared && m === 'gateway' });
    inp.addEventListener('change', () => {
      setMode(m, storage);
      keyBox.hidden = m !== 'byok';
      toast(`Model calls: ${title}.`);
    });
    fs.append(h('label', { class: 'mode' }, inp, h('span', null, h('strong', null, title), h('span', { class: 'small muted' }, text))));
  }
  if (anonymous && !shared && current === 'gateway') fs.append(h('p', { class: 'small warn' }, 'Not signed in: until you sign in or choose your own key, quizzes use the questions written in advance.'));
  const account = h('div', { id: 'account' });
  app.onLeave(mountSignIn(account, app.deps.signIn));
  const stored = anonymous
    ? h(
        'ul',
        null,
        h('li', null, 'Not signed in: your answers on the samples stay in this browser (its local storage, one entry per sample): for each answer the unit, the ladder level, the question, the verdict, your confidence and whether the answer was shown. Never the text of your answer. Nothing of it is sent to HintWise.'),
        h('li', null, 'Answers you checked yourself against a reference answer are marked self-checked, and the debt says so.'),
        h('li', null, 'When you sign in, udebt asks once whether to copy these answers into your account, keep them separate, or discard them.'),
        h('li', null, 'The samples’ analyses and questions are files of this site; their code is read from GitHub at the pinned commit through udebt’s functions, which keep nothing.'),
        h('li', null, VISITS),
      )
    : h(
        'ul',
        null,
        h('li', null, 'For each answer: the unit, the ladder level, the question, the verdict, your confidence and whether the answer was shown. Never the text of your answer, and not the grader’s feedback. Answers you checked yourself against a reference answer are marked self-checked.'),
        h('li', null, 'The repositories you added, your quiz sessions (the questions asked) and the analyses you asked for.'),
        h('li', null, 'Analyses of public repositories (units, decisions, file and line references, never code) are shared with every signed-in person, as is the fact that a repository is in udebt.'),
        h('li', null, 'Where: HintWise’s database (Supabase, US East). The HintWise gateway records the cost of each model call. "Delete my account" above removes your records, sessions and repositories in udebt and every other HintWise app.'),
        h('li', null, VISITS),
      );
  const local = app.deps.anon;
  const forget = local
    ? button('Delete the answers kept in this browser', () => {
        void local.forgetAll().then((n) => {
          app.forget();
          toast(n ? `Deleted ${n} answer(s) kept in this browser.` : 'This browser keeps no answers.');
        });
      })
    : null;
  const s = h(
    'section',
    { class: 'settings' },
    h('h1', null, 'Settings'),
    h('div', { class: 'card' }, fs, keyBox),
    h('div', { class: 'card' }, h('h2', null, anonymous ? 'Sign in' : 'Account'), account),
    h('div', { class: 'card' }, h('h2', null, 'What udebt stores'), stored, forget ? h('div', { class: 'row gap-top' }, forget) : null),
  );
  s.dataset.title = 'Settings';
  return s;
}
