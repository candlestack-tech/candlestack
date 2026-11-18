import type { QueryClient } from '@tanstack/react-query';
import {
  Link,
  Outlet,
  createRootRouteWithContext,
  createRoute,
  lazyRouteComponent,
  redirect,
} from '@tanstack/react-router';

import {
  authorizationRequestQueryOptions,
  deniedSignInQueryOptions,
  instrumentQueryOptions,
  meQueryOptions,
  preferencesQueryOptions,
} from '@/api';
import { Layout } from '@/components/Layout';
import { RouteError } from '@/components/RouteStates';
import { AppearanceSync } from '@/features/preferences';
import { HomePage } from '@/pages/HomePage';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { SignInPage } from '@/pages/SignInPage';
import { requireSignedIn, skipSignInWhenSignedIn } from '@/routes/accountGuards';
import { validateBacktestsSearch } from '@/routes/backtestsSearch';
import { validateBuilderSearch } from '@/routes/builderSearch';
import { validateCompareSearch } from '@/routes/compareSearch';
import { consentRequestOf, isConsentPath, validateConsentSearch } from '@/routes/consentSearch';
import { validateExperimentsSearch } from '@/routes/experimentsSearch';
import { validateInstrumentSearch } from '@/routes/instrumentSearch';
import { validateInstrumentsSearch } from '@/routes/instrumentsSearch';
import { FIRST_SECTION, isSettingsSection, showsPreferences, validateSettingsSearch } from '@/routes/settingsSections';
import { validateSignInSearch } from '@/routes/signInSearch';

// Every route gets the query client, so a route loader can fetch its data before it renders.
export type RouterContext = { queryClient: QueryClient };

// Whether someone is signed in decides the public header's buttons, so it is known before the
// first page shows. A failed answer is no reason to hold back a public page: the header then
// offers Sign in, and the signed-in pages ask again. On every page, signed in, the account's theme
// and Reduce motion apply.
const rootRoute = createRootRouteWithContext<RouterContext>()({
  beforeLoad: ({ context }) => context.queryClient.prefetchQuery(meQueryOptions()),
  component: () => (
    <>
      <AppearanceSync />
      <Outlet />
    </>
  ),
  notFoundComponent: () => (
    <Layout>
      <NotFoundPage />
    </Layout>
  ),
});

// The public pages, under the site's header (Layout.tsx).
const publicRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: '_public',
  component: Layout,
});

// The public start page. Before #199 it was the instrument list: an old link with the list's
// search (/?q=btc&market=crypto&page=2) goes on to /instruments with it, in place of itself.
const homeRoute = createRoute({
  getParentRoute: () => publicRoute,
  path: '/',
  beforeLoad: ({ location }) => {
    // The route declares no search of its own, so the raw params are read as the list's input.
    const search = validateInstrumentsSearch(location.search as Parameters<typeof validateInstrumentsSearch>[0]);
    if (Object.keys(search).length > 0) throw redirect({ to: '/instruments', search, replace: true });
  },
  component: HomePage,
});

// /signin?next=/backtests&error=denied. An open session skips it. After a denied sign-in the
// loader fetches who was turned away, and the app when the sign-in started on its consent page;
// when that fails, the page shows its buttons instead. On its way to the consent page it hands
// over to that page, which signs in itself and names the app.
const signInRoute = createRoute({
  getParentRoute: () => publicRoute,
  path: '/signin',
  validateSearch: validateSignInSearch,
  beforeLoad: ({ context, search }) => {
    if (search.error === undefined && search.next !== undefined && isConsentPath(search.next))
      throw redirect({ href: search.next, replace: true });
    return skipSignInWhenSignedIn({ queryClient: context.queryClient, search });
  },
  loaderDeps: ({ search }) => ({ error: search.error, next: search.next }),
  loader: ({ context, deps }) => {
    if (deps.error !== 'denied') return undefined;
    const request = consentRequestOf(deps.next);
    return Promise.all([
      context.queryClient.prefetchQuery(deniedSignInQueryOptions()),
      request === undefined ? undefined : context.queryClient.prefetchQuery(authorizationRequestQueryOptions(request)),
    ]);
  },
  component: SignInPage,
});

// /oauth/consent?request=<id>: an app (Claude, ChatGPT) asks for access to the account, on its own
// card like the sign-in page (design/mockups/api-mcp, #consent). No guard: signed out, the card
// offers the sign-in and names the app, as reading the request needs no session. The loader asks
// for the request while the page's code loads.
const consentRoute = createRoute({
  getParentRoute: () => publicRoute,
  path: '/oauth/consent',
  validateSearch: validateConsentSearch,
  loaderDeps: ({ search }) => ({ request: search.request }),
  loader: ({ context, deps }) =>
    deps.request === undefined
      ? undefined
      : context.queryClient.prefetchQuery(authorizationRequestQueryOptions(deps.request)),
  component: lazyRouteComponent(() => import('@/pages/ConsentPage'), 'ConsentPage'),
});

// The signed-in pages, under the app header beside the sidebar. Signed out, the guard sends the
// browser to the sign-in page, which returns to the page asked for. When the API cannot say
// who is signed in, the error shows under the site's header: the app's needs the user. Loaded on
// demand: a visitor of the public pages never downloads the app.
const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: '_app',
  beforeLoad: ({ context, location }) => requireSignedIn({ queryClient: context.queryClient, location }),
  errorComponent: (props) => (
    <Layout>
      <RouteError {...props} />
    </Layout>
  ),
  component: lazyRouteComponent(() => import('@/features/workspace'), 'AppLayout'),
});

const dashboardRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/dashboard',
  component: lazyRouteComponent(() => import('@/pages/DashboardPage'), 'DashboardPage'),
});

// /backtests?q=aapl&show=starred&sort=sharpe&order=asc&page=2; broken values are dropped.
const backtestsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/backtests',
  validateSearch: validateBacktestsSearch,
  component: lazyRouteComponent(() => import('@/pages/BacktestsPage'), 'BacktestsPage'),
});

// /experiments?q=nvda&mine=true&market=crypto&sort=sharpe&page=2: the backtests the beta published;
// broken values are dropped. Cards or Table is the browser's, not the URL's.
const experimentsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/experiments',
  validateSearch: validateExperimentsSearch,
  component: lazyRouteComponent(() => import('@/pages/ExperimentsPage'), 'ExperimentsPage'),
});

// /backtests/new?model=mdl_7f3k2m9qx4: the builder, without the sidebar (mockups/app, pageBuilder).
// It starts from the account's Backtest defaults, so they load first.
const newBacktestRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/backtests/new',
  validateSearch: validateBuilderSearch,
  staticData: { sidebar: false },
  loader: ({ context }) => context.queryClient.ensureQueryData(preferencesQueryOptions()),
  component: lazyRouteComponent(() => import('@/pages/NewBacktestPage'), 'NewBacktestPage'),
});

// /backtests/compare?ids=bt_7f3k2m9qx4,bt_t9n8v7d6a5: the chosen backtests side by side, without the
// sidebar. A link from elsewhere (an agent's, a copied address) chooses the backtests it names before
// the page shows; from then on the page writes the choice into the URL (useComparedInUrl).
const compareRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/backtests/compare',
  validateSearch: validateCompareSearch,
  staticData: { sidebar: false },
  loaderDeps: ({ search }) => ({ ids: search.ids }),
  loader: async ({ context, deps, cause, location }) => {
    if (cause !== 'enter' || deps.ids === undefined || location.state.comparedInUrl) return;
    const [me, { claimCompare, openInCompare }] = await Promise.all([
      context.queryClient.ensureQueryData(meQueryOptions()),
      import('@/features/backtests'),
    ]);
    // The choice becomes the signed-in account's first, as the panel would make it, so another
    // account's left in this browser does not empty the link's afterwards.
    if (me) claimCompare(me.id);
    openInCompare(deps.ids.split(','));
  },
  component: lazyRouteComponent(() => import('@/pages/ComparePage'), 'ComparePage'),
});

// /backtests/bt_7f3k2m9qx4: one backtest's page, without the sidebar (mockups/app, pageRun), the
// user's or another account's published one, read only. An ID neither is says so on the page.
const backtestRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/backtests/$backtestId',
  staticData: { sidebar: false },
  component: lazyRouteComponent(() => import('@/pages/BacktestPage'), 'BacktestPage'),
});

const modelsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/models',
  component: lazyRouteComponent(() => import('@/pages/ModelsPage'), 'ModelsPage'),
});

// /models/mdl_7f3k2m9qx4: one model's page. An ID the user has no model with says so on the page.
const modelRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/models/$modelId',
  component: lazyRouteComponent(() => import('@/pages/ModelPage'), 'ModelPage'),
});

// /upload/<token>: the upload page of a one-time link (#406), without the sidebar. The token is the
// link's secret: the API filters it from its logs, and from Sentry the sign-in's `next` it rides in
// while signed out.
const uploadRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/upload/$token',
  staticData: { sidebar: false },
  component: lazyRouteComponent(() => import('@/pages/UploadPage'), 'UploadPage'),
});

// /instruments?q=btc&market=crypto&page=2; broken values are dropped in validateSearch. Market
// data is for signed-in accounts only (#340), so the list is part of the app.
const instrumentsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/instruments',
  validateSearch: validateInstrumentsSearch,
  component: lazyRouteComponent(() => import('@/pages/InstrumentsPage'), 'InstrumentsPage'),
});

// /instrument/crypto:BTCUSDT?tf=1h: one instrument's page, without the sidebar like a backtest's,
// filling the height for its chart. The search params are checked once, in validateSearch; the
// loader fetches the instrument while the page's code loads, into the same cache entry the page
// reads. When it fails (an unknown instrument, the rate limit), the way out is the list.
const instrumentRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/instrument/$id',
  validateSearch: validateInstrumentSearch,
  staticData: { sidebar: false, fill: true },
  loader: ({ context, params }) => context.queryClient.ensureQueryData(instrumentQueryOptions(params.id)),
  errorComponent: (props) => <RouteError {...props} back={<Link to="/instruments">Back to instruments</Link>} />,
  component: lazyRouteComponent(() => import('@/pages/InstrumentPage'), 'InstrumentPage'),
});

const openSettingsSection = (section: string) =>
  redirect({ to: '/settings/$section', params: { section }, replace: true });

// /settings opens its first section.
const settingsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/settings',
  beforeLoad: () => {
    throw openSettingsSection(FIRST_SECTION);
  },
});

// /settings/appearance: one section at a time, its tab a link. An unknown section opens the first.
// The sections with the account's preferences load them first; ?error= says how adding a sign-in
// method ended.
const settingsSectionRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/settings/$section',
  validateSearch: validateSettingsSearch,
  beforeLoad: ({ params }) => {
    if (!isSettingsSection(params.section)) throw openSettingsSection(FIRST_SECTION);
  },
  loader: ({ context, params }) =>
    isSettingsSection(params.section) && showsPreferences(params.section)
      ? context.queryClient.ensureQueryData(preferencesQueryOptions())
      : undefined,
  component: lazyRouteComponent(() => import('@/pages/SettingsPage'), 'SettingsPage'),
});

export const routeTree = rootRoute.addChildren([
  publicRoute.addChildren([homeRoute, signInRoute, consentRoute]),
  appRoute.addChildren([
    dashboardRoute,
    backtestsRoute,
    newBacktestRoute,
    compareRoute,
    backtestRoute,
    experimentsRoute,
    modelsRoute,
    modelRoute,
    uploadRoute,
    instrumentsRoute,
    instrumentRoute,
    settingsRoute,
    settingsSectionRoute,
  ]),
]);
