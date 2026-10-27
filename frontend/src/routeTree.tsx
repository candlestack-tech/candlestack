import type { QueryClient } from '@tanstack/react-query';
import {
  Link,
  Outlet,
  createRootRouteWithContext,
  createRoute,
  lazyRouteComponent,
  redirect,
} from '@tanstack/react-router';

import { deniedSignInQueryOptions, instrumentQueryOptions, meQueryOptions, preferencesQueryOptions } from '@/api';
import { Layout } from '@/components/Layout';
import { RouteError } from '@/components/RouteStates';
import { AppearanceSync } from '@/features/preferences';
import { HomePage } from '@/pages/HomePage';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { SignInPage } from '@/pages/SignInPage';
import { requireSignedIn, skipSignInWhenSignedIn } from '@/routes/accountGuards';
import { validateBacktestsSearch } from '@/routes/backtestsSearch';
import { validateBuilderSearch } from '@/routes/builderSearch';
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

// The public start page.
const homeRoute = createRoute({
  getParentRoute: () => publicRoute,
  path: '/',
  component: HomePage,
});

// /signin?next=/backtests&error=denied. An open session skips it. After a denied sign-in the
// loader fetches who was turned away; when that fails, the page shows its buttons instead.
const signInRoute = createRoute({
  getParentRoute: () => publicRoute,
  path: '/signin',
  validateSearch: validateSignInSearch,
  beforeLoad: ({ context, search }) => skipSignInWhenSignedIn({ queryClient: context.queryClient, search }),
  loaderDeps: ({ search }) => ({ error: search.error }),
  loader: ({ context, deps }) =>
    deps.error === 'denied' ? context.queryClient.prefetchQuery(deniedSignInQueryOptions()) : undefined,
  component: SignInPage,
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

// /backtests/bt_7f3k2m9qx4: one backtest's page, without the sidebar (mockups/app, pageRun). An ID
// the user has no backtest with says so on the page.
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
  publicRoute.addChildren([homeRoute, signInRoute]),
  appRoute.addChildren([
    dashboardRoute,
    backtestsRoute,
    newBacktestRoute,
    backtestRoute,
    modelsRoute,
    modelRoute,
    instrumentsRoute,
    instrumentRoute,
    settingsRoute,
    settingsSectionRoute,
  ]),
]);
