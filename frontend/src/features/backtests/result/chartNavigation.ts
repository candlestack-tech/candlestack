import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { BacktestDocument } from '@/api';
import { useDateStyle } from '@/features/preferences';
import { priceDecimals } from '@/lib/numbers';
import { useDebouncedValue } from '@/lib/useDebouncedValue';

import { percentPoints, positionBands, returnsOn, type Candles, type Series, type TradeRow } from './chartData';
import type { Frame, TradesChartView } from './chartView';
import { LEVEL_SECONDS, levelsOf, nextWant, pickLevel, type Level } from './priceLevels';
import { usePriceFrame, type PriceFrame, type Want } from './usePriceFrame';
import { useChartEvents, useChartView, type View } from './useTradesChart';

// Where a backtest's chart is and which candles it shows (#309): the whole period first, then the
// last month; a range or a trade picked in the table moves it, and once the user's zooming and
// scrolling settles it asks for the size and the blocks the view wants.

const DAY = 86_400;

// The ranges of the bar under the chart, in days; All is the whole period.
export const RANGES = [
  ['1W', 7],
  ['1M', 30],
  ['3M', 91],
  ['1Y', 365],
  ['All', 0],
] as const;

export type RangeName = (typeof RANGES)[number][0];

// Wait this long after the chart stopped moving before asking for other candles.
const SETTLE_MS = 250;

export type Run = {
  source: { instrument: string; market: string; period: { start: number; end: number } };
  levels: Level[];
  // The run's candle and its first and last candle's times (the end of the last).
  seconds: number;
  first: number;
  last: number;
};

function runOf(data: BacktestDocument['data']): Run {
  const { instrument, period, timeframe, timeframe_seconds: seconds, rows } = data;
  return {
    source: { instrument: instrument.id, market: instrument.market, period },
    levels: levelsOf(timeframe),
    seconds,
    first: rows?.first_ts ?? period.start,
    last: (rows?.last_ts ?? period.end - seconds) + seconds,
  };
}

const coarsest = (run: Run) => run.levels.at(-1) ?? '1d';

export function rangeView(name: RangeName, run: Run): View {
  const days = RANGES.find(([range]) => range === name)?.[1] ?? 0;
  return { from: days ? Math.max(run.first, run.last - days * DAY) : run.first, to: run.last };
}

// Whether the view is still about where a range put it.
function near(view: View, range: View): boolean {
  const room = (range.to - range.from) * 0.1;
  return Math.abs(view.from - range.from) <= room && Math.abs(view.to - range.to) <= room;
}

// The candles' times, what lies on them and what the chart draws. Without candles, when they
// did not load, the document's own rows stand in for them: the strategy, buy and hold and the
// drawdown need nothing else.
function useDrawn(
  frame: PriceFrame | undefined,
  failed: boolean,
  run: Run,
  series: Series,
  trades: readonly TradeRow[],
) {
  const candles = useMemo<Candles | undefined>(() => {
    if (frame) return { times: frame.bars.map((bar) => bar.time), end: frame.end };
    const { ts } = series.columns;
    return failed && ts.length > 0 ? { times: ts, end: (ts.at(-1) ?? 0) + run.seconds } : undefined;
  }, [frame, failed, run, series]);
  const returns = useMemo(() => candles && returnsOn(candles, series), [candles, series]);
  const drawn = useMemo<Frame | undefined>(
    () =>
      candles &&
      returns && {
        bars: frame?.bars ?? [],
        times: candles.times,
        seconds: frame ? LEVEL_SECONDS[frame.level] : run.seconds,
        bands: positionBands(candles, trades),
        strategy: percentPoints(candles.times, returns.strategy),
        benchmark: returns.benchmark && percentPoints(candles.times, returns.benchmark),
        decimals: priceDecimals(frame?.bars.at(-1)?.close ?? 0),
      },
    [frame, run, candles, returns, trades],
  );
  return { candles, returns, drawn };
}

type Target = View & { level: Level };

// The user's own zooming or scrolling (a wheel, a drag, a swipe) drops the target still waiting.
function useDropOnUserMove(view: TradesChartView | null, pending: { current: Target | null }) {
  useEffect(() => {
    if (!view) return;
    const element = view.chart.chartElement();
    const drop = () => {
      pending.current = null;
    };
    const onDrag = (event: PointerEvent) => {
      if (event.buttons !== 0) drop();
    };
    element.addEventListener('wheel', drop, { passive: true });
    element.addEventListener('pointermove', onDrag);
    element.addEventListener('touchmove', drop, { passive: true });
    return () => {
      element.removeEventListener('wheel', drop);
      element.removeEventListener('pointermove', onDrag);
      element.removeEventListener('touchmove', drop);
    };
  }, [view, pending]);
}

// Going to a part of time: at once while the candles shown cover it, else once the right ones
// are in (the frame effect shows it then).
function useGoTo(view: TradesChartView | null, frame: PriceFrame | undefined, run: Run, setWant: (want: Want) => void) {
  const pending = useRef<Target | null>(null);
  const shown = useRef(frame);
  useEffect(() => {
    shown.current = frame;
  }, [frame]);
  useDropOnUserMove(view, pending);
  const goTo = useCallback(
    (target: View) => {
      const now = shown.current;
      const level = pickLevel(target.to - target.from, run.levels, now?.level ?? coarsest(run), run.source.market);
      if (view && now?.level === level && now.start <= target.from && now.end >= target.to) {
        pending.current = null;
        view.show(target);
        return;
      }
      pending.current = { level, ...target };
      setWant({ level, view: { start: target.from, end: target.to } });
    },
    [view, run, setWant],
  );
  // The target waiting for candles of this size, taken once they are in.
  const take = useCallback((level: Level) => {
    const target = pending.current;
    if (target?.level !== level) return null;
    pending.current = null;
    return target;
  }, []);
  const waiting = useCallback(() => pending.current !== null, []);
  const current = useCallback(() => shown.current, []);
  return { goTo, take, waiting, current };
}

// New candles on the chart, keeping what is in view or going where it was asked to. After the
// first ones (the whole period), the last month.
function useFrameOnChart(
  view: TradesChartView | null,
  drawn: Frame | undefined,
  frame: PriceFrame | undefined,
  take: (level: Level) => Target | null,
  onFirst: () => void,
) {
  const opened = useRef(false);
  const first = useRef(onFirst);
  useEffect(() => {
    first.current = onFirst;
  }, [onFirst]);
  useEffect(() => {
    if (!view || !drawn) return;
    view.setFrame(drawn, frame ? take(frame.level) : null);
    if (frame && !opened.current) {
      opened.current = true;
      first.current();
    }
  }, [view, drawn, frame, take]);
}

// Once the chart stopped moving: other candles when the span in view wants another size or the
// view reached past the blocks around it. Not while a target waits for its candles: the chart
// only moved there by itself (the first candles came in), and the user's own move drops it.
function useLoadOnMove(
  visible: View | null,
  current: () => PriceFrame | undefined,
  waiting: () => boolean,
  run: Run,
  setWant: (want: Want) => void,
) {
  const settled = useDebouncedValue(visible, SETTLE_MS);
  useEffect(() => {
    const frame = current();
    if (!settled || !frame || waiting()) return;
    const next = nextWant({ start: settled.from, end: settled.to }, frame, run.levels, run.source);
    if (next) setWant(next);
  }, [settled, current, waiting, run, setWant]);
}

// The range the chart was put on: a pick goes there, a move of the user's own leaves it. A range
// just picked first waits for its candles.
function useRange(goTo: (target: View) => void, run: Run, visible: View | null, waiting: () => boolean) {
  const [range, setRange] = useState<RangeName | null>(null);
  const current = useRef(range);
  useEffect(() => {
    current.current = range;
  }, [range]);
  useEffect(() => {
    const name = current.current;
    if (name && visible && !waiting() && !near(visible, rangeView(name, run))) setRange(null);
  }, [visible, run, waiting]);
  const pick = useCallback(
    (name: RangeName) => {
      setRange(name);
      goTo(rangeView(name, run));
    },
    [goTo, run],
  );
  return { range, setRange, pick };
}

// The chart with its candles: what it shows, what is in view and the range it was put on.
export function useChartNavigation(doc: BacktestDocument, series: Series, trades: readonly TradeRow[]) {
  const run = useMemo(() => runOf(doc.data), [doc.data]);
  const [want, setWant] = useState<Want>(() => ({
    level: pickLevel(run.last - run.first, run.levels, coarsest(run), run.source.market),
    view: { start: run.first, end: run.last },
  }));
  const prices = usePriceFrame(run.source, want);
  const { frame } = prices;
  const level = frame?.level ?? want.level;
  const style = useDateStyle();
  const { container, view } = useChartView(style, LEVEL_SECONDS[level] >= DAY);
  const { candles, returns, drawn } = useDrawn(frame, prices.failed, run, series, trades);
  const events = useChartEvents(view, { candles, seconds: drawn?.seconds ?? LEVEL_SECONDS[level] });
  const { goTo, take, waiting, current } = useGoTo(view, frame, run, setWant);
  const { range, setRange, pick } = useRange(goTo, run, events.visible, waiting);
  useFrameOnChart(view, drawn, frame, take, () => pick('1M'));
  useLoadOnMove(events.visible, current, waiting, run, setWant);
  return {
    run,
    prices,
    frame,
    level,
    style,
    container,
    view,
    candles,
    returns,
    drawn,
    events,
    goTo,
    range,
    setRange,
    pick,
  };
}
