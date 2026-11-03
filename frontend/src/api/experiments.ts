import { infiniteQueryOptions, keepPreviousData, queryOptions } from '@tanstack/react-query';

import { get } from './client';
import type { components } from './schema';

// A published backtest as Experiments shows it to every account of the beta: its author by
// display name, the model by name and what it reads, never the file.
export type Experiment = components['schemas']['ExperimentOut'];
export type ExperimentsPage = components['schemas']['ExperimentsPage'];
export type ExperimentSort = components['schemas']['ExperimentSort'];
export type SetupTimeframe = components['schemas']['SetupTimeframe'];

// What Experiments asks for; a value left out is the API's default (everyone's, every market,
// newest first, page 1).
export type ExperimentsParams = {
  q?: string | undefined;
  mine?: true | undefined;
  author?: string | undefined;
  market?: components['schemas']['InstrumentMarket'] | undefined;
  symbol?: string | undefined;
  representation?: components['schemas']['Representation'] | undefined;
  timeframe?: SetupTimeframe | undefined;
  sort?: Exclude<ExperimentSort, 'newest'> | undefined;
  order?: components['schemas']['SortOrder'] | undefined;
  page?: number | undefined;
};

// GET /api/v1/experiments: a page of 12, with the counts and what the filters offer.
function listExperiments(params: ExperimentsParams, signal?: AbortSignal): Promise<ExperimentsPage> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') query.set(key, String(value));
  }
  const text = query.toString();
  return get<ExperimentsPage>(text === '' ? '/v1/experiments' : `/v1/experiments?${text}`, signal);
}

// Under ['account', 'experiments', ...] so signing out forgets them and a publish asks for all of
// them again. The table's page: while the next one loads, the previous one stays on screen.
export function experimentsQueryOptions(params: ExperimentsParams) {
  return queryOptions({
    queryKey: ['account', 'experiments', 'page', params],
    queryFn: ({ signal }) => listExperiments(params, signal),
    placeholderData: keepPreviousData,
  });
}

// The cards: page after page, each Load more adding the next one under those already shown.
export function experimentCardsQueryOptions(params: Omit<ExperimentsParams, 'page'>) {
  return infiniteQueryOptions({
    queryKey: ['account', 'experiments', 'cards', params],
    queryFn: ({ signal, pageParam }) => listExperiments({ ...params, page: pageParam }, signal),
    initialPageParam: 1,
    getNextPageParam: (last: ExperimentsPage) => (last.page < last.pages ? last.page + 1 : undefined),
    placeholderData: keepPreviousData,
  });
}
