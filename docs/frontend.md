# Frontend stack

Tools for the real frontend ([#110](https://github.com/candlestack-tech/candlestack/issues/110)).
Limits that shaped the choice:

- prod serves the frontend as static files (Caddy), so the app must build to HTML, JS and CSS;
- the API has no CORS headers, and candle and instrument-detail requests are limited to 60 per
  minute (search is not);
- the chart shows crypto (24/7) and US stocks (no candles at night, on weekends and holidays).

| Area | Pick |
| --- | --- |
| Framework | React + TypeScript + Vite |
| Routing | TanStack Router |
| UI | shadcn/ui + Tailwind CSS |
| Chart | Lightweight Charts |
| API | TanStack Query, all calls in one module |

## Framework

| Option | Pro | Con |
| --- | --- | --- |
| React + Vite | static build, fits the current container; most libraries support React | routing is a separate library |
| Svelte | less code, small bundles | fewer libraries for UI and charts |
| Next.js | routing and layouts built in | made for a Node server; as static export most features are off |

**Pick: React + Vite.** The Dockerfile only gets a Node build step, Caddy stays.

## Routing

| Option | Pro | Con |
| --- | --- | --- |
| TanStack Router | typed routes and links; search params validated by a schema; route loaders work with TanStack Query | newer, smaller community |
| React Router | most used, simple | search params are plain strings, every page parses and checks them itself |
| Wouter | tiny | no search param handling, no loaders |

**Pick: TanStack Router.** Later pages keep a whole experiment in the URL (model, threshold,
stop loss, fill, period), so a link can be shared. Each route declares its search params with
a schema: a broken or outdated link falls back to defaults or shows an error instead of
crashing the page. A route can preload its data through TanStack Query before it renders.

## UI and styling

| Option | Pro | Con |
| --- | --- | --- |
| shadcn/ui + Tailwind | components live in our repo; accessible (Radix); theme is CSS variables | long class lists |
| Mantine | full component set, fast start | own look, hard to get the Vercel / Linear style |
| CSS Modules | no dependencies | every dropdown and dialog written by hand |

**Pick: shadcn/ui + Tailwind.** Colors, font and theme sit in one CSS file, one set for dark
and one for light, which is what #111 needs. Teal `#0f766e` / `#3cc7a6` and Geist stay.

## Chart

| Option | Pro | Con |
| --- | --- | --- |
| Lightweight Charts | candles + volume; stock sessions join without gaps; easy "scroll left, load more"; small | only price charts; time is UTC |
| ECharts | any chart type | large; nights and weekends show as empty gaps |
| Recharts | simple React API | no candlesticks; slow with thousands of points |

**Pick: Lightweight Charts.** It covers #113 as is. Stock times need a formatter to show New
York time, and candle colors must differ from the teal accent.

## API

| Option | Pro | Con |
| --- | --- | --- |
| TanStack Query | cache, merges duplicate requests, loading and error states, infinite loading | one more library to learn |
| SWR | smaller, same idea | weaker infinite loading |
| `fetch` in `useEffect` | no dependency | no cache; easy to hit the rate limit |

**Pick: TanStack Query.** The cache keeps candle and instrument-detail requests under 60 per
minute.

- All API calls live in `src/api/`, so the API changes (#104–#109) are fixed in one place.
- In dev, the Vite proxy sends `/api` to `https://app.candlestack.tech` (no CORS problem).
  In prod the API is on the same host.

## Rules

- **Same origin only.** The CSP (`infra/edge/caddy/Caddyfile`, report-only for now) allows
  scripts and fonts from our own host. No CDN: Geist comes from npm
  (`@fontsource-variable/geist`), and the theme script is its own file, not inline in
  `index.html`.
- **Typed API.** Response types are generated from `docs/openapi.json` with
  `openapi-typescript`, so the API changes (#104–#109) show up as type errors.
- **Caching.** Older candle pages never change and are cached for long. The latest page
  refreshes at most once a minute. No automatic retry on 429: the page shows "try again in N s"
  counting down from `Retry-After`.
- **Pages by count.** The chart asks for candles by number, not by time, and the API counts
  them by the market schedule (stocks: sessions only). A page is `limit=min(500, max_candles)`
  candles. The latest page is always the last of them (`?limit=…`); the older pages hang below
  its start at the first scroll to the left (the anchor), each ending where the one above it
  begins (`?end=<meta.start>&limit=…`). Older pages never change and stay cached. When the
  refreshed latest page has moved on above the anchor, a bridge asks for the
  `min(1000, max_candles)` candles before its start (`?end=<its start>&limit=…`); the candles
  from the anchor on fill the gap, so none go missing after an idle tab. Until the bridge
  arrives, or while it fails (to be retried), the chart keeps showing the latest page and the
  bridge as they were, and the new candles wait. History starts over below the latest page when
  the bridge begins above the anchor (more than 1000 candles went by) or the API cannot give it
  (`too-many-candles`, `period-out-of-range`). History ends at the first candle
  (`available_from`), at a page that starts at its end, or after three older pages in a row
  without candles (one or two are an outage).
- **Gaps.** When `meta.gaps_total > 0`, the chart shows a small note: it joins sessions, so
  real gaps in the data would be hidden otherwise.
- **Phones.** Every page fits a 360 px screen without sideways scrolling, in both themes. Long
  text gives way instead of the layout: the instrument list has fixed column widths (see
  [Instrument list](#instrument-list); the name takes the rest), so names, symbols and exchanges
  truncate (their title shows them in full); an instrument's id and name wrap, and error messages
  break their long paths (`break-words`). Below `sm` the instrument page shows the symbol fact only to
  screen readers (it ends the id above the name) and narrows the gaps of the facts and the
  timeframe switch, so each fits one line.
- **Chart height.** The layout is at least as tall as the visible screen (`100dvh`, `100vh`
  without dvh) and the page is a flex column (`Layout.tsx`; in the app the instrument route's
  `staticData: { fill: true }` keeps the workspace's wrappers growing flex columns too,
  `AppLayout.tsx`). The candles section, also while it
  loads or fails, and the chart in it are the part that grows: the chart takes the height the
  header, the instrument info, the switch, the status line and the footer leave, however many
  lines they take. At least 16rem on a phone and 20rem from `sm` up, below that the page
  scrolls; the section stops at 64rem (the chart at about 60rem), and on a taller screen the
  space left stays below it. `chartHeight.test.tsx` and `routeTree.test.tsx` check every link of
  that chain.

## Home page

`/` is the public start page (#199), approved in the design repository (`mockups/home`).
`src/pages/HomePage.tsx` puts together its parts from `src/features/home/`:

- a headline with Try Backtest over the pipeline of a run: market data, the three layers
  (representation, model, decision) and the results;
- the three layers you can swap, each with what it offers;
- the markets with an example of the instrument list (`ExampleInstruments.tsx`): five rows of
  sample figures written in the code, each with its real icon, in the list's style, nothing in
  them a link, under an "EXAMPLE" line. Market data is for signed-in accounts only (#340), so
  the home page asks the API for none. Sign in to browse leads to `/signin?next=/instruments`;
  signed in, Browse instruments to the list;
- compare and public experiments, both shown as "Coming soon".

The instrument list (`/instruments`) and the instrument pages (`/instrument/$id`) are part of the
signed-in app ([Sign-in](#sign-in)). The list sits beside the sidebar: the title, four facts,
then the search, the filters and the table ([Instrument list](#instrument-list)). An instrument
takes the whole width like a backtest, its back link "Instruments" returning to the list's last
search (`BackToList.tsx`).

The header links Product (`/`), Instruments (signed in only) and the API docs of the same host,
so stage links to stage. Sign in and Try Backtest (also the hero's) lead to the sign-in page; signed in, the
header shows Dashboard and the avatar with its menu instead ([Sign-in](#sign-in)). Below `sm` the
nav goes, and Sign in below `md`: with the nav, the row needs about 675 px.

The 40 px grid behind the pages is stronger than the website's (0.09 light, 0.075 dark). The
footer, the cards and the list's table are solid on it, so it never shows through them.

## Sign-in

Sign-in is GitHub or Google only (#208), designed in the design repository: the sign-in page in
`mockups/home` (`#/signin`), the signed-in app in `mockups/app`. The API side is in
`docs/architecture.md`; the frontend builds on four endpoints (`src/api/accounts.ts`):

- `GET /api/v1/me` gives the user; its 401 (`not-signed-in`) means signed out, which the app
  keeps as `null`, not as an error. One cache entry (`['account', 'me']`) serves the header, the
  guards and the pages. The root route asks before the first page shows, so the header never
  flips; it stays fresh until the app changes it (a sign-in is a full page load, signing out
  sets it to `null`).
- Signing in is a full page navigation to `/api/v1/auth/{github|google}/start?next=<path>`. The
  API sends the browser to the provider, and from there back to `next` (default `/dashboard`),
  to `/signin?error=denied` or to `/signin?error=failed`.
- `GET /api/v1/auth/denied` names the account the beta list just turned away.
- `POST /api/v1/auth/sign-out` ends the session. The avatar menu's Sign out then sets the user
  to `null` and goes to the home page; a 401 (the session had already ended) counts the same.
  Any other failure leaves the session open, so the user stays signed in on the page and can
  sign out again from the menu.

Routes (`src/routeTree.tsx`): the public pages sit in a pathless layout `_public` with the
site's header, the signed-in pages in `_app` with the app header. A page's `useSearch` therefore
names the layout: `useSearch({ from: '/_app/instruments' })`.

- `/signin?next=&error=` shows the start (GitHub, Google, the beta note) or, after
  `error=denied`, who was turned away with Use another account (back to the start, `next`
  kept) and Contact the team. A clicked button stays busy ("Opening GitHub", a spinner) while
  the browser leaves; Back from the provider offers the buttons again. An open session skips
  the page and goes to `next` or `/dashboard`.
- `/dashboard`, `/backtests`, `/models`, `/instruments`, `/instrument/$id` and `/settings` sit
  behind a guard (`src/routes/accountGuards.ts`): signed out, it redirects to `/signin?next=<the
  page asked for>`. The app code loads on demand.
- `next` is only ever a path of this app (`appPath` in `src/routes/signInSearch.ts`): anything
  that leaves the origin (`https://…`, `//…`, `/\…`), `/signin` itself and `/api/…` are dropped,
  so signing in is no open redirect. The validator sets every key it drops to `undefined`: the
  router keeps search params a route leaves out, and would hand the raw value on otherwise.
- An unknown address shows the public "Page not found", also under `/dashboard/…`
  (`notFoundMode: 'root'`).

The app shell (`src/features/workspace/`) follows `mockups/app`: the app header (logo to the
dashboard, no nav, New backtest, the theme toggle, the avatar), a 200 px sidebar (Dashboard,
Backtests, Models with the model count, Instruments, Settings, then the storage the models take, under "Model
storage" on one line ("4.7 MB of 100 MB"), and the note that market data is delayed 15 min; up to 980 px above the page as a row that scrolls sideways
without a scrollbar, the foot left out) and the footer.
The avatar is the user's initials (`initials.ts`), never a provider picture; its menu
(`src/features/account/AccountMenu.tsx`) is a shadcn/ui DropdownMenu with Settings, Home page,
API docs and, in red, Sign out. New backtest is a link to the builder ([Backtest builder](#backtest-builder)).
`ComingSoonDialog` is a shadcn/ui Dialog with the home page's Coming soon card over a sketch, for
every part of the app that is not built yet.
Icons are lucide's where lucide draws the design's icon; the others (Backtests, Models,
Instruments, Settings, Home page, the moon) are drawn from the mockup in `src/components/icons.tsx`. The
mockups' button looks (`.btn`, `.btn.ghost`, `.btn.pri`) are class constants on shadcn's Button
in `src/components/buttonStyles.ts`.

Motion comes from tw-animate-css on the Radix components, as in the design: dialogs fade and
scale in from 10 px lower, the menu pops in from 4 px above, the sign-in card rises 8 px and a
newly opened page (another route; another section of Settings moves only the section) fades up
6 px. Less motion turns every animation and transition off, as in the design: the system's
`prefers-reduced-motion` or the account's Reduce motion (`data-motion="reduce"` on `<html>`). A
rule in `src/index.css` does it for both, and the `motion-reduce:` variant follows both too.

Tests of whole pages run in jsdom (`// @vitest-environment jsdom`) with Testing Library:
`src/test/app.tsx` renders the app's routes on a memory history and `src/test/api.ts` answers
fetch by method and path.

## Settings

`/settings/<section>` (#208, `src/pages/SettingsPage.tsx`, the parts in `src/features/settings/`)
follows the Settings page of `mockups/app`: the title, the sections as tabs (links, the open one
marked by the router) and one section at a time, which fades up 6 px when it opens. `/settings`
and an unknown section open `profile`; the others are `sign-in`, `backtest-defaults`,
`appearance`, `data-and-storage`, `api-and-mcp` and `account` (`src/routes/settingsSections.ts`).
The two sections with the account's preferences load them in the route's loader
(`GET /api/v1/me/preferences`, under `['account', 'preferences']`, so signing out forgets them).

- Saving (#216): no Save buttons. Each card saves itself as it changes
  (`src/features/settings/autosave.ts`): a click on a segment, a direction card, the benchmark or
  a switch at once, a slider once it is let go (or moved with a key), a text field on leaving it
  or on Enter; Esc in a text field puts its saved value back. The saves of a card go out one
  after another and the last change wins: a change that a newer one follows in the queue is not
  sent, so no slower answer undoes a newer value. A value the API would refuse is never sent: the
  field turns red and says what to fix (once left). The indicator at the right of the card's
  title (`SaveIndicator.tsx`), the same in Profile, Backtest defaults and Appearance, says
  Saving… (only after 300 ms), Saved with a check (fading out after 2 s), Not saved in amber
  while a field is to fix, or Couldn't save with Retry, which sends the latest value again (the
  typed value stays; why it failed is a line under the card). Leaving the section or the page
  while a field is to fix asks first (`LeaveGuard.tsx`, the router's blocker): Keep editing or
  Discard.
- Profile: the drawn initials, the display name to change (`PATCH /api/v1/me`, trimmed; an empty
  one is not sent, the answer becomes the user in the header and the menu), the email and the
  user ID.
- Sign-in: GitHub and Google (names and marks from `src/features/signin/providers.ts`, as on the
  sign-in page), a connected one with the email of its own account (`sign_in_methods` of
  `GET /api/v1/me`). Connect is a full page navigation to
  `/api/v1/auth/{provider}/start?intent=connect&next=/settings/sign-in`; the API comes back with
  `?error=taken` or `?error=failed`, which the card says under the list. Disconnect
  (`DELETE /api/v1/me/sign-in-methods/{provider}`) is off for the last method, with the title
  "Your only way to sign in"; a 409 `last-sign-in-method` says so too.
- Backtest defaults: every control of the design (shadcn/ui ToggleGroup for the timeframe and the
  direction cards, Slider for the threshold and the stop loss, Select for the benchmark). Each
  save sends the whole set, the nine fields and no other (`PUT .../backtest-defaults`,
  `BacktestDefaultsIn`), once the typed fields are right (`defaultsForm.ts`: real days, the
  period ending after it starts, 0 to 5 %); a change made while one is not waits for it.
- Appearance: each control saves as it changes (`PATCH .../appearance`,
  `src/features/preferences/`): at once in the cache the page reads, then on the account, one
  change after another; a failed one goes back, the indicator says Couldn't save (Retry makes the
  change again) and the card says why (until the next change, not on coming back to the
  section). The header's toggle shows in the indicator too. The theme is the page's (`src/theme.ts`): the header's
  toggle and the card change the same one, and signed in both save it to the account.
  `AppearanceSync`, on every page, puts the account's theme and Reduce motion on `<html>` once
  they arrive (the account wins over the browser) and keeps both in `localStorage`, which
  `public/theme.js` reads before the page paints, so neither flashes. Signed out, Reduce motion is
  the system's alone.
- Dates and times go through `src/lib/dates.ts` (`formatDate`, `formatTime`, the zone of the
  setting); `useDateStyle()` gives the account's choice, zone and all. A visitor has no setting:
  the API's formats, in New York time or the zone the page passes. The instrument page writes its
  first day, the chart's crosshair and axis and the note under it in the user's zone and formats
  (until the account's setting arrives, the market's zone: New York for stocks, their own for
  crypto, `src/features/instrument/time.ts`). The list's mini candles write their day in the user's date
  format.
  The zone setting is for market times (candles, trades). When something happened here (a
  backtest ran, a model was added) is on the viewer's own clock in the user's formats
  (`useViewerDateStyle()`).
- The cards are the dashboard's (`src/components/DashboardCard.tsx`: the icon tile by the title),
  their rows in `src/features/settings/rows.tsx` and their text field and segments in
  `controls.tsx`. A failure is a line in the design's hint style (12 px, muted) with
  `role="alert"`; what to fix in a field is a red line under it.
- Data and storage shows the market data and the storage the models take (the sidebar's meter
  and numbers, how many were uploaded); the backtests row comes with that page.
  API and MCP shows both cards as Coming soon, with the API docs of the same host.
- Account: Sign out everywhere (`POST /api/v1/auth/sign-out-everywhere`) and Delete account
  (`DELETE /api/v1/me`, after the app's dialog). Both then forget the user and their data and go
  to the home page, as Sign out does (`useLeaveAccount.ts`).

## Models

`/models` and `/models/<id>` (`src/features/models/`, #208, #272) follow `mockups/app`
(`ddModels`, `mdTileA`, the upload in three steps, `pageModel`, `inputMatrix`, the rename and
delete modals):

- One query, `modelsQueryOptions()` in `src/api/models.ts` under `['account', 'models']`, feeds
  the pages and the sidebar's count and storage. Every change writes the API's answer into it at
  once and then asks again; the star changes before the API answers and goes back when it
  refuses. A model's whole manifest (its example too, which the list leaves out) is its own query,
  `['account', 'manifest', <id>]`, and the catalog (`GET /api/v1/preprocessing/catalog`) is asked
  for once.
- The line under the title says how many models there are, the storage they take and, in the
  warning colour, how many still need a manifest. At the right of the title row (under it when
  the row is too narrow), Upload model: an outline button with the upload icon and "Keras or
  ONNX, up to 50 MB" under it (the API's `file_max_bytes`), which opens the upload dialog; the Upload card stays on the
  Dashboard's quick actions. The tiles start under the title row, across the whole width.
- A tile per model, oldest first: the framework's mark (Simple Icons paths and brand colours in
  `frameworks.ts`, a quarter lighter in the dark theme), the state (Verified, Ready, Needs setup;
  `StatePill.tsx`), the star (`src/components/StarButton.tsx`), the name (up to two lines, broken
  anywhere, in full in its title), `Keras · size · added day` ("added today"), what it reads
  from its manifest ("Renko · 30m · 25 × 23", else "in 25 × 23 · no
  manifest"), then the best Sharpe and the backtest count, or Add manifest while it has none. The
  name's link is stretched over the tile, so the whole tile opens the model's page; the star and
  Add manifest sit above it. Sizes are binary MB like the API's limits. The day goes through
  `src/lib/dates.ts` in the account's zone and date format.
- Add a model (`UploadDialog`, exported for the dashboard): three steps, all in view
  (`UploadParts.tsx`). 1, the model: a Keras or ONNX file goes to `POST /api/v1/models?name=…` as
  the raw body; a PyTorch, TensorFlow or scikit-learn file says to export it to ONNX, and any
  other file that it is not supported, both without a request (as does a file over the limit). A
  failed check shows the API's sentence under "Does not open as a safe model" or "No trained
  weights in the file". 2, the manifest: the file is read as JSON and sent to
  `PUT /api/v1/models/{id}/manifest`; a file that is no JSON or over 1 MB fails before it is sent,
  and a refused one shows the API's sentences (`problems`, then how many more) in the step with
  Choose another file. A kept manifest folds into a line of facts (input, output, what it reads,
  the warm-up) and the bar "model [25, 5] = manifest [25, 5]", which opens the input matrix.
  A model needs its manifest to be added: there is no way past the step. 3, one line of what goes
  to Models, and Add model, which opens the model's page. The API keeps the model, and then the
  manifest, as soon as each passed its checks, so closing the dialog before Add model deletes the
  model it uploaded (`useUpload`), and an upload still running goes on to its answer and is
  deleted then. While the dialog holds such a model, a `pagehide` (a reload, the tab closed)
  sends its DELETE with `keepalive`. Change on a kept manifest keeps it until another passes: one
  that fails says the model keeps the earlier one, and Add model stays on. Add manifest (`ManifestDialog`) is the same steps with the model done, ending in
  Save; closing it keeps the model. The API still holds a model without a manifest (Needs setup),
  which gets one with Add manifest. Every dialog (`src/components/ui/dialog.tsx`) is anchored a
  tenth of the screen from the top, so one whose steps grow only grows downwards.
- The model's page (`src/pages/ModelPage.tsx`), one screen without tabs: the header (the name,
  the state, `Keras · size · added day`; Backtest with it and Download manifest, or Add manifest;
  Rename and Delete), a strip of facts (what it reads and the warm-up, or the input shape without
  a manifest; the backtests and the best Sharpe), the input drawn as a matrix beside the checks,
  then the first 10 backtests of `GET /api/v1/backtests?model=<name>` with a link to all of them.
  The checks end with the trial run and the example from `checked_at`, `check_failed` and
  `check_error`: not run yet, passed, or failed with the worker's sentences, the example
  "Example matches" once verified, "No example" when the manifest has none, and "Example does not
  match" when it failed on the example after a trial run that passed.
  Backtest with it opens the builder with the model chosen. Delete goes back to Models.
- The matrix (`InputMatrix.tsx`): the window's rows down (the oldest at the top, at most 32 drawn,
  spread over a longer window), the columns across under their catalog groups, then the one
  number it predicts. The cells are shaded by the first sample of the manifest's `example`,
  scaled per column; without one every cell has the same tint, as there are no values to draw.
  A column is a button (one tab stop for all, the arrows, Home and End move between them): it
  shows its catalog description, its parameters and its scaling ("as is", "BLESK scaling"). The
  chosen column is drawn in the accent.
- A model deleted meanwhile (404) closes its Rename or Delete dialog, and its page says it is not
  found.

## Lists of backtests

The Backtests page (`src/features/backtests/BacktestsTable.tsx`, #315) follows the approved
table design: the star and the number, the instruments, the model, the status, Return, Sharpe, Max
drawdown and When. The layout is fixed (`table-fixed`): every column but the model has the width
of `src/features/backtests/widths.ts`, room for its longest value and its header with the sort
arrow, so no number is cut; the model takes the rest. Its name is 12 px (one step under the
row's 13 px), ends in an ellipsis when it does not fit, shows in full in its title and links to
the model while it exists. Return has room for "+9,999.9 %", When for "28 May 2025 11:58 AM",
the longest of every date and time format. Trades is left out: with it the model would keep under
100 px. From which width down a column hides, so the table never scrolls sideways: Max drawdown
at 1150 px, When at 800, Model at 620, Status at 500 (Return then shows the badge), Sharpe at 420.

The dashboard's Last backtests (fixed too; When hides in a card under 520 px), Best backtests and
a model's backtests give their columns the same widths. Every list names its instruments with
`BacktestInstruments`: up to as many as it has room for, else one less and "+N" with all of them
in the title, each symbol cut with an ellipsis where its column is narrower (one and "+N" on the
Backtests page, two elsewhere).

## Backtest builder

`/backtests/new` (#270, `src/pages/NewBacktestPage.tsx`, the parts in
`src/features/backtests/builder/`) follows `pageBuilder` of `mockups/app`: the page has no sidebar (the route's
`staticData: { sidebar: false }`), a back link to where New backtest was clicked (Dashboard,
Backtests or Models, the link's history state `from`), the title, the five steps in a row and one
step at a time in a 760 px card. Steps reached so far can be opened again; Next stays off while
the open step has something to fix, and Run backtest exists only on the last one. The header's
New backtest, the dashboard's quick action and Get started link here; a model's Backtest with it
adds `?model=<id>`.

- The choices start from Settings, Backtest defaults (the route's loader reads them): the period,
  the direction, the threshold, the stop loss, fees, slippage and the benchmark. The timeframe and
  the whole input are the model's manifest's.
- Model: every model of the user (`modelsQueryOptions()`), each with its preprocessing in one line
  from `manifest_summary` ("Renko 0.6 × ATR · 30m · 23 features · window 25"). A model without a
  manifest is dimmed and cannot be chosen, with Add manifest (`ManifestDialog`); Upload model is
  `UploadDialog`, which comes back with the new model chosen. Without a choice, the first model
  that can run is chosen.
- Data: one instrument of the model's market (a `24/7` session is crypto, `regular` stocks) from
  the instrument search, the most traded first until one is chosen, named with its icon, symbol and
  name on the button (24 px icon) and in the list (20 px); the timeframe from the model;
  the period as in Settings with Last 12 months, the last whole year and All data. A period that
  starts before the instrument's first candle and the model's warm-up after it (for stocks an
  estimate with room for weekends and holidays), ends before it starts or after today is marked
  with the reason.
- Decision: the threshold, the direction cards and the stop loss of Settings; under Advanced the
  calibration with its window, smoothing (EMA 5, 10, 20), the rule (threshold, hysteresis with
  where it leaves, k-means with its groups, window and refit), confirm and minimum hold in candles
  and the drawdown stop. The windows are checked against the API's limits as they are typed.
- Execution: honest fill (the next candle's open, on by default, with what it means behind the
  i), fees and slippage in percent, and the benchmark: buy and hold of the instrument, the index
  funds of Settings shown as coming soon (`BacktestIn` takes buy and hold only).
- Run: the four steps in a line each with Edit (the instrument with its icon and name), a warning when filling at the signal's close, and
  Run backtest, which sends the setup groups (`POST /api/v1/backtests`, `BacktestIn`: the days
  as `[first day, the day after the last)` in UTC, ending now at the latest, percent as basis
  points) and opens the new backtest's page. A refusal goes back to the step it is about and says
  it in plain words from the problem's extensions (`problems.ts`): the model gone or without a
  manifest on Model; the instrument, the period, the warm-up and too many candles on Data; a
  backtest already running, backtests unavailable or the market data down on Run. Any change
  clears it.
- Below 640 px the labels go above their controls and only the open step keeps its name.

## Backtest page

`/backtests/<id>` (#271, `src/pages/BacktestPage.tsx`, the parts in
`src/features/backtests/result/`) follows the compact backtest page of `mockups/app` (`runHead`, `resA`, `runStats`,
`mountRunCharts`). It takes the whole width, without the sidebar (`staticData: { sidebar: false }`
on its route). Every row of a list of backtests (the Backtests page, the dashboard, a model's page)
is a link to it; one opened from the dashboard or a model's page sets `from` in the history state,
so its back link returns there (else "← Backtests").

- The page reads the result document in the light view (`GET /api/v1/backtests/{id}`,
  `backtestQueryOptions` under `['account', 'backtest', <id>]`). A running backtest is asked for
  again every 2 s; a finished or failed one is never asked for again while it is in the cache. When
  a run the page watched ends, the lists, the dashboard and the models are asked for again.
- The header: "Backtest #N" with its status, then the instrument (icon and symbol), the model (a
  link while it exists),
  the period's first and last UTC day, when it ran and how long. A finished one has
  `[Download JSON | ?]` (the download is `GET /api/v1/backtests/{id}/download`, which the browser
  saves as `candlestack-backtest-<N>.json`; `?` opens https://candlestack.tech/docs/backtest-json)
  and Add to compare.
- Finished: the six first results in a strip (`src/components/FactsStrip.tsx`, the model page's
  too), then the charts and the trades beside every metric of the engine and the setup (one column
  up to 980 px). Running: a moving bar and the setup. Failed: the error and the setup. A 404 says
  there is no such backtest; a test backtest of the seed command says it has no document (the
  API's `no-document`).
- The header names the model with its framework's mark next to it.
- "Price and trades" (#309, `TradesChart.tsx`) is one Lightweight Charts chart in the theme's
  colours with two panes on one time axis and one crosshair: the price (candles or a line,
  the volume at its bottom) and, 110 px under it, the strategy's return against buy and hold
  (dashed) in percent. It zooms with the wheel and scrolls by dragging, and opens on the last
  month. The candles come from the data API (`GET /api/v1/data/instruments/{id}/candles` with
  `start` and `end`), not from the document: their size follows the zoom like TradingView's
  Auto (`priceLevels.ts`): weeks (built from days) for the whole of a long run, then days,
  4 hours (and hours for runs under 30 minutes) and the run's own candles, never finer, with
  some room before it goes finer so it does not flip. The coarse size loads for the whole
  period first, a finer one only around the view, in blocks of about 2000 candles (5000 days)
  on a fixed grid that stay cached for the visit, and only once the chart stopped moving for
  250 ms, so zooming stays far from the 60 requests a minute. The returns are the document's
  series, laid on the candles (a series of days of a long run gives a finer candle a value
  once a day).
- The trades are Buy (accent, under the candle) and Sell (red, over it) arrows: a long opens
  with a buy and closes with a sell, a short the other way round. Only the trades in view have
  arrows: written out up to 14, full size up to 50, smaller up to 160, none above (the legend
  then says to zoom in or pick 1M). Behind the candles, a tint while in a position, the accent
  for a long and red for a short. The trade under the cursor gets bigger arrows, a dashed line
  from its entry to its exit and a line in the legend (side, times, prices, return, candles,
  why it closed); its row in the table is marked. A row clicked shows its trade on the chart
  the same way, with room around it.
- The legend at the top left follows the crosshair (the last candle off the chart): symbol,
  candle size and exchange, open, high, low, close and the change in the candle's colour, the
  volume, then the key and the trades in view. The pane under it has the strategy's and buy
  and hold's return (each a button that hides its line) and the drawdown. The bar under the
  chart: 1W, 1M, 3M, 1Y and All on the left; the candle size, Candles or Line and the Trades
  switch, which hides the arrows and the tint, on the right. The price's line is
  `--price-line`, blue in both themes, as the accent is the strategy's.
- The trades, the newest six, all of them after Show all (in a box that scrolls from 480 px). A
  long-only run has Bought and Sold; one that also goes short Side, Opened and Closed. The box
  drops the candles and why it closed below 720 px of its own width, the prices below 560 px.
- The setup in the builder's steps, as groups between rules with one value per row: the model
  (its framework's mark and name in two lines at most, what it reads, the timeframe, the
  input), the data (the instrument's icon, symbol and name, the first and last day, the
  candles), the decision (calibration and smoothing, the rule's settings, confirm and minimum
  hold when on, the positions) and the execution (the fill, the stops, fees, slippage and the
  benchmark), then how long the run took.
- Limits: the returns are daily for runs of more than 10 000 rows (the light view), and the
  candles carry the data API's adjustment of today (`adjustment=all`), so a stock's prices can
  differ from those the run traded at after a later split or dividend.

## Compare backtests

`/backtests/compare` (#355, #356, `src/pages/ComparePage.tsx`, the parts in
`src/features/backtests/compare/`), from the approved design (`design/mockups/compare-backtests`).

- The choice: up to 4 finished backtests in slots, kept in `localStorage` (`selection.ts`), so it
  stays after a reload in this browser. A slot gives a backtest its colour, and removing another
  moves nothing. One that no longer exists leaves the choice when the page gets a 404.
- Adding: a scale icon on every list of backtests, off with the reason in its title for a running
  or failed one or at 4, and a button on a backtest's page. The Compare panel in the bottom right
  corner of the signed-in pages lists them; Compare opens the page from 2. It can fold to a small
  button and is not on the compare page.
- The page: Equity and Drawdown on two TradingView Lightweight Charts with one crosshair and one
  view of time, a line per backtest from its own start, on the light view of each document; then a
  table. In each row of the results the best value is green and the worst red, the others plain,
  and all plain when they read the same or when no value is better (Trades, Time in market, Buy
  and hold). The setup follows, with a dash where a backtest has no such row.

## Instrument icons

`src/components/InstrumentIcon.tsx` shows the icon of `GET /api/v1/data/instruments/{id}/icon`
(the logo from logo.dev or the backend's fallback, `docs/data.md`). In the signed-in app every
place that names an instrument does it with `src/components/InstrumentSymbol.tsx` (#318): the
icon, the symbol in mono and optionally the name, muted. Alone, the symbol ends in an ellipsis
where it has no room; with the name, the name does; each shows in full in its title.

| Place | Size | Corner radius | Row |
| --- | --- | --- | --- |
| Instrument list, before the symbol | 32 px | 3.2 px | 41 px (4 px padding), as high as without it |
| Home page, the example rows, before the symbol | 32 px | 3.2 px | as in the list |
| Instrument page header, next to the name | 64 px | 6.4 px | - |
| Lists of backtests (Backtests page and its search, dashboard, a model's page), a backtest's header, the builder's instrument list and Run step | 20 px | 2 px | as high as without it |
| A backtest's setup, next to the model's 28 px framework tile | 28 px | 2.8 px | - |
| The builder's instrument button | 24 px | 2.4 px | the 34 px button |
| The dashboard's starred backtests, a backtest's price chart title | 16 px | 1.6 px | - |

- The radius is 10 % of the size. The fallback SVG is square, so the frontend rounds it like a
  logo; a rounding of its own would show the white tile in its corners.
- Every icon sits on a white tile in both themes: many logos have a transparent background, and
  black ones such as Apple's would disappear on the dark background. The image fills the tile
  1 px inside its edge, clipped to its corners, under a 1 px hairline (a solid grey in dark
  mode: a translucent white would leave a light rim), so no logo runs over the edge.
- The image is same-origin (allowed by the CSP), `loading="lazy"`, with a fixed width and height
  so rows do not jump while it loads, and an empty `alt`: the symbol is always next to it.
- When the image does not load at all (e.g. a 502 during a deploy), the tile draws the backend's
  fallback itself (Lucide's `chart-candlestick` in white on dark grey), never a broken image.
- In the list, the icon is not a link of its own: the symbol's link still covers the whole row.

## Instrument list

The list on `/instruments` (`src/pages/InstrumentsPage.tsx`, the table in
`src/features/instruments/InstrumentTable.tsx`, #184) opens with the most traded
instruments first and shows each one's 24 h stats from the API (`price`, `change_24h`,
`volume_24h`, see [data.md](data.md#stats)) and its last 10 daily candles (`spark`, #189):

| Column | Width | Content |
| --- | --- | --- |
| Symbol | 168 px | 32 px icon, 10 px gap, the symbol (the row's link) |
| Price | 128 px, right | 2 decimals from 10 (83,249.35), 4 between 1 and 10 (1.3662), up to 4 significant digits below 1 (0.08438, 0.0000042) |
| 24h | 104 px, right | an 8 px triangle and the change without a sign, 2 decimals (1.51%); up `--up` (#1a7f37, dark #43b049), down `--down`, 0.00% muted without a triangle |
| 10d | 136 px | the last 10 daily candles in 112 × 32 px; from `lg` (1024 px) |
| Volume 24h | 112 px, right | USD with 3 significant digits ($1.26B, $685M, $62K) |
| Name | the rest | muted, one line with an ellipsis |
| Type | 88 px | Stock or Crypto, muted; from `lg` (1024 px) |
| Exchange | 104 px | muted |

- Numbers are in Geist Mono, rows 41 px (cell padding 4 12). Without stats the price, change and
  volume are "—". No market open or closed hint: a stock shows its last price.
- The header is 12/16 medium and muted; the sorted column's header is ink with a 12 px arrow:
  Volume 24h ↓ (default), 24h ↓ (Top gainers) or ↑ (Top losers), Symbol ↑.
- Mini candles (`MiniCandles.tsx`, `candlePaths.ts`): an SVG with one path per colour, a slot
  per day (11.2 px for 10 in 112 px), 5 px bodies (odd, so the 1 px wick sits in the middle; at
  least 1 px high), scaled to the days' low and high with 1 px padding. Up (close ≥ open)
  `--candle-up` (#2e9e44, dark #43b049), down `--candle-down` (#f23645), as on the chart. A day
  without a candle (`null`) is an empty slot; no candles at all is a 1.5 px dashed line (3/3,
  muted at 45 %). Hovering a day shows a 1 px hairline and "9 Oct 2025 · close 82,635.55" (the
  UTC date in the user's date format, the list's price format) above it, Geist Mono 11/16 on ink. The hover layer lies over the
  row's stretched link, so it is a link to the instrument too, with `tabindex="-1"` and
  `aria-hidden`: the symbol stays the row's one tab stop. Screen readers hear "10 daily candles,
  +1.25%" (first open to last close) or "No chart yet".
- Phones (below `md`, 768 px): three columns. The symbol over "exchange · volume" (12/14; "NYSE ·
  no stats yet"), "7d" in 60 px with the last 7 candles in 44 × 24 (3 px bodies, no hover), and
  in 100 px the price over the change with its sign (+1.51%, −0.71%; "No stats yet"). The header says "Symbol · by volume ↓", "Symbol ↑", "Price", or "Price · 24h ↓/↑"
  when sorted by change; those two headers are their own cells, so their `aria-sort` names
  the order a phone shows. Cells have 8 px between the columns and 12 px at the sides.
- From `md` to `lg` the Type and 10d columns are left out, so the name keeps about 100 px at
  768 px (with 10d it would have none); from `lg` every column shows. Nothing scrolls sideways at
  any width.
- While the previous page stands in for the next one, the header's arrow stays with the rows on
  screen and moves when the new rows arrive.
- The formats are pure functions with unit tests in `src/lib/numbers.ts`, which the chart legend
  uses too, so both write an instrument's numbers alike: since #184 the legend groups thousands
  (83,249.35), gives prices between 1 and 10 four decimals like the list (the chart's price
  scale too), writes volumes with 3 significant digits (12.3K) and a falling change with a minus
  sign (−1.20%).
- The 10-day mini chart of the approved design comes with #189.

## Instrument filters

The list filters by type (the market) and exchange and sorts by 24 h volume, change or symbol,
all in the URL (`/instruments?market=stock&exchange=NYSE`, `/instruments?exchange=BINANCE`,
`/instruments?sort=gainers`), behind one
**Filters** button (`src/features/instruments/Filters.tsx`, rules in `filterRules.ts` and
`sorting.ts`, #182, #184):

- The button is 34 × 200 px whatever it says ("All instruments", "Stocks · NYSE", "Crypto ·
  Binance", "Top gainers", "Stocks · Top losers"), so nothing in the row moves when the filters
  change. Its panel is 412 px wide (Type over Sort by in the 128 px left column, Exchange on the
  right, 12 px padding, at most 480 px high), right-aligned under it. On phones Type and Sort by
  sit side by side with Exchange below, the panel is the screen width minus 32 px and tightened
  (8 px padding and gaps, headings 4 px above their list, group labels 24 px, 2 px around the
  separators) to 476 px, so every entry shows without scrolling.
- Sort by: Volume (the default, left out of the URL), Top gainers (`?sort=gainers`), Top losers
  (`?sort=losers`) and Symbol (`?sort=symbol`); the API gets `sort=change`, `-change` or `symbol`.
  The summary adds any order but Volume. With a query the search ranking stays first and the
  order only breaks its ties.
- Exchange: All exchanges, then the stock exchanges (NASDAQ, NYSE, ARCA, AMEX, BATS), then the
  crypto exchanges (Binance). Under Stocks, Binance is disabled and marked "Crypto only"; under
  Crypto, the stock exchanges are disabled and marked "Stocks only": shown, not hidden, so the
  list keeps its shape and says why. Under All every exchange can be picked, and Type stays All.
- Changing the type keeps an exchange it has and drops one it does not; picking an exchange
  never changes the type, and an order changes neither. Every pick starts at page 1 and adds a history entry, so Back undoes
  it; the panel stays open for the second pick.
- A link the panel cannot make (`/instruments?market=crypto&exchange=NYSE`) shows an empty list
  that says why ("No crypto pairs on NYSE", "NYSE lists stocks only.") with a "Show all
  exchanges" link, and the panel shows NYSE chosen but disabled.
- Keyboard: Enter, Space or ArrowDown on the button opens the panel on the chosen type. The
  arrows follow the layout and skip disabled entries: from `sm` up, ArrowUp, ArrowDown, Home
  and End move within a column (Type and Sort by on the left, Exchange on the right) and
  ArrowLeft and ArrowRight go between the columns; on phones ArrowLeft and ArrowRight go
  between Type and Sort by, ArrowDown from either goes on into Exchange and ArrowUp from the
  top of Exchange into Type; a letter jumps to the next entry starting with it, and
  Enter or Space picks. Escape closes the panel and the focus returns to the button; Tab and
  Shift+Tab close it and move on to the element after or before the button, like leaving any
  other control; a click outside closes it too. Each section is an ARIA listbox; the focus
  itself moves between its options.
- The panel is a shadcn/ui Popover (`src/components/ui/popover.tsx`, `@radix-ui/react-popover`):
  it places the panel, keeps it on the screen and closes it on Escape or a click outside.

## Licenses

| Library | License |
| --- | --- |
| React, Vite, Tailwind, shadcn/ui, Radix UI, tw-animate-css, TanStack Query, TanStack Router, openapi-typescript | MIT |
| Testing Library, jsdom (tests only) | MIT |
| TypeScript, Lightweight Charts | Apache 2.0 |
| Geist font | SIL Open Font License 1.1 |

Lightweight Charts asks for a TradingView attribution, and logo.dev's free plan for a link to
Logo.dev (the instrument icons, `docs/data.md`). Both are a line in the footer of every page
(`src/components/Footer.tsx`); the chart is created with `attributionLogo: false`, so it shows no
logo of its own.

## Prototype

A small prototype (not in this PR) shows 500 BTCUSDT candles with volume from the prod API
using this stack. The build is about 130 kB gzipped.
