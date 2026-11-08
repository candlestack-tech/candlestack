import { useQueries } from '@tanstack/react-query';
import { useEffect } from 'react';

import { ApiError, backtestQueryOptions, type BacktestDocument, type ComparedBacktest } from '@/api';

import { comparedDescription, documentDescription } from '../names';
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

// The backtest a comparison answered 404 for (its `id`) leaves the choice, or says it was
// unpublished; the comparison of the rest names the next one, if any.
export function useLeaveCompared(error: unknown) {
  const body = isMissing(error) ? (error as ApiError).body : null;
  const gone = typeof body === 'object' && body !== null && 'id' in body ? String(body.id) : '';
  useEffect(() => {
    if (gone) leaveCompare(gone);
  }, [gone]);
}

// The chosen backtests take these names and lines ([id, name, description]).
function useNames(names: readonly (readonly [string, string, string])[]) {
  const namesKey = JSON.stringify(names);
  useEffect(() => {
    for (const [id, name, description] of JSON.parse(namesKey) as [string, string, string][]) {
      nameInCompare(id, name, description);
    }
  }, [namesKey]);
}

// The chosen backtests take the name and the line of their documents once these are in: a choice
// kept before backtests had names gets them, and another account's backtest its author renamed
// shows its new name. `results` are the queries of `picks`, in their order.
export function useNamesOf(picks: readonly ComparePick[], results: readonly { data?: BacktestDocument | undefined }[]) {
  useNames(
    picks.flatMap((pick, index) => {
      const doc = results[index]?.data;
      return doc ? [[pick.id, doc.backtest.name, documentDescription(doc)] as const] : [];
    }),
  );
}

// The same from the comparison, on the compare page.
export const useComparedNames = (backtests: readonly ComparedBacktest[]) => {
  useNames(backtests.map((backtest) => [backtest.id, backtest.name, comparedDescription(backtest)] as const));
};

// The panel asks for the documents of the other accounts' backtests it holds, so one unpublished
// since it was added says so wherever the panel shows, and one renamed shows its new name; and for
// the user's own kept without a name, to name them. Not on the compare page (`enabled` false),
// whose comparison tells the same.
export function usePanelPicks(enabled: boolean) {
  const { picks } = useCompareSelection();
  const asked = enabled
    ? picks.filter((pick) => pick.author !== undefined || pick.name === undefined || pick.description === undefined)
    : [];
  const results = useQueries({ queries: asked.map((pick) => backtestQueryOptions(pick.id)) });
  useLeaveMissing(asked, results);
  useNamesOf(asked, results);
}
