# Architecture

The CandleStack backend is one Python service: a FastAPI application that serves the HTTP API
under `/api`. In this milestone it has five modules: `core` (configuration, logging, errors,
Redis, Postgres and HTTP clients, CSRF protection, health, Sentry), `data` (market data:
instrument catalog and candles from Binance and Alpaca, see [data.md](data.md)), `accounts`
(users, GitHub and Google sign-in, sessions, the beta list, Settings), `ml` (the models
users upload: the upload and its checks, the files, the list) and `experiments` (the stored
backtests: the Backtests page and the Dashboard). Postgres stores what users create,
accounts, sessions, settings, the models and the backtests so far, and a volume the files of the
models; market data is not stored: the sources are the truth and Redis is a cache that can be
lost at any time. The backtest is being built next to them
(`signals`, `engine`, `metrics` and the `candlestack-bt` command in `cli`, see
[Modules](#modules) and the contract in [engine.md](engine.md)); it runs from the command line,
not in the service.

## Runtime

prod and stage run the same containers on one VM. Deployment, hosts, secrets and backups are
described in [infra/README.md](../infra/README.md).

```mermaid
flowchart LR
    browser["Browser or API client"] -->|HTTPS| cf["Cloudflare<br/>TLS, Access (stage)"]
    cf --> tunnel["Cloudflare Tunnel"]
    subgraph vm["VM (no inbound ports)"]
        tunnel --> cloudflared["cloudflared"]
        cloudflared --> caddy["edge Caddy<br/>routes by hostname"]
        subgraph envbox["one environment: prod or stage"]
            caddy -->|"/api/*"| backend["backend<br/>FastAPI on granian :8000"]
            caddy -->|"everything else"| frontend["frontend<br/>static page, Caddy :8080"]
            backend --> redis[("redis<br/>cache only")]
            backend --> postgres[("postgres<br/>users, sessions, settings, models, backtests")]
            backend --> models[("models volume<br/>uploaded model files")]
            modelsbackup["models-backup<br/>nightly archive"] -.->|"read-only"| models
        end
    end
    backend -->|HTTPS| binance["Binance<br/>REST + data.binance.vision"]
    backend -->|HTTPS| alpaca["Alpaca<br/>trading API + market data"]
    backend -->|"HTTPS, sign-in"| oauth["GitHub and Google<br/>OAuth"]
    backend -.->|"HTTPS, prod and stage"| sentry["Sentry (EU)<br/>errors, traces, profiles, logs"]
```

| Container | Image | Notes |
| --- | --- | --- |
| `backend` | `ghcr.io/candlestack-tech/candlestack/backend` | calls Binance and Alpaca; gets the environment's Alpaca keys at deploy time; keeps the uploaded model files in the volume `models` (`/data/models`, its only writable path) |
| `frontend` | `ghcr.io/candlestack-tech/candlestack/frontend` | the web app, static files served by Caddy |
| `redis` | `redis:8-alpine` | private network of the environment, no persistence, `allkeys-lru` |
| `postgres` | `postgres:18-alpine` | private network of the environment, data in a volume; migrated by a one-shot `migrate` container (the backend image, `alembic upgrade head`) before the backend starts; prod and stage dump it every night (`backup`) |
| `models-backup` | `offen/docker-volume-backup` | archives the `models` volume (read-only) every night at 00:30 UTC into the volume `model_backups`, keeps 7 days; no network |

## HTTP surface

| Path | What |
| --- | --- |
| `GET /api/health` | liveness + Redis + Postgres; used by deploys and the ops page; not versioned |
| `GET /api/health/sources` | reachability of Binance and Alpaca (cached 60 s) |
| `GET /api/v1/data/...` | instruments and candles, see [data.md](data.md) |
| `GET /api/v1/auth/{provider}/start?next=<path>` | sign-in with `github` or `google`: redirects to the provider, or to `/signin?error=failed` (provider not configured, too many sign-ins from the address); with `intent=connect` it adds a sign-in method to the signed-in user instead ([Accounts](#accounts)) |
| `GET /api/v1/auth/{provider}/callback` | where the provider returns; sets the session cookie and redirects to `next` (default `/dashboard`), or to `/signin?error=denied` (not on the beta list) or `/signin?error=failed`; a connect returns to Settings instead; not in the API reference |
| `GET /api/v1/auth/denied` | the account this browser was just turned away with (email, provider), for the sign-in page |
| `POST /api/v1/auth/sign-out` | ends this browser's session |
| `POST /api/v1/auth/dev` | signs in as a fixed dev user; only `APP_ENV` `local` and `test`, not in the API reference |
| `GET /api/v1/me` | the signed-in user, with the provider's account (email, GitHub login) of each sign-in method |
| `PATCH /api/v1/me` | changes the display name (1 to 80 characters, surrounding whitespace dropped); an unknown field is a 422 |
| `DELETE /api/v1/me` | deletes the account with everything it owns and ends its sessions |
| `DELETE /api/v1/me/sign-in-methods/{provider}` | removes a sign-in method; `409 last-sign-in-method` for the last one; a later sign-in with that provider and the user's email adds it again |
| `POST /api/v1/auth/sign-out-everywhere` | ends every session of the user, this browser's included |
| `GET /api/v1/me/preferences` | the user's settings (backtest defaults, appearance), defaults included |
| `PUT /api/v1/me/preferences/backtest-defaults` | replaces the backtest defaults: every field, no other (`period_start` before `period_end`), else 422 |
| `PATCH /api/v1/me/preferences/appearance` | changes the appearance settings it is given; an unknown field is a 422 |
| `GET /api/v1/models` | the user's uploaded models, oldest first, and the storage they take (`used_bytes`, `quota_bytes`, `file_max_bytes`) |
| `POST /api/v1/models?name=<file name>` | uploads a model: the body is the file (`application/octet-stream`); checked before it is kept ([Models](#models)); a name the user has already gets a number (`lstm (2).keras`); one upload at a time per user, 100 models at most |
| `PATCH /api/v1/models/{model_id}` | renames a model (the extension stays; `409 model-name-taken`) or stars and unstars it |
| `DELETE /api/v1/models/{model_id}` | deletes a model and its file |
| `GET /api/v1/preprocessing/catalog` | the blocks a model's manifest can name, by step: id, description, the display group of a feature (`Candle`, `Bands`, `Momentum`, `Trend`, `Volume`, `Brick`), warm-up and parameters (type, range, default, `tunable`); public, served by `ml` ([preprocessing.md](preprocessing.md#the-blocks)) |
| `GET /api/v1/backtests` | a page of 10 of the user's backtests: search `q` (number, symbol or model name), `show` (`all`, `starred`, `failed`), `model` (a model's name), `sort` and `order`, `page`; with the total, the pages and what the filter panel shows (the counts of all, starred and failed backtests, the models used) ([Backtests](#backtests)) |
| `PATCH /api/v1/backtests/{backtest_id}` | stars or unstars a backtest |
| `GET /api/v1/dashboard` | everything the Dashboard shows in one response: the counts, the 6 newest backtests, the 5 best finished ones by Sharpe ratio, return, drawdown and win rate, the 4 best starred ones |
| `GET /api/v1/docs` | API reference (Scalar); public on prod |
| `GET /api/v1/openapi.json` | OpenAPI schema; snapshot committed as [openapi.json](openapi.json) |

- Responses are JSON. Pydantic response models serialize themselves; the candles body, up to
  50000 candles, is written with orjson straight from the Polars columns (its model only
  documents it). Errors are RFC 9457 `application/problem+json` with
  `type` `https://candlestack.tech/problems/<slug>`, `title`, `status`, a `detail` that says
  which limit fired and what to do, and extension fields. Request validation errors use slug
  `validation` (422), the framework's own errors `not-found` (404) and `method-not-allowed`
  (405), and an unhandled exception `internal-error` (500), whose `detail` reveals nothing and
  asks to report the response's `X-Request-ID`. The slugs of the data module are listed in
  [data.md](data.md#errors). The accounts module adds `not-signed-in` (401: no session, or it
  ended), `cross-origin-request` (403: a change that does not come from the app's own
  pages) and `last-sign-in-method` (409: removing the account's only sign-in method), and uses
  `not-found` (404) and `rate-limited` (429) too. The ml module adds `model-too-large` (413: a
  file over 50 MB), `storage-full` (409: the file does not fit into the user's 100 MB, with
  `used_bytes` and `quota_bytes`), `unsupported-model-format` (415: not `.keras`, "coming
  soon"), `model-check-failed` (422: a check failed, `check` names it, `opens` or `weights`)
  `model-name-taken` (409: renaming to a name the user has, rarely an upload's numbered name),
  `too-many-models` (409: the user
  has 100 models, with `limit`), `upload-in-progress` (409: another upload of the user runs)
  and `upload-timeout` (408: the file did not arrive within 2 minutes), and uses `not-found`
  (404: no model of the user with the ID, another user's included) and `not-signed-in` (401:
  also the account was deleted while its file uploaded). Each `type` URI opens a page on the
  website that says what the problem means and what the client should do. The experiments
  module adds none: it uses `not-found` (404: no backtest of the user with the ID).
- Every response carries `X-Request-ID` and `Server-Timing: app;dur=<ms>` (the app's time
  until the response started), so response times can be measured from outside.
- Logs: one JSON object per line on stdout (`ts`, `level`, `logger`, `msg`); the access line
  adds `method`, `path`, `status`, `duration_ms`, `request_id` (the `CF-Ray` header, else a
  random hex).

## Accounts

- **Sign-in**: the OAuth round trip with GitHub or Google ([HTTP surface](#http-surface)). The
  beta list admits users: a new account by its email, an account that signed in before by the
  email of its user (that of the user's first sign-in), so a method connected in Settings signs
  in too, and a user whose email leaves the list cannot sign in with any method. A provider's
  account signs in to the user it was added to; one that never signed in joins the user with
  its email, so the email links a user's GitHub and Google into one account, else it makes a
  new user. A user who already signs in with another account of that provider (connected in
  Settings with another email) gets `/signin?error=failed` instead of a second one.
- **Sign-in methods**: `GET /api/v1/auth/{provider}/start?intent=connect&next=<path>`, the
  Connect link of Settings, starts the same round trip for the signed-in user (without a
  session it redirects to `/signin?next=/settings/sign-in`). Being a navigation, it carries no
  `Origin`, so it must come with `Sec-Fetch-Site: same-origin`: a link of another site cannot
  connect the provider's account the browser is signed in to there. Its pending state in Redis
  remembers the session that started it (the token's SHA-256), and the callback adds the
  provider's account to that session's user only while the browser is still signed in with it,
  so a connect started by a session that was signed out since (everywhere, too, after a theft)
  or by another user of the browser fails. The callback adds the account without the beta list
  and without changing the user's email, then returns to `next` (default `/settings/sign-in`).
  An account that signs in to another user returns `/settings/sign-in?error=taken`, one
  connected to this user already just returns, and any failure (cancelled, expired, the
  session ended, a second account of the same provider) returns
  `/settings/sign-in?error=failed`. A connect's state starts with `connect.`, so the
  callback knows where to return even when it failed before its pending state was found. The
  last sign-in method cannot be removed; changes of a user's methods lock the user's row, so
  two removals at once cannot leave none. Removing a method deletes its identity only: signing
  in later with that provider's account adds it again when its email is the user's.
  `GET /api/v1/me` lists each method's account (`sign_in_methods`: provider, email, GitHub
  login), as a connected one may have another email than the user's.
- **Preferences**: table `user_preferences`, one row per user who saved anything (the user ID
  as primary key, deleted with the user), one JSONB document per section of Settings
  (`backtest_defaults`, `appearance`) and `updated_at`. A document holds only what was saved
  and is read through its Pydantic model (`BacktestDefaults`, `Appearance`): no row or a
  missing key reads as the default, a key the model does not know is ignored, so a new setting
  needs no data migration. The backtest defaults are replaced as a whole (the body
  `BacktestDefaultsIn` has every field and no default, so a client cannot reset a setting by
  leaving it out); an appearance change
  is merged into the stored document in one statement (`||`), so changes sent at the same time
  all stay.
- **Deleting an account**: `DELETE /api/v1/me` deletes the user's row in one transaction; the
  foreign keys cascade to identities, sessions, preferences and backtests. A module that keeps
  more data of a user, such as files (models), deletes it through an `AccountDeletionHook`
  (`async (user_id, db) -> AfterDeletion | None`), which `candlestack.main` puts on
  `app.state.account_deletion_hooks`, in two phases. The hooks run in the deletion's
  transaction before the user's row goes and delete the module's rows; one that raises rolls
  the whole deletion back (500). They do not touch storage outside Postgres (files on disk)
  then, as the commit may still fail: each returns what deletes that (`async () -> None`, or
  None), and those run only after the commit. The account is gone by then, so one that raises
  is logged (by the error's type only, never its message) and the others still run: an
  orphaned file at worst, never a user without their files. `accounts` never imports the
  modules that own the data. `ml` registers one: the user's models in the transaction, the
  directory of their files after the commit.
- Every route of the signed-in user that returns user data sends `Cache-Control: no-store`, and
  every change requires the app's own origin.

## Models

- **Upload**: `POST /api/v1/models?name=<file name>` with the file as the body, so the server
  streams it to disk instead of holding a multipart form in memory (the backend has 384 MB).
  Only Keras files (`.keras`) for now: another extension is a 415 before anything is read,
  whose detail says the file is not supported and what is: Keras now, ONNX later, PyTorch,
  TensorFlow and scikit-learn models through ONNX, pickle-based files never. A
  `Content-Length` over 50 MB is a 413 at once. A user uploads one file at a time: a lock in
  Redis (`SET NX EX 300`, released when the upload ends) turns a second upload away with
  `409 upload-in-progress`, so nobody fills the disk with uploads side by side. While Redis
  is down, uploads go on without that lock (a warning in the log): the lock of the user's
  models in Postgres (below) still keeps the quota and the names. Then, still
  before reading anything, a user with 100 models gets `409 too-many-models`, and a file that
  does not fit `409 storage-full`: by its `Content-Length`, or without one when the storage is
  full. The file goes to an incoming file of the storage while it is counted and hashed
  (SHA-256, which later backtests name the model by); the stream stops at 50 MB (413) or after
  2 minutes (`408 upload-timeout`). Then it is checked (below). A file that passed is kept in
  one transaction under an advisory lock of the user (`pg_advisory_xact_lock`), so two uploads
  cannot both pass the quota or take one name: the model count and the quota again, a free name
  (`lstm (2).keras`, the names it may become read in one query, lowercased by Postgres as the
  unique index on `(user_id, lower(name))` compares them; should the index still refuse the
  name, the upload is a `409 model-name-taken`), the row, the file moved to its place, the
  commit (the file goes again when it fails, also when the request was cancelled). An account
  deleted meanwhile (its deletion takes the same lock) fails the insert: `401 not-signed-in`,
  nothing is kept. The incoming files go whatever happens; the ones a crash left are removed
  when the backend starts and every 10 minutes while it runs.
- **The check** (`ml/inspect.py`) reads the file and never loads it: a `.keras` file is a zip of
  `config.json` (the architecture), `metadata.json` (the Keras version) and
  `model.weights.h5` (the weights), and loading it could run code from it. It runs in a child
  process (`ml/sandbox.py`), killed after 20 s; on Linux it limits itself to 192 MB of data,
  15 s of CPU and files of 100 MB, and writes no core dump. The checks of all users run one
  after another: an upload that waits more than 2 minutes for its turn fails `opens` ("Many
  models are being checked right now..."). The parent takes the child's result only in the
  form the table holds (the Keras version, positive counts, shapes of positive sizes), else
  the check failed. A crafted file that crashes or exhausts the
  parsers (zipfile, json, h5py and the HDF5 C library) takes down only the child and fails the
  check; the API process never imports h5py. `opens` fails for a zip whose index declares more
  than 1000 entries, is larger than 1 MB or is zip64 (read from the end record before zipfile
  reads the index), a file that is no zip, holds a path outside it or other files than those
  three and `assets/`, unpacks to more than 200 MB (the weights to more than 100 MB, a
  `config.json` to more than 1 MB), has no Keras version or is no Functional or Sequential
  model. Every serialized object of the architecture must be on the allowlist
  (`ml/allowlist.py`): the built-in classes of Keras 3.15.1 (layers, operations of
  `keras.ops`, initializers, regularizers, constraints, dtype policies, optimizers and their
  schedules, losses, metrics) without the ones that load files, run code given to them or wrap
  another framework's (`Lambda`, `TFSMLayer`, `TorchModuleWrapper`, `JaxLayer`, `FlaxLayer`,
  the lookup layers, the control flow operations), and a `function` only when it is a public
  function of those modules (an activation, a loss, a metric). So `keras.utils.get_file`
  (CVE-2025-8747) and `keras.config.enable_unsafe_deserialization` (CVE-2025-9906) are
  refused. The `*_regularizer` and `*_constraint` objects, which only shape training, may also
  be the user's own registered objects (`Custom>OrthogonalL2Regularizer`), which the worker
  replaces with stubs, but nothing else off the allowlist. The model's own `compile_config`
  (optimizer, loss, metrics) is not checked at all, as the worker removes it before loading:
  models saved with another backend name its optimizer (`keras.src.backend.torch...`), and
  users register their own metrics and metric functions there. The `compile_config` of a model
  nested in the model is checked like the rest. The detail names the layer. `weights` fails
  when the weights do not read as HDF5 (from a copy unpacked to the incoming directory), hold
  a soft or external link or more than 100 000 objects, or the
  layers hold no weights: `parameters` counts the datasets under `layers`, not the optimizer's
  state. The input shape (one input) and the output shape (`Dense`, `Flatten` and layers that
  keep their input's shape) are read from the config when it says them plainly, else null.
  `tests/fixtures/keras/make_allowlist.py` regenerates the allowlist from a Keras release.
- **Loading an accepted file** (the worker, next milestone): the check is static, so the worker
  is the second line. It loads with a pinned Keras release that has the fixes of the advisories
  above (3.15.1 or later, the one the allowlist was generated from). It removes the model's
  `compile_config` from `config.json` (a copy of the file) and loads it with
  `keras.saving.load_model(path, compile=False, safe_mode=True)`, so nothing of the compile
  settings, which the check does not look at, can ever be deserialized or run. It walks
  `config.json` against the same allowlist again before loading, and
  passes `custom_objects` with a no-op stub for every user object under `*_regularizer` and
  `*_constraint` (a regularizer that adds no loss, a constraint that returns its input). It
  runs without network access and with the same kind of limits as the check.
- **Manifest** ([preprocessing.md](preprocessing.md#the-manifest)): a model is kept without
  one and gets it with its own request, `PUT /api/v1/models/{model_id}/manifest` with the
  manifest as the JSON body, which adds it or replaces the one the model had; `GET` on the same
  path reads it back as it was given, without the defaults it left out (`404 no-manifest`
  without one). There is no DELETE: the app replaces a manifest, never removes it. The body is
  at most 1 MB (`413 manifest-too-large`, by its `Content-Length` at once, else where the
  stream passes it). The PUT runs the checks that read the files
  ([preprocessing.md](preprocessing.md#checking-a-model-with-its-manifest), 2 and 3) in the
  request: the form (`parse_manifest`), the catalog, the parameters, the names, the signature
  and the example (`check_manifest`), a text holding the character NUL (JSON takes it, a
  JSONB column cannot keep it), then the signature against the input and output shapes
  the upload's check stored (`check_with_model`). Only a manifest that passed all of them is
  kept, in the JSONB column `models.manifest`, under the lock of the user's models; else the
  answer is `422 manifest-check-failed` with one sentence for each thing wrong in `problems`
  (`check`, `detail`), at most 10, `more_problems` counting the rest and `unknown_blocks`
  naming the blocks the catalog does not have. A large document wrong throughout stays cheap:
  the form caps what a manifest lists (256 features, 32 parameters a block, 8 axes, block ids
  of 64 characters), a list stops at its first wrong item (an example full of NaN is one
  error), and more than 100 errors of the form are counted, not read.
- **States**: each model says its `state`: `needs_setup` without a manifest, `ready` with one
  (it passed every check above), `verified` once `models.verified_at` is set. The worker's
  trial run (#268) will set it when the model gives the output of the manifest's `example`,
  and only while the manifest is still the one it ran with; replacing the manifest clears it.
  Each model also carries `manifest_summary` (the representation, timeframe, session, input
  shape and warm-up), so the Models tiles and the model's page need no second request. The
  column `manifest` is deferred: the queries of the list and of a change read
  `manifest - ARRAY['example', 'about']`, so a list of 100 models never reads their examples.
- **Files**: `ModelStorage` (`ml/storage.py`) is what the service uses: save a finished incoming
  file, delete one, delete a user's, sweep what crashed uploads left. `LocalModelStorage` keeps
  them in `MODEL_STORAGE_DIR` as `<user_id>/<model_id>.keras` and the uploads in progress as
  `.incoming/<random>.part`, on one filesystem, so a move is an atomic rename. On the VM that is
  the volume `models`, archived every night ([infra/README.md](../infra/README.md#backups)). The
  files are served only through the API.
- **Limits**: 50 MB per file, 100 MB and 100 models per user (binary megabytes, as the app
  shows them), one upload at a time, 2 minutes to send a file.
- **Names**: an uploaded file keeps its base name without control and format characters
  (Unicode Cc and Cf: a bidirectional override could make `gpj.exe` read as `exe.jpg`), the part
  before the extension cut to 60 characters; Rename refuses them with a 422.
- Deleting a model deletes its row, then its file; a file that cannot be removed is a warning
  and stays as an orphan. Another user's model is a 404, never a 403. Every refused upload is
  logged at INFO with the user's ID and the problem's slug, never the file's name.
- **Usage**: each model of the list says how many backtests used it (`backtest_count`, failed
  ones included) and the best Sharpe ratio of its finished ones (`best_sharpe`). The backtests
  are `experiments`', which imports `ml`, so `ml` asks a `ModelUsage`
  (`async (db, user_id, model_ids) -> {model_id: (count, best Sharpe)}`) that
  `candlestack.main` puts on `app.state.model_usage`: `experiments.model_usage`, one grouped
  query for the whole list. Without one (`ml.no_usage`) no model was used.

## Backtests

- **Table** `backtests`, one row per run of a user: the user's own number (`#N`: 1, 2, 3 per
  user, unique with the user, given under an advisory lock of the user), the status
  (`finished` or `failed`; `running` comes with the backtest builder, the check constraint
  allows it already), the instruments in the order chosen (JSONB: market as the market data
  API names it, `stock` or `crypto`, and symbol), the representation (`renko`, `kagi`,
  `windows`), the model, the setup (JSONB, the fields of Settings > Backtest defaults), the
  results (total and buy-and-hold return, Sharpe ratio, max drawdown, win rate, trades, how
  long the run took, the engine version), a short equity series, the error of a failed one,
  the star, and when it started and finished. Results are null for a failed backtest.
- **The equity series** (`equity`: `{strategy, benchmark}`, JSONB) draws the small lines of the
  Dashboard's starred backtests: the cumulative return of the strategy and of buy and hold as
  fractions (0 at the start), at most 60 points each, at the same evenly spaced times over the
  test period. `Backtest.set_equity` checks it on the way in (as many points in both, 2 to 60,
  finite numbers); the list neither searches nor sorts by it. It is null for a failed backtest
  and for one stored without it. The seed command draws it as the mockup does (a random walk
  seeded by the run's number in the mockup, ending exactly at the run's returns), so every run
  of the command draws the same lines.
- **Results are fractions** (0.246 is 24.6 %), in the table and the API, as `metrics.stats`
  gives them; the app formats them. The max drawdown is stored as the fall it is, negative
  (`metrics.stats` gives it positive): -0.197.
- **The model** is a foreign key that becomes null when the model is deleted, plus the model's
  name and framework when the backtest ran. A backtest shows the model's current name while the
  model exists (a rename shows in its backtests) and the name it ran with once it is gone (the
  backtest stays, with its results).
- **The list** (`GET /api/v1/backtests`) searches the number (`#12` or `12`), the symbols and
  the model's shown name as substrings in any case (`%` and `_` match themselves), filters by
  show and by a model's name (exact, in any case) and sorts: `default` newest first, or by
  number, model, return, Sharpe ratio, drawdown, trades or `when` (as number), `asc` or
  `desc`. Backtests without the sorted value (failed ones) are last in both directions, ties
  newest first. 10 a page; a page past the last is empty, with the real `pages`. The counts
  (all, starred, failed) and the models used (by count, then name) ignore the search and the
  filters: they are the filter panel's. The models used are one per name in any case, as the
  model filter compares them (spelled as and with the framework of the newest backtest of the
  name), so each count is what its filter returns.
- A stored setup and equity series are read without the limits of a new backtest
  (`BacktestSetupIn`, `EquitySeriesIn`, which check them on the way in), so a limit made
  tighter later never fails the list.
- **The Dashboard** (`GET /api/v1/dashboard`) is one response: the counts, the 6 newest
  backtests (failed ones too), the 5 best finished ones by Sharpe ratio, return, drawdown
  (the smallest fall first) and win rate, and the 4 starred ones with the best Sharpe ratio
  (those without one last) with the total starred. When `running` comes with the builder, the
  newest backtests must leave out the running ones.
- Users cannot make backtests yet: the builder comes later and will fill the same table. Until
  then, `python -m candlestack.experiments.seed --email <email>` gives one account the 15
  backtests of the mockup on stage or local ([infra/README.md](../infra/README.md#test-backtests));
  it refuses `APP_ENV` `prod`, marks its rows (`seeded`) and replaces only those when run again.
- A user's backtests go with the account by the foreign key's cascade (no deletion hook: no
  files). Another user's backtest is a 404.

## Modules

A module is a subpackage of `candlestack` under `backend/src/candlestack/`.

| Module | Status | Responsibility |
| --- | --- | --- |
| `core` | now | settings, logging, problem errors, Redis, Postgres and HTTP clients, CSRF protection, health endpoint |
| `data` | now | instrument catalog, candles, sessions, cache, source adapters, rate limits |
| `accounts` | now | users, GitHub and Google sign-in, sessions, the beta list, Settings (name, sign-in methods, preferences, deleting the account); `CurrentUserDep` for the routes of the signed-in user, `AccountDeletionHook` for what other modules delete with an account |
| `preprocessing` | started | the model's input from its manifest: the manifest's schema and checks, the catalog of blocks, and the input built from candles: representation (candles now; Renko, Kagi later), features, normalization, windows ([preprocessing.md](preprocessing.md)) |
| `ml` | now | the models users upload: the upload and its static check, the file storage, the list, rename, star, delete; running models later, in a worker (below) |
| `signals` | started | turn predictions into signals: align them with the candles, thresholds, long only; later smoothing, holding period, sizing, risk limits |
| `engine` | started | the backtest loop: candles and signals in, trades and an equity series out; `ENGINE_VERSION` names its rules ([engine.md](engine.md)) |
| `metrics` | started | unified metrics and charts of one run |
| `cli` | started | the `candlestack-bt` command: backtests from files on the command line |
| `experiments` | now | the stored backtests: the list of the Backtests page, stars, the Dashboard, the models' usage, the seed command; later assembling `preprocessing`, `ml`, `signals`, `engine` and `metrics` into runs |
| `admin` | later | team-only administration (roles, audit) under `/admin` |

Folders for later modules are created when their work starts, not before; `started` marks
the ones whose folder exists while their code is being written.

## Import rules

- Dependencies point one way: `experiments` -> `ml` -> `accounts` | `data` -> `core`. `core`
  imports no other module. For now `experiments` imports only `ml`, `accounts` and `core`: not
  `data` and not the backtest modules, as it runs no backtest yet.
- The backtest modules `signals`, `engine` and `metrics`, and `preprocessing`, stand alone:
  they import no other module of the project, not each other, and no server library
  (`fastapi`, `starlette`, `scalar_fastapi`, `granian`, `redis`, `httpx`, `sentry_sdk`,
  `pydantic_settings`, `sqlalchemy`, `asyncpg`, `alembic`, `authlib`). They are
  plain functions on Polars frames (Pydantic for their settings and the manifest, NumPy for
  the model's input), put together only by the code that runs them: `cli` now, `experiments`
  and the worker later. The worker runs `preprocessing` without network, so it must not need
  the server's libraries.
- `preprocessing` serves no HTTP itself: `ml` imports it and serves its catalog
  (`GET /api/v1/preprocessing/catalog`), as the manifest belongs to a model and `ml` will check
  it on upload. `experiments` will import it to build a backtest's input.
- No module imports `cli`: it is the entry point of the `candlestack-bt` command
  (`[project.scripts]` in `backend/pyproject.toml`).
- The API part of `ml` reads the uploaded files (zip, JSON, the HDF5 metadata with h5py, in
  the check's child process) and never imports a deep learning framework, so the API process
  never loads one and never runs code from a file. An import-linter `forbidden` contract enforces it: sources `candlestack.ml`,
  `candlestack.main` and `candlestack.preprocessing` (which builds arrays for a model without
  knowing the framework that runs it), forbidden `keras`, `tensorflow`, `torch` and `jax`
  (`include_external_packages = true`). Running models comes later, in a worker: only its
  composition root (a future `candlestack/worker.py`) will import the framework, which goes
  into a `worker` dependency group (or extra) that the API image does not install, and the
  API will enqueue worker tasks by name instead of importing them. The name is `ml`, not
  `models`, which `data/models.py` and the ORM models use.
- Another module is imported only through its package root:
  `from candlestack.data import DataService`, never `from candlestack.data.sources import ...`.
  The package root's `__init__.py` is the module's public API.
- `candlestack.main` (builds the app) and `candlestack.openapi` (schema snapshot) wire the
  modules together and may import any module root except `cli`.
- Checked by import-linter (`uv run lint-imports`, contracts in `backend/pyproject.toml`) in
  the CI `lint` job.

## Stack

| Concern | Choice | Why |
| --- | --- | --- |
| Language | Python 3.13 | the team's language and the ML ecosystem the later modules need |
| Packaging | uv, committed `uv.lock` | fast, reproducible installs (`uv sync --locked`) in dev, CI and images |
| Web | FastAPI, Pydantic v2, pydantic-settings | typed validation and an OpenAPI schema from the same code; typed env config |
| Server | granian | fast Rust ASGI server, one command for prod and `--reload` in dev |
| JSON | orjson | serializes large numeric arrays (up to 50000 candles) quickly |
| Data frames | Polars | fast columnar processing, Arrow-native; cache blobs are Arrow IPC + zstd |
| Cache | Redis with `redis[hiredis]` asyncio | one store for cache blobs, locks and rate-limit counters |
| Database | Postgres 18, SQLAlchemy 2 (asyncio) on asyncpg, Alembic migrations in `backend/migrations` | relational data with constraints; typed models; versioned schema changes that run before each deploy's backend starts |
| Sign-in | Authlib (OAuth 2.0 messages, PKCE) over the app's httpx client | a maintained implementation of the protocol; the requests stay on the client the tests mock with respx |
| Model files | h5py | reads the weights' metadata of an uploaded Keras file (the number of weights) without Keras, in the check's child process only |
| HTTP client | httpx (async), timeouts always set | async calls to the sources; mocked in tests with respx |
| API docs | scalar-fastapi at `/api/v1/docs` | readable reference with request examples; Swagger and ReDoc disabled |
| Time zones | stdlib `zoneinfo` + `tzdata` | DST-correct New York sessions even in slim images without system tz data |
| Lint and format | ruff | one fast tool for linting, import sorting and formatting |
| Types | ty, exact version pinned | fast type checker; pinned so a new release cannot turn CI red by itself |
| Boundaries | import-linter | keeps the module rules above enforced, not just written down |
| Tests | pytest, pytest-xdist, anyio plugin, respx | parallel runs, async tests, no real network in unit and integration tests |
| Errors, traces, profiles, logs | Sentry (`sentry-sdk`), EU region | the team sees what fails or is slow with the code, commit and request behind it; free for a year with the GitHub Student Developer Pack |

## Configuration

Environment variables, read by pydantic-settings without a prefix. All are documented in
[`.env.example`](../.env.example).

| Variable | Default | Meaning |
| --- | --- | --- |
| `APP_ENV` | `local` | `local`, `test`, `stage`, `prod` |
| `APP_VERSION` | `dev` | deployed version (`main-<sha>`, `v0.2.0`) |
| `APP_COMMIT` | `unknown` | commit sha, baked into the image at build time |
| `LOG_LEVEL` | `INFO` | |
| `PUBLIC_URL` | prod `https://app.candlestack.tech`, stage `https://stage.candlestack.tech`, local `http://localhost:5173`, test `http://testserver` | where the app is reached: OAuth callbacks, cookies (`Secure` and `__Host-` over https), the same-origin check |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | |
| `DATABASE_URL` | `postgresql+asyncpg://candlestack:candlestack@127.0.0.1:5432/candlestack` | Postgres (whitespace stripped); the pool holds at most 10 connections |
| `MODEL_STORAGE_DIR` | `var/models` | directory of the uploaded model files, relative to the working directory; `/data/models` (a volume) on the VM; created at startup |
| `OAUTH_GITHUB_CLIENT_ID`, `OAUTH_GITHUB_CLIENT_SECRET` | empty | GitHub OAuth app; without both, GitHub sign-in is off |
| `OAUTH_GOOGLE_CLIENT_ID`, `OAUTH_GOOGLE_CLIENT_SECRET` | empty | Google OAuth client; without both, Google sign-in is off |
| `BETA_ALLOWLIST` | empty | comma-separated emails that may sign in with GitHub or Google, any case; empty lets nobody in |
| `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY` | empty | Alpaca paper account keys (whitespace stripped) |
| `ALPACA_API_URL` | `https://paper-api.alpaca.markets` | assets, calendar, clock |
| `ALPACA_DATA_URL` | `https://data.alpaca.markets` | bars |
| `BINANCE_API_URL` | `https://api.binance.com` | REST |
| `BINANCE_DATA_URL` | `https://data.binance.vision` | public archives |
| `LOGO_DEV_KEY` | empty | logo.dev publishable key for the instrument icons (whitespace stripped); empty: every icon is the fallback |
| `LOGO_DEV_URL` | `https://img.logo.dev` | logo images |
| `CANDLES_MAX` | `50000` | max candles in one candle response |
| `CLIENT_RATE_LIMIT` | `60` | candle and instrument-detail requests per minute per client IP (IPv6: per /64); `0` disables |
| `ALPACA_RATE_LIMIT` | prod `150`, stage `100`, else `60` | our Alpaca request budget per minute |
| `BINANCE_WEIGHT_LIMIT` | `1000` | our Binance REST weight budget per minute per environment |
| `LOGO_DEV_RATE_LIMIT` | prod `50`, else `20` | our logo.dev request budget per minute |
| `STATS_REFRESH` | prod and stage `true`, else `false` | refresh the instruments' 24 h stats and daily mini charts in the background ([data.md](data.md#stats)); without it the list has no stats or mini charts and sorts by symbol |
| `SENTRY_DSN` | empty | Sentry project for errors, traces, profiles and logs; empty sends nothing (set for prod and stage only) |
| `SENTRY_TRACES_SAMPLE_RATE` | prod `0.2`, else `1.0` | share of requests traced in Sentry |

## Observability

| Question | Where |
| --- | --- |
| Are prod and stage up? How loaded is the server? What was deployed or restarted? | https://ops.candlestack.tech, checks from outside every minute |
| What failed, where in the code, since which release? What is slow? | Sentry, organization `candlestack`, project `backend`: https://candlestack.sentry.io |
| Is prod down right now? | Sentry's uptime monitor calls `/api/health` of prod every minute and e-mails the tech lead after 3 failures |

What the backend sends to Sentry (only prod and stage have a DSN), set up in
`core/observability.py`:

- **Errors**: an unhandled exception (the client gets a 500 with `X-Request-ID`) and every
  record logged at ERROR or above. A problem response (4xx, or 502/503 when a source fails) and
  a warning are not errors: they are the service working as designed.
- **Traces**: 20% of prod requests and every stage request, never `/api/health`. Calls to
  Binance, Alpaca and Redis are spans of them.
- **Profiles**: while a traced request runs, the profiler samples the stacks about 100 times a
  second, so Sentry shows which functions take the CPU time (a trace's profile, or Explore >
  Profiles). The plan includes 750 profile hours a month, no pay-as-you-go.
- **Logs**: records from INFO up, the access line of each request included (except health
  checks), each with its `request_id`.
- Every event carries `environment` (`prod`, `stage`), `release` (`APP_VERSION`) and
  `request_id`: the `X-Request-ID` a user reports finds the error.
- Nothing personal or secret: no IP addresses, cookies, request bodies, Cloudflare Access
  logins or emails, no parameters of SQL statements in database errors (`hide_parameters`); the
  Alpaca keys, the DSN, the database URL and the OAuth client secrets,
  codes, verifiers, states and tokens are filtered, in the query of a request too; Redis keys
  hold the sign-in's state and the denial's id only as SHA-256; only our own frames keep their
  local variables, without the ASGI scope. The logs of a sign-in name the provider and why it
  failed, never the account.
- At most 100 error events an hour per process, so an error in a loop cannot use up the
  month's quota (50,000 errors, no pay-as-you-go).

Alerts are e-mails to the tech lead only: a new, regressed or reappearing issue on prod (at
most every 30 minutes), 50 events of one issue in an hour on prod, and prod down. Stage sends
none. Every deploy records its release in Sentry (`.github/scripts/sentry-release.sh`), which
links an issue to its suspect commit.

`GET /api/debug/sentry-error`, on stage only and not in the API reference, fails on purpose
to check that an error reaches Sentry. The Sentry settings are recorded in
[`infra/sentry/`](../infra/sentry/README.md).

## Not in this milestone

- Celery (job queue and worker)
- TensorFlow / Keras
- admin, roles, audit log
- user data upload (no CSV import)
- live data (streaming, websockets)
- Kubernetes, Terraform
- a coverage gate in CI

## Decisions

| Decision | Why |
| --- | --- |
| Modular monolith, one package, boundaries checked by import-linter | one image and one deploy for a small team; the boundaries still hold |
| Market data is not stored; Redis as a disposable cache (no persistence, `allkeys-lru`) | market data comes from the sources on demand; losing Redis costs only refetch time |
| Postgres for what users create, one database per environment, nightly dumps on prod and stage | accounts and later models and backtests must survive restarts and deploys; one small instance per environment keeps stage away from prod's data |
| Sign-in with GitHub or Google only, no passwords | no password storage, reset or verification flows to build and secure; both providers vouch for a verified email, which also links a user's two providers into one account |
| A beta list (`BETA_ALLOWLIST`) decides who signs in, closed when empty | the app is for the team and invited testers while it is built; a turned-away account is named on the sign-in page (from Redis, for 10 minutes, never in a URL) and gets no account |
| Server-side sessions: a random token in an HttpOnly, SameSite=Lax cookie (`__Host-` over https), only its SHA-256 in Postgres, 30 days sliding | a session can be ended at once (sign-out, later admin), a database leak does not leak sessions, and no script can read the cookie |
| Changes on behalf of the user require the app's own origin (`core.require_same_origin`), the OAuth round trip a state bound to the browser (Redis and a cookie) and PKCE | every host under candlestack.tech is the same site to the browser, so SameSite cookies alone do not stop a page on another of them; the state stops login CSRF; PKCE stops a stolen code |
| Preferences as one JSONB document per section of Settings, read through Pydantic models with defaults | a new setting needs no migration of the rows; the models validate both what is saved and what is read |
| Account deletion through hooks that other modules register in `main.py` | `accounts` stays at the bottom of the import graph, yet the account's rows in other modules go with it in one transaction; data outside Postgres (model files) is the hook's to keep consistent with a rollback |
| An upload is checked statically (the zip, its JSON and the HDF5 metadata) in a limited child process, never loaded; only what an allowlist generated from Keras names passes | loading a Keras file can run code from it (a `Lambda` layer is pickled Python, a `function` object any Keras function), and the API process must run no user's code and carry no framework; an allowlist fails closed on what a new Keras release or an attacker adds, where a list of what is known to be bad would not; the parsers of zip and HDF5 meet hostile files, so they run where a crash or a leak costs only a failed check |
| A backtest keeps its model's name and framework of when it ran next to a foreign key that a deleted model sets to null; it shows the model's current name while the model exists | a rename shows in the backtests that used the model, and deleting the model keeps its backtests with their results and a name to show |
| Results in the API as fractions (0.246), the drawdown negative | the numbers `metrics` computes, unrounded; the app formats them for the reader |
| The Dashboard in one request | one round trip and one loading state for the page's six panels; each is a small query on the user's index |
| `ml` gets the models' backtest counts through a `ModelUsage` that `main.py` wires | `ml` stays below `experiments` in the import graph, and the models list still costs one extra query, not one per model |
| Test backtests only from a seed command, refused on prod, that replaces only its own rows | new accounts start empty and prod never gets test data, yet stage and local show the pages with data until the backtest builder exists |
| Model files on a local Docker volume behind a `ModelStorage` interface, served only through the API, archived every night for 7 days | no object storage to run or pay for while the files are few and small; Cloudflare R2 can replace the volume without touching the service |
| Quotas: 50 MB per file, 100 MB and 100 models per user, one upload at a time (Redis), 2 minutes to send it; checked before the file is read (`Content-Length`) and again under a lock of the user | the VM's disk is small and shared by prod and stage; the lock in Redis keeps one user from filling the disk with uploads side by side, the second check stops two uploads from both passing |
| A dev sign-in for `APP_ENV` `local` and `test` only, fixed in code, not a setting | signed-in pages can be built and tested without OAuth keys; no setting can turn it on in stage or prod |
| One source per market, never mixed in one series: Binance spot for crypto, Alpaca SIP for US stocks (15 minutes delayed) | consistent data; both are free |
| Full history fetched lazily on request, catalog cached 24 h and refreshed in the background | no ingestion pipeline and no scheduler to run |
| The instruments' 24 h stats refreshed by one asyncio task in the API process (started in the app's lifespan, every 5-10 minutes, a Redis lock per market), only in prod and stage | a list sorted by volume needs every instrument's numbers before anyone asks; a task in the process needs no worker, queue or cron container, and the lock keeps a second process from fetching them again |
| The list's "Data since" (`available_from`) is read from the first-candle cache in Redis for the page in one `MGET`; up to 5 unknown ones per request are looked up in the background, deduplicated, at most 10 at once per process and a small share of the source's budget per minute, and kept 90 days | the first candle costs one source request per instrument, so a page must never ask the sources for all its rows or wait for them; the first candle never moves, so a value once known serves for months |
| Stocks use the regular session only (09:30-16:00 New York, calendar from Alpaca) | comparable candles; extended-hours data is thin |
| Same timeframes for both markets: `1m 5m 15m 30m 1h 4h 1d` | experiments can switch markets without changing the pipeline |
| UTC epoch seconds everywhere, candle label = open time, `start` inclusive, `end` exclusive | no time zone ambiguity at the API; New York time exists only in session math |
| Gaps are reported, never filled | filled candles would be invented prices |
| Explicit limits instead of silent truncation (50000 candles, per-IP and per-source rate limits) | the client always gets exactly what it asked for or an error saying what to change |
| A fingerprint over every candle set | later experiments can detect that a source changed its data |
| Prod API and docs public (per-IP rate limit); stage behind Cloudflare Access | anyone can try the released API without an account; unreleased builds stay team-only |
| granian instead of uvicorn, ty instead of mypy, Polars instead of pandas | faster tools with the same role |
| Coverage and TDD are team conventions, not CI gates | CI stays fast; test quality is checked in review |
| Security headers at the edge, the Content-Security-Policy in report-only mode first; Trivy on every image in CI and weekly on what runs | the headers protect every page without app code; the policy's reports show what it would break before it blocks anything; the scans catch known vulnerabilities before a merge and after a release |
| Sentry for errors, traces, profiles and logs of prod and stage; ops for uptime and the server | ops keeps working when the VM is down; Sentry shows failures with their code, commit and request, and local runs stay out of its quota |
