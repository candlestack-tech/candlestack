import type { BacktestDocument } from '@/api';
import { formatPercent } from '@/lib/results';

// What the backtest page's chart draws (#309): the document's trades and series laid on the
// candles of the data API. Times stay UTC epoch seconds of a candle's open, as lightweight-charts
// takes them. `times` are the open times of the candles on the chart, `end` where the last one
// ends.

type Results = NonNullable<BacktestDocument['results']>;
export type Series = Results['series'];
export type Trades = Results['trades'];

export type TradeRow = {
  // The trade's place in the document, oldest first: the chart and the table both name it so.
  index: number;
  long: boolean;
  openTs: number;
  openPrice: number;
  closeTs: number;
  closePrice: number;
  return: number;
  candles: number;
  reason: string;
};

// The trades in the document's order, the oldest first.
export function chartTrades(trades: Trades): TradeRow[] {
  const c = trades.columns;
  return c.open_ts.map((openTs, index) => ({
    index,
    long: (c.side[index] ?? 1) > 0,
    openTs,
    openPrice: c.open_price[index] ?? Number.NaN,
    closeTs: c.close_ts[index] ?? openTs,
    closePrice: c.close_price[index] ?? Number.NaN,
    return: c.return[index] ?? Number.NaN,
    candles: c.candles_held[index] ?? 0,
    reason: c.exit_reason[index] ?? '',
  }));
}

// The trades as the table lists them, the newest first.
export const tradeRows = (trades: Trades): TradeRow[] => chartTrades(trades).reverse();

// The first index whose value is at least `value` (the length when none is).
function lowerBound(length: number, at: (index: number) => number, value: number): number {
  let low = 0;
  let high = length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (at(middle) < value) low = middle + 1;
    else high = middle;
  }
  return low;
}

export type Candles = { times: readonly number[]; end: number };

// The candle a moment falls in, by its index: the last that opened at or before it; -1 before
// the first candle or from the end on.
export function candleAt({ times, end }: Candles, time: number): number {
  if (time >= end) return -1;
  return lowerBound(times.length, (index) => times[index] ?? 0, time + 1) - 1;
}

// The trades in view, `[from, to)`: those that close at or after `from` and open before `to`, by
// their indexes `[first, last)`. One position at a time, so the closes are in order too.
export function tradesIn(trades: readonly TradeRow[], from: number, to: number): { first: number; last: number } {
  const first = lowerBound(trades.length, (index) => trades[index]?.closeTs ?? 0, from);
  const last = lowerBound(trades.length, (index) => trades[index]?.openTs ?? 0, to);
  return { first, last: Math.max(first, last) };
}

// Up to this many trades in view each has its full-size arrows, up to TEXT_MOST with Buy and
// Sell written by them; past DENSE_MOST the arrows would hide the price, so only the shading
// shows until the chart zooms in.
export const MARK_MOST = 50;
export const TEXT_MOST = 14;
export const DENSE_MOST = 160;
// The arrows of a trade under the cursor, or picked in the table.
const BIG = 1.6;
const SMALL = 0.6;

export type Mark = { time: number; kind: 'buy' | 'sell'; size: number; text: string };

const NAMES = { buy: 'Buy', sell: 'Sell' } as const;

// The Buy and Sell arrows of the trades in view: a long opens with a buy and closes with a sell,
// a short the other way round. Each at the candle its time falls in; none for a time off the
// candles. The highlighted trade's arrows are bigger and the exit says its return. Sorted by
// time, as the chart wants them.
export function marksIn(
  trades: readonly TradeRow[],
  view: { from: number; to: number },
  candles: Candles,
  highlighted: number | null,
): { marks: Mark[]; inView: number } {
  const { first, last } = tradesIn(trades, view.from, view.to);
  const inView = last - first;
  const text = inView <= TEXT_MOST;
  const size = inView <= MARK_MOST ? 1 : SMALL;
  const marks: Mark[] = [];
  const add = (time: number, kind: Mark['kind'], markSize: number, label: string) => {
    const at = candles.times[candleAt(candles, time)];
    if (at !== undefined) marks.push({ time: at, kind, size: markSize, text: label });
  };
  const mark = (trade: TradeRow, big: boolean) => {
    const [open, close] = trade.long ? (['buy', 'sell'] as const) : (['sell', 'buy'] as const);
    add(trade.openTs, open, big ? BIG : size, text || big ? NAMES[open] : '');
    const exit = big ? `${NAMES[close]} ${formatPercent(trade.return)}` : text ? NAMES[close] : '';
    add(trade.closeTs, close, big ? BIG : size, exit);
  };
  let shown = false;
  for (const trade of trades.slice(first, last)) {
    const big = trade.index === highlighted;
    shown ||= big;
    if (inView <= DENSE_MOST || big) mark(trade, big);
  }
  // The highlighted trade also when it lies outside the view, e.g. just picked in the table.
  const picked = shown || highlighted === null ? undefined : trades[highlighted];
  if (picked) mark(picked, true);
  return { marks: marks.sort((a, b) => a.time - b.time), inView };
}

export type Band = { time: number; long: number; short: number };

// In a position: 1 on every candle a long (or a short) is open in, else 0, for the shading behind
// the candles. A candle is in a trade from the one it opens in to the one it closes in.
export function positionBands(candles: Candles, trades: readonly TradeRow[]): Band[] {
  const { times, end } = candles;
  let next = 0;
  const closed = (time: number) => (trades[next]?.closeTs ?? Number.POSITIVE_INFINITY) < time;
  return times.map((time, index) => {
    while (closed(time)) next += 1;
    const trade = trades[next];
    const side = trade && trade.openTs < (times[index + 1] ?? end) ? (trade.long ? 1 : -1) : 0;
    return { time, long: side > 0 ? 1 : 0, short: side < 0 ? 1 : 0 };
  });
}

// The trade open during a candle `[from, to)`, if any: the last to open before its end, while it
// had not closed before its start.
export function tradeAt(trades: readonly TradeRow[], from: number, to: number): TradeRow | null {
  const index = lowerBound(trades.length, (at) => trades[at]?.openTs ?? 0, to) - 1;
  const trade = trades[index];
  return trade && trade.closeTs >= from ? trade : null;
}

// A column of the series on the candles: the value of the last row in each candle (or the
// lowest, for the drawdown), undefined for a candle without a row. A series of days (the light
// view of a long run) has one row per day at its last candle, so finer candles get a value once a
// day.
export function onCandles(
  candles: Candles,
  ts: readonly number[],
  values: readonly number[],
  pick: 'last' | 'min' = 'last',
): (number | undefined)[] {
  const out: (number | undefined)[] = candles.times.map(() => undefined);
  ts.forEach((time, row) => {
    const index = candleAt(candles, time);
    const value = values[row];
    if (index < 0 || value === undefined) return;
    const before = out[index];
    out[index] = pick === 'min' && before !== undefined ? Math.min(before, value) : value;
  });
  return out;
}

// The value at a candle or the last one before it, for the legend.
export function valueAt(values: readonly (number | undefined)[], index: number): number | undefined {
  for (let at = Math.min(index, values.length - 1); at >= 0; at -= 1) {
    const value = values[at];
    if (value !== undefined) return value;
  }
  return undefined;
}

export type Returns = {
  strategy: (number | undefined)[];
  // Buy and hold on the same rows; null without a benchmark.
  benchmark: (number | undefined)[] | null;
  drawdown: (number | undefined)[];
};

// The strategy's and buy and hold's returns (1.12 → 0.12) and the drawdown, as fractions on the
// candles.
export function returnsOn(candles: Candles, series: Series): Returns {
  const { ts, equity, drawdown } = series.columns;
  const asReturn = (values: (number | undefined)[]) =>
    values.map((value) => (value === undefined ? undefined : value - 1));
  const benchmark = Object.values(series.benchmarks)[0];
  return {
    strategy: asReturn(onCandles(candles, ts, equity)),
    benchmark: benchmark ? asReturn(onCandles(candles, ts, benchmark)) : null,
    drawdown: onCandles(candles, ts, drawdown, 'min'),
  };
}

// A return on the candles as the chart's points in percent, only the candles with a row.
export function percentPoints(times: readonly number[], values: readonly (number | undefined)[]) {
  return values.flatMap((value, index) => {
    const time = times[index];
    return value === undefined || time === undefined ? [] : [{ time, value: value * 100 }];
  });
}

// Where the chart goes for a trade picked in the table: the trade with room on both sides, three
// times as long as it is and at least 40 of the run's candles, within the period.
export function tradeView(trade: TradeRow, candle: number, period: { start: number; end: number }) {
  const length = trade.closeTs + candle - trade.openTs;
  const room = Math.max(length * 1.5, 20 * candle);
  return {
    from: Math.max(period.start, trade.openTs - room),
    to: Math.min(period.end, trade.closeTs + candle + room),
  };
}

// The time at a logical position of the chart (a candle's index, fractions between them): off the
// candles on either side it goes on a candle at a time, so a view past the loaded candles still
// says how far it reaches.
export function timeAtLogical(times: readonly number[], logical: number, seconds: number): number {
  const last = times.length - 1;
  const first = times[0] ?? 0;
  if (logical <= 0 || last < 0) return first + logical * seconds;
  if (logical >= last) return (times[last] ?? 0) + (logical - last) * seconds;
  return times[Math.round(logical)] ?? first;
}
