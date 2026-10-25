import { keepPreviousData, queryOptions } from '@tanstack/react-query';

import { apiUrl, get, patch } from './client';
import type { components } from './schema';

// A backtest as the API sends it. Returns and rates are fractions (0.246 is 24.6 %); the results
// are null for a backtest that did not finish.
export type Backtest = components['schemas']['BacktestOut'];
export type BacktestsPage = components['schemas']['BacktestsPage'];
export type BacktestShow = components['schemas']['BacktestShow'];
export type BacktestSort = components['schemas']['BacktestSort'];
export type SortOrder = components['schemas']['SortOrder'];
export type Representation = components['schemas']['Representation'];
export type Dashboard = components['schemas']['DashboardOut'];
// One backtest as its page shows it: the result document (docs/engine.md) in the light view.
export type BacktestDocument = components['schemas']['BacktestDocument'];
export type BacktestStatus = components['schemas']['BacktestStatus'];
// The results Best backtests ranks by.
export type BestMetric = keyof Dashboard['best'];

// What the Backtests page asks for; a value left out is the API's default (all, newest first,
// page 1).
export type BacktestsParams = {
  q?: string | undefined;
  show?: Exclude<BacktestShow, 'all'> | undefined;
  model?: string | undefined;
  sort?: Exclude<BacktestSort, 'default'> | undefined;
  order?: SortOrder | undefined;
  page?: number | undefined;
};

const backtestPath = (id: string) => `/v1/backtests/${encodeURIComponent(id)}`;

// GET /api/v1/backtests: a page of 10, with the counts and the models the filters offer.
function listBacktests(params: BacktestsParams, signal?: AbortSignal): Promise<BacktestsPage> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') query.set(key, String(value));
  }
  const text = query.toString();
  return get<BacktestsPage>(text === '' ? '/v1/backtests' : `/v1/backtests?${text}`, signal);
}

// Under ['account', 'backtests', ...] so signing out forgets them and a star can update every
// page at once. While the next page, search or order loads, the previous one stays on screen.
export function backtestsQueryOptions(params: BacktestsParams) {
  return queryOptions({
    queryKey: ['account', 'backtests', params],
    queryFn: ({ signal }) => listBacktests(params, signal),
    placeholderData: keepPreviousData,
  });
}

// GET /api/v1/dashboard: the counts, the newest, the best and the starred backtests at once. The
// sidebar's count comes from it too.
export function dashboardQueryOptions() {
  return queryOptions({
    queryKey: ['account', 'dashboard'],
    queryFn: ({ signal }) => get<Dashboard>('/v1/dashboard', signal),
  });
}

// PATCH /api/v1/backtests/{id}: stars or unstars the backtest.
export function updateBacktest(id: string, update: components['schemas']['BacktestUpdate']): Promise<Backtest> {
  return patch<Backtest>(backtestPath(id), update);
}

// While a backtest runs, its page asks again this often until it finished or failed.
export const POLL_MS = 2000;

// GET /api/v1/backtests/{id}: the result document in the light view, whatever its status. A
// running one is asked for again every POLL_MS; a finished or failed one never changes, so it is
// not asked for again while it is in the cache.
export function backtestQueryOptions(id: string) {
  return queryOptions({
    queryKey: ['account', 'backtest', id],
    queryFn: ({ signal }) => get<BacktestDocument>(backtestPath(id), signal),
    staleTime: ({ state }) => (state.data?.backtest.status === 'running' ? 0 : Infinity),
    refetchInterval: ({ state }) => (state.data?.backtest.status === 'running' ? POLL_MS : false),
  });
}

// GET /api/v1/backtests/{id}/download: the whole document, every row and trade, which the browser
// saves as candlestack-backtest-<number>.json (the API's Content-Disposition).
export function backtestDownloadUrl(id: string): string {
  return apiUrl(`${backtestPath(id)}/download`);
}
