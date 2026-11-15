import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { defineConfig } from '@playwright/test';

// The walkthrough runs against `vite preview` built without Supabase env, so the app uses the
// fixture rows (fixtures/*.json) and the fake sign-in, and the dev server's /api runs the
// Netlify functions with their fakes (dev/api-plugin.ts). The fake GitHub reads excerpts from a
// local clone: UDEBT_FAKE_REPOS, by default ../../digitalme beside this repository.
const local = fileURLToPath(new URL('../../digitalme', import.meta.url));
const repos = process.env.UDEBT_FAKE_REPOS ?? (existsSync(local) ? JSON.stringify({ 'pisanuw/digitalme': local }) : '{}');

export default defineConfig({
  testDir: 'e2e',
  timeout: 60_000,
  workers: 1,
  use: { baseURL: 'http://localhost:4173' },
  webServer: {
    command: 'npm run build && npm run preview -- --port 4173 --strictPort',
    url: 'http://localhost:4173',
    reuseExistingServer: false,
    timeout: 120_000,
    env: { UDEBT_FAKE_REPOS: repos, VITE_SUPABASE_URL: '', VITE_SUPABASE_ANON_KEY: '' },
  },
});
