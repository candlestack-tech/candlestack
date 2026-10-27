#!/usr/bin/env bash
# Smoke test of a deployed environment, run by the deploy workflows after the health check:
#   .github/scripts/smoke.sh https://stage.candlestack.tech
# stage is behind Cloudflare Access: set CF_ACCESS_CLIENT_ID and
# CF_ACCESS_CLIENT_SECRET to send the service token. prod is checked without it.
#
# Every check is a function check_<name> listed in CHECKS. It calls get, then fails with a
# message that says what was expected; what it prints on success is shown after "ok". All
# checks run; the script fails if any failed. To add one, write the function and append its
# name to CHECKS. The market data is for signed-in accounts only and the smoke test cannot sign
# in, so its checks expect the not-signed-in problem: they never reach the sources. The icon is
# public: its check goes through the deployed API to logo.dev.
# shellcheck disable=SC2329 # the checks are called by name from the loop at the end
set -euo pipefail

CHECKS=(health openapi docs frontend headers access sources search instruments candles icon)

base=${1:?usage: smoke.sh <base-url>}
base=${base%/}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

curl_opts=(--silent --show-error --max-time 20 --retry 2)
if [[ -n ${CF_ACCESS_CLIENT_ID:-} ]]; then
  curl_opts+=(-H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" -H "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET:?}")
fi

fail() {
  echo "$*" >&2
  exit 1
}

# get <path>: GET base-url + path. Sets $status, $type (the media type) and $body.
get() {
  local meta
  meta=$(curl "${curl_opts[@]}" --output "$work/body" --dump-header "$work/headers" \
    --write-out '%{http_code} %{content_type}' "$base$1") ||
    fail "GET $1: request failed"
  status=${meta%% *}
  type=${meta#* }
  type=${type%%;*}
  body=$(<"$work/body")
}

# expect <status> <media type>
expect() {
  [[ $status == "$1" ]] || fail "expected HTTP $1, got $status: ${body:0:300}"
  [[ $type == "$2" ]] || fail "expected $2, got '$type'"
}

check_health() {
  get /api/health
  expect 200 application/json
  jq -e '.status == "ok"' <<<"$body" >/dev/null || fail "status is not ok: $body"
}

check_openapi() {
  get /api/v1/openapi.json
  expect 200 application/json
  jq -e '.openapi | strings' <<<"$body" >/dev/null || fail "not an OpenAPI document"
}

check_docs() {
  get /api/v1/docs
  expect 200 text/html
  grep -qi scalar "$work/body" || fail "the page does not load Scalar"
}

# The app, on / and on a direct link to one of its routes (the frontend's Caddy answers a path
# that is no file with index.html), and the script that index.html loads.
check_frontend() {
  local path script
  for path in / '/instrument/crypto:BTCUSDT?tf=1h'; do
    get "$path"
    expect 200 text/html
    grep -q '<div id="root">' "$work/body" || fail "$path: not the app's index.html: ${body:0:300}"
  done
  script=$(grep -o 'src="/assets/[^"]*\.js"' "$work/body" | head -n 1 | cut -d '"' -f 2) || true
  [[ -n $script ]] || fail "index.html loads no script from /assets/"
  get "$script"
  expect 200 text/javascript
  grep -qi '^cache-control: .*immutable' "$work/headers" || fail "$script: not cached as immutable"
}

# The security headers the edge Caddy adds (infra/edge/caddy/Caddyfile), on a page and on the API.
check_headers() {
  local path header
  for path in / /api/health; do
    get "$path"
    for header in 'strict-transport-security: max-age=' 'x-frame-options: deny' \
      'x-content-type-options: nosniff' 'referrer-policy: strict-origin-when-cross-origin' \
      'permissions-policy: camera=()' 'content-security-policy-report-only: default-src'; do
      grep -qi "^$header" "$work/headers" || fail "$path: no '$header' header"
    done
    if grep -qi '^via: .*caddy' "$work/headers"; then
      fail "$path: the Via header names the server"
    fi
  done
}

# stage is team-only: without the service token, Cloudflare Access answers
# instead of the app, with a redirect to its login (or 401/403). prod is public: skipped.
check_access() {
  local meta status location
  if [[ -z ${CF_ACCESS_CLIENT_ID:-} ]]; then
    echo "skipped: no Access token, the environment is public"
    return
  fi
  meta=$(curl --silent --show-error --max-time 20 --retry 2 --output /dev/null \
    --write-out '%{http_code} %{redirect_url}' "$base/api/health") ||
    fail "GET /api/health without the token: request failed"
  status=${meta%% *}
  location=${meta#* }
  case $status in
    401 | 403) echo "HTTP $status without the token" ;;
    30[1237] | 308)
      [[ $location == https://*.cloudflareaccess.com/cdn-cgi/access/login/* ]] ||
        fail "without the token, /api/health redirects elsewhere than the Access login: $location"
      echo "HTTP $status to the Access login without the token"
      ;;
    *) fail "without the token, /api/health answered HTTP $status, not Access" ;;
  esac
}

check_sources() {
  get /api/health/sources
  expect 200 application/json
  jq -e '.sources.binance.status == "ok" and .sources.alpaca.status == "ok"' <<<"$body" >/dev/null ||
    fail "a source is not reachable: $body"
}

# not_signed_in <path>: expects the 401 not-signed-in problem the market data answers without a
# session.
not_signed_in() {
  get "$1"
  expect 401 application/problem+json
  jq -e '.type == "https://candlestack.tech/problems/not-signed-in"' <<<"$body" >/dev/null ||
    fail "$1: not the not-signed-in problem: ${body:0:300}"
}

check_search() {
  not_signed_in /api/v1/data/instruments
  not_signed_in '/api/v1/data/instruments?q=btc'
}

check_instruments() {
  not_signed_in /api/v1/data/instruments/crypto:BTCUSDT
  not_signed_in /api/v1/data/instruments/stock:AAPL
}

check_candles() {
  not_signed_in '/api/v1/data/instruments/crypto:BTCUSDT/candles?timeframe=1h&start=2024-06-03&end=2024-06-04'
  not_signed_in '/api/v1/data/instruments/stock:AAPL/candles?timeframe=1h&start=2024-06-03&end=2024-06-04'
}

# The logo or the fallback image, without a session: the icons are public. Not through get, which
# reads the body as text.
check_icon() {
  local path=/api/v1/data/instruments/stock:AAPL/icon meta status type
  meta=$(curl "${curl_opts[@]}" --output /dev/null --write-out '%{http_code} %{content_type}' "$base$path") || fail "GET $path: request failed"
  status=${meta%% *}
  type=${meta#* }
  [[ $status == 200 ]] || fail "expected HTTP 200, got $status"
  [[ $type == image/* ]] || fail "expected an image, got '$type'"
  echo "$type"
}

failed=0
for check in "${CHECKS[@]}"; do
  # Each check runs in a subshell, so fail ends only that check.
  if message=$("check_$check" 2>&1); then
    echo "ok    $check${message:+: $message}"
  else
    message=${message//$'\n'/ }
    echo "FAIL  $check: $message"
    [[ -z ${GITHUB_ACTIONS:-} ]] || echo "::error title=Smoke test $check::$message"
    failed=1
  fi
done
exit "$failed"
