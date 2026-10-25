import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';

import { backtestQueryOptions, type BacktestStatus } from '@/api';

// What a finished run changes elsewhere: the lists and the dashboard show its results, the
// models their backtest count and best Sharpe.
const CHANGED = [
  ['account', 'backtests'],
  ['account', 'dashboard'],
  ['account', 'models'],
] as const;

// A backtest's document, asked for again while it runs. Once a run this page watched ends, the
// lists that show it are asked for again.
export function useBacktestResult(id: string) {
  const queryClient = useQueryClient();
  const result = useQuery(backtestQueryOptions(id));
  const status = result.data?.backtest.status;
  const last = useRef<BacktestStatus | undefined>(undefined);
  useEffect(() => {
    if (last.current === 'running' && status !== undefined && status !== 'running') {
      for (const queryKey of CHANGED) void queryClient.invalidateQueries({ queryKey });
    }
    last.current = status;
  }, [status, queryClient]);
  return result;
}
