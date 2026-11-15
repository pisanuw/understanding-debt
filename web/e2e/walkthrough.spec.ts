import { expect, test } from '@playwright/test';

// Stub of the main-flow walkthrough. It runs against `vite preview` built
// without Supabase env (playwright.config.ts), so the app uses the in-memory
// repository and the test never reaches the network. Extend it with the
// app's main flow as milestones land.
test('the app shell renders in preview mode', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('h1')).toBeVisible();
  await expect(page.getByLabel('Notes')).toContainText('Preview mode');
});
