/**
 * Placeholder entry: replace with the app. It shows the three platform pieces
 * wired together: the sign-in panel (only when Supabase env is configured),
 * a repository (Supabase live, in memory in preview), and the LLM mode.
 */
import { mountSignIn } from './lib/auth';
import { getMode } from './lib/llm';
import { InMemoryRepository, newId, SupabaseRepository, type Entity, type Repository } from './lib/repository';
import { APP_SLUG, getSupabase, hasSupabase } from './lib/supabase';

interface Note extends Entity {
  title: string;
}

const root = document.getElementById('app');
if (!root) throw new Error('index.html needs <div id="app">');

root.innerHTML = `
  <h1>${APP_SLUG}</h1>
  <p>Template placeholder. Replace <code>src/main.ts</code> with the app.</p>
  <section id="signin" aria-label="Sign in"></section>
  <p id="mode"></p>
  <ul id="notes" aria-label="Notes"></ul>
`;

const live = hasSupabase();
const repo: Repository<Note> = live
  ? new SupabaseRepository<Note>(getSupabase(), 'notes')
  : new InMemoryRepository<Note>([{ id: newId(), title: 'Preview mode: data stays in this tab.' }]);

const signin = document.getElementById('signin') as HTMLElement;
if (live) mountSignIn(signin);
else signin.textContent = 'Sign-in appears once VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY are set.';

(document.getElementById('mode') as HTMLElement).textContent = `Model features: ${getMode()}.`;

void repo.list().then((notes) => {
  const ul = document.getElementById('notes') as HTMLElement;
  for (const n of notes) {
    const li = document.createElement('li');
    li.textContent = n.title;
    ul.append(li);
  }
});
