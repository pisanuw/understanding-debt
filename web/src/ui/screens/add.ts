/**
 * Adding a repository: a public GitHub URL. If udebt already has an analysis of it (anyone's:
 * analyses of public repositories are shared), it joins the person's list at once; otherwise
 * the analysis service on Render analyses it with the person's own Anthropic key, and the
 * page follows the job through the person's own app_udebt.jobs rows.
 */
import { parseGitHubUrl } from '../../core/specs';
import { getByokKey } from '../../model/prefs';
import { repoHref, type App } from '../app';
import { badge, busy, button, clear, day, h } from '../dom';

export const POLL_MS = 5000;

function jobsList(app: App, box: HTMLElement): void {
  void app.deps.store
    .jobs()
    .then((jobs) => {
      clear(box);
      box.hidden = !jobs.length;
      if (!jobs.length) return;
      const ul = h('ul', { class: 'list' });
      for (const j of jobs) ul.append(h('li', null, badge(j.status, j.status === 'done' ? 'correct' : j.status === 'failed' ? 'wrong' : 'partial'), ` ${j.url}`, j.sha ? ` at ${j.sha.slice(0, 8)}` : '', h('div', { class: 'small muted' }, `${day(j.created_at)}${j.message ? ` · ${j.message}` : ''}`)));
      box.append(h('h2', null, 'Your analyses'), ul);
    })
    .catch(() => undefined);
}

export async function addScreen(app: App): Promise<HTMLElement> {
  const url = h('input', { type: 'url', id: 'add-url', class: 'grow', placeholder: 'https://github.com/owner/name', 'aria-label': 'Repository URL', required: true });
  const rev = h('input', { type: 'text', id: 'add-rev', placeholder: 'commit (optional)', 'aria-label': 'Commit', size: '14' });
  const out = h('div', { class: 'gap-top' });
  const jobs = h('div', { class: 'card', id: 'jobs', hidden: true });
  const form = h('form', { class: 'row' }, url, rev);
  const go = h('button', { type: 'submit', class: 'primary' }, 'Add');
  form.append(go);
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const g = parseGitHubUrl(url.value);
    clear(out);
    if (!g) {
      out.append(h('p', { class: 'error' }, 'Use a public GitHub URL: https://github.com/owner/name.'));
      return;
    }
    const done = busy(go, 'Checking');
    void (async () => {
      try {
        const known = await app.deps.store.findRepository(g.url);
        if (known && (await app.deps.store.analyses(known.id)).length) {
          await app.deps.store.addMine(known.id);
          app.forget();
          out.append(h('p', null, `${known.name} is in your list: `, h('a', { href: repoHref(known.id) }, 'open it'), '.'));
          return;
        }
        const key = getByokKey(app.deps.storage);
        if (!key) {
          out.append(h('p', { class: 'warn' }, 'udebt has no analysis of this repository yet. Analysing one needs your own Anthropic key: ', h('a', { href: '#/settings' }, 'add it in Settings'), '.'));
          return;
        }
        if (!app.deps.worker) {
          out.append(h('p', { class: 'warn' }, app.deps.preview ? 'The analysis service does not run in the preview.' : 'The analysis service is not configured for this site yet.'));
          return;
        }
        const token = await app.deps.getToken();
        if (!token) throw new Error('Sign in first.');
        const { DEFAULT_MODEL } = await import('../../lib/llm');
        const r = await app.deps.worker.start({ url: g.url, ...(rev.value.trim() ? { rev: rev.value.trim() } : {}), model: DEFAULT_MODEL }, key, token);
        out.append(h('p', null, `Analysing ${g.owner}/${g.repo} (${r.status}). This takes a few minutes; this page shows the result when it is done.`));
        jobsList(app, jobs);
        const timer = setInterval(() => {
          void app.deps.store.jobs().then((js) => {
            const j = r.job === null ? js[0] : js.find((x) => x.id === r.job);
            if (j && (j.status === 'done' || j.status === 'failed')) {
              clearInterval(timer);
              app.forget();
              jobsList(app, jobs);
            }
          });
        }, POLL_MS);
      } catch (e) {
        out.append(h('p', { class: 'error' }, e instanceof Error ? e.message : String(e)));
      } finally {
        done();
      }
    })();
  });
  jobsList(app, jobs);
  const s = h(
    'section',
    null,
    h('h1', null, 'Add a repository'),
    h('p', { class: 'sub' }, 'A public GitHub repository. If someone already analysed it, it is added at once and costs nothing.'),
    h('div', { class: 'card' }, form, out),
    h(
      'div',
      { class: 'card' },
      h('h2', null, 'A new analysis'),
      h(
        'ul',
        null,
        h('li', null, 'Needs your own Anthropic key (Settings). udebt’s analysis service uses it for this job only and never stores it; the calls are billed to your account, a few for a small repository.'),
        h('li', null, 'Public repositories within the service’s limits (clone size and number of commits); the clone is deleted when the job ends, and no code is kept.'),
        h('li', null, 'The analysis is shared: anyone who adds the same repository sees it.'),
      ),
      button('Back to the repositories', () => app.go('#/')),
    ),
    jobs,
  );
  s.dataset.title = 'Add a repository';
  return s;
}
