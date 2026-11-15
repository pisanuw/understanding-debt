/**
 * Screenshots of every screen at 1280 and 390 px, light and dark, for a person to look at
 * (UDEBT_SHOTS=1 npm run e2e; written to UDEBT_SHOTS_DIR, default ../.udebt/dashboard,
 * as hosted-<screen>-<width>-<scheme>.png), the Review tab and a review sitting among them
 * (hosted-review-*.png, a day later on Playwright's fake clock) and a quiz on a unit that names
 * only directories (hosted-tree-*.png: the question, and the grade with the files it rests on),
 * and udebt without signing in (hosted-anon-*.png: the front page, a sample, an advance-quiz
 * question, the self-check step, the recorded sitting with its label, the debt, and the copy
 * offer at sign-in), and the quiz-kind choice signed in with the HintWise key (hosted-choice-*.png:
 * the advance kind first, a recorded self-checked sitting, the model kind, and a review the next
 * day with an item of each kind). Also checks that no page scrolls sideways.
 */
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

const DIR = process.env.UDEBT_SHOTS_DIR ?? fileURLToPath(new URL('../../.udebt/dashboard', import.meta.url));

test.skip(process.env.UDEBT_SHOTS !== '1', 'screenshots only with UDEBT_SHOTS=1');

async function shot(page: Page, name: string, width: number, scheme: string): Promise<void> {
  // The "Model calls" toast from Settings is still up for a few seconds; it would cover the page.
  if (name !== 'settings') await page.evaluate(() => document.getElementById('toast')?.setAttribute('hidden', ''));
  const sideways = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(sideways, `${name} at ${width}px scrolls sideways`).toBeLessThanOrEqual(0);
  await page.screenshot({ path: `${DIR}/hosted-${name}-${width}-${scheme}.png`, fullPage: true });
}

for (const width of [1280, 390]) {
  for (const scheme of ['light', 'dark'] as const) {
    test(`every screen at ${width}px, ${scheme}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ colorScheme: scheme });
      await page.goto('/');
      await expect(page.getByRole('button', { name: 'Continue with Google' })).toBeVisible();
      await shot(page, 'signin', width, scheme);
      await page.getByRole('button', { name: 'Continue with Google' }).click();
      await expect(page.getByRole('heading', { name: 'Repositories' })).toBeVisible();
      await shot(page, 'repos', width, scheme);
      await page.locator('a.repo-card', { hasText: 'digitalme' }).click();
      await expect(page.getByRole('heading', { name: 'Units' })).toBeVisible();
      await shot(page, 'specs', width, scheme);
      await page.getByRole('link', { name: 'Chat request lifecycle (SSE)' }).click();
      await expect(page.locator('.walkthrough .excerpt').first()).not.toContainText('Loading', { timeout: 15_000 });
      await shot(page, 'unit', width, scheme);
      await page.goto('/#/r/1/u/persona-retrieval%2Frag');
      await expect(page.getByRole('heading', { name: 'Orientation' })).toBeVisible();
      await shot(page, 'subunit', width, scheme);
      await page.goto('/#/settings');
      await page.getByLabel(/The HintWise key/).check();
      await shot(page, 'settings', width, scheme);
      await page.goto('/#/r/1/quiz');
      await page.getByLabel(/Written and graded by the model/).check();
      await page.getByLabel('Questions').selectOption('2');
      await shot(page, 'quiz-start', width, scheme);
      await page.getByRole('button', { name: 'Start' }).click();
      await expect(page.getByText('[1/2]')).toBeVisible();
      await page.getByLabel('Your answer').fill('It streams the reply to the browser as it is written and saves the exchange.');
      await shot(page, 'quiz-question', width, scheme);
      await page.getByRole('button', { name: 'Next' }).click();
      await page.getByRole('button', { name: "I don't know" }).click();
      await expect(page.getByText('A known unknown: no penalty.')).toBeVisible();
      await shot(page, 'quiz-reveal', width, scheme);
      await page.getByRole('button', { name: 'Grade my answers' }).click();
      await expect(page.getByRole('heading', { name: 'Graded' })).toBeVisible();
      await shot(page, 'quiz-graded', width, scheme);
      await page.getByRole('link', { name: 'See your debt per unit' }).click();
      await expect(page.getByTestId('debt-tile')).toBeVisible();
      await shot(page, 'debt', width, scheme);
      await page.goto('/#/');
      await expect(page.getByRole('heading', { name: 'Repositories' })).toBeVisible();
      await shot(page, 'repos-after', width, scheme);
      await page.goto('/#/add');
      await page.getByLabel('Repository URL').fill('https://github.com/octocat/hello-world');
      await page.getByRole('button', { name: 'Add' }).click();
      await expect(page.locator('.warn, .error').first()).toBeVisible();
      await shot(page, 'add', width, scheme);
      await page.evaluate(() => sessionStorage.clear());
    });
  }
}

for (const width of [1280, 390]) {
  for (const scheme of ['light', 'dark'] as const) {
    test(`the review tab and a sitting at ${width}px, ${scheme}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ colorScheme: scheme });
      await page.clock.setFixedTime(new Date('2026-11-15T10:00:00Z'));
      await page.goto('/');
      await page.getByRole('button', { name: 'Continue with Google' }).click();
      await page.goto('/#/settings');
      await page.getByLabel(/The HintWise key/).check();
      await page.goto('/#/r/1/quiz');
      await page.getByLabel(/Written and graded by the model/).check();
      await page.getByLabel('Questions').selectOption('2');
      await page.getByRole('button', { name: 'Start' }).click();
      await page.getByLabel('Your answer').fill('It is wrong on purpose: it renders the slides.');
      await page.getByRole('button', { name: 'Next' }).click();
      await page.getByRole('button', { name: "I don't know" }).click();
      await page.getByRole('button', { name: 'Grade my answers' }).click();
      await expect(page.getByRole('heading', { name: 'Graded' })).toBeVisible();
      await page.getByRole('link', { name: /^Review/ }).click();
      await expect(page.getByTestId('review-later')).toBeVisible();
      await shot(page, 'review-coming', width, scheme);
      await page.clock.setFixedTime(new Date('2026-11-16T11:00:00Z'));
      await page.getByRole('link', { name: 'Repositories' }).click();
      await expect(page.getByTestId('repo-review-due')).toBeVisible();
      await shot(page, 'review-repos', width, scheme);
      await page.locator('a.repo-card', { hasText: 'digitalme' }).click();
      await page.getByRole('link', { name: /^Review/ }).click();
      await expect(page.getByRole('heading', { name: 'Due now: 2' })).toBeVisible();
      await shot(page, 'review-tab', width, scheme);
      await page.getByRole('button', { name: 'Review what is due' }).click();
      await expect(page.getByText('[1/2]')).toBeVisible();
      await page.getByLabel('Your answer').fill('It streams the reply to the browser as it is written and saves the exchange.');
      await shot(page, 'review-question', width, scheme);
      await page.getByRole('button', { name: 'Next' }).click();
      await page.getByRole('button', { name: "I don't know" }).click();
      await expect(page.getByText('A known unknown: no penalty.')).toBeVisible();
      await shot(page, 'review-reveal', width, scheme);
      await page.getByRole('button', { name: 'Grade my answers' }).click();
      await expect(page.getByRole('heading', { name: 'Graded' })).toBeVisible();
      await shot(page, 'review-graded', width, scheme);
      await page.getByRole('link', { name: 'Back to Review' }).click();
      await expect(page.getByRole('heading', { name: 'Nothing due now' })).toBeVisible();
      await shot(page, 'review-after', width, scheme);
      await page.evaluate(() => sessionStorage.clear());
    });
  }
}

for (const width of [1280, 390]) {
  for (const scheme of ['light', 'dark'] as const) {
    test(`a quiz on a unit that names only directories at ${width}px, ${scheme}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ colorScheme: scheme });
      await page.goto('/');
      await page.getByRole('button', { name: 'Continue with Google' }).click();
      await page.goto('/#/settings');
      await page.getByLabel(/The HintWise key/).check();
      await page.goto('/#/r/1/quiz');
      await page.getByLabel(/Written and graded by the model/).check();
      await page.getByLabel('Questions').selectOption('1');
      await page.getByLabel('Unit').selectOption('frontend-spa');
      await page.getByRole('button', { name: 'Start' }).click();
      await expect(page.getByText('[1/1] React/Vite frontend')).toBeVisible();
      await page.getByLabel('Your answer').fill('It is the browser app: the chat, the warmth meter and the admin page.');
      await shot(page, 'tree-question', width, scheme);
      await page.getByRole('button', { name: 'Answer and grade' }).click();
      await expect(page.getByTestId('evidence')).toBeVisible();
      await shot(page, 'tree-graded', width, scheme);
      await page.evaluate(() => sessionStorage.clear());
    });
  }
}

for (const width of [1280, 390]) {
  for (const scheme of ['light', 'dark'] as const) {
    test(`without signing in at ${width}px, ${scheme}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ colorScheme: scheme });
      await page.clock.setFixedTime(new Date('2026-11-15T10:00:00Z'));
      await page.goto('/');
      await expect(page.getByRole('heading', { name: 'Start with a sample: no account needed' })).toBeVisible();
      await shot(page, 'anon-home', width, scheme);
      await page.locator('a.repo-card', { hasText: 'digitalme' }).click();
      await expect(page.getByRole('heading', { name: 'Units' })).toBeVisible();
      await shot(page, 'anon-sample', width, scheme);
      await page.getByRole('link', { name: 'Quiz' }).click();
      await page.getByRole('button', { name: 'Start' }).click();
      await expect(page.getByText(/^\[1\/2\]/)).toBeVisible();
      await page.getByLabel('Your answer').fill('It streams the reply to the browser as it is written and saves the exchange.');
      await shot(page, 'anon-question', width, scheme);
      await page.getByRole('button', { name: 'Show the answer' }).click();
      await expect(page.getByTestId('reference')).toBeVisible();
      await shot(page, 'anon-check', width, scheme);
      await page.getByRole('button', { name: 'Correct' }).click();
      await page.getByLabel('Your answer').fill('It renders the slides.');
      await page.getByRole('button', { name: 'Show the answer' }).click();
      await page.getByRole('button', { name: 'Wrong' }).click();
      await expect(page.getByRole('heading', { name: 'Recorded' })).toBeVisible();
      await shot(page, 'anon-graded', width, scheme);
      await page.getByRole('link', { name: 'See your debt per unit' }).click();
      await expect(page.getByTestId('debt-self-note')).toBeVisible();
      await shot(page, 'anon-debt', width, scheme);
      await page.goto('/#/settings');
      await expect(page.getByRole('heading', { name: 'What udebt stores' })).toBeVisible();
      await shot(page, 'anon-settings', width, scheme);
      await page.getByRole('navigation', { name: 'Account' }).getByRole('link', { name: 'Sign in' }).click();
      await page.getByRole('button', { name: 'Continue with Google' }).click();
      await expect(page.getByTestId('offer')).toBeVisible();
      await shot(page, 'anon-offer', width, scheme);
      await page.evaluate(() => {
        sessionStorage.clear();
        localStorage.clear();
      });
    });
  }
}

for (const width of [1280, 390]) {
  for (const scheme of ['light', 'dark'] as const) {
    test(`the quiz-kind choice signed in at ${width}px, ${scheme}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ colorScheme: scheme });
      await page.clock.setFixedTime(new Date('2026-11-15T10:00:00Z'));
      await page.goto('/');
      await page.getByRole('button', { name: 'Continue with Google' }).click();
      await page.goto('/#/settings');
      await page.getByLabel(/The HintWise key/).check();
      await page.goto('/#/r/1/quiz');
      await expect(page.getByLabel(/Written in advance, self-checked/)).toBeChecked();
      await shot(page, 'choice-advance', width, scheme);
      await page.getByRole('button', { name: 'Start' }).click();
      await page.getByLabel('Your answer').fill('It streams the reply to the browser as it is written and saves the exchange.');
      await page.getByRole('button', { name: 'Show the answer' }).click();
      await expect(page.getByTestId('reference')).toBeVisible();
      await shot(page, 'choice-check', width, scheme);
      await page.getByRole('button', { name: 'Correct' }).click();
      await page.getByLabel('Your answer').fill('It renders the slides.');
      await page.getByRole('button', { name: 'Show the answer' }).click();
      await page.getByRole('button', { name: 'Wrong' }).click();
      await expect(page.getByRole('heading', { name: 'Recorded' })).toBeVisible();
      await shot(page, 'choice-recorded', width, scheme);
      await page.getByLabel(/Written and graded by the model/).check();
      await expect(page.getByText(/^Questions written and graded by the model/)).toBeVisible();
      await shot(page, 'choice-model', width, scheme);
      await page.getByLabel('Questions').selectOption('1');
      await page.getByLabel('Unit').selectOption('persona-retrieval');
      await page.getByRole('button', { name: 'Start' }).click();
      await page.getByLabel('Your answer').fill('It is wrong on purpose: it renders the slides.');
      await page.getByRole('button', { name: 'Answer and grade' }).click();
      await expect(page.getByRole('heading', { name: 'Graded' })).toBeVisible();
      await shot(page, 'choice-graded', width, scheme);
      await page.clock.setFixedTime(new Date('2026-11-16T11:00:00Z'));
      await page.getByRole('link', { name: 'Your debt', exact: true }).click();
      await expect(page.getByTestId('debt-self-note')).toBeVisible();
      await shot(page, 'choice-debt', width, scheme);
      await page.getByRole('link', { name: /^Review/ }).click();
      await expect(page.getByRole('button', { name: 'Review the self-checked ones' })).toBeVisible();
      await shot(page, 'choice-review', width, scheme);
      await page.evaluate(() => {
        sessionStorage.clear();
        localStorage.clear();
      });
    });
  }
}
