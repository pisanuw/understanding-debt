/**
 * Settings: how udebt calls the model (the HintWise key through the gateway, the person's
 * own Anthropic key, or off), the account (sign out, "Delete my account"), and what is stored.
 */
import { mountSignIn } from '../../lib/auth';
import { getByokKey, getMode, setByokKey, setMode, type LlmMode } from '../../model/prefs';
import type { App } from '../app';
import { button, h, toast } from '../dom';

export function settingsScreen(app: App): HTMLElement {
  const storage = app.deps.storage;
  const current = getMode(storage);
  const modes: [LlmMode, string, string][] = [
    ['gateway', 'The HintWise key', 'Questions are written and graded through HintWise, within a daily allowance per person. Nothing to set up.'],
    ['byok', 'My own Anthropic key', 'Your key stays in this browser and goes only to api.anthropic.com, except when you ask udebt to analyse a new repository: then it is sent once to udebt’s analysis service, used for that job only and never stored. No allowance applies.'],
    ['off', 'Off', 'Read the specs and walkthroughs without any model call.'],
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
    const inp = h('input', { type: 'radio', name: 'llm-mode', value: m, checked: m === current });
    inp.addEventListener('change', () => {
      setMode(m, storage);
      keyBox.hidden = m !== 'byok';
      toast(`Model calls: ${title}.`);
    });
    fs.append(h('label', { class: 'mode' }, inp, h('span', null, h('strong', null, title), h('span', { class: 'small muted' }, text))));
  }
  const account = h('div', { id: 'account' });
  app.onLeave(mountSignIn(account, app.deps.signIn));
  const s = h(
    'section',
    { class: 'settings' },
    h('h1', null, 'Settings'),
    h('div', { class: 'card' }, fs, keyBox),
    h('div', { class: 'card' }, h('h2', null, 'Account'), account),
    h(
      'div',
      { class: 'card' },
      h('h2', null, 'What udebt stores'),
      h(
        'ul',
        null,
        h('li', null, 'For each answer: the unit, the ladder level, the question, the verdict, your confidence and whether the answer was shown. Never the text of your answer, and not the grader’s feedback.'),
        h('li', null, 'The repositories you added, your quiz sessions (the questions asked) and the analyses you asked for.'),
        h('li', null, 'Analyses of public repositories (units, decisions, file and line references, never code) are shared with every signed-in person, as is the fact that a repository is in udebt.'),
        h('li', null, 'Where: HintWise’s database (Supabase, US East). The HintWise gateway records the cost of each model call. "Delete my account" above removes your records, sessions and repositories in udebt and every other HintWise app.'),
      ),
    ),
  );
  s.dataset.title = 'Settings';
  return s;
}
