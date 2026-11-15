/**
 * Serves the Netlify functions (netlify/functions/lib/handlers.ts) at /api on the Vite dev
 * and preview servers, and the preview's fixture rows at /__udebt/fixture.json, so
 * `npm run dev`, `vite preview` and the Playwright walkthrough exercise the same function
 * code the live site runs. Without Supabase env it turns on the functions' fakes
 * (UDEBT_FAKES=1: the preview token, the fake gateway, GitHub read from a local clone named
 * by UDEBT_FAKE_REPOS). Never part of the build.
 */
import { readFileSync, readdirSync } from 'node:fs';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { join } from 'node:path';
import type { Connect, Plugin } from 'vite';
import { handleExcerpt, handleGrade, handleQuestions, handleTree } from '../netlify/functions/lib/handlers';
import type { Fixture } from '../src/data/store';

const ROUTES: Record<string, (req: Request) => Promise<Response>> = {
  '/api/questions': (r) => handleQuestions(r),
  '/api/grade': (r) => handleGrade(r),
  '/api/excerpt': (r) => handleExcerpt(r),
  '/api/tree': (r) => handleTree(r),
};

/** Every fixture file in `dir` merged (fixtures/*.json, then fixtures/local/*.json). */
export function readFixtures(dir: string): Fixture {
  const out: Fixture = { repositories: [], analyses: [] };
  for (const sub of ['', 'local']) {
    let names: string[] = [];
    try {
      names = readdirSync(join(dir, sub)).filter((n) => n.endsWith('.json')).sort();
    } catch {
      continue;
    }
    for (const n of names) {
      const f = JSON.parse(readFileSync(join(dir, sub, n), 'utf8')) as Partial<Fixture>;
      for (const r of f.repositories ?? []) if (!out.repositories.some((x) => x.url === r.url)) out.repositories.push(r);
      out.analyses.push(...(f.analyses ?? []));
    }
  }
  return out;
}

async function toRequest(req: IncomingMessage): Promise<Request> {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  const headers = new Headers();
  for (const [k, v] of Object.entries(req.headers)) if (typeof v === 'string') headers.set(k, v);
  const method = req.method ?? 'GET';
  return new Request(`http://localhost${req.url ?? '/'}`, { method, headers, body: method === 'GET' || method === 'HEAD' ? undefined : Buffer.concat(chunks) });
}

export function apiMiddleware(fixtureDir: string): Connect.NextHandleFunction {
  return (req: IncomingMessage, res: ServerResponse, next: () => void) => {
    const path = (req.url ?? '').split('?')[0].replace(/^.*?(\/(?:api|__udebt)\/)/, '$1');
    if (path === '/__udebt/fixture.json') {
      res.setHeader('content-type', 'application/json');
      res.end(JSON.stringify(readFixtures(fixtureDir)));
      return;
    }
    const route = ROUTES[path];
    if (!route) return next();
    void toRequest(req)
      .then(route)
      .then(async (r) => {
        res.statusCode = r.status;
        r.headers.forEach((v, k) => res.setHeader(k, v));
        res.end(Buffer.from(await r.arrayBuffer()));
      })
      .catch(() => {
        res.statusCode = 500;
        res.end('{"error":"dev server"}');
      });
  };
}

export function apiPlugin(fixtureDir: string): Plugin {
  if (!process.env.SUPABASE_URL && !process.env.VITE_SUPABASE_URL) process.env.UDEBT_FAKES = '1';
  return {
    name: 'udebt-api',
    configureServer(server) {
      server.middlewares.use(apiMiddleware(fixtureDir));
    },
    configurePreviewServer(server) {
      server.middlewares.use(apiMiddleware(fixtureDir));
    },
  };
}
