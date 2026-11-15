# udebt web app (HintWise)

The hosted udebt app at https://udebt.hintwise.org: read a repository's architecture (units,
decisions, the tour, walkthroughs) and climb the quiz ladder until your understanding debt
falls. Built from the HintWise app template (claude-built-apps `platform/templates/app/`,
copied at aeed6d5); the user-facing description is in the repository's README ("Hosted
app"), the deployment in `docs/DEPLOY.md`.

## Layout

| path | what |
|---|---|
| `src/lib/` | the platform's modules, copied from the template unchanged but for the slug: `supabase.ts`, `auth.ts` (Google through Supabase, email through `sign-in-link`), `account.ts` ("Delete my account"), `llm.ts` (gateway or own key), `repository.ts`, `signin-styles.ts`, `google-g-light.svg`; their tests in `test/` |
| `src/core/` | pure TypeScript shared by the page and the functions: the ladder and scoring ported from `udebt/ladder.py` and `udebt/scoring.py` (checked against `test/fixtures/parity.json`, which `tests/test_hosted_parity.py` writes), the spaced review queue ported from `udebt/review.py` (`review.ts`, checked against `test/fixtures/review-parity.json`, which `tests/test_hosted_review_parity.py` writes), the quiz flow, the hosted prompts (their rules come from `udebt/prompts.py` through `prompt-parts.json`), excerpts by reference, request validation and the one-call writing and grading (`assemble.ts`), the tree fallback's rule for a unit that names only directories (`topfiles.ts`, checked against `test/fixtures/topfiles-parity.json`, which `tests/test_hosted_topfiles_parity.py` writes from `udebt/topfiles.py`) |
| `src/data/` | the store: `SupabaseStore` (schema `app_udebt`, the person's token) and `MemoryStore` (fixture rows, for tests and the preview) |
| `src/model/` | the quiz client in the person's mode (`GatewayClient` through `/api`, `byok.ts` with their own key, loaded on demand), the worker client, the mode and key preferences, the page's memory of directory listings for the tree fallback (`tree.ts`, per analysis and directory, this session only) |
| `src/ui/` | the shell, router and screens: sign-in, repositories, specs and units (with walkthroughs), quiz, review (the items due, a sitting through the quiz's flow), debt, settings, add a repository |
| `netlify/functions/` | `/api/questions`, `/api/grade` (the HintWise gateway with the person's token; grading also records the sitting, quiz or review, each row with its review state) `/api/excerpt` (a line range of a file at a sha from raw.githubusercontent.com, never cached) and `/api/tree` (the top files under directories at a sha: one unauthenticated call to GitHub's recursive git trees API per repository and sha, kept in the function's memory, a refusal respected until GitHub's reset; never cached by the browser) |
| `dev/` | the dev and preview servers' `/api` (the same handlers, with fakes when there is no Supabase env) and the Content-Security-Policy that `netlify.toml` and `vite preview` send |
| `fixtures/` | rows from `udebt worker sql --dialect json --public` (digitalme, mapped without a model call); `fixtures/local/` is git-ignored for others |

## Commands

```bash
npm install
npm run dev                  # http://localhost:5173, preview mode: fixture data, fake sign-in and gateway
npm run lint && npm run typecheck && npm run coverage && npm run build   # the gate, coverage >= 85%
npm run e2e                  # Playwright against vite preview with fakes (builds first)
UDEBT_SHOTS=1 npm run e2e    # also screenshots every screen into ../.udebt/dashboard/hosted-*.png
```

Without `VITE_SUPABASE_URL` the app runs in preview: the fake sign-in hands out a preview token
the functions accept only with their fakes on (`UDEBT_FAKES=1`, which the dev server sets and
Netlify ignores), questions and grades come from a fake gateway keyed on the prompt's `TASK:`
marker, and excerpts and tree listings (`git show`, `git ls-tree`) come from a local clone named
by `UDEBT_FAKE_REPOS` (the Playwright config points at `../../digitalme` when it exists). Nothing
leaves the machine.

## Rules kept from the platform

- Sign-in only through the template's modules; "Delete my account" under Sign out (Settings).
- No Anthropic key and no service-role key anywhere: the functions act with the person's token;
  the own key stays in the browser and goes to api.anthropic.com (or, for an analysis, once to
  the worker, never stored).
- The HintWise footer is the exact `footerHtml('udebt')` in `index.html`, outside `#app`, pinned
  by `test/csp.test.ts` (sha256 `dc713ce9...4685c8`); the favicon is the HintWise tag.
- A strict CSP: `style-src` lists the sign-in block's hash and `FOOTER_STYLE_CSP`, no
  `'unsafe-inline'`; `style-src-attr` allows only the two style attributes of Google's G.
