import { keepPreviousData, queryOptions } from '@tanstack/react-query';

import { del, get, getBlob, patch, postFor, postJson } from './client';
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
// A step of a backtest's run (candles, queue, model, trades) and how far it got.
export type BacktestStep = components['schemas']['BacktestStep'];
// What a new backtest is made of: the setup groups of the result document.
export type BacktestIn = components['schemas']['BacktestIn'];
// What a bulk delete did: the IDs deleted and those the user does not have.
export type BacktestsDeleted = components['schemas']['BacktestsDeleted'];
// The results Best backtests ranks by.
export type BestMetric = keyof Dashboard['best'];

// What a compared backtest ran on and with (`setup` of GET /api/v1/compare), from its document.
export type ComparedSetup = {
  instrument: string;
  model: string;
  reads: string | null;
  input: number[] | null;
  timeframe: string;
  period: BacktestDocument['data']['period'];
  decision: BacktestDocument['decision'];
  execution: BacktestDocument['execution'];
  benchmarks: string[];
};
export type ComparedBacktest = Omit<components['schemas']['ComparedBacktest'], 'setup'> & { setup: ComparedSetup };
// Backtests side by side as the server compares them: the statistics with the best and the worst
// marked, the setups, and the curves on one time axis.
export type Comparison = Omit<components['schemas']['CompareOut'], 'backtests'> & { backtests: ComparedBacktest[] };
export type CompareMetric = Comparison['metrics'][number];

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

// Whether a backtest has not ended yet: queued or running.
export const isUnderway = (status: BacktestStatus | undefined) => status === 'queued' || status === 'running';

// GET /api/v1/backtests: a page of 10, with the counts and the models the filters offer.
function listBacktests(params: BacktestsParams, signal?: AbortSignal): Promise<BacktestsPage> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') query.set(key, String(value));
  }
  const text = query.toString();
  return get<BacktestsPage>(text === '' ? '/v1/backtests' : `/v1/backtests?${text}`, signal);
}

// While a list shows a backtest that waits or runs, it is asked for again this often, so its
// place in the queue and its status keep up.
const LIST_POLL_MS = 5000;

const listPoll = (backtests: readonly Backtest[] | undefined) =>
  backtests?.some((backtest) => isUnderway(backtest.status)) ? LIST_POLL_MS : false;

// Under ['account', 'backtests', ...] so signing out forgets them and a star can update every
// page at once. While the next page, search or order loads, the previous one stays on screen.
export function backtestsQueryOptions(params: BacktestsParams) {
  return queryOptions({
    queryKey: ['account', 'backtests', params],
    queryFn: ({ signal }) => listBacktests(params, signal),
    placeholderData: keepPreviousData,
    refetchInterval: ({ state }) => listPoll(state.data?.backtests),
  });
}

// GET /api/v1/dashboard: the counts, the newest, the best and the starred backtests at once. The
// sidebar's count comes from it too.
export function dashboardQueryOptions() {
  return queryOptions({
    queryKey: ['account', 'dashboard'],
    queryFn: ({ signal }) => get<Dashboard>('/v1/dashboard', signal),
    refetchInterval: ({ state }) => listPoll(state.data && [...state.data.last, ...state.data.starred.backtests]),
  });
}

// PATCH /api/v1/backtests/{id}: stars, unstars or renames the backtest.
export function updateBacktest(id: string, update: components['schemas']['BacktestUpdate']): Promise<Backtest> {
  return patch<Backtest>(backtestPath(id), update);
}

// DELETE /api/v1/backtests/{id}: deletes the user's backtest, whatever its status. A queued one
// leaves the queue, a running one is stopped first, a published one leaves Experiments. One the
// user does not have (deleted already, say) is 404 `not-found`.
export function deleteBacktest(id: string): Promise<void> {
  return del(backtestPath(id));
}

// POST /api/v1/backtests/delete: deletes 1 to 100 of the user's backtests at once, as
// deleteBacktest does one. IDs the user does not have come back in `not_found`, not as an error.
export const deleteBacktests = (ids: readonly string[]) => postJson<BacktestsDeleted>('/v1/backtests/delete', { ids });

// While a backtest waits or runs, its page asks again this often until it finished or failed.
export const POLL_MS = 2000;

// GET /api/v1/backtests/{id}: the result document in the light view, whatever its status. A
// queued or running one is asked for again every POLL_MS; a finished or failed one never changes,
// so it is not asked for again while it is in the cache.
export function backtestQueryOptions(id: string) {
  return queryOptions({
    queryKey: ['account', 'backtest', id],
    queryFn: ({ signal }) => get<BacktestDocument>(backtestPath(id), signal),
    staleTime: ({ state }) => (isUnderway(state.data?.backtest.status) ? 0 : Infinity),
    refetchInterval: ({ state }) => (isUnderway(state.data?.backtest.status) ? POLL_MS : false),
  });
}

// GET /api/v1/backtests/{id}/download: the whole document, every row and trade, as a Blob. The page
// saves it under its own name (candlestack-backtest-<number>.json): some browsers drop the API's
// Content-Disposition name and save a bare id without an extension.
export const getBacktestFile = (id: string) => getBlob(`${backtestPath(id)}/download`);

// POST /api/v1/backtests: starts a backtest and answers with its document, `running`, or
// `queued` while another backtest of the user runs.
export const startBacktest = (body: BacktestIn) => postJson<BacktestDocument>('/v1/backtests', body);

// GET /api/v1/compare: the chosen backtests side by side, from what each stored when it ran (about
// 20 KB for four). While another choice loads, the last answer stays on screen.
export function compareQueryOptions(ids: readonly string[]) {
  return queryOptions({
    queryKey: ['account', 'compare', ids],
    queryFn: ({ signal }) => get<Comparison>(`/v1/compare?ids=${ids.map(encodeURIComponent).join(',')}`, signal),
    enabled: ids.length > 0,
    placeholderData: keepPreviousData,
  });
}

// POST /api/v1/backtests/{id}/cancel: takes the user's queued backtest out of the queue, so it
// never runs, and answers with its document, `failed` with the cancel in its error. One that has
// begun to run or has ended is 409 `backtest-not-queued`.
export const cancelBacktest = (id: string) => postFor<BacktestDocument>(`${backtestPath(id)}/cancel`);
