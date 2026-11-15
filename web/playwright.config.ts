import { defineConfig } from '@playwright/test';

// The walkthrough runs against `vite preview` built without Supabase env, so
// the app uses the in-memory repository and nothing reaches the network.
export default defineConfig({
  testDir: 'e2e',
  timeout: 30_000,
  use: { baseURL: 'http://localhost:4173' },
  webServer: {
    command: 'npm run build && npm run preview -- --port 4173 --strictPort',
    url: 'http://localhost:4173',
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
