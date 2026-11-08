import { useMutation, useQuery, useQueryClient, type QueryClient, type QueryKey } from '@tanstack/react-query';

import {
  backtestQueryOptions,
  backtestsQueryOptions,
  cancelBacktest,
  dashboardQueryOptions,
  deleteBacktest,
  deleteBacktests,
  isGone,
  updateBacktest,
  type Backtest,
  type BacktestsPage,
  type BacktestsParams,
  type Dashboard,
} from '@/api';

import { nameInCompare, removeFromCompare } from './compare/selection';

// Every page of the list, whatever its search and filters, the dashboard, and every page and the
// cards of Experiments.
const LISTS: QueryKey = ['account', 'backtests'];
const DASHBOARD = dashboardQueryOptions().queryKey;
const EXPERIMENTS: QueryKey = ['account', 'experiments'];
const MODELS: QueryKey = ['account', 'models'];

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
// Renames the backtest, or with a name of null goes back to its generated one: its page and its
// slot in the comparison say so at once from the API's answer, and the lists, the dashboard and
// Experiments are asked for again.
export function useRenameBacktest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name }: { id: string; name: string | null }) => updateBacktest(id, { name }),
    onSuccess: (changed, { id }) => {
      queryClient.setQueryData(
        backtestQueryOptions(id).queryKey,
        (doc) =>
          doc && { ...doc, backtest: { ...doc.backtest, name: changed.name, generated_name: changed.generated_name } },
      );
      nameInCompare(id, changed.name);
      return Promise.all([
        queryClient.invalidateQueries({ queryKey: LISTS }),
        queryClient.invalidateQueries({ queryKey: DASHBOARD }),
        queryClient.invalidateQueries({ queryKey: EXPERIMENTS }),
      ]);
    },
  });
}

// Publishes the backtest to Experiments or takes it back: its page says so at once from the API's
// answer, and the lists, the dashboard and Experiments are asked for again.
export function usePublishBacktest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, published }: { id: string; published: boolean }) => updateBacktest(id, { public: published }),
    onSuccess: (changed, { id }) => {
      queryClient.setQueryData(
        backtestQueryOptions(id).queryKey,
        (doc) =>
          doc && { ...doc, backtest: { ...doc.backtest, public: changed.public, published_at: changed.published_at } },
      );
      return Promise.all([
        queryClient.invalidateQueries({ queryKey: LISTS }),
        queryClient.invalidateQueries({ queryKey: DASHBOARD }),
        queryClient.invalidateQueries({ queryKey: EXPERIMENTS }),
      ]);
    },
  });
}

// Cancels a queued backtest: its page shows the API's answer at once, failed with the cancel, and
// that end of a run it watched asks for the lists, the dashboard and the models again
// (useBacktestResult). A 409 means it has begun to run (or ended) meanwhile: its page is asked for
// again, so it shows that.
export function useCancelBacktest(id: string) {
  const queryClient = useQueryClient();
  const key = backtestQueryOptions(id).queryKey;
  return useMutation({
    mutationFn: () => cancelBacktest(id),
    onSuccess: (doc) => {
      queryClient.setQueryData(key, doc);
    },
    onError: () => queryClient.invalidateQueries({ queryKey: key }),
  });
}

// Deletes one backtest (DELETE, as its page does) or several at once (POST .../delete), and gives
// back the IDs that are gone now. One that was gone already (deleted in another tab, say) counts
// as deleted.
async function deleteAll(ids: readonly string[]): Promise<readonly string[]> {
  const [only, ...more] = ids;
  if (only === undefined || more.length > 0) {
    const answer = await deleteBacktests(ids);
    return [...answer.deleted, ...answer.not_found];
  }
  await deleteBacktest(only).catch((error: unknown) => {
    if (!isGone(error)) throw error;
  });
  return ids;
}

// Deletes the user's backtests: they leave the comparison at once, and the lists, the dashboard,
// Experiments and the models (their backtest count and best Sharpe) are asked for again. A deleted
// backtest's own document stays until its page has gone (forgetBacktest), so the page does not ask
// for it again meanwhile.
export function useDeleteBacktests() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteAll,
    onSuccess: (gone) => {
      for (const id of gone) removeFromCompare(id);
      for (const queryKey of [LISTS, DASHBOARD, EXPERIMENTS, MODELS]) void queryClient.invalidateQueries({ queryKey });
    },
  });
}

// Drops a deleted backtest's document, so going back to its page asks the API, which says it is
// not found.
export function forgetBacktest(queryClient: QueryClient, id: string) {
  queryClient.removeQueries({ queryKey: backtestQueryOptions(id).queryKey });
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
