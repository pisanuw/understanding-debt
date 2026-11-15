/**
 * The copy offer (HOSTING.md decision 10): after signing in, when this browser keeps answers
 * given without an account that were never offered, udebt asks once what to do with them:
 *
 *   copy      insert them into the account with the person's own token (server store's
 *             `addRecords`), each sample's records under the account's repository of the same
 *             URL, every field kept (a self-checked answer stays self-checked);
 *   keep      leave them in this browser, for when the person is signed out, and send nothing;
 *   discard   delete them from this browser.
 *
 * Nothing happens without a choice, and whichever is chosen the same answers are never offered
 * again (LocalStore.decide). A sample the account's store does not know (not loaded on the server
 * yet) keeps its answers in this browser and says so.
 */
import { isSelfChecked } from '../../core/quiz';
import type { App } from '../app';
import { busy, button, h, toast } from '../dom';

function count(n: number, what: string): string {
  return `${n} ${what}${n === 1 ? '' : 's'}`;
}

export async function offerCard(app: App): Promise<HTMLElement | null> {
  const anon = app.deps.anon;
  if (!anon || !app.user) return null;
  const pending = await anon.pending().catch(() => []);
  if (!pending.length) return null;
  const total = pending.reduce((n, p) => n + p.records.length, 0);
  const self = pending.reduce((n, p) => n + p.records.filter(isSelfChecked).length, 0);
  const where = pending.map((p) => `${p.records.length} on ${p.repo.name}`).join(', ');
  const out = h('p', { class: 'small', role: 'status' });
  const card = h(
    'div',
    { class: 'card offer', 'data-testid': 'offer', role: 'region', 'aria-label': 'Answers from before you signed in' },
    h('h2', null, 'Answers from before you signed in'),
    h('p', null, `This browser keeps ${count(total, 'answer')} you gave without an account (${where}). Copy them into your account?${self ? ` ${self === total ? 'They stay' : `The ${self} self-checked ones stay`} marked as self-checked.` : ''}`),
  );
  const decided = (text: string) => {
    app.forget();
    toast(text);
    void app.render();
  };
  const copy = button(
    'Copy into my account',
    () => {
      const done = busy(copy, 'Copying');
      void (async () => {
        let copied = 0;
        const left: string[] = [];
        try {
          for (const p of pending) {
            const repo = await app.deps.store.findRepository(p.repo.url);
            if (repo) {
              await app.deps.store.addRecords(p.records.map(({ id: _id, ...r }) => ({ ...r, repo_id: repo.id })));
              copied += p.records.length;
            } else left.push(p.repo.name);
            await anon.decide(p.repo.id);
          }
        } catch (e) {
          done();
          out.textContent = `Not copied: ${e instanceof Error ? e.message : String(e)}. Your answers are still in this browser; try again.`;
          return;
        }
        decided(`Copied ${count(copied, 'answer')} into your account.${left.length ? ` ${left.join(', ')} is not in udebt's database yet: those answers stay in this browser.` : ''}`);
      })();
    },
    'primary',
  );
  const keep = button('Keep them separate', () => {
    void Promise.all(pending.map((p) => anon.decide(p.repo.id))).then(() => decided('Kept in this browser only; your account is unchanged.'));
  });
  const discard = button('Discard them', () => {
    void Promise.all(pending.map((p) => anon.decide(p.repo.id, true))).then(() => decided('The answers kept in this browser are deleted.'));
  });
  card.append(
    h('div', { class: 'row' }, copy, keep, discard),
    out,
    h('p', { class: 'muted small' }, 'Asked once: whatever you choose, these answers are not offered again. Kept separate, they stay in this browser for when you are signed out.'),
  );
  return card;
}
