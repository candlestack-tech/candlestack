import { useQueries } from '@tanstack/react-query';
import { useEffect } from 'react';

import { ApiError, backtestQueryOptions } from '@/api';

import { leaveCompare, useCompareSelection, type ComparePick } from './selection';

export const isMissing = (error: unknown) => error instanceof ApiError && error.status === 404;

// The chosen backtests whose pages answer 404 leave the choice, or say they were unpublished
// (leaveCompare), once the answers are in. `results` are the queries of `picks`, in their order.
export function useLeaveMissing(picks: readonly ComparePick[], results: readonly { error: unknown }[]) {
  const goneKey = picks
    .filter((_, index) => isMissing(results[index]?.error))
    .map((pick) => pick.id)
    .join();
  useEffect(() => {
    if (goneKey) goneKey.split(',').forEach(leaveCompare);
  }, [goneKey]);
}

// The panel asks for the documents of the other accounts' backtests it holds (the compare page
// reads the same ones from the cache), so one unpublished since it was added says so wherever
// the panel shows.
export function useUnpublishedPicks() {
  const { picks } = useCompareSelection();
  const others = picks.filter((pick) => pick.author !== undefined);
  const results = useQueries({ queries: others.map((pick) => backtestQueryOptions(pick.id)) });
  useLeaveMissing(others, results);
}
