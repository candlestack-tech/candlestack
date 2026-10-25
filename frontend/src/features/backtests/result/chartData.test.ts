import { describe, expect, it } from 'vitest';

import { backtestDocument } from '@/test/api';

import {
  benchmarkPoints,
  drawdownPoints,
  equityPoints,
  pricePoints,
  showsMarkers,
  snapTime,
  tradeMarkers,
  tradeRows,
  type Trades,
} from './chartData';

const { series, trades } = backtestDocument().results!;
const TS = series.columns.ts;

// Two trades of a run that also goes short: a long, then a short.
const BOTH: Trades = {
  ...trades,
  count: 2,
  total: 2,
  columns: {
    ...trades.columns,
    open_ts: [TS[1]!, TS[3]!],
    close_ts: [TS[2]!, TS[4]!],
    side: [1, -1],
    open_price: [101, 97],
    close_price: [103, 98],
    return: [0.02, -0.01],
    exit_reason: ['signal', 'end'],
    candles_held: [1, 1],
  },
};

describe('the series', () => {
  it('draws the equity and buy and hold as returns in percent and the drawdown in percent', () => {
    expect(equityPoints(series).map((point) => Number(point.value.toFixed(2)))).toEqual([0, 0, 0.88, -5.2, -5.2]);
    expect(benchmarkPoints(series).map((point) => Number(point.value.toFixed(2)))).toEqual([0, 1, 3, -3, -2]);
    expect(drawdownPoints(series).at(-1)).toEqual({ time: TS[4], value: -6.0224 });
    expect(pricePoints(series)[0]).toEqual({ time: TS[0], value: 100 });
  });

  it('has no buy and hold without a benchmark', () => {
    expect(benchmarkPoints({ ...series, benchmarks: {} })).toEqual([]);
  });
});

describe('snapTime', () => {
  it('finds the row at or after a time, else the last', () => {
    expect(snapTime(TS, TS[2]!)).toBe(TS[2]);
    expect(snapTime(TS, TS[2]! - 60)).toBe(TS[2]);
    expect(snapTime(TS, 0)).toBe(TS[0]);
    expect(snapTime(TS, TS[4]! + 3600)).toBe(TS[4]);
    expect(snapTime([], 5)).toBeUndefined();
  });
});

describe('the trades', () => {
  it('marks a long with a buy and a sell and a short the other way round, by time', () => {
    expect(tradeMarkers(series, BOTH)).toEqual([
      { time: TS[1], kind: 'buy' },
      { time: TS[2], kind: 'sell' },
      { time: TS[3], kind: 'sell' },
      { time: TS[4], kind: 'buy' },
    ]);
  });

  it('marks a trade of a series of days on the row of its day', () => {
    const days = {
      ...series,
      resolution: 'day',
      columns: { ...series.columns, ts: [86_400 * 2 - 1800, 86_400 * 3 - 1800] },
    };
    const one = { ...trades, columns: { ...trades.columns, open_ts: [86_400 + 3600], close_ts: [86_400 * 2 + 3600] } };
    expect(tradeMarkers(days, one).map((marker) => marker.time)).toEqual([86_400 * 2 - 1800, 86_400 * 3 - 1800]);
  });

  it('lists the trades newest first', () => {
    expect(tradeRows(BOTH).map((row) => [row.long, row.openPrice, row.reason])).toEqual([
      [false, 97, 'end'],
      [true, 101, 'signal'],
    ]);
  });

  it('marks the trades on the price only while there are not hundreds', () => {
    expect(showsMarkers(trades)).toBe(true);
    expect(showsMarkers({ ...trades, count: 0 })).toBe(false);
    expect(showsMarkers({ ...trades, count: 251 })).toBe(false);
  });
});
