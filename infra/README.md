# infra

Everything needed to run CandleStack on a single VM: one edge proxy in front of the
prod and stage environments.

```text
Internet -> Cloudflare (TLS) -> Tunnel -> cloudflared on the VM -> edge Caddy -> environment
```

The VM has no open inbound ports. `app`, `stage`, `ssh` and `*` under `candlestack.tech`
are proxied CNAMEs to the tunnel, so moving to another server does not touch DNS; see
[Replace the VM](#replace-the-vm) for the swap itself.

## Environments

| Environment | URL | Deployed when | Approval |
| --- | --- | --- | --- |
| prod | https://app.candlestack.tech | a `v*` tag is pushed on a commit of `main` | tech lead (GitHub environment `production`) |
| stage | https://stage.candlestack.tech | every push to `main` | none |

Every environment is one compose project (`env/compose.yaml`). The edge Caddy sends `/api/*`
of the environment's hostname to its backend and everything else to its frontend, and
compresses the responses (zstd or gzip) on the way to Cloudflare.

| Container | Image | Reached as | Memory limit |
| --- | --- | --- | --- |
| `backend` | `ghcr.io/candlestack-tech/candlestack/backend` (built from `backend/`) | `<env>-backend:8000` on the edge network (below) | `384m` |
| `frontend` | `ghcr.io/candlestack-tech/candlestack/frontend` (built from `frontend/`: the app) | `<env>-frontend:8080` on the edge network (below) | `32m` |
| `redis` | `redis:8-alpine`, pinned by digest | `redis:6379` on the environment's own network only | see below |
| `postgres` | `postgres:18-alpine`, pinned by digest | `postgres:5432` on the environment's own network only | `128m` |
| `migrate` | the backend image, `alembic upgrade head` | runs once per deploy, before the backend starts | `160m` |
| `backup` | `prodrigestivill/postgres-backup-local:18-alpine`, pinned by digest | nothing reaches it | `32m` |
| `models-backup` | `offen/docker-volume-backup`, pinned by digest | nothing reaches it, no network | `32m` |
| `worker` | `ghcr.io/candlestack-tech/candlestack/worker` (the stage `worker` of `backend/Dockerfile`) ([Worker](#worker)) | nothing reaches it; it reaches only `queue`, on the internal network `<env>_jobs` | `1g`, no swap, one CPU |
| `queue` | `redis:8-alpine`, pinned by digest | `queue:6379` on `<env>_jobs` only | `32m` |

The frontend image holds the app, built with Node in the image's build stage and served by its
Caddy (`frontend/Caddyfile`), the same in every environment. A path that is no file gets the
app's `index.html`, so a direct link such as `/instrument/crypto:BTCUSDT?tf=1h` opens the app;
the hashed files under `/assets/` are cached for a year, the rest is revalidated on every load.
To run the image locally (no `/api/*` there: the edge routes it),
`docker build -t candlestack-frontend frontend` and
`docker run --rm -p 8080:8080 candlestack-frontend` serve the app on http://localhost:8080. For
development, the dev server talks to the prod API instead, with no local backend and no Alpaca
keys: [frontend/README.md](../frontend/README.md).

Redis is a cache for the backend: no persistence, least recently used keys are evicted at
`maxmemory`, and losing it only costs refetch time.

| Environment | Redis `maxmemory` | Container limit |
| --- | --- | --- |
| prod | `256mb` | `288m` |
| stage | `256mb` | `288m` |

Postgres keeps what users create (accounts, sessions, settings, the models' rows) in the volume
`<env>_postgres`; the backend keeps the files of the uploaded models in the volume
`<env>_models` (`/data/models`, the only path it writes to, served only through the API). The backend image carries the migrations (`backend/migrations`): every deploy
starts with the `migrate` service, which brings the database to them and exits, and the backend
starts only once it succeeded, so a failing migration fails the deploy before the new backend
serves anything. Postgres is sized for a few connections (`max_connections` 20: the backend's
pool of at most 10, the migration, the backup) with `shared_buffers` 32 MB.

All containers of an environment run with a read-only root filesystem, no Linux capabilities
and `no-new-privileges`. Postgres runs as the image's `postgres` user, which owns its volume;
the backend as `65534`, which owns the models volume (the image's `/data/models`); the backup
services as root, which owns their volumes, but also without any capability.

### Memory

The VM has 4 GB of memory (3.8 GB that the system sees) and a 2 GB swap file. The limits are
ceilings, not reservations: together they stay below the memory, and what runs stays far below
them. After the resize to 4 GB (2026-10-25, the worker not deployed yet), the VM used about
1.05 GB, with 2.78 GB available and 4 MB in swap.

| Container | Limit per environment | Measured |
| --- | --- | --- |
| `backend` | `384m` | about 120 MB idle, 150 MB after uploads; a check of an upload adds a child process of about 45 MB for under a second, one at a time |
| `frontend` | `32m` | small: Caddy serving static files |
| `redis` | `288m` (`maxmemory` 256 MB) | grows with the cache, up to `maxmemory` |
| `postgres` | `128m` | about 30 MB with the backend's pool connected |
| `backup`, `models-backup` | `32m` each | about 2 MB between their nightly runs; the models archive took under 32 MB for 300 MB of files |
| `migrate` | `160m` | runs only during a deploy |
| `worker` | `1g`, no swap | about 85 MB idle (the loop); a job adds a process with Keras and JAX (below) |
| `queue` | `32m` (`maxmemory` 16 MB) | a few MB idle, a few kilobytes per job |

The limits of one environment add up to 896 MB without `migrate`, 1.79 GB for prod and stage,
on top of the edge Caddy, cloudflared, the server agent (`2 x 64m`) and the system. Besides
Redis' cache, what runs is about 200 MB per environment; the cache grows up to `maxmemory`,
so the two environments stay within the memory with room for the system and a check. The
backend's 384 MB hold the app (about 150 MB at its peak so far) and the check's child, which
limits itself to 192 MB of data (`RLIMIT_DATA`; a real model's check takes about 45 MB): about
40 MB stay free even when both are at their limits.

With the worker in both environments (since 2026-10-27), each one's limits add up to 1.95 GB
(the 896 MB above, the worker's 1 GB and the queue's 32 MB), 3.9 GB for the two: just over the
3.8 GB, before the edge, cloudflared, the agent and the system. The limits are ceilings: what
runs is far less, and a real job stays near half of the worker's limit (below):

| | each environment |
| --- | --- |
| idle, cache empty | about 290 MB (the worker's loop 85 MB, the queue a few MB) |
| idle, cache full | about 550 MB |
| a job running, cache full | about 1.05 GB (a 25 x 28 model over 50000 candles, about 500 MB) |
| a job at the worker's limit | up to 1.49 GB |

Measured on the VM after v0.8.0 (prod without the worker yet, stage with it idle): 1.41 GB used,
2.42 GB available. Both environments running a real job at once take about 2.1 GB, which leaves
about 1.7 GB for the edge, cloudflared, the agent, the system and the page cache. Only two jobs
at the worker's limit together (3 GB) would push the other containers' idle pages into the 2 GB
swap file; each environment runs one job at a time, and stage's are the team's. The
worker has no swap (`memswap_limit` equal to `mem_limit`): a model over its limit fails its job
(`stopped`) instead of pushing the other containers into the 2 GB swap file. What goes to swap
under pressure are the other containers' idle pages, the caches' first. If stage's jobs make
prod slow, lower stage's Redis (`redis_memory` in `vm/candlestack-deploy`, 256 MB now) first.

What a job takes, measured with the worker image locally (Docker on WSL2; the container's
`memory.peak`, which counts the page cache of Keras' and JAX' libraries too):

| Job | Peak of the worker | Under `1g` |
| --- | --- | --- |
| `check`, a 25 x 28 model of the research repository (a 5 MB file) | 276 MB | `done` |
| `predict`, the raw-candles test model (25 x 5), 50000 candles | 303 MB | `done` |
| `predict`, the 25 x 28 model, 20000 / 30000 / 40000 candles | 396 / 436 / 487 MB | `done` |
| `predict`, the 25 x 28 model, 50000 candles | 602 MB | `done` (measured under `768m`; under the earlier `512m` `stopped`: the kernel killed the job's process, the loop went on) |

The same jobs with `env/compose.yaml` itself (stage, the profile `worker`, at the earlier
`512m`) ended the same way. A peak at the limit without a kill is the page cache, which the
kernel gives back first.

Those jobs ran before the worker built the input in parts. Since then the job's process builds
and predicts the model's input a part of at most 32 MB at a time
([docs/architecture.md](../docs/architecture.md#worker)), where it held all of it at once.
Measured again with the worker image locally (2026-10-26), on the 25 x 28 model with its Renko
input (`renko-triples@1`, `last: current`): the job's process alone, its peak RSS and its own
memory (`RssAnon`, without the libraries' pages). The container's `memory.peak` says less here:
it counts the libraries' pages only when no earlier container read them.

| Candles | Before: peak RSS / own memory | With the parts |
| --- | --- | --- |
| 1000 | 386 / 193 MB | 383 / 189 MB |
| 20000 | 463 / 275 MB | 476 / 287 MB |
| 50000 | 622 / 431 MB | 488 to 511 / 296 to 318 MB |
| 100000 | 822 / 628 MB | 534 / 347 MB |

With 50000 candles the job is `done` under a limit of `512m` and of `384m` (no swap); before
the parts it was killed under `384m`. The predictions are the same to the bit. The same model
on 50000 candles of `candles@1` took 623 / 426 MB before and 500 / 305 MB with the parts.

The job's process imports Keras and JAX and loads the model (about 190 MB of its own memory,
385 MB with the libraries' pages), then builds the input's rows (28 floats per candle for that
model) and the samples a part at a time: 32 MB at most whatever the period, where all of them
at once took 25 x 28 float32 per candle (134 MB for 50000 candles). What still grows with the
period are the rows, the frames they are built from and the predictions, about 1 MB per 1000
candles. So at `1g` a model of that size runs on the 50000 candles a backtest may load
(`CANDLES_MAX`) with about 500 MB to spare, and a model with a larger input per candle needs
more only for its rows, not for its samples (an estimate, not measured).

The jobs' files (the volume `<env>_jobs`) are a tmpfs of at most 64 MB: they take memory, not
disk, about 2 MB of candles and 0.5 MB of predictions for a `predict` of 50000 candles. The
backend removes a job's files once it has the result, the worker those older than an hour (a
backend that stopped waiting), and they are gone when no container mounts the volume. A model
cannot fill the VM's disk through them. The worker image takes 1.45 GB of the VM's disk, 987 MB
of it the dependencies, pulled again only when `uv.lock` changes; `up` removes the old images
after a deploy.

#### CPU

The VM is a t3.medium: 2 vCPUs with burstable CPU credits, in the credit mode `standard`
(`vm/aws-create.sh`); the resize from a t3.small added memory, not CPU. It earns 24 credits
an hour (one credit is one vCPU at 100% for a minute; the baseline is 20% of each vCPU, 0.4
vCPU together) and keeps at most 576. Once the balance is spent, the whole VM, prod included,
runs at that baseline until it earns credits again: slower, never an extra cost (the mode
`unlimited` would bill the vCPU hours above the baseline instead).

The worker runs one job at a time on at most one vCPU (`cpus: 1`): JAX' threads would take both,
and the backend and Postgres keep the other. Measured locally, a job ran at that limit
throughout: about 3 s of CPU for a `check` or a `predict` of 50000 candles of the raw-candles
model, 4.2 s for 40000 candles of the 25 x 28 model, most of it the process' start and Keras'
import; the idle loop takes 0.4% of a vCPU (it asks the queue twice a second). On the VM's
slower vCPUs that is about 0.1 to 0.15 credits per job (an estimate), so a few dozen jobs an
hour cost a few of the 24 credits earned. The most a job can take is its time limit, 120 s of
one vCPU: 2 credits. Jobs back to back at that limit spend 60 credits an hour, so a full
balance lasts about 16 hours of them, less what prod and stage spend anyway. One job at a time
is the right cap on this VM: a second one would need another 1 GB and share the same vCPU.
Once the worker runs, watch the balance (`CPUCreditBalance` in CloudWatch).

### Backups

The `backup` service runs `pg_dump` every night at 00:00 UTC into the volume `<env>_backups`,
as `last/candlestack-<date>-<time>.sql.gz` with a link `last/candlestack-latest.sql.gz`, and
keeps the dumps of the last 7 days (`daily/candlestack-<date>.sql.gz`). The backups live on the
VM's disk: they cover a broken migration or deleted data, not the loss of the VM. To take one
now (SSM shell on the VM): `sudo docker exec prod-backup-1 /backup.sh`.

To restore one, e.g. prod from a given night (stage: `stage-` instead of `prod-`):

```sh
sudo docker stop prod-backend-1
sudo docker exec prod-postgres-1 dropdb --username candlestack candlestack
sudo docker exec prod-postgres-1 createdb --username candlestack candlestack
sudo docker exec prod-backup-1 cat /backups/daily/candlestack-<yyyymmdd>.sql.gz | gunzip |
  sudo docker exec -i prod-postgres-1 psql -v ON_ERROR_STOP=1 --username candlestack --dbname candlestack
sudo docker start prod-backend-1
```

`sudo docker exec prod-backup-1 ls /backups/daily` lists the dumps. A dump has the schema of
its night: after restoring one older than the last migration, the next deploy migrates it
again.

The `models-backup` service archives the files of the uploaded models (the volume
`<env>_models`, mounted read-only, without the uploads in progress) every night at 00:30 UTC
into the volume `<env>_model_backups`, as `models-<date>T<time>.tar.gz`, and keeps the
archives of the last 7 days. It runs half an hour after the database dump, so a dump and the
archive of the same night belong together: restore both from one night, or a model's row may
point to no file (its file is gone) or a file to no row (an orphan, which does no harm). To
take an archive now: `sudo docker exec prod-models-backup-1 backup`; to list them:
`sudo docker exec prod-models-backup-1 ls /archive`.

To restore the files of a given night (after the database of the same night, the backend
still stopped), with the Postgres image the environment already runs (pinned by digest, it has
a shell and tar):

```sh
sudo docker stop prod-backend-1
image=$(sudo docker inspect --format '{{.Config.Image}}' prod-postgres-1)
sudo docker run --rm --entrypoint sh -v prod_models:/data/models \
  -v prod_model_backups:/archive:ro "$image" -c 'rm -rf /data/models/* \
  && tar -xzf /archive/models-<yyyy-mm-dd>T00-30-00.tar.gz -C /data/models --strip-components 2 \
  && chown -R 65534:65534 /data/models'
sudo docker start prod-backend-1
```

Disk: the archives of 7 nights take up to 7 times the models volume (model files hardly
compress), and while an archive is built, `<env>_model_backups_work` holds one more copy. With
the quota of 100 MB per user, 20 users with full storage need 2 GB for the files, 14 GB for the
archives and 2 GB while one is built, per environment: check `df -h /var/lib/docker` before
admitting more users.

The Docker networks:

| Network | Who is on it |
| --- | --- |
| `edge` | the edge Caddy, the backend and frontend of prod and stage, the server agent (`vm-agent`) |
| `<env>_default` | the backend, Redis, Postgres, `migrate` and `backup` of that environment (`models-backup` is on no network) |
| `<env>_jobs` (internal) | the backend, the worker and its queue (the profile `worker`): no way out |
| `agent_docker` (internal) | the server agent and its Docker socket proxy |

Every push to `main` builds the backend, worker, frontend and agent images once, and a release
builds nothing. The images that keep
running on the VM (Redis, the edge Caddy, the agent's Docker socket proxy) are pinned in the
compose files, and Dependabot proposes their updates; `edge` recreates the edge Caddy when its
version changes.

stage and https://ops.candlestack.tech (the team status page) are behind Cloudflare Access: members of the `candlestack-tech` GitHub organization sign in with GitHub. prod is
public. Any new subdomain is team-only by default (Access application `*.candlestack.tech`).

### Worker

The worker runs the models users upload, one job at a time, for the backend
([docs/architecture.md](../docs/architecture.md#worker)). `env/compose.yaml` defines it with
its job queue (`worker`, `queue`) in the profile `worker`, and the backend already on the
network `<env>_jobs` (internal: nothing on it reaches the internet) with the volume
`<env>_jobs` for the jobs' files. The worker sees the models volume read-only, runs as `65534`
with a read-only root filesystem (a tmpfs `/tmp` of 256 MB for Keras and the copy of a model),
no capabilities (it starts as root to change to `65534`, see the queue's password below),
`no-new-privileges`, an init, at most 512 processes and threads, 1 GB without swap and one CPU
([Memory](#memory), [CPU](#cpu)). Its job process is the first the kernel stops at that limit, so a model that needs more fails its job and the worker goes on.
The queue is a Redis of its own, so the worker reaches none of the cache's keys: `noeviction`
and 16 MB, no persistence. The worker has no `SENTRY_DSN`, as it could not reach Sentry: the
backend logs a job that failed with a bug of the worker (`internal`) as an error, and the
worker's log (`sudo docker logs stage-worker-1`) has the traceback under the job's ID.

**The queue's password.** The queue requires `QUEUE_PASSWORD` (`requirepass`; the secret of the
environment `staging`, see the table in
[How a deployment reaches the VM](#how-a-deployment-reaches-the-vm)), and the backend's and the
worker's `JOBS_REDIS_URL` carry it (`redis://:<password>@queue:6379/0`); its health check
authenticates with it and wants `PONG`. A model that broke out of Keras' safe mode shares the
network and the user of the worker's loop, so the password must be out of its reach, not only
out of its environment: a process reads the environment of every process of its user in
`/proc/<pid>/environ`, and the container's init and the loop have the password there. So the
worker starts as root (`user: "0:0"`) with no capability but `SETUID` and `SETGID`: the init
stays root, and the loop, once it read its settings, becomes `65534` for good and makes itself
not dumpable (`worker/service.py`, `drop_privileges`), so its `/proc` files belong to root
whatever the host's `fs.suid_dumpable`. Every job's process runs as `65534` without
capabilities, with an environment that has no Redis URL, and reads neither the init's nor the
loop's environment or memory. Without `QUEUE_PASSWORD` (empty, as until the secret is set) the
queue runs without a password, as before it had one, and every stage deploy says so in its log
(`no QUEUE_PASSWORD: the job queue of stage runs without a password`); a deploy never fails on
it. CI's `infra` job starts the queue of `env/compose.yaml` with and without one and checks what
it answers. To change the password, set the secret and re-run the stage deploy: the queue, the
backend and the worker start again with the new one, and the jobs queued then are lost (they
end as timeouts at the backend).

| Environment | Worker and queue |
| --- | --- |
| prod | on since v0.8.1: every release starts them with the worker image stage ran for its commit |
| stage | on: every stage deploy starts them with the worker image of its commit |

Every push to `main` builds the stage `worker` of `backend/Dockerfile` (`build.yml` with the
input `target`) next to the backend, as `ghcr.io/candlestack-tech/candlestack/worker`, and
`stage.yml` passes it to `up stage` as a third image
([How a deployment reaches the VM](#how-a-deployment-reaches-the-vm)). `vm/candlestack-deploy`
checks it (a GHCR digest reference) and pulls it like the other two, and sets
`COMPOSE_PROFILES=worker` and `WORKER_IMAGE=<worker@digest>` (`runs_worker`). `up prod` takes
the worker's image from the stage record's line of the release's commit, like the other two,
and a release tags it with the version too. CI builds and scans the image (job `e2e`), and
`security-scan.yml` scans the ones prod and stage run every Monday (`worker-prod`,
`worker-stage`).

An `up stage` without a worker image, as a re-run of a stage workflow run from before the
worker sends it, and an `up prod` of a commit stage ran without one (a line of three fields),
run the environment without the worker and its queue: the script removes them
(`compose rm --stop --force worker queue`), since compose leaves the services of a profile it
was not given running, and the worker of an earlier deploy would go on next to a backend it was
not built with. The next stage deploy starts them again.

The status page lists them under each environment as `worker` (with its deploy's version) and
`queue`: the server agent reports every container (`prod-worker-1`, `stage-queue-1`).

### Test backtests

New accounts start without backtests, and users cannot make any yet. So that the Backtests
page and the Dashboard have something to show, the tech lead's account on stage gets the 15
backtests of the app's mockup from a command in the backend image. The account must exist (sign
in once first). On the VM (SSM shell):

```sh
sudo docker exec stage-backend-1 python -m candlestack.experiments.seed --email <email>
```

It prints how many it added and their numbers. Running it again replaces only the backtests it
added before (they are marked `seeded`), never the account's other backtests, whose numbers it
continues after. With `APP_ENV` `prod` it refuses to run (exit code 1): prod never gets test
data. Locally, with the Postgres of `compose.yaml` running and migrated, from `backend/`:
`uv run python -m candlestack.experiments.seed --email <email>` (or `docker compose exec backend
python -m candlestack.experiments.seed --email <email>` from the repository root).

## Monitoring

https://ops.candlestack.tech is a Cloudflare Worker in its own repository,
[candlestack-tech/ops](https://github.com/candlestack-tech/ops), so it keeps working and
records the outage when this VM is down. Every minute it checks prod and stage from outside and
reads `https://vm.candlestack.tech/api/snapshot`, served by the server agent (`agent/`): host
CPU, memory and disk, and the containers. The agent keeps no history
and holds no secrets; its JSON is a contract with the ops repository (schema 2).

The backend of prod and stage also reports to Sentry (organization `candlestack`, EU): errors,
traces, profiles and logs. An uptime monitor there e-mails the tech lead when prod's
`/api/health` fails 3 times in a row. What is sent and when it alerts:
[docs/architecture.md](../docs/architecture.md#observability).

Each commit of `main` is built once. A release does not rebuild: it deploys the exact images
(same digests) that stage last ran for the tagged commit, started with that commit's own
`infra/env/compose.yaml` rather than whatever main's checkout has by then (`edge` keeps
resetting it to origin/main on every stage deploy). The VM takes those images from its stage
record (below), not from the registry: any workflow run allowed to write packages, including
one from a pull request's branch, could move a registry tag.

To release: `git tag v0.2.0 <commit on main> && git push origin v0.2.0`, then approve the
deployment in the Actions tab. Only a commit that stage has deployed can be released; any other
fails before prod changes. After every deployment the workflow waits for `/api/health` to
report the new commit or version and runs `.github/scripts/smoke.sh` against the environment;
prod and stage then record the release in Sentry (`.github/scripts/sentry-release.sh`, which
only warns when it fails):

| Check | What it expects |
| --- | --- |
| `health` | `/api/health` answers 200 with `status` `ok` |
| `openapi`, `docs` | the OpenAPI schema, and the API reference page that loads Scalar |
| `frontend` | the page at `/` |
| `access` | stage only: `/api/health` without the Access token gets Access (a redirect to its login, or 401/403), not the app |
| `sources` | `/api/health/sources`: Binance and Alpaca reachable |
| `search`, `instruments`, `candles` | the instrument list (with and without `q`), the detail of `crypto:BTCUSDT` and `stock:AAPL` and their 1h candles of 2024-06-03 answer 401 `not-signed-in`: the market data is for signed-in accounts only, and the smoke test has no session |
| `icon` | the icon of `stock:AAPL` answers 200 with an image without a session |

The stage deployment also reloads the edge Caddy, which serves prod too, so right after that it
checks that https://app.candlestack.tech/api/health reports `ok` and the page at `/` answers
200 (without the Access token: prod is public).

## Security headers and image scans

The edge Caddy adds to every response HSTS (a year; no subdomains or preload, since
`candlestack.tech` itself is GitHub Pages), `X-Frame-Options: DENY`, `nosniff`,
`Referrer-Policy: strict-origin-when-cross-origin` and a `Permissions-Policy` without camera,
microphone and location, and drops the `Via` header. A Content-Security-Policy runs in
report-only mode: browsers report what it would block to the Sentry project `frontend`
(environments `prod`, `stage`), and once the reports show nothing the pages need, it
becomes enforcing. The smoke test checks these headers after every deploy.

Trivy scans the images for vulnerabilities. In CI the backend and worker images (job `e2e`) and
the frontend and agent images (job `infra`) fail on a HIGH or CRITICAL one that has a fix,
except those `.trivyignore.yaml` accepts until a date. Every Monday `security-scan.yml` scans
the images prod and stage run and the third-party ones the compose files pin, and every
finding, accepted ones included, shows up in the repository's Security tab. Trivy comes from its release archive,
checked against a pinned SHA-256 (`.github/actions/trivy`), not from an action or an image tag:
its release channels were compromised in March 2026.

## How a deployment reaches the VM

GitHub Actions connects over SSH to `ssh.candlestack.tech` through the tunnel. That hostname
is a Cloudflare Access application that only lets in the service token `github-actions-deploy`
(environments `production` and `staging`). Behind it, the `deploy` user has one key per
environment, and each key is forced to run `vm/candlestack-deploy` for its own scope only (for
example, the stage key cannot touch prod).

```text
up stage <version> <backend image@digest> <frontend image@digest> [<worker image@digest>]
up prod <release tag>
```

Every `up stage` adds a line `<version> <backend@digest> <frontend@digest> <worker@digest>` to
the stage record, `/home/deploy/stage-images` on the VM, once its containers run. `up prod`
takes no images: it finds the commit of the release tag and runs the images of the last
`up stage main-<commit>` in the record, or fails when there is none. Only the stage key writes
the record, and only the workflow of `main` holds that key. Every `up` prints
`images <backend@digest> <frontend@digest>` at the end, followed by `<worker@digest>` when it
started the worker; the release tags the images prod runs with the version in the
registry.

The lines from before the worker have three fields, and so does the line of an `up stage`
without a worker image: the record needs no migration. The first stage deploy with the worker
adds the first line of four; `up prod` reads both kinds and runs only the backend and frontend
of either ([Worker](#worker)).

To roll back stage, re-run the `deploy` job of the stage workflow run of the commit to go back
to: its `edge` step resets the VM's checkout to main first, so it runs main's deploy script and
compose file with that run's images, and adds its line to the record. A run from before the
worker passes no worker image, so stage runs without the worker until the next deploy. To roll
back prod, release a commit that stage ran before (a new tag on it): `up prod` takes its images
from the record and its compose file from the tag, so prod gets exactly what it ran then.
Neither goes back past a migration: the older image's `migrate` does not know the database's
revision and fails the deploy.

`up` reads its stdin: the first line is a GHCR token used once for the pull, then `KEY=VALUE`
lines with the environment's secrets. Only `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY`,
`LOGO_DEV_KEY`, `SENTRY_DSN`, `POSTGRES_PASSWORD`, the four `OAUTH_` keys, `BETA_ALLOWLIST` and
`QUEUE_PASSWORD` are accepted (at most 256 printable characters, the beta list 4096, no spaces
or quotes; the database and the queue's passwords only the characters the table below names). The script hands them to
`docker compose` in its environment only: it never writes them to a file or prints them (Docker
itself keeps them in the container's configuration, as with any container environment variable).
The workflows send them with the `env-secrets` input of `.github/actions/deploy`. After `up` the
script removes the images no container uses any more (every backend build adds a
source layer of about 150 KB, and a dependency layer of about 250 MB when `uv.lock` changes).
The other commands are `edge` and `agent <image@digest>` (stage key); the header of
`vm/candlestack-deploy` lists which key may run what.

| Secret or variable | Where | What |
| --- | --- | --- |
| `DEPLOY_SSH_KEY` | environments `production`, `staging` | private deploy key of that scope |
| `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY` | environments `production`, `staging` | Alpaca paper account keys for the backend (US stocks); each has its own account |
| `LOGO_DEV_KEY` | environments `production`, `staging` | logo.dev publishable key for the instrument icons; one free account for both |
| `SENTRY_DSN` | environments `production`, `staging` | where the backend sends errors, traces, profiles and logs (Sentry project `backend`) |
| `POSTGRES_PASSWORD` | environments `production`, `staging` | password of the environment's Postgres user `candlestack`, 16 to 256 letters, digits or `. _ ~ -` (it goes into the database URL as it is), e.g. `openssl rand -hex 32`; set when its database is created (the first deploy), so changing it later needs `ALTER ROLE` in the database too |
| `OAUTH_GITHUB_CLIENT_ID`, `OAUTH_GITHUB_CLIENT_SECRET` | environments `production`, `staging` | the environment's GitHub OAuth app, callback `https://<host>/api/v1/auth/github/callback` |
| `OAUTH_GOOGLE_CLIENT_ID`, `OAUTH_GOOGLE_CLIENT_SECRET` | environments `production`, `staging` | the environment's Google OAuth client, callback `https://<host>/api/v1/auth/google/callback` |
| `BETA_ALLOWLIST` | environments `production`, `staging` | comma-separated emails that may sign in with GitHub or Google; empty lets nobody in |
| `QUEUE_PASSWORD` | environment `staging` | password of the worker's job queue ([Worker](#worker)), 16 to 256 letters, digits or `. _ ~ -` (it goes into the queue's URL as it is), e.g. `openssl rand -hex 32`; empty or unset, the queue runs without a password |
| `SENTRY_AUTH_TOKEN` | environments `production`, `staging` | Sentry organization token (scope `org:ci`) that records each release |
| `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET` | environments `production`, `staging` | Access service token `github-actions-deploy` |
| `DEPLOY_KNOWN_HOSTS` | repository variable | SSH host key of the VM |

## Files

| Path | What it is |
| --- | --- |
| `vm/cloud-init.yaml` | Provider-neutral bootstrap: Docker, swap, cloudflared, edge, deploy user |
| `vm/aws-create.sh` | Creates the VM on AWS EC2 (t3.medium, no inbound, SSM shell access) |
| `vm/candlestack-deploy` | The only command the deploy keys can run |
| `vm/test-candlestack-deploy.sh` | Tests of `candlestack-deploy` (see [Tests](#tests)) |
| `vm/deploy_authorized_keys` | Public deploy keys with their scopes |
| `cloudflared/config.yml` | Tunnel ingress: SSH for deployments, everything else to the edge |
| `edge/` | Caddy that routes each hostname to its environment |
| `env/compose.yaml` | One environment (prod or stage): backend, frontend, Redis, Postgres with its migration and nightly backup, the nightly backup of the model files; the worker and its queue in the profile `worker` |
| `agent/` | Server agent for ops: host metrics and containers as JSON, read-only Docker proxy |
| `github/` | Snapshot of the organization's GitHub settings: rulesets, environments, Actions policy, security, project |
| `cloudflare/` | Snapshot of the Cloudflare settings: zone, DNS, Access, tunnel, Workers |
| `sentry/` | Snapshot of the Sentry settings: privacy, alerts, the uptime monitor, the GitHub integration |

The images of an environment are built from `backend/` and `frontend/` at the repository root.

## Tests

The CI `infra` job runs these, besides validating the compose files, the Caddyfiles and the
tunnel ingress rules; the `Pre-commit` job runs shfmt and shellcheck on every shell script,
hadolint on the Dockerfiles and actionlint on the workflows. Locally, from the repository root:

```sh
infra/vm/test-candlestack-deploy.sh    # bash with GNU tools (Linux, WSL), or in a container:
docker run --rm -v "$PWD:/repo:ro" -w /repo ubuntu:24.04 infra/vm/test-candlestack-deploy.sh
docker run --rm -v "$PWD/infra/agent:/agent:ro" -w /agent python:3.13-alpine python -m unittest -v
cd backend && uv run ruff check ../infra/agent && uv run ruff format --check ../infra/agent
```

- `vm/test-candlestack-deploy.sh` runs the real `vm/candlestack-deploy` as sshd runs it for each
  key (the scope from `vm/deploy_authorized_keys`, the command in `SSH_ORIGINAL_COMMAND`, the
  token and secrets on stdin) with fake `docker` and `git` that record their calls. It checks
  which key may run what, that malformed images, versions and secrets are refused before
  anything runs, that prod takes only a release tag on main's history and starts
  with that tag's compose file and the images stage last ran for its commit, never the worker
  (from lines of three or four fields), that stage starts the worker and its queue with its
  third image and removes them without one, that only a successful `up stage` adds to the
  stage record (four fields, or three without a worker image), that no token or secret is
  printed (the database password and the OAuth secrets included), and the Docker commands of
  every allowed command. It touches nothing outside a temporary directory.
- `agent/test_agent.py` (standard library `unittest`, like the agent) pins the schema-2
  snapshot that ops reads, the order of its containers and the HTTP answers (`starting`, the
  snapshot, `stale`), with a stub Docker API and fixture files for `/proc`. The agent is linted
  with the backend's ruff release and `agent/ruff.toml`.

## Create the VM on AWS

Requires the AWS CLI logged in (`aws login --profile candlestack`) and the tunnel
credentials file from `cloudflared tunnel create` (never commit it).

```sh
infra/vm/aws-create.sh ~/.cloudflared/<tunnel-id>.json main
```

Shell on the VM: `aws ssm start-session --profile candlestack --region eu-north-1 --target <instance-id>`
(needs the AWS Session Manager plugin), or EC2 console > the instance > Connect > Session Manager.

The account is on AWS's Paid plan, with a monthly spend limit of $26 above the credits (early
cost controls on); about $158 of credits were left on 2026-10-25. The t3.medium costs about
$0.0432 an hour in eu-north-1, about $32 a month, plus the 25 GB gp3 disk.

## Replace the VM

Both VMs would run cloudflared on the same tunnel, so until the old one stops, Cloudflare
balances `ssh.candlestack.tech` and all web traffic between them: deploys can reach the wrong
machine or fail the host-key check. cloud-init only starts the edge Caddy, so every environment
needs a fresh deploy afterwards.

1. Boot the new VM (above), with the same tunnel credentials file as the old one.
2. Before deploying anything to the new VM, stop the old VM's tunnel (SSM shell:
   `sudo systemctl stop cloudflared`) or shut the old VM down.
3. Read the new VM's SSH host key (SSM shell: `cat /etc/ssh/ssh_host_ed25519_key.pub`) and
   replace the `DEPLOY_KNOWN_HOSTS` repository variable (Settings > Secrets and variables >
   Actions > Variables) with `ssh.candlestack.tech` followed by the key's type and base64
   fields, in the format the variable already holds.
4. Copy the stage record, without which prod cannot be redeployed: its lines from the old VM
   (SSM: `sudo cat /home/deploy/stage-images`) into the same file on the new one (SSM:
   `sudo -u deploy tee /home/deploy/stage-images`, paste, Ctrl-D). If the old VM is gone, re-run
   the stage workflow run of the released commit before the release run in the next step, and
   the latest stage run after it.
5. Take the databases along, as the deploys below start empty ones: on the old VM, take a
   backup of prod and of stage (`sudo docker exec prod-backup-1 /backup.sh`) and copy the two
   `last/candlestack-latest.sql.gz` files to the new VM; once step 6 deployed prod and stage
   there, restore each as in [Backups](#backups).
6. Redeploy every environment, since nothing but the edge Caddy is running yet:
   - stage: `stage.yml` has no manual trigger, so re-run its latest workflow run, or push to
     `main`; this also deploys the server agent.
   - prod: re-run the latest release workflow run and approve the deployment again.
7. Two things no deploy repeats, on this VM or the next one, because cloud-init only did them
   once at boot: after a change to `infra/cloudflared/config.yml`, restart cloudflared on the
   VM (SSM: `sudo systemctl restart cloudflared`); after a change to
   `infra/vm/deploy_authorized_keys`, reinstall it for the deploy user by hand (SSM:
   `sudo install -o deploy -g deploy -m 600 /opt/candlestack/infra/vm/deploy_authorized_keys
   /home/deploy/.ssh/authorized_keys`). cloudflared itself is also never upgraded after boot.
8. Once the new VM is confirmed healthy, terminate the old one.
