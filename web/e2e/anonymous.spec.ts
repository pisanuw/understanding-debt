/**
 * Using udebt without signing in (HOSTING.md decision 10), with fakes: open a sample, read a unit
 * (its code read through /api/excerpt with no token), take a self-checked advance quiz, see the
 * debt fall with the "self-checked" label, miss one, find it due in Review a day later on
 * Playwright's fake clock, review it; then sign in, accept the copy offer and find the answers in
 * the signed-in store (the preview's server fake). No request the page makes while not signed in
 * reaches the gateway functions or carries a token, and no Content-Security-Policy violation.
 */
import { expect, test, type Page } from '@playwright/test';

function watch(page: Page): { csp: string[]; api: { path: string; auth: string | null }[] } {
  const out = { csp: [] as string[], api: [] as { path: string; auth: string | null }[] };
  page.on('console', (m) => {
    if (m.type() === 'error' && /Content Security Policy|Refused to/i.test(m.text())) out.csp.push(m.text());
  });
  page.on('request', (r) => {
    const u = new URL(r.url());
    if (u.pathname.startsWith('/api/') || /supabase|hintwise\.org|anthropic/.test(u.host)) out.api.push({ path: u.host === 'localhost:4173' ? u.pathname : u.href, auth: r.headers().authorization ?? null });
  });
  return out;
}

test('without signing in: a sample, a self-checked quiz, a review; then the copy at sign-in', async ({ page }) => {
  const seen = watch(page);
  await page.clock.setFixedTime(new Date('2026-11-15T10:00:00Z'));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Understand the code an AI wrote' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Start with a sample: no account needed' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Account' }).getByRole('link', { name: 'Sign in' })).toBeVisible();
  expect(await page.locator('site-footer[data-site-footer]').count()).toBe(1);
  const card = page.locator('a.repo-card', { hasText: 'digitalme' });
  await expect(card).toContainText('100.0');
  await card.click();

  // The sample's specs and a unit, its walkthrough's code read without a token.
  await expect(page.getByRole('heading', { name: 'Units' })).toBeVisible();
  await expect(page.getByTestId('local-note')).toContainText('stay in this browser');
  await page.getByRole('link', { name: 'Chat request lifecycle (SSE)' }).click();
  await expect(page.getByRole('heading', { name: 'Walkthrough: one request through the code' })).toBeVisible();
  await expect(page.locator('.walkthrough .excerpt pre').first()).toBeVisible({ timeout: 15_000 });

  // A self-checked advance quiz: one marked correct, one marked wrong.
  await page.getByRole('link', { name: 'Quiz' }).click();
  await expect(page.getByText(/Questions written in advance for this repository/)).toBeVisible();
  await page.getByRole('button', { name: 'Start' }).click();
  await expect(page.getByText(/^\[1\/2\]/)).toContainText('level 1 of 5: purpose and placement');
  await expect(page.getByText('Orientation: read this first')).toBeVisible();
  await page.getByLabel('Your answer').fill('It streams the reply to the browser as it is written and saves the exchange.');
  await page.getByRole('button', { name: 'Show the answer' }).click();
  await expect(page.getByTestId('reference')).toContainText('The answer');
  await page.getByRole('button', { name: 'Correct' }).click();
  await expect(page.getByText(/^\[2\/2\]/)).toBeVisible();
  const missed = await page.locator('p.q-text').textContent();
  await page.getByLabel('Your answer').fill('It renders the slides.');
  await page.getByRole('button', { name: 'Show the answer' }).click();
  await page.getByRole('button', { name: 'Wrong' }).click();
  await expect(page.getByRole('heading', { name: 'Recorded' })).toBeVisible();
  await expect(page.getByText('1 correct, 1 wrong, self-checked (no model call).')).toBeVisible();
  const after = Number((await page.getByTestId('debt-delta').locator('strong').nth(1).textContent()) ?? '100');
  expect(after).toBeLessThan(100);
  await expect(page.getByTestId('self-checked-index')).toBeVisible();
  await expect(page.locator('.result').getByText(/back in review on/)).toBeVisible();

  await page.getByRole('link', { name: 'See your debt per unit' }).click();
  await expect(page.getByTestId('debt-tile')).toHaveText(after.toFixed(1));
  await expect(page.getByTestId('debt-self-note')).toContainText('rests in part on self-checked answers');
  await expect(page.locator('table.debt .badge.self-checked').first()).toBeVisible();

  // Not due yet; a day later on the fake clock it is, on the card and the tab.
  await page.getByRole('link', { name: /^Review/ }).click();
  await expect(page.getByRole('heading', { name: 'Nothing due now' })).toBeVisible();
  await expect(page.getByTestId('review-later').locator('li')).toHaveCount(1);
  await page.clock.setFixedTime(new Date('2026-11-16T11:00:00Z'));
  await page.getByRole('navigation', { name: 'Account' }).getByRole('link', { name: 'Samples' }).click();
  await expect(page.getByTestId('repo-review-due')).toHaveText('1 to review');
  await page.locator('a.repo-card', { hasText: 'digitalme' }).click();
  await page.getByRole('link', { name: /^Review/ }).click();
  await expect(page.getByRole('heading', { name: 'Due now: 1' })).toBeVisible();
  const before = Number(await page.getByTestId('debt-index').textContent());
  await page.getByRole('button', { name: 'Review what is due' }).click();
  await expect(page.locator('p.q-text')).not.toHaveText(missed ?? '');
  await page.getByLabel('Your answer').fill('It keeps the cost of each visitor within budget.');
  await page.getByRole('button', { name: 'Show the answer' }).click();
  await page.getByRole('button', { name: 'Correct' }).click();
  await expect(page.getByText('1 correct, self-checked (no model call).')).toBeVisible();
  expect(Number(await page.getByTestId('debt-index').textContent())).toBeLessThan(before);
  await page.getByRole('link', { name: 'Back to Review' }).click();
  await expect(page.getByRole('heading', { name: 'Nothing due now' })).toBeVisible();

  const kept = (await page.evaluate(() => JSON.parse(localStorage.getItem('udebt.local.v1.digitalme') ?? '{}'))) as { records: { type: string; source: string; verdict: string }[] };
  expect(kept.records.map((r) => [r.type, r.source, r.verdict])).toEqual([
    ['self-checked', 'advance', 'correct'],
    ['self-checked', 'advance', 'wrong'],
    ['self-checked', 'review', 'correct'],
  ]);
  // Nothing while not signed in reached the gateway functions or carried a token.
  expect(seen.api.filter((r) => r.path === '/api/questions' || r.path === '/api/grade' || !r.path.startsWith('/api/'))).toEqual([]);
  expect(seen.api.length).toBeGreaterThan(0);
  expect(seen.api.every((r) => r.auth === null)).toBe(true);

  // Sign in: the offer, once; copied, the answers are in the account with their provenance.
  await page.getByRole('navigation', { name: 'Account' }).getByRole('link', { name: 'Sign in' }).click();
  await page.getByRole('button', { name: 'Continue with Google' }).click();
  await expect(page.getByRole('heading', { name: 'Repositories' })).toBeVisible();
  const offer = page.getByTestId('offer');
  await expect(offer).toContainText('This browser keeps 3 answers you gave without an account (3 on digitalme)');
  await offer.getByRole('button', { name: 'Copy into my account' }).click();
  await expect(page.locator('#toast')).toContainText('Copied 3 answers into your account.');
  await expect(page.getByTestId('offer')).toHaveCount(0);
  const server = (await page.evaluate(() => JSON.parse(sessionStorage.getItem('udebt.preview') ?? '{}'))) as { records: { type: string; source: string; verdict: string }[] };
  expect(server.records.map((r) => [r.type, r.source, r.verdict])).toEqual(kept.records.map((r) => [r.type, r.source, r.verdict]));
  await page.locator('a.repo-card', { hasText: 'digitalme' }).click();
  await expect(page.getByTestId('self-checked-index')).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'digitalme' })).toBeVisible();
  await expect(page.getByTestId('offer')).toHaveCount(0);
  expect(seen.csp).toEqual([]);
});
