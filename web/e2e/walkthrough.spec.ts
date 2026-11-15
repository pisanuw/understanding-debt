/**
 * The main flow with fakes: sign in, open the sample, read the specs and a unit's walkthrough
 * (code fetched through /api/excerpt from the fake GitHub), take a two-question level-1 quiz
 * through the fake gateway, see the debt fall, then delete the account; and the spaced review:
 * a missed question comes back in Review the next day (the browser's clock moved by
 * Playwright's fake clock, not by waiting) and a correct answer moves its state on. Fails on
 * any Content-Security-Policy violation: the preview sends the live site's headers.
 */
import { expect, test, type Page } from '@playwright/test';

function watchCsp(page: Page): string[] {
  const seen: string[] = [];
  page.on('console', (m) => {
    if (m.type() === 'error' && /Content Security Policy|Refused to/i.test(m.text())) seen.push(m.text());
  });
  return seen;
}

test('sign in, read the specs, take a level-1 quiz, see the debt fall', async ({ page }) => {
  const csp = watchCsp(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Understand the code an AI wrote' })).toBeVisible();
  expect(await page.locator('site-footer[data-site-footer]').count()).toBe(1);
  await page.getByRole('button', { name: 'Continue with Google' }).click();

  await expect(page.getByRole('heading', { name: 'Repositories' })).toBeVisible();
  const card = page.locator('a.repo-card', { hasText: 'digitalme' });
  await expect(card).toContainText('100.0');
  await card.click();

  await expect(page.getByRole('heading', { name: 'Units' })).toBeVisible();
  await expect(page.getByText('Architecture tour', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Chat request lifecycle (SSE)' }).click();
  await expect(page.getByRole('heading', { name: 'Orientation' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Walkthrough: one request through the code' })).toBeVisible();
  await expect(page.locator('.walkthrough .excerpt').first()).not.toContainText('Loading', { timeout: 15_000 });

  await page.getByRole('link', { name: 'Settings' }).click();
  await page.getByLabel(/The HintWise key/).check();
  await page.getByRole('link', { name: 'Repositories' }).click();
  await page.locator('a.repo-card', { hasText: 'digitalme' }).click();
  await page.getByRole('link', { name: 'Quiz' }).click();
  await page.getByLabel('Questions').selectOption('2');
  await page.getByRole('button', { name: 'Start' }).click();

  await expect(page.getByText('[1/2]')).toBeVisible();
  await expect(page.getByText('level 1 of 5: purpose and placement')).toBeVisible();
  await expect(page.getByText('Orientation: read this first')).toBeVisible();
  await page.getByLabel('Your answer').fill('It streams the reply to the browser as it is written and saves the exchange.');
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByText('[2/2]')).toBeVisible();
  await page.getByLabel('Your answer').fill('It keeps the cost of each visitor within the daily budget.');
  await page.getByRole('button', { name: 'Answer and grade' }).click();

  await expect(page.getByRole('heading', { name: 'Graded' })).toBeVisible();
  await expect(page.getByText('2 correct (one grading call).')).toBeVisible();
  const delta = page.getByTestId('debt-delta');
  await expect(delta).toContainText('100.0');
  const after = Number((await delta.locator('strong').nth(1).textContent()) ?? '100');
  expect(after).toBeLessThan(100);

  await page.getByRole('link', { name: 'See your debt per unit' }).click();
  await expect(page.getByTestId('debt-tile')).toHaveText(after.toFixed(1));

  await page.getByRole('link', { name: 'Settings' }).click();
  await page.getByText('Delete my account', { exact: true }).first().click();
  await page.getByLabel('Type DELETE to confirm').fill('DELETE');
  await page.getByRole('button', { name: 'Delete my account' }).click();
  await expect(page.getByRole('heading', { name: 'Understand the code an AI wrote' })).toBeVisible();
  expect(csp).toEqual([]);
});

test('"I don\'t know" at level 1 shows the answer with no call and records a known unknown', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Continue with Google' }).click();
  await page.goto('/#/settings');
  await page.getByLabel(/The HintWise key/).check();
  await page.goto('/#/r/1/quiz');
  await page.getByLabel('Questions').selectOption('1');
  await page.getByRole('button', { name: 'Start' }).click();
  await page.getByRole('button', { name: "I don't know" }).click();
  await expect(page.getByText('A known unknown: no penalty.')).toBeVisible();
  await expect(page.locator('.answer strong', { hasText: 'The answer' })).toBeVisible();
  await page.getByRole('button', { name: 'Grade my answers' }).click();
  await expect(page.getByText('(no call: every answer was blank)')).toBeVisible();
});

test('a missed question comes back in Review the next day, and a correct answer moves it on', async ({ page }) => {
  const csp = watchCsp(page);
  await page.clock.setFixedTime(new Date('2026-11-15T10:00:00Z'));
  await page.goto('/');
  await page.getByRole('button', { name: 'Continue with Google' }).click();
  await page.goto('/#/settings');
  await page.getByLabel(/The HintWise key/).check();
  await page.goto('/#/r/1/review');
  await expect(page.getByRole('heading', { name: 'Nothing due now' })).toBeVisible();
  await expect(page.getByText(/Nothing to review yet/)).toBeVisible();
  await expect(page.getByTestId('review-count')).toBeHidden();

  // A one-question quiz answered wrong: graded wrong through the fake gateway, the answer shown (level 1).
  await page.getByRole('link', { name: 'Quiz' }).click();
  await page.getByLabel('Questions').selectOption('1');
  await page.getByRole('button', { name: 'Start' }).click();
  await expect(page.getByText('[1/1]')).toBeVisible();
  const asked = await page.locator('p.q-text').textContent();
  await page.getByLabel('Your answer').fill('It is wrong on purpose: it renders the slides.');
  await page.getByRole('button', { name: 'Answer and grade' }).click();
  await expect(page.getByText('1 wrong (one grading call).')).toBeVisible();
  await expect(page.locator('.result .answer strong', { hasText: 'The answer' })).toBeVisible();
  await expect(page.locator('.result').getByText(/back in review on/)).toBeVisible();

  // Not due yet: it is coming up, and there is no badge.
  await page.getByRole('link', { name: /^Review/ }).click();
  await expect(page.getByRole('heading', { name: 'Nothing due now' })).toBeVisible();
  await expect(page.getByTestId('review-later').locator('li')).toHaveCount(1);
  await expect(page.getByTestId('review-count')).toBeHidden();

  // A day later on the fake clock: due, with the badge on the tab and the repository card.
  await page.clock.setFixedTime(new Date('2026-11-16T11:00:00Z'));
  await page.getByRole('link', { name: 'Repositories' }).click();
  await expect(page.getByTestId('repo-review-due')).toHaveText('1 to review');
  await page.locator('a.repo-card', { hasText: 'digitalme' }).click();
  await expect(page.getByTestId('review-count')).toHaveText('1');
  await page.getByRole('link', { name: /^Review/ }).click();
  await expect(page.getByRole('heading', { name: 'Due now: 1' })).toBeVisible();
  await expect(page.getByTestId('review-due').locator('li')).toContainText('level 1 of 5: purpose and placement · due since');
  const before = Number(await page.getByTestId('debt-index').textContent());

  // The review asks a stored phrasing, not the one asked last; one grading call, none for questions.
  await page.getByRole('button', { name: 'Review what is due' }).click();
  await expect(page.getByText('[1/1]')).toBeVisible();
  const again = await page.locator('p.q-text').textContent();
  expect(again).not.toBe(asked);
  await page.getByLabel('Your answer').fill('It streams the reply to the browser as it is written.');
  await page.getByLabel('certain').check();
  await page.getByRole('button', { name: 'Answer and grade' }).click();
  await expect(page.getByText('1 correct (one grading call).')).toBeVisible();
  await expect(page.locator('.result').getByText(/back in review on/)).toBeVisible();
  await expect(page.getByTestId('review-count')).toBeHidden();
  expect(Number(await page.getByTestId('debt-index').textContent())).toBeLessThan(before);

  // The state moved on: nothing due, the item back the next day (SM-2: 1 day after the first pass).
  await page.getByRole('link', { name: 'Back to Review' }).click();
  await expect(page.getByRole('heading', { name: 'Nothing due now' })).toBeVisible();
  await expect(page.getByTestId('review-later').locator('li')).toHaveCount(1);
  const saved = (await page.evaluate(() => JSON.parse(sessionStorage.getItem('udebt.preview') ?? '{}'))) as { records: { source: string; verdict: string; question: string; data: { review: unknown } }[] };
  expect(saved.records.map((r) => [r.source, r.verdict, r.data.review])).toEqual([
    ['quiz', 'wrong', { ease: 2.3, interval: 1, reps: 0, due: '2026-11-16T10:00:00+00:00' }],
    ['review', 'correct', { ease: 2.4, interval: 1, reps: 1, due: '2026-11-17T11:00:00+00:00' }],
  ]);
  expect(saved.records[1].question).toBe(again);
  await page.getByRole('link', { name: 'Your debt' }).click();
  expect(Number(await page.getByTestId('debt-tile').textContent())).toBeLessThan(before);
  expect(csp).toEqual([]);
});
