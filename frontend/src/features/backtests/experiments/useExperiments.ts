import { useInfiniteQuery, useQuery } from '@tanstack/react-query';

import { experimentCardsQueryOptions, experimentsQueryOptions, type Experiment, type ExperimentsPage } from '@/api';
import type { ExperimentsSearch } from '@/routes/experimentsSearch';

import type { ExperimentsView } from './view';

export type ExperimentsResult = {
  // The first page: the counts and the facets.
  first: ExperimentsPage | undefined;
  experiments: Experiment[];
  error: Error | null;
  // The table's next page, search or order is on its way, the previous one on screen.
  loading: boolean;
  more: { has: boolean; loading: boolean; load: () => void };
};

// Each backtest once: one published while the cards were read can come back on the next page.
function once(pages: readonly ExperimentsPage[]): Experiment[] {
  const seen = new Set<string>();
  return pages.flatMap((page) => page.experiments).filter(({ id }) => !seen.has(id) && Boolean(seen.add(id)));
}

// What Experiments shows in its view: the table's page of the URL, or the cards loaded so far, one
// page of 12 after another (Load more). Only the view on screen is asked for.
export function useExperiments(search: ExperimentsSearch, view: ExperimentsView): ExperimentsResult {
  const { page: _page, ...params } = search;
  const table = useQuery({ ...experimentsQueryOptions(search), enabled: view === 'table' });
  const cards = useInfiniteQuery({ ...experimentCardsQueryOptions(params), enabled: view === 'cards' });
  if (view === 'table') {
    return {
      first: table.data,
      experiments: table.data?.experiments ?? [],
      error: table.error,
      loading: table.isPlaceholderData,
      more: { has: false, loading: false, load: () => undefined },
    };
  }
  return {
    first: cards.data?.pages[0],
    experiments: once(cards.data?.pages ?? []),
    error: cards.error,
    loading: cards.isPlaceholderData,
    more: {
      has: cards.hasNextPage,
      loading: cards.isFetchingNextPage,
      load: () => void cards.fetchNextPage(),
    },
  };
}
