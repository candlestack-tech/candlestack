import { useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query';

import {
  backtestsQueryOptions,
  dashboardQueryOptions,
  updateBacktest,
  type Backtest,
  type BacktestsPage,
  type BacktestsParams,
  type Dashboard,
} from '@/api';

// Every page of the list, whatever its search and filters, and the dashboard.
const LISTS: QueryKey = ['account', 'backtests'];
const DASHBOARD = dashboardQueryOptions().queryKey;

// A page of the user's backtests for what the Backtests page shows.
export function useBacktestsPage(params: BacktestsParams) {
  return useQuery(backtestsQueryOptions(params));
}

// Everything the dashboard shows; the sidebar counts the backtests from it.
export function useDashboard() {
  return useQuery(dashboardQueryOptions());
}

const restar = (list: Backtest[], changed: Backtest) =>
  list.map((item) => (item.id === changed.id ? { ...item, starred: changed.starred } : item));

const countStarred = <T extends { counts: Dashboard['counts'] }>(data: T, starred: boolean): T => ({
  ...data,
  counts: { ...data.counts, starred: Math.max(0, data.counts.starred + (starred ? 1 : -1)) },
});

// A page after the star changed: with the Starred filter on, a backtest that loses its star
// leaves the page.
export function starOnPage(page: BacktestsPage, params: BacktestsParams, changed: Backtest): BacktestsPage {
  const counted = countStarred(page, changed.starred);
  if (params.show !== 'starred' || changed.starred) return { ...counted, backtests: restar(page.backtests, changed) };
  const backtests = page.backtests.filter((item) => item.id !== changed.id);
  return { ...counted, backtests, total: page.total - (page.backtests.length - backtests.length) };
}

// The best Sharpe first, those without one last, then the newest: the order of Starred backtests.
const bySharpe = (a: Backtest, b: Backtest) => (b.sharpe ?? -Infinity) - (a.sharpe ?? -Infinity) || b.number - a.number;

// The dashboard after the star changed: Starred backtests gains or loses it at once.
export function starOnDashboard(dashboard: Dashboard, changed: Backtest): Dashboard {
  const { starred } = dashboard;
  const others = starred.backtests.filter((item) => item.id !== changed.id);
  const backtests = changed.starred ? [...others, changed].sort(bySharpe).slice(0, 4) : others;
  return {
    ...countStarred(dashboard, changed.starred),
    last: restar(dashboard.last, changed),
    best: {
      sharpe: restar(dashboard.best.sharpe, changed),
      total_return: restar(dashboard.best.total_return, changed),
      max_drawdown: restar(dashboard.best.max_drawdown, changed),
      win_rate: restar(dashboard.best.win_rate, changed),
    },
    starred: { backtests, total: Math.max(0, starred.total + (changed.starred ? 1 : -1)) },
  };
}

// Stars or unstars at once everywhere it shows, with the counts; everything goes back when the
// API refuses, and is asked for again either way.
// Renames the backtest, or takes its name away; what shows it is asked for again.
export function useRenameBacktest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name }: { id: string; name: string | null }) => updateBacktest(id, { name }),
    onSuccess: (_backtest, { id }) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: LISTS }),
        queryClient.invalidateQueries({ queryKey: DASHBOARD }),
        queryClient.invalidateQueries({ queryKey: ['account', 'backtest', id] }),
      ]),
  });
}

export function useStarBacktest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (backtest: Backtest) => updateBacktest(backtest.id, { starred: !backtest.starred }),
    onMutate: async (backtest) => {
      await Promise.all([
        queryClient.cancelQueries({ queryKey: LISTS }),
        queryClient.cancelQueries({ queryKey: DASHBOARD }),
      ]);
      const changed = { ...backtest, starred: !backtest.starred };
      const pages = queryClient.getQueriesData<BacktestsPage>({ queryKey: LISTS });
      for (const [key, page] of pages) {
        if (page) queryClient.setQueryData(key, starOnPage(page, key[2] as BacktestsParams, changed));
      }
      const dashboard = queryClient.getQueryData(DASHBOARD);
      if (dashboard) queryClient.setQueryData(DASHBOARD, starOnDashboard(dashboard, changed));
      return { pages, dashboard };
    },
    onError: (_error, _backtest, context) => {
      for (const [key, page] of context?.pages ?? []) queryClient.setQueryData(key, page);
      queryClient.setQueryData(DASHBOARD, context?.dashboard);
    },
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: LISTS }),
        queryClient.invalidateQueries({ queryKey: DASHBOARD }),
      ]),
  });
}
