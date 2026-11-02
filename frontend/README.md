# CandleStack frontend

React + TypeScript app built with Vite, styled with Tailwind CSS and shadcn/ui. The stack and
why: [docs/frontend.md](../docs/frontend.md).

## Run locally

Needs Node.js 20.19 or newer (`node -v`).

```sh
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The dev server forwards `/api/*` to the prod API
(https://app.candlestack.tech), so no local backend and no Alpaca keys are needed.

When `main` uses API changes that prod does not have yet, point the dev server at a backend
that has them with `API_TARGET`, for example the local one from the repository root
(`docker compose up`, see the root README). Set it in the shell or in the repository's `.env`
(see `.env.example`):

```sh
API_TARGET=http://localhost:8000 npm run dev
```

| Command | What |
| --- | --- |
| `npm run dev` | dev server with hot reload |
| `npm run build` | type-check and build into `dist/` |
| `npm test` | unit tests and page tests (Vitest; the page tests in jsdom) |
| `npm run e2e` | end-to-end smoke tests in Chromium against the local stack (Playwright, see below) |
| `npm run lint` | oxlint (type-aware), Prettier check and knip (unused files, exports and dependencies) |
| `npm run format` | format every file with Prettier |
| `npm run api:types` | regenerate `src/api/schema.d.ts` from `docs/openapi.json` after an API change |

Run `npm run build`, `npm run lint` and `npm test` before every push.

## End-to-end tests

`e2e/*.e2e.ts` open the app in a real browser: the home and sign-in pages, then, signed in as
the dev user, the instrument list (search, filter, pages) and the chart of BTCUSDT. They run
against the local stack, not prod, and CI does not run them yet.

```sh
docker compose up -d                 # from the repository root; crypto needs no keys
npx playwright install chromium      # once
npm run e2e
```

`npm run e2e` starts the dev server itself (or reuses one on port 5173) with `API_TARGET`
defaulting to http://localhost:8000; set `API_TARGET` for another backend. A failed run leaves
its traces in `test-results/` (`npx playwright show-trace <trace.zip>`).
