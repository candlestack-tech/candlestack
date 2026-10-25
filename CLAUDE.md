# CLAUDE.md

Monorepo of CandleStack (STU FEI team project): the FastAPI backend in `backend/`, the web app
in `frontend/`, and the VM setup in `infra/`. Before changing code, read
[CONTRIBUTING.md](CONTRIBUTING.md) (workflow, checks, commands) and
[docs/architecture.md](docs/architecture.md); [docs/data.md](docs/data.md) is the market data
contract.

## Rules

- Everything in English: code, comments, docs, commit messages, pull requests.
- Branch from `main` as `<area>/<topic>`; one small pull request per topic. Its title becomes
  the squash commit: imperative, sentence case, no trailing period.
- No AI attribution anywhere: no `Co-Authored-By` trailers of AI tools, no "Generated with ..."
  lines, no session links in commits or pull requests. The `no-ai-signs` check fails on them;
  `.claude/settings.json` already turns Claude Code's attribution off.
- Do only what the task asks. No drive-by refactors, renames or new dependencies.
- Modules import each other only through their package root
  (`from candlestack.core import ProblemError`), and `candlestack.core` imports no other module.
  The backtest modules `engine`, `signals` and `metrics`, and `preprocessing`, import no other
  module, not each other and no server library; no module imports `cli`. A new module gets its entry in the
  import-linter contracts in `backend/pyproject.toml`.
- The API is a contract: a change to it updates `docs/openapi.json`
  (`uv run python -m candlestack.openapi > ../docs/openapi.json`) and `docs/data.md` or
  `docs/architecture.md` in the same pull request. Keep existing clients working; the CI job
  `api` fails on a breaking change, and only the user decides to accept one in
  `docs/api-breaking-changes.txt`.
- Every change comes with tests at the lowest level that proves it: unit by default,
  integration for Redis, Postgres or the sources (mocked with respx), e2e only for behaviour
  over HTTP. Tests never use the network or real keys; warnings fail the run.
- `infra/`, `.github/`, the Dockerfiles, the toolchain pins and the other paths in
  `.github/CODEOWNERS` belong to the tech lead: a change there needs their approval to merge,
  so propose it in the pull request description first.
- The backtest engine (`candlestack.engine`) is the tech lead's area: extend it through
  `signals` or `metrics`, not by changing the loop.
- Dependencies only with `uv add <package>` (`--dev` for tools); commit `uv.lock` with them.
- Settings are environment variables documented in `.env.example`. Never commit `.env` files,
  keys or tokens.
- Errors: `ProblemError` for what the client should see, `logger.warning` for what the service
  works around, `logger.exception` for what needs a developer (it becomes a Sentry issue).
  Never swallow an exception or log keys, tokens or personal data.
- Match the surrounding code: naming, short comments that say why, the style of the tests next
  to yours.

## Verify

The git hooks (`uvx pre-commit install`, once per clone) fix formatting and stop secrets, typos,
shell, Dockerfile, workflow and Markdown findings and AI attribution on every commit; CI's
`Pre-commit` job runs them on every file, as `uvx pre-commit run --all-files` does locally. From
`backend/`, before every push:

```sh
uv run ruff check . && uv run ruff format --check .
uv run ty check
uv run lint-imports
uv run pytest                                   # unit tests
uv run pytest --cov -m "not e2e and not slow"   # unit + integration, coverage >= 95%;
                                                # needs `docker compose up -d redis postgres`
                                                # from the repository root
```
