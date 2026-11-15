import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import { apiPlugin } from './dev/api-plugin';
import { SECURITY_HEADERS, contentSecurityPolicy } from './dev/csp';

export default defineConfig({
  base: './',
  plugins: [apiPlugin(fileURLToPath(new URL('./fixtures', import.meta.url)))],
  build: {
    outDir: 'dist',
    sourcemap: true,
    target: 'es2022',
  },
  // The preview sends the live site's headers (netlify.toml), so the walkthrough runs under them.
  preview: { headers: { 'Content-Security-Policy': contentSecurityPolicy(), ...SECURITY_HEADERS } },
});
