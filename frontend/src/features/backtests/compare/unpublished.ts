import { useQueries } from '@tanstack/react-query';
import { useEffect } from 'react';

import { ApiError, backtestQueryOptions, type BacktestDocument } from '@/api';

import { documentDescription } from '../names';
import { leaveCompare, nameInCompare, useCompareSelection, type ComparePick } from './selection';

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

// The chosen backtests take the name and the line of their documents once these are in: a choice
// kept before backtests had names gets them, and another account's backtest its author renamed
// shows its new name. `results` are the queries of `picks`, in their order.
export function useNamesOf(picks: readonly ComparePick[], results: readonly { data?: BacktestDocument | undefined }[]) {
  const names = picks.flatMap((pick, index) => {
    const doc = results[index]?.data;
    return doc ? [[pick.id, doc.backtest.name, documentDescription(doc)] as const] : [];
  });
  const namesKey = JSON.stringify(names);
  useEffect(() => {
    for (const [id, name, description] of JSON.parse(namesKey) as [string, string, string][]) {
      nameInCompare(id, name, description);
    }
  }, [namesKey]);
}

// The panel asks for the documents of the other accounts' backtests it holds (the compare page
// reads the same ones from the cache), so one unpublished since it was added says so wherever the
// panel shows, and one renamed shows its new name; and for the user's own kept without a name, to
// name them.
export function usePanelPicks() {
  const { picks } = useCompareSelection();
  const asked = picks.filter(
    (pick) => pick.author !== undefined || pick.name === undefined || pick.description === undefined,
  );
  const results = useQueries({ queries: asked.map((pick) => backtestQueryOptions(pick.id)) });
  useLeaveMissing(asked, results);
  useNamesOf(asked, results);
}
