/**
 * The main flow with fakes: sign in, open the sample, read the specs and a unit's walkthrough
 * (code fetched through /api/excerpt from the fake GitHub), take a two-question level-1 quiz
 * through the fake gateway, see the debt fall, then delete the account. Fails on any
 * Content-Security-Policy violation: the preview sends the live site's headers.
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
