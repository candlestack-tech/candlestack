#!/usr/bin/env bash
# Tests of candlestack-deploy, the forced command of the deploy keys. Runs the real script as
# sshd does (the key's scope from deploy_authorized_keys as its argument, the client's command
# in SSH_ORIGINAL_COMMAND, the GHCR token and the secrets on stdin) with fake docker and git
# on PATH that record their calls, and checks what each key may run and what reaches Docker.
# Needs bash and coreutils; touches nothing outside a temporary directory:
#   infra/vm/test-candlestack-deploy.sh
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Fake docker and git: every call is a line "<tool> <args>". docker also records the token
# of the login (its stdin) and where the login is kept, and for compose the first line of a
# compose file outside the checkout and the variables the deploy script gives it.
# FAKE_DOCKER=fail-up: "compose up" fails.
mkdir "$work/bin"
cat >"$work/bin/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $*" >>"$CALLS"
case $1 in
  login)
    echo "  stdin $(cat)" >>"$CALLS"
    echo "  config $DOCKER_CONFIG" >>"$CALLS"
    ;;
  compose)
    file=$(sed -n 's/.* -f \([^ ]*\) .*/\1/p' <<<"$*")
    if [[ $file != /opt/candlestack/* ]]; then
      echo "  file $(head -n 1 "$file")" >>"$CALLS"
    fi
    env | grep -E '^(ENV_NAME|APP_VERSION|BACKEND_IMAGE|FRONTEND_IMAGE|WORKER_IMAGE|COMPOSE_PROFILES|IMAGE|EDGE_NETWORK|REDIS_[A-Z_]+|POSTGRES_PASSWORD|QUEUE_PASSWORD|ALPACA_[A-Z_]+|LOGO_DEV_KEY|SENTRY_DSN|OAUTH_[A-Z_]+|BETA_ALLOWLIST)=' |
      sort | sed 's/^/  env /' >>"$CALLS"
    [[ $FAKE_DOCKER != fail-up || $* != *" up "* ]]
    ;;
esac
EOF
# The VM's checkout, where the release tag of "up prod" is fine unless FAKE_GIT names what is
# wrong: no-tag (not on origin), no-commit, not-on-main, no-compose (no infra/env/compose.yaml).
# not-shallow: the checkout is a full clone.
cat >"$work/bin/git" <<'EOF'
#!/usr/bin/env bash
echo "git $*" >>"$CALLS"
case "$*" in
  *" fetch --quiet --no-tags origin tag "*) [[ $FAKE_GIT != *no-tag* ]] ;;
  *" rev-parse --verify --quiet refs/tags/"*)
    [[ $FAKE_GIT != *no-commit* ]] && echo 5eed5eed5eed5eed5eed5eed5eed5eed5eed5eed
    ;;
  *" rev-parse --is-shallow-repository")
    if [[ $FAKE_GIT == *not-shallow* ]]; then echo false; else echo true; fi
    ;;
  *" merge-base --is-ancestor "*) [[ $FAKE_GIT != *not-on-main* ]] ;;
  *" show "*":infra/env/compose.yaml")
    [[ $FAKE_GIT != *no-compose* ]] && echo "# infra/env/compose.yaml of the release"
    ;;
esac
EOF
chmod +x "$work/bin/docker" "$work/bin/git"
CALLS=$work/calls
COMMIT=5eed5eed5eed5eed5eed5eed5eed5eed5eed5eed
# The stage record of candlestack-deploy, in the deploy user's home ($work in these tests).
RECORD=$work/stage-images

# image <name>: a GHCR digest reference, as the workflows pass them.
image() {
  printf 'ghcr.io/candlestack-fei-stu/candlestack/%s@sha256:%s' "$1" \
    "$(sha256sum <<<"$1" | cut -c1-64)"
}
BACKEND=$(image backend)
FRONTEND=$(image frontend)
AGENT=$(image agent)
WORKER=$(image worker)
# What prod takes from the stage record; stage also gets the worker's image.
IMAGES="$BACKEND $FRONTEND"
STAGE_IMAGES="$IMAGES $WORKER"
OLD_IMAGES="$(image old-backend) $(image old-frontend)"
OLD_STAGE_IMAGES="$OLD_IMAGES $(image old-worker)"
TOKEN=ghs_TestToken0123456789
KEY_ID=PKTEST0123456789
SECRET='t3st/Secret+0123=~!'
SECRETS="ALPACA_KEY_ID=$KEY_ID
ALPACA_SECRET_KEY=$SECRET"
DSN=https://0123abcd@o1.ingest.de.sentry.io/2
LOGO_KEY=pk_test_logo
DB_PASSWORD=pg_test_password
QUEUE_PW=queue_test_password
OAUTH="OAUTH_GITHUB_CLIENT_ID=Iv1.test
OAUTH_GITHUB_CLIENT_SECRET=gh_test_secret
OAUTH_GOOGLE_CLIENT_ID=g.test.apps
OAUTH_GOOGLE_CLIENT_SECRET=g_test_secret
BETA_ALLOWLIST=ada@example.com,bob@example.com"

tests=0
failures=0

# run <scope> <command> [stdin]: runs the script with only the variables sshd sets (and
# FAKE_GIT, FAKE_DOCKER); sets $status, $output, $temporary (the temporary directories the
# calls name: the Docker config of a login, the directory of a release's compose file) and
# $calls, with those directories as TMP. An empty stdin sends nothing at all.
run() {
  local dir
  : >"$CALLS"
  status=0
  output=$(if [[ -n ${3-} ]]; then printf '%s\n' "$3"; fi |
    env -i HOME="$work" PATH="$work/bin:$PATH" CALLS="$CALLS" FAKE_GIT="${FAKE_GIT-}" \
      FAKE_DOCKER="${FAKE_DOCKER-}" SSH_ORIGINAL_COMMAND="$2" "$here/candlestack-deploy" "$1" 2>&1) ||
    status=$?
  temporary=$(sed -n -e 's/^  config //p' -e 's|^docker compose .* -f \(/[^ ]*\)/compose\.yaml .*|\1|p' \
    "$CALLS" | grep -v '^/opt/candlestack' || true)
  calls=$(<"$CALLS")
  for dir in $temporary; do
    calls=${calls//"$dir"/TMP}
  done
}

failed() {
  failures=$((failures + 1))
  echo "FAIL  $1"
  shift
  printf '      %s\n' "$@"
}

# The script's output never shows the token or a secret value, whatever happened.
quiet() {
  local value
  for value in "$TOKEN" "$KEY_ID" "$SECRET" "$DSN" "$DB_PASSWORD" "$QUEUE_PW" gh_test_secret \
    g_test_secret "${@:2}"; do
    if [[ $output == *"$value"* ]]; then
      failed "$1" "the output shows '$value': $output"
      return 1
    fi
  done
}

# allowed <name> <scope> <command> <stdin> <expected calls>
allowed() {
  tests=$((tests + 1))
  run "$2" "$3" "$4"
  quiet "$1" || return 0
  if [[ $status != 0 ]]; then
    failed "$1" "exit $status: $output"
  elif [[ $calls != "$5" ]]; then
    failed "$1" "calls differ (< expected, > actual):" "$(diff <(echo "$5") <(echo "$calls"))"
  else
    left_behind "$1" || echo "ok    $1"
  fi
}

# left_behind <name>: fails the test when a temporary directory of the script is still there.
left_behind() {
  local dir
  for dir in $temporary; do
    if [[ -e $dir ]]; then
      failed "$1" "the temporary directory $dir is left behind"
      return 0
    fi
  done
  return 1
}

# refused <name> <scope> <command> <stdin> <message> [value never shown]: fails with the
# message before any docker call, and before any git call unless $after_git lists the calls.
refused() {
  tests=$((tests + 1))
  run "$2" "$3" "$4"
  quiet "$1" "${@:6}" || return 0
  if [[ $status == 0 ]]; then
    failed "$1" "allowed, calls: $calls"
  elif [[ $calls != "${after_git-}" ]]; then
    failed "$1" "refused after other calls (< expected, > actual):" \
      "$(diff <(echo "${after_git-}") <(echo "$calls"))"
  elif [[ $output != "candlestack-deploy: $5" ]]; then
    failed "$1" "expected 'candlestack-deploy: $5', got: $output"
  else
    echo "ok    $1"
  fi
}

# release_git <tag> <count>: the first <count> git calls with which "up prod" checks its
# release tag and reads the tag's compose file.
release_git() {
  printf '%s\n' \
    "git -C /opt/candlestack fetch --quiet --no-tags origin tag $1" \
    "git -C /opt/candlestack rev-parse --verify --quiet refs/tags/$1^{commit}" \
    "git -C /opt/candlestack rev-parse --is-shallow-repository" \
    "git -C /opt/candlestack fetch --quiet --unshallow origin +refs/heads/main:refs/remotes/origin/main" \
    "git -C /opt/candlestack merge-base --is-ancestor $COMMIT origin/main" \
    "git -C /opt/candlestack show $COMMIT:infra/env/compose.yaml" | head -n "$2"
}

# up_calls <env> <version> <maxmemory> <container limit> [with-secrets [with-dsn [with-logo
# [with-db [with-oauth]]]]]: what "up"
# asks of Docker; prod with the compose file of its release tag. Both with the worker ($WORKER)
# and its queue, or without them and removing them when $no_worker is set.
up_calls() {
  local worker='' file=/opt/candlestack/infra/env/compose.yaml
  if [[ -z ${no_worker-} ]]; then
    worker=$WORKER
  fi
  if [[ $1 == prod ]]; then
    file=TMP/compose.yaml
  fi
  echo "docker login ghcr.io --username deploy --password-stdin"
  echo "  stdin $TOKEN"
  echo "  config TMP"
  echo "docker pull --quiet $BACKEND"
  echo "docker pull --quiet $FRONTEND"
  if [[ -n $worker ]]; then
    echo "docker pull --quiet $worker"
  fi
  if [[ $1 == prod ]]; then
    echo "docker compose -p prod -f TMP/compose.yaml up --detach --remove-orphans"
    echo "  file # infra/env/compose.yaml of the release"
  else
    echo "docker compose -p $1 -f /opt/candlestack/infra/env/compose.yaml up --detach --remove-orphans"
  fi
  up_env "$@"
  if [[ -z $worker ]]; then
    echo "docker compose -p $1 -f $file rm --stop --force worker queue"
    if [[ $1 == prod ]]; then
      echo "  file # infra/env/compose.yaml of the release"
    fi
    up_env "$@"
  fi
  echo "docker image prune --all --force"
}

# up_env <up_calls' arguments>: the variables compose gets from "up", sorted (uses $worker, and
# $queue_password: the value of QUEUE_PASSWORD when stdin has one).
up_env() {
  if [[ -n ${5:-} ]]; then
    echo "  env ALPACA_KEY_ID=$KEY_ID"
    echo "  env ALPACA_SECRET_KEY=$SECRET"
  fi
  echo "  env APP_VERSION=$2"
  echo "  env BACKEND_IMAGE=$BACKEND"
  if [[ -n ${9:-} ]]; then
    echo "  env BETA_ALLOWLIST=ada@example.com,bob@example.com"
  fi
  if [[ -n $worker ]]; then
    echo "  env COMPOSE_PROFILES=worker"
  fi
  echo "  env EDGE_NETWORK=edge"
  echo "  env ENV_NAME=$1"
  echo "  env FRONTEND_IMAGE=$FRONTEND"
  if [[ -n ${7:-} ]]; then
    echo "  env LOGO_DEV_KEY=$LOGO_KEY"
  fi
  if [[ -n ${9:-} ]]; then
    echo "  env OAUTH_GITHUB_CLIENT_ID=Iv1.test"
    echo "  env OAUTH_GITHUB_CLIENT_SECRET=gh_test_secret"
    echo "  env OAUTH_GOOGLE_CLIENT_ID=g.test.apps"
    echo "  env OAUTH_GOOGLE_CLIENT_SECRET=g_test_secret"
  fi
  if [[ -n ${8:-} ]]; then
    echo "  env POSTGRES_PASSWORD=$DB_PASSWORD"
  fi
  if [[ -n ${queue_password+set} ]]; then
    echo "  env QUEUE_PASSWORD=$queue_password"
  fi
  echo "  env REDIS_MAXMEMORY=$3"
  echo "  env REDIS_MEM_LIMIT=$4"
  if [[ -n ${6:-} ]]; then
    echo "  env SENTRY_DSN=$DSN"
  fi
  if [[ -n $worker ]]; then
    echo "  env WORKER_IMAGE=$worker"
  fi
}

echo "# The keys: every one is restricted to candlestack-deploy with one of the scopes"
tests=$((tests + 1))
forced='^restrict,command="/opt/candlestack/infra/vm/candlestack-deploy \([a-z]*\)" ssh-ed25519 .*'
scopes=$(sed -n "s|$forced|\1|p" "$here/deploy_authorized_keys" | sort | tr '\n' ' ')
keys=$(grep -c '^[^#]' "$here/deploy_authorized_keys")
if [[ $scopes == "prod stage " && $keys == 2 ]]; then
  echo "ok    deploy_authorized_keys: prod and stage, each forced"
else
  failed "deploy_authorized_keys" "expected 2 forced keys prod, stage; got $keys: $scopes"
fi

echo "# What each key may run"
echo "main-$COMMIT $STAGE_IMAGES" >"$RECORD"
allowed "prod: up prod, with the compose file of the release, the worker and its queue" prod \
  "up prod v0.2.0" "$TOKEN
$SECRETS" "$(release_git v0.2.0 6)
$(up_calls prod v0.2.0 256mb 288m with-secrets)"
FAKE_GIT=not-shallow allowed "prod: up prod, a release candidate in a full clone" prod \
  "up prod v0.3.0-rc.1" "$TOKEN
$SECRETS" "$(release_git v0.3.0-rc.1 6 | sed 's/ --unshallow//')
$(up_calls prod v0.3.0-rc.1 256mb 288m with-secrets)"
# A line from before the worker: three fields. prod then runs without the worker and its queue.
echo "main-$COMMIT $IMAGES" >"$RECORD"
allowed "prod: a commit stage ran before the worker removes the worker and its queue" prod \
  "up prod v0.2.0" "$TOKEN
$SECRETS" "$(release_git v0.2.0 6)
$(no_worker=1 up_calls prod v0.2.0 256mb 288m with-secrets)"
allowed "stage: up stage, with the worker and its queue" stage "up stage main-0123abc $STAGE_IMAGES" "$TOKEN
$SECRETS" "$(up_calls stage main-0123abc 256mb 288m with-secrets)"
# A stage workflow run from before the worker (re-run) passes no worker image.
allowed "stage: up stage without a worker image removes the worker and its queue" \
  stage "up stage main-0123abc $IMAGES" "$TOKEN
$SECRETS" "$(no_worker=1 up_calls stage main-0123abc 256mb 288m with-secrets)"
# The organization's new name, while both are accepted.
old_backend=$BACKEND old_frontend=$FRONTEND old_worker=$WORKER
BACKEND=${BACKEND/candlestack-fei-stu/candlestack-tech}
FRONTEND=${FRONTEND/candlestack-fei-stu/candlestack-tech}
WORKER=${WORKER/candlestack-fei-stu/candlestack-tech}
allowed "stage: up stage with images of candlestack-tech" stage "up stage main-0123abc $BACKEND $FRONTEND $WORKER" "$TOKEN
$SECRETS" "$(up_calls stage main-0123abc 256mb 288m with-secrets)"
BACKEND=$old_backend FRONTEND=$old_frontend WORKER=$old_worker
allowed "stage: up stage with the Sentry DSN" stage "up stage main-0123abc $STAGE_IMAGES" "$TOKEN
$SECRETS
SENTRY_DSN=$DSN" "$(up_calls stage main-0123abc 256mb 288m with-secrets with-dsn)"
allowed "stage: up stage with the logo.dev key" stage "up stage main-0123abc $STAGE_IMAGES" "$TOKEN
$SECRETS
LOGO_DEV_KEY=$LOGO_KEY
SENTRY_DSN=$DSN" "$(up_calls stage main-0123abc 256mb 288m with-secrets with-dsn with-logo)"
allowed "stage: up stage with the database, the OAuth apps and the beta list" stage \
  "up stage main-0123abc $STAGE_IMAGES" "$TOKEN
$SECRETS
LOGO_DEV_KEY=$LOGO_KEY
SENTRY_DSN=$DSN
POSTGRES_PASSWORD=$DB_PASSWORD
$OAUTH" "$(up_calls stage main-0123abc 256mb 288m with-secrets with-dsn with-logo with-db with-oauth)"
queue_password=$QUEUE_PW allowed "stage: up stage with the job queue's password" stage \
  "up stage main-0123abc $STAGE_IMAGES" "$TOKEN
$SECRETS
QUEUE_PASSWORD=$QUEUE_PW" "$(queue_password=$QUEUE_PW up_calls stage main-0123abc 256mb 288m with-secrets)"
allowed "stage: up stage without secrets" stage "up stage main-0123abc $STAGE_IMAGES" "$TOKEN" \
  "$(up_calls stage main-0123abc 256mb 288m)"
allowed "stage: edge" stage "edge" "" \
  "git -C /opt/candlestack fetch --quiet --depth 1 origin main
git -C /opt/candlestack reset --quiet --hard FETCH_HEAD
docker compose -f /opt/candlestack/infra/edge/compose.yaml up --detach
docker compose -f /opt/candlestack/infra/edge/compose.yaml exec -T caddy caddy reload --config /etc/caddy/Caddyfile"
allowed "stage: agent" stage "agent $AGENT" "$TOKEN" \
  "docker login ghcr.io --username deploy --password-stdin
  stdin $TOKEN
  config TMP
docker pull --quiet $AGENT
docker compose -p agent -f /opt/candlestack/infra/agent/compose.yaml up --detach --remove-orphans
  env IMAGE=$AGENT"

echo "# What each key may not run"
refused "stage: up prod" stage "up prod v0.2.0" "$TOKEN" \
  "the stage key may not deploy 'prod'"
refused "stage: up another environment" stage "up test main-0123abc $STAGE_IMAGES" "$TOKEN" \
  "the stage key may not deploy 'test'"
refused "prod: up stage" prod "up stage main-0123abc $STAGE_IMAGES" "$TOKEN" \
  "the prod key may not deploy 'stage'"
refused "prod: up another environment" prod "up test main-0123abc $STAGE_IMAGES" "$TOKEN" \
  "the prod key may not deploy 'test'"
refused "prod: edge" prod "edge" "" "only the stage key updates the edge"
refused "prod: agent" prod "agent $AGENT" "$TOKEN" "only the stage key deploys the server agent"
refused "unknown scope" admin "up prod v0.2.0" "$TOKEN" \
  "the admin key may not deploy 'prod'"
refused "unknown command" stage "sh -c id" "" "unknown command: 'sh -c id'"
refused "no command (a shell)" stage "" "" "unknown command: ''"

echo "# prod: only a release tag on main's history, with the tag's own compose file"
for version in main-0123abc v0.2 0.2.0 v0.2.0.1 v0.2.0-; do
  refused "prod: version $version" prod "up prod $version" "$TOKEN" \
    "prod version must be a release tag (vX.Y.Z): '$version'"
done
FAKE_GIT=no-tag after_git=$(release_git v0.2.0 1) refused "prod: a tag not on origin" prod \
  "up prod v0.2.0" "$TOKEN" "release tag 'v0.2.0' not found on origin"
FAKE_GIT=no-commit after_git=$(release_git v0.2.0 2) refused "prod: a tag of no commit" prod \
  "up prod v0.2.0" "$TOKEN" "release tag 'v0.2.0' does not point to a commit"
FAKE_GIT=not-on-main after_git=$(release_git v0.2.0 5) refused "prod: a tag off main" prod \
  "up prod v0.2.0" "$TOKEN" "release tag 'v0.2.0' is not on main's history"
FAKE_GIT=no-compose after_git=$(release_git v0.2.0 6) refused "prod: a tag without the file" \
  prod "up prod v0.2.0" "$TOKEN" "release tag 'v0.2.0' has no infra/env/compose.yaml"

# recorded <name> <expected record> <scope> <command> [stdin]: runs the command (whether it
# succeeds or not is up to the test) and checks that the stage record is <expected record>.
recorded() {
  tests=$((tests + 1))
  run "$3" "$4" "${5-}"
  local record
  record=$(cat "$RECORD" 2>/dev/null || true)
  if [[ $record != "$2" ]]; then
    failed "$1" "record differs (< expected, > actual):" "$(diff <(echo "$2") <(echo "$record"))"
  else
    echo "ok    $1"
  fi
}

echo "# The stage record: up stage adds its images, up prod runs the last ones of its commit"
# printed <name> <expected output>: the output of the last run is "images ...".
printed() {
  tests=$((tests + 1))
  if [[ $output == "$2" ]]; then
    echo "ok    $1"
  else
    failed "$1" "expected '$2', got: $output"
  fi
}

rm -f "$RECORD"
recorded "stage: up stage starts the record" "main-$COMMIT $OLD_STAGE_IMAGES" \
  stage "up stage main-$COMMIT $OLD_STAGE_IMAGES" "$TOKEN
QUEUE_PASSWORD=$QUEUE_PW"
printed "stage: up prints the images it runs, the worker's too" "images $OLD_STAGE_IMAGES"
recorded "stage: every up stage adds a line" "main-$COMMIT $OLD_STAGE_IMAGES
main-0123abc $OLD_STAGE_IMAGES" stage "up stage main-0123abc $OLD_STAGE_IMAGES" "$TOKEN"
printed "stage: up without the job queue's password says so" "candlestack-deploy: no QUEUE_PASSWORD: the job queue of stage runs without a password
images $OLD_STAGE_IMAGES"
recorded "stage: the same commit again" "main-$COMMIT $OLD_STAGE_IMAGES
main-0123abc $OLD_STAGE_IMAGES
main-$COMMIT $STAGE_IMAGES" stage "up stage main-$COMMIT $STAGE_IMAGES" "$TOKEN"
record=$(<"$RECORD")
FAKE_DOCKER=fail-up recorded "stage: a failed up stage adds nothing" "$record" \
  stage "up stage main-$COMMIT $OLD_STAGE_IMAGES" "$TOKEN"
# A version that only starts with the commit is another stage deploy, never the release's.
echo "main-$COMMIT-x $OLD_STAGE_IMAGES" >>"$RECORD"
record=$(<"$RECORD")
allowed "prod: the images of the last up stage of the tag's commit, its worker's too" prod \
  "up prod v0.2.0" "$TOKEN" "$(release_git v0.2.0 6)
$(up_calls prod v0.2.0 256mb 288m)"
recorded "prod: up prod adds nothing" "$record" prod "up prod v0.2.0" "$TOKEN"
printed "prod: up prints the images it runs, the worker's too" "candlestack-deploy: no QUEUE_PASSWORD: the job queue of prod runs without a password
images $STAGE_IMAGES"
recorded "stage: up stage without a worker image records three fields" "$record
main-0123abc $OLD_IMAGES" stage "up stage main-0123abc $OLD_IMAGES" "$TOKEN"
printed "stage: up without a worker image prints two images" "images $OLD_IMAGES"

refused "prod: images in the command" prod "up prod v0.2.0 $IMAGES" "$TOKEN" \
  "up prod takes no images: it runs the ones stage ran for the release's commit"
refused "prod: a worker image in the command" prod "up prod v0.2.0 $STAGE_IMAGES" "$TOKEN" \
  "up prod takes no images: it runs the ones stage ran for the release's commit"
printf '%s\n' "main-0123abc $IMAGES" "main-$COMMIT-x $IMAGES" >"$RECORD"
not_run="stage has not run $COMMIT: release a commit of main that stage deployed"
after_git=$(release_git v0.2.0 6) refused "prod: a commit stage has not run" prod \
  "up prod v0.2.0" "$TOKEN" "$not_run"
rm "$RECORD"
after_git=$(release_git v0.2.0 6) refused "prod: no stage record yet" prod "up prod v0.2.0" \
  "$TOKEN" "$not_run"
echo "main-$COMMIT ${BACKEND%@*}:latest $FRONTEND" >"$RECORD"
after_git=$(release_git v0.2.0 6) refused "prod: a bad line in the record" prod "up prod v0.2.0" \
  "$TOKEN" "image must be a GHCR digest reference: '${BACKEND%@*}:latest'"

echo "# Malformed arguments"
long=$(printf 'v%.0s' {1..65})
# shellcheck disable=SC2016 # a command substitution the script must not run
for version in "$long" 'v1;id' 'v1$(id)' "v1'" 'main/0123' 'v1*'; do
  refused "version '${version:0:12}'" stage "up stage $version $STAGE_IMAGES" "$TOKEN" \
    "bad version: '$version'"
done
refused "no images" stage "up stage v1" "$TOKEN" \
  "image must be a GHCR digest reference: ''"
for bad in \
  "ghcr.io/candlestack-fei-stu/candlestack/backend:latest" \
  "ghcr.io/candlestack-fei-stu/candlestack/backend@sha256:0123abcd" \
  "${BACKEND^^}" \
  "docker.io/library/redis@${BACKEND#*@}" \
  "ghcr.io/other-org/candlestack/backend@${BACKEND#*@}" \
  "ghcr.io/candlestack-fei-stu/candlestack/../backend@${BACKEND#*@}" \
  "registry.example/$BACKEND" \
  "$BACKEND;id"; do
  refused "image ${bad:0:60}" stage "up stage v1 $bad $FRONTEND $WORKER" "$TOKEN" \
    "image must be a GHCR digest reference: '$bad'"
  refused "frontend ${bad:0:57}" stage "up stage v1 $BACKEND $bad $WORKER" "$TOKEN" \
    "image must be a GHCR digest reference: '$bad'"
  refused "worker ${bad:0:59}" stage "up stage v1 $IMAGES $bad" "$TOKEN" \
    "image must be a GHCR digest reference: '$bad'"
done
refused "agent image" stage "agent ${AGENT%@*}:latest" "$TOKEN" \
  "image must be a GHCR digest reference: '${AGENT%@*}:latest'"

echo "# stdin: the token, then only the environment's secrets, with safe values"
only_secrets="only ALPACA_KEY_ID, ALPACA_SECRET_KEY, LOGO_DEV_KEY, SENTRY_DSN, POSTGRES_PASSWORD, the OAUTH_ keys, BETA_ALLOWLIST and QUEUE_PASSWORD may follow the token on stdin"
refused "up without a token" stage "up stage v1 $STAGE_IMAGES" "" "expected a GHCR token on stdin"
refused "agent without a token" stage "agent $AGENT" "" "expected a GHCR token on stdin"
refused "another variable" stage "up stage v1 $STAGE_IMAGES" "$TOKEN
LD_PRELOAD=/tmp/x.so" "$only_secrets" \
  /tmp/x.so
refused "PATH" stage "up stage v1 $STAGE_IMAGES" "$TOKEN
PATH=/tmp" "$only_secrets"
refused "a line without =" stage "up stage v1 $STAGE_IMAGES" "$TOKEN
$KEY_ID" "expected KEY=VALUE lines after the token on stdin"
for value in 'with space' 'quote"d' "quote'd" 'back`tick' 'back\slash' $'tab\there' \
  "$(printf 'x%.0s' {1..257})" $'caf\xc3\xa9'; do
  refused "secret value ${value:0:12}" stage "up stage v1 $STAGE_IMAGES" "$TOKEN
ALPACA_SECRET_KEY=$value" \
    "ALPACA_SECRET_KEY must be at most 256 printable characters without spaces or quotes" \
    "$value"
done
# The password goes into the database URL as it is: '@', '%', '/', ':' would change the URL.
for value in 'p@ss-0123456789abcdef' 'pass%40-0123456789abc' 'pass/0123456789abcdef' \
  'pass:0123456789abcdef' 'short-0123456' ''; do
  refused "database password ${value:0:12}" stage "up stage v1 $STAGE_IMAGES" "$TOKEN
POSTGRES_PASSWORD=$value" \
    "POSTGRES_PASSWORD must be 16 to 256 letters, digits or the characters . _ ~ -" ${value:+"$value"}
done
# The job queue's password goes into its URL as it is too; empty is none.
for value in 'p@ss-0123456789abcdef' 'pass/0123456789abcdef' 'short-0123456'; do
  refused "queue password ${value:0:12}" stage "up stage v1 $STAGE_IMAGES" "$TOKEN
QUEUE_PASSWORD=$value" \
    "QUEUE_PASSWORD must be empty or 16 to 256 letters, digits or the characters . _ ~ -" "$value"
done
queue_password='' allowed "an empty queue password" stage "up stage v1 $STAGE_IMAGES" "$TOKEN
QUEUE_PASSWORD=" "$(queue_password='' up_calls stage v1 256mb 288m)"
secret=$SECRET
SECRET=$(printf '~%.0s' {1..256})
allowed "secret value of 256 characters, blank lines" stage "up stage v1 $STAGE_IMAGES" "$TOKEN

ALPACA_KEY_ID=$KEY_ID
ALPACA_SECRET_KEY=$SECRET
" "$(up_calls stage v1 256mb 288m with-secrets)"
SECRET=$secret
long_list=$(printf 'person%04d@example.com,' {1..175})
allowed "a beta list longer than 256 characters" stage "up stage v1 $STAGE_IMAGES" "$TOKEN
BETA_ALLOWLIST=${long_list%,}" "$(up_calls stage v1 256mb 288m | sed "s|^  env BACKEND_IMAGE=.*|&\n  env BETA_ALLOWLIST=${long_list%,}|")"
refused "a beta list over 4096 characters" stage "up stage v1 $STAGE_IMAGES" "$TOKEN
BETA_ALLOWLIST=${long_list}${long_list:0:100}" \
  "BETA_ALLOWLIST must be at most 4096 printable characters without spaces or quotes"

echo
if ((failures)); then
  echo "$failures of $tests tests failed"
  exit 1
fi
echo "all $tests tests passed"
