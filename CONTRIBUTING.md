# Contributing

The [Definition of Ready and of Done](https://github.com/candlestack-tech/.github/blob/main/CONTRIBUTING.md#definition-of-ready)
and the issue forms (Task, Bug, Spike) are shared by every repository of the organization. This
file adds what is specific to this one.

## Branches and pull requests

- `main` is protected. Every change goes through a pull request and is squash-merged once it
  is reviewed and its checks are green.
- The branch must be up to date with `main` to merge: when `main` moved, use "Update branch" on the
  pull request and wait for the checks again. Without it, a pull request that was green before
  another one merged can turn `main` red.
- Branch from `main` and name the branch `<area>/<topic>`, e.g. `backend/candles`.
  Keep pull requests small and about one thing.
- The pull request title becomes the commit on `main`: imperative, sentence case, no trailing
  period, at most 72 characters, e.g. "Add the candles endpoint". The `pr-hygiene` check fails
  on a title or branch name that breaks these rules (edit the title and it runs again) and
  warns when a pull request changes more than 1000 lines.
- The description closes its issue: "Closes #N" (the `linked-issue` check fails without it).
- No AI attribution in commits or pull requests: no co-author trailers of AI tools,
  "Generated with ..." lines or session links. The `no-ai-signs` check fails on them.
- Using Claude Code is fine: `CLAUDE.md` gives it the project's rules and
  `.claude/settings.json` turns its commit and pull request attribution off. Keep personal
  settings in `.claude/settings.local.json` (ignored).
- Once per clone, install the git hooks: `uvx pre-commit install`. On every commit they fix
  ruff findings and formatting, trailing whitespace, missing final newlines and shell script
  formatting (shfmt), and stop merge conflict markers, files over 1 MB, secrets (gitleaks),
  typos, `.editorconfig` violations, Markdown lint, shellcheck and hadolint findings, workflow
  errors (actionlint, zizmor), TODOs without an issue and commented-out code, and AI
  attribution in the commit message (`.pre-commit-config.yaml`). A hook that fixed files stops
  the commit: review the changes, `git add` them and commit again. Run them on every file
  with `uvx pre-commit run --all-files`; the `Pre-commit` job in CI does the same.

## License of contributions

The project is licensed under the PolyForm Noncommercial License 1.0.0 (`LICENSE`), and Arsen
Labovich grants commercial licenses for it. By opening a pull request you license your
contribution under the MIT License (`LICENSE-MIT`), so it can ship under both. You keep the
copyright of what you wrote.

## Merging

What the ruleset of `main` enforces:

- one approving review; a new push dismisses earlier approvals;
- an approval from @ArsenLabovich when the pull request changes a path listed in
  `.github/CODEOWNERS` (infrastructure, CI, the toolchain, the dependency set, the shared
  editor and Claude Code settings, the backtest engine and its reference data);
- every review thread resolved;
- the required checks `no-ai-signs / No AI signs`, `linked-issue / Linked issue`,
  `pr-hygiene / Title and branch` and `ci` (below) green;
- squash merging only (merge commits and rebase merging are off in the repository settings
  too);
- no force pushes to `main` and no deleting it.

The squash commit takes the pull request title and number, e.g. "Add the candles endpoint
(#12)", and an empty message (repository settings).

## Checks

All four are required to merge:

| Check | What it runs |
| --- | --- |
| `no-ai-signs / No AI signs` | commit messages, authors and the pull request text |
| `linked-issue / Linked issue` | the pull request description closes an issue ("Closes #N"); the tech lead and Dependabot are exempt |
| `pr-hygiene / Title and branch` | the title (starts with a capital letter and an imperative verb, no `type:` prefix, no trailing period, at most 72 characters) and the branch name (`<area>/<topic>`); Dependabot's titles are exempt. `pr-hygiene / Size` only warns, above 1000 changed lines (lockfiles, fixtures and generated snapshots not counted) |
| `ci` | `Pre-commit` (the git hooks on every file, ruff aside), the backend jobs `lint` (ruff, ty, import-linter, the root `compose.yaml` validated and its dev image built), `unit` (no network: pytest-socket), `integration` (the unit and integration tests with Redis and Postgres, at least 95% branch coverage, and on a pull request at least 90% of the changed lines covered, diff-cover) and `e2e` (the built image, then scanned by Trivy, and the worker's image built and scanned); `frontend` (in `frontend/`: `npm run lint`, `npm test`, `npm run build`, and `src/api/schema.d.ts` regenerated from `docs/openapi.json` with `npm run api:types` must not change); `api` (breaking changes to the API, see below); `infra` (the deploy script's tests, the compose files of `infra/` validated, the server agent's ruff and tests, the edge Caddyfile and the tunnel ingress rules validated, the frontend and server agent images built and scanned by Trivy; see [infra/README.md](infra/README.md#tests)) |

The backend jobs run only when `backend/`, `docs/openapi.json`, a `compose*.yaml` file in the
repository root (`compose.yaml`) or `.github/workflows/ci.yml` changed, and `infra` only when
`infra/`, `frontend/`, `.github/` or `backend/uv.lock` (the agent is linted with the backend's
ruff) changed, and `frontend` only when `frontend/`, `docs/openapi.json` or
`.github/workflows/ci.yml` changed; `api` runs on pull requests that change `docs/openapi.json`.
`ci` passes when they are skipped. `Pre-commit` always runs.

The Trivy scans fail on a HIGH or CRITICAL vulnerability in an image that has a fix: usually
a newer base image or dependency fixes it. One that cannot be fixed yet goes to
`.trivyignore.yaml` with a date to look again, and only with the tech lead's approval.

## Backend

Needs [uv](https://docs.astral.sh/uv/) and Docker. The uv release line is `required-version`
in `backend/pyproject.toml` (CI and the image use the same line), and uv refuses to run outside
it. Get a matching release with `uv self update <version>`, e.g. the lower bound of that range
(uv from the standalone installer; otherwise through the tool that installed it).

The commands are POSIX shell (macOS Terminal, Linux, Git Bash on Windows). In Windows
PowerShell 5.1, chain commands with `;` instead of `&&` and set a variable before the command:
`$env:E2E_PORT='18001'; uv run pytest -m e2e` (it stays set in that window). From `backend/`:

```sh
uv sync                                               # virtualenv with the dev tools and the worker's Keras, JAX and onnxruntime
uv run ruff check . && uv run ruff format --check .   # lint and format
uv run ty check                                       # types
uv run lint-imports                                   # module boundaries
uv run pytest                                         # unit tests (-n auto: in parallel)
uv run pytest -m slow                                 # speed and memory checks, by hand only (CI skips them)
docker compose up -d redis postgres                   # Redis and Postgres of ../compose.yaml on 127.0.0.1 ...
uv run pytest -m integration                          # ... Redis DB 15 and the database candlestack_test, which the tests empty; runs serially
uv run pytest -m e2e                                  # builds and runs the image with compose on 127.0.0.1:18000 (E2E_PORT to change)
```

- `REDIS_URL` points the integration tests at another Redis; they flush the database they
  get, so never give them one whose data you need. `DATABASE_URL` does the same for Postgres:
  the tests create the database when it is missing, migrate it down to nothing and up again and
  empty its tables before each test.
- The Postgres schema changes only through Alembic migrations in `migrations/`: after changing
  a model, `uv run alembic revision --autogenerate -m "<what changes>"` (with the Postgres of
  `../compose.yaml` running and migrated: `uv run alembic upgrade head`), then read and fix the
  generated file. An integration test fails when the migrations and the models differ.
- Coverage of the unit and integration tests (with Redis and Postgres running):
  `uv run pytest --cov -m "not e2e and not slow"` fails under 95% (branch coverage included);
  CI also wants at least 90% of the lines a pull request changes covered
  (`uv run pytest --cov --cov-report=xml -m "not e2e and not slow" && uv run diff-cover
  coverage.xml --compare-branch=origin/main --fail-under=90`).
- Unit tests cannot open network sockets (pytest-socket); only the integration and e2e
  tests may.
- The market data endpoints (instruments, candles, `/api/health/sources`) are specified in
  [docs/data.md](docs/data.md). With `compose.yaml` running (`docker compose up --build` in the
  repository root), try them in the API reference at http://localhost:8000/api/v1/docs (test
  request panel of each endpoint); crypto needs no keys, US stocks need `ALPACA_KEY_ID` and
  `ALPACA_SECRET_KEY` in `.env` (see the [README](README.md#alpaca-keys-for-us-stocks)).
- Tests mirror the modules: `tests/unit/<module>/` and `tests/integration/<module>/`; tests
  of the whole app (lifespan, API reference, OpenAPI snapshot) sit in `tests/unit/`.
- Unit tests fake the `DataService`; integration tests mock the sources with respx. The e2e
  stack replaces Binance and Alpaca with `tests/e2e/mock_sources.py`, which serves the
  recorded responses listed in `tests/fixtures/README.md` under the sources' paths, so no test
  needs the network or keys.
- The API contract is committed in `docs/openapi.json` and a unit test fails when it is stale.
  After changing the API, regenerate it (in a POSIX shell such as Git Bash) and commit it with
  the change: `uv run python -m candlestack.openapi > ../docs/openapi.json`.
- The CI job `api` compares that file with the one on `main` (oasdiff) and lists every change
  in its summary. It fails on a change that breaks existing clients: a removed or renamed
  field, parameter or endpoint, a new required parameter, a narrower type, a new value in a
  response enum. Prefer a change that keeps them working (a new optional field or parameter, a
  new endpoint). A break meant on purpose is agreed on in the pull request: the job prints one
  line per change, and those lines go to `docs/api-breaking-changes.txt`.
- Modules import each other only through their package root
  (`from candlestack.core import ProblemError`), and `candlestack.core` imports no other module.
  The backtest modules `engine`, `signals` and `metrics` import no other module, not each
  other and no server library, and no module imports `cli` (the `candlestack-bt` command).
  `lint-imports` checks all of these rules.
- `ruff check` enforces more than style: complexity (McCabe 8, at most 8 branches, 30
  statements, 6 returns and 6 arguments, 5 of them positional, per function; a flag is a
  keyword-only argument), type annotations in `src/`, no unused arguments (prefix one a
  signature requires with `_`), no commented-out code, no `print`, no relative imports, and
  the format of docstrings. Docstrings are optional; one that is written follows the Google
  style (a summary sentence, then `Args:`, `Returns:`, `Raises:` sections where they help).
  A rule broken on purpose gets `# noqa: <code>` with the reason next to it.
- `ty check` also wants the type arguments of generics (`list[str]`, not `list`) and
  `@override` on a method that overrides one of its base class.
- Add dependencies with `uv add <package>` (`--dev` for tools) and commit `uv.lock`.
- Settings are environment variables, documented in `.env.example`.

## Errors and logs

The errors, traces, profiles and logs of prod and stage go to Sentry (https://candlestack.sentry.io;
the tech lead grants access). What it records is in
[docs/architecture.md](docs/architecture.md#observability). In code:

- What the client should see is a `ProblemError` (4xx, or 502/503 when a source fails); what
  the service works around is `logger.warning`. Neither becomes a Sentry issue.
- What needs a developer is `logger.exception(...)` in the `except` block (or `logger.error`):
  it becomes a Sentry issue, as does any exception nobody catches.
- Never swallow an exception (`except Exception: pass`), and never put keys, tokens or personal
  data in a log message or its `extra`.
- Only `candlestack.core` imports `sentry_sdk`; `lint-imports` checks it.
- Without `SENTRY_DSN` (local runs, tests) nothing is sent. To check that errors
  reach Sentry, open https://stage.candlestack.tech/api/debug/sentry-error (stage only): it
  fails on purpose, and the error shows up in Sentry under the environment `stage`.
