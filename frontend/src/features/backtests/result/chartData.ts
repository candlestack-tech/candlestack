import type { BacktestDocument } from '@/api';

// What the backtest page's charts draw, from the document's columns (index i of every column is
// one row). Times stay UTC epoch seconds of a candle's open, as lightweight-charts takes them.

type Results = NonNullable<BacktestDocument['results']>;
export type Series = Results['series'];
export type Trades = Results['trades'];

export type Point = { time: number; value: number };

const points = (ts: readonly number[], values: readonly number[], scale: (value: number) => number): Point[] =>
  ts.map((time, index) => ({ time, value: scale(values[index] ?? Number.NaN) }));

// The equity as a return in percent from the start: 1.12 → +12.
const asReturn = (equity: number) => (equity - 1) * 100;

export const equityPoints = (series: Series) => points(series.columns.ts, series.columns.equity, asReturn);

// Buy and hold on the same rows; none without a benchmark.
export function benchmarkPoints(series: Series): Point[] {
  const first = Object.values(series.benchmarks)[0];
  return first ? points(series.columns.ts, first, asReturn) : [];
}

// The fall from the peak in percent, at most 0.
export const drawdownPoints = (series: Series) => points(series.columns.ts, series.columns.drawdown, (v) => v * 100);

export const pricePoints = (series: Series) => points(series.columns.ts, series.columns.close, (v) => v);

// The row a time falls on: the first at or after it, else the last. A series of days (the light
// view of a long run) has one row per day at its last candle, so a trade's candle lands on its
// day; a series of candles has every trade's candle as it is.
export function snapTime(ts: readonly number[], time: number): number | undefined {
  let low = 0;
  let high = ts.length - 1;
  if (high < 0) return undefined;
  while (low < high) {
    const middle = (low + high) >> 1;
    if ((ts[middle] ?? 0) < time) low = middle + 1;
    else high = middle;
  }
  return ts[low];
}

// The price chart marks the trades up to this many; more would hide the price under the arrows.
export const MARKED_TRADES = 250;

export const showsMarkers = (trades: Trades) => trades.count > 0 && trades.count <= MARKED_TRADES;

export type Marker = { time: number; kind: 'buy' | 'sell' };

// A buy and a sell for every trade: a long opens with a buy and closes with a sell, a short the
// other way round. Sorted by time, as the chart wants them.
export function tradeMarkers(series: Series, trades: Trades): Marker[] {
  const { open_ts, close_ts, side } = trades.columns;
  const markers: Marker[] = [];
  open_ts.forEach((opened, index) => {
    const long = (side[index] ?? 1) > 0;
    const open = snapTime(series.columns.ts, opened);
    const close = snapTime(series.columns.ts, close_ts[index] ?? opened);
    if (open !== undefined) markers.push({ time: open, kind: long ? 'buy' : 'sell' });
    if (close !== undefined) markers.push({ time: close, kind: long ? 'sell' : 'buy' });
  });
  return markers.sort((a, b) => a.time - b.time);
}

export type TradeRow = {
  long: boolean;
  openTs: number;
  openPrice: number;
  closeTs: number;
  closePrice: number;
  return: number;
  candles: number;
  reason: string;
};

// The trades as rows, the newest first.
export function tradeRows(trades: Trades): TradeRow[] {
  const c = trades.columns;
  return c.open_ts
    .map((openTs, index) => ({
      long: (c.side[index] ?? 1) > 0,
      openTs,
      openPrice: c.open_price[index] ?? Number.NaN,
      closeTs: c.close_ts[index] ?? openTs,
      closePrice: c.close_price[index] ?? Number.NaN,
      return: c.return[index] ?? Number.NaN,
      candles: c.candles_held[index] ?? 0,
      reason: c.exit_reason[index] ?? '',
    }))
    .reverse();
}
