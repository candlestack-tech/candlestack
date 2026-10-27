import { useQueries, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useCallback, useMemo, useState } from 'react';

import { candleSpanQueryOptions, type Candles } from '@/api';
import { toBars, type Bar } from '@/features/instrument';

import { blocksAround, sourceOf, weekly, type Level, type Span } from './priceLevels';

// The candles of a backtest's chart from the data API (#309): one size of them around the part in
// view, in blocks on a fixed grid, each asked for once per visit (staleTime Infinity) so zooming
// and scrolling back come from the cache. The page asks for a new size or place only once the
// chart stopped moving (TradesChart.tsx), far from the 60 requests a minute.

// What the chart wants: a size of candles around a view.
export type Want = { level: Level; view: Span };

// What it has: the candles of a size from `start` to `end`, the blocks it asked for.
export type PriceFrame = { level: Level; bars: Bar[]; start: number; end: number };

// The candles of all blocks in time order, each time once; weeks are made of the days.
function frameOf(level: Level, blocks: readonly Span[], pages: readonly Candles[]): PriceFrame {
  const byTime = new Map<number, Bar>();
  for (const page of pages) for (const bar of toBars(page)) byTime.set(bar.time, bar);
  const bars = [...byTime.values()].sort((a, b) => a.time - b.time);
  return {
    level,
    bars: level === '1w' ? weekly(bars) : bars,
    start: blocks[0]?.start ?? 0,
    end: blocks.at(-1)?.end ?? 0,
  };
}

type Combined = { frame: PriceFrame | undefined; failed: boolean };

type Source = { instrument: string; market: string; period: Span };

// The frame of the latest want once all its blocks are in; until then the one before, so the
// chart never goes blank while a finer size loads. `failed` while a block could not load, with
// `retry` to ask again.
export function usePriceFrame({ instrument, market, period }: Source, want: Want) {
  const { level, view } = want;
  const blocks = useMemo(() => blocksAround(level, view, period, market), [level, view, period, market]);
  const combine = useCallback(
    (results: UseQueryResult<Candles>[]): Combined => {
      const pages = results.flatMap((result) => (result.data ? [result.data] : []));
      const done = pages.length > 0 && pages.length === results.length;
      return {
        frame: done ? frameOf(level, blocks, pages) : undefined,
        failed: results.some((result) => result.isError),
      };
    },
    [level, blocks],
  );
  const queries = useMemo(
    () => blocks.map((block) => candleSpanQueryOptions({ instrument, timeframe: sourceOf(level), ...block })),
    [blocks, instrument, level],
  );
  const { frame, failed } = useQueries({ queries, combine });
  const client = useQueryClient();
  const retry = useCallback(() => {
    for (const { queryKey } of queries) void client.refetchQueries({ queryKey, type: 'all' });
  }, [client, queries]);
  // The last frame that was complete, kept while the render goes on (React's way to remember a
  // value from an earlier render).
  const [last, setLast] = useState<PriceFrame>();
  if (frame && frame !== last) setLast(frame);
  return { frame: frame ?? last, loading: frame === undefined, failed, retry };
}
