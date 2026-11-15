/**
 * The main flow with fakes: sign in, open the sample, read the specs and a unit's walkthrough
 * (code fetched through /api/excerpt from the fake GitHub), with the HintWise key take the
 * questions written in advance (the default kind: self-checked, no call), then a two-question
 * level-1 quiz the fake gateway writes and grades on the same account, see the debt fall and both
 * kinds labelled in the debt table, then delete the account; and the spaced review:
 * a missed question comes back in Review the next day (the browser's clock moved by
 * Playwright's fake clock, not by waiting) and a correct answer moves its state on; and a quiz
 * on a unit that names only directories, written from the top files under them (the tree
 * fallback). Fails on any Content-Security-Policy violation: the preview sends the live site's
 * headers.
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

  // Both kinds on offer; the questions written in advance first, self-checked with no call.
  const asked = async () => ((await page.locator('.question p.small').first().textContent()) ?? '').replace(/^\[\d\/\d\] /, '').split(' · ')[0];
  const apiCalls: string[] = [];
  page.on('request', (r) => {
    const path = new URL(r.url()).pathname;
    if (path === '/api/questions' || path === '/api/grade') apiCalls.push(path);
  });
  await expect(page.getByTestId('quiz-kind')).toBeVisible();
  await expect(page.getByLabel(/Written in advance, self-checked/)).toBeChecked();
  await expect(page.getByText(/Questions written in advance for this repository/)).toContainText('no model call');
  await page.getByLabel('Questions').selectOption('2');
  await page.getByRole('button', { name: 'Start' }).click();
  await expect(page.getByText(/^\[1\/2\]/)).toContainText('level 1 of 5: purpose and placement · self-checked');
  await expect(page.getByLabel(/Written and graded by the model/)).toBeDisabled(); // locked while the sitting runs
  const selfUnits = [await asked()];
  await page.getByLabel('Your answer').fill('It streams the reply to the browser as it is written and saves the exchange.');
  await page.getByRole('button', { name: 'Show the answer' }).click();
  await page.getByRole('button', { name: 'Correct' }).click();
  await expect(page.getByText(/^\[2\/2\]/)).toBeVisible();
  selfUnits.push(await asked());
  await page.getByLabel('Your answer').fill('It keeps the cost of each visitor within the daily budget.');
  await page.getByRole('button', { name: 'Show the answer' }).click();
  await page.getByRole('button', { name: 'Correct' }).click();
  await expect(page.getByRole('heading', { name: 'Recorded' })).toBeVisible();
  await expect(page.getByText('2 correct, self-checked (no model call).')).toBeVisible();
  await expect(page.locator('.result .badge.self-checked')).toHaveCount(2);
  await expect(page.getByTestId('self-checked-index')).toBeVisible();
  const delta = page.getByTestId('debt-delta');
  await expect(delta).toContainText('100.0');
  const first = Number((await delta.locator('strong').nth(1).textContent()) ?? '100');
  expect(first).toBeLessThan(100);
  expect(apiCalls).toEqual([]);

  // Then the model's quiz on the same account, one choice away.
  await expect(page.getByLabel(/Written and graded by the model/)).toBeEnabled();
  await page.getByLabel(/Written and graded by the model/).check();
  await expect(page.getByText(/through the HintWise key \(they count against your daily allowance\)/)).toBeVisible();
  await page.getByLabel('Questions').selectOption('2');
  await page.getByRole('button', { name: 'Start' }).click();

  await expect(page.getByText('[1/2]')).toBeVisible();
  await expect(page.getByText('level 1 of 5: purpose and placement')).toBeVisible();
  await expect(page.getByText('Orientation: read this first')).toBeVisible();
  const modelUnits = [await asked()];
  await page.getByLabel('Your answer').fill('It builds the persona prompt and picks how to retrieve.');
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByText('[2/2]')).toBeVisible();
  modelUnits.push(await asked());
  await page.getByLabel('Your answer').fill('It lets the owner review answers and fix the persona.');
  await page.getByRole('button', { name: 'Answer and grade' }).click();

  await expect(page.getByRole('heading', { name: 'Graded' })).toBeVisible();
  await expect(page.getByText('2 correct (one grading call).')).toBeVisible();
  await expect(page.locator('.result .badge.self-checked')).toHaveCount(0);
  expect(apiCalls).toEqual(['/api/questions', '/api/grade']);
  const after = Number((await delta.locator('strong').nth(1).textContent()) ?? '100');
  expect(after).toBeLessThan(first);

  // The debt table: each unit labelled by how it was checked.
  await page.getByRole('link', { name: 'See your debt per unit' }).click();
  await expect(page.getByTestId('debt-tile')).toHaveText(after.toFixed(1));
  await expect(page.getByTestId('debt-self-note')).toBeVisible();
  const modelOnly = modelUnits.filter((u) => !selfUnits.includes(u));
  expect(modelOnly.length).toBeGreaterThan(0);
  const rowOf = (name: string) => page.locator('table.debt tbody tr').filter({ has: page.getByRole('link', { name, exact: true }) });
  for (const name of selfUnits) {
    await expect(rowOf(name).locator('.badge.measured')).toBeVisible();
    await expect(rowOf(name).locator('.badge.self-checked')).toBeVisible();
  }
  for (const name of modelOnly) {
    await expect(rowOf(name).locator('.badge.measured')).toBeVisible();
    await expect(rowOf(name).locator('.badge.self-checked')).toHaveCount(0);
  }

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
  await page.getByLabel(/Written and graded by the model/).check();
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
  await page.getByLabel(/Written and graded by the model/).check();
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

test('a quiz on a unit that names only directories gets code from the top files under them', async ({ page }) => {
  // React/Vite frontend lists only `frontend`, with no walkthrough or orientation: before the tree
  // fallback its question was written with no code. Now the page lists the directory through
  // /api/tree (the fake GitHub: `git ls-tree` of the local clone), sends the top files as
  // references, the function fetches their first lines, and the fake gateway cites them.
  const csp = watchCsp(page);
  const trees: string[] = [];
  page.on('request', (r) => {
    if (new URL(r.url()).pathname === '/api/tree') trees.push(r.url());
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Continue with Google' }).click();
  await page.goto('/#/settings');
  await page.getByLabel(/The HintWise key/).check();
  for (const sitting of [1, 2]) {
    await page.goto('/#/r/1/quiz');
    // Chosen once, kept by this browser for the second sitting.
    if (sitting === 1) await page.getByLabel(/Written and graded by the model/).check();
    await expect(page.getByLabel(/Written and graded by the model/)).toBeChecked();
    await page.getByLabel('Questions').selectOption('1');
    await page.getByLabel('Unit').selectOption('frontend-spa');
    const written = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/questions');
    await page.getByRole('button', { name: 'Start' }).click();
    const res = await written;
    const sent = res.request().postDataJSON() as { units: { id: string; refs: { path: string; start: number; end: number }[] }[] };
    expect(sent.units.map((u) => u.id)).toEqual(['frontend-spa']);
    expect(sent.units[0].refs.map((r) => `${r.path}:${r.start}-${r.end}`)).toEqual(['frontend/src/AdminPage.jsx:1-60', 'frontend/src/App.jsx:1-60', 'frontend/src/WarmthMeter.jsx:1-60', 'frontend/src/SharedConversation.jsx:1-60']);
    const { questions } = (await res.json()) as { questions: { evidence: string[] }[] };
    expect(questions[0].evidence).toEqual(['frontend/src/AdminPage.jsx', 'frontend/src/App.jsx']);
    await expect(page.getByText('[1/1] React/Vite frontend')).toBeVisible();
    await page.getByLabel('Your answer').fill(`It is the browser app: chat, the warmth meter and the admin page (sitting ${sitting}).`);
    await page.getByRole('button', { name: 'Answer and grade' }).click();
    await expect(page.getByText('1 correct (one grading call).')).toBeVisible();
    const line = page.getByTestId('evidence');
    await expect(line).toHaveText('In the code: frontend/src/AdminPage.jsx, frontend/src/App.jsx');
    await expect(line.getByRole('link').first()).toHaveAttribute('href', 'https://github.com/pisanuw/digitalme/blob/f93102b121f1da9055c148e8eccde275ccf18236/frontend/src/AdminPage.jsx');
  }
  // One listing per directory per session: the second sitting asked GitHub nothing.
  expect(trees).toHaveLength(1);
  expect(new URL(trees[0]).searchParams.getAll('path')).toEqual(['frontend']);
  expect(csp).toEqual([]);
});
