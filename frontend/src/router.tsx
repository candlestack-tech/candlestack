import { createRouter, type RouterHistory } from '@tanstack/react-router';

import { queryClient } from '@/api';
import { RouteError, RoutePending } from '@/components/RouteStates';

import { routeTree, type RouterContext } from './routeTree';

// The app's router; tests make their own on a memory history with a fresh query client.
export const createAppRouter = (context: RouterContext, history?: RouterHistory) =>
  createRouter({
    routeTree,
    context,
    history,
    // Start loading a page when the user hovers or focuses its link.
    defaultPreload: 'intent',
    // TanStack Query decides when data is stale, not the router.
    defaultPreloadStaleTime: 0,
    // Instrument ids contain ':' (crypto:BTCUSDT); keep it readable instead of %3A.
    pathParamsAllowedCharacters: [':'],
    defaultPendingComponent: RoutePending,
    defaultErrorComponent: RouteError,
    // An address that matches no page shows the public "Page not found", also under /dashboard/…,
    // instead of the signed-in layout around it.
    notFoundMode: 'root',
    // Back and forward return to where the page was scrolled.
    scrollRestoration: true,
  });

export const router = createAppRouter({ queryClient });

// Makes routes, params and search params type-checked everywhere (Link, useParams, useSearch).
declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
  // New backtest sets the part of the app it was opened in (dashboard, backtests, models), for
  // the builder's back link; a link to a backtest from the dashboard or a model's page sets
  // 'dashboard' or 'model'.
  interface HistoryState {
    from?: 'dashboard' | 'backtests' | 'models' | 'model' | 'compare';
    // What the back link of the compare page calls the page it was opened from.
    comparedFrom?: string;
  }
  // A page of the signed-in app without the sidebar sets sidebar: false (the builder); one that
  // fills the height left under the header sets fill: true (the instrument page, for its chart).
  interface StaticDataRouteOption {
    sidebar?: false;
    fill?: true;
  }
}
