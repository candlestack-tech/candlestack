import { describe, expect, it } from 'vitest';

import { backtestDocument } from '@/test/api';

import {
  DENSE_MOST,
  MARK_MOST,
  TEXT_MOST,
  candleAt,
  chartTrades,
  logicalAtTime,
  logicalOfTimes,
  marksIn,
  onCandles,
  percentPoints,
  positionBands,
  returnsOn,
  timeAtLogical,
  timesOfLogical,
  tradeAt,
  tradeRows,
  tradeView,
  tradesIn,
  valueAt,
  type TradeRow,
  type Trades,
} from './chartData';

const { series, trades } = backtestDocument().results!;
const TS = series.columns.ts;
const HOUR = 3600;

// Hourly candles from 0, ten of them.
const CANDLES = { times: Array.from({ length: 10 }, (_, index) => index * HOUR), end: 10 * HOUR };

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

// `count` trades on the hourly candles: each opens on an hour and closes half an hour later,
// longs and shorts in turn.
function many(count: number, step = HOUR): TradeRow[] {
  return Array.from({ length: count }, (_, index) => ({
    index,
    long: index % 2 === 0,
    openTs: index * step,
    openPrice: 100,
    closeTs: index * step + step / 2,
    closePrice: 101,
    return: 0.01,
    candles: 1,
    reason: 'signal',
  }));
}

describe('the trades', () => {
  it('keeps them in order with their place, and lists them newest first', () => {
    expect(chartTrades(BOTH).map((trade) => [trade.index, trade.long, trade.openPrice])).toEqual([
      [0, true, 101],
      [1, false, 97],
    ]);
    expect(tradeRows(BOTH).map((row) => [row.index, row.reason])).toEqual([
      [1, 'end'],
      [0, 'signal'],
    ]);
  });

  it('finds the candle a time falls in, none before the first or from the end', () => {
    expect(candleAt(CANDLES, 0)).toBe(0);
    expect(candleAt(CANDLES, 2 * HOUR + 59)).toBe(2);
    expect(candleAt(CANDLES, 9 * HOUR + 1800)).toBe(9);
    expect(candleAt(CANDLES, -1)).toBe(-1);
    expect(candleAt(CANDLES, 10 * HOUR)).toBe(-1);
  });

  it('counts the trades in view, also one that opened before it and closes in it', () => {
    const list = many(5);
    expect(tradesIn(list, 0, 5 * HOUR)).toEqual({ first: 0, last: 5 });
    // The second trade closes at 1:30, in view from 1:15.
    expect(tradesIn(list, HOUR + 900, 3 * HOUR)).toEqual({ first: 1, last: 3 });
    expect(tradesIn(list, 7 * HOUR, 8 * HOUR)).toEqual({ first: 5, last: 5 });
  });

  it('finds the trade open during a candle', () => {
    const list = many(3, 2 * HOUR);
    expect(tradeAt(list, 0, HOUR)?.index).toBe(0);
    // 1:00 to 2:00: the first closed at 1:00, still in this candle.
    expect(tradeAt(list, HOUR, 2 * HOUR)?.index).toBe(0);
    expect(tradeAt(list, HOUR + 1, 2 * HOUR)).toBeNull();
    expect(tradeAt(list, 4 * HOUR, 5 * HOUR)?.index).toBe(2);
    expect(tradeAt([], 0, HOUR)).toBeNull();
  });
});

describe('the arrows', () => {
  it('marks a long with a buy and a sell and a short with a sell and a buy, written out while few', () => {
    const { marks, inView } = marksIn(many(2), { from: 0, to: 2 * HOUR }, CANDLES, null);
    expect(inView).toBe(2);
    expect(marks).toEqual([
      { time: 0, kind: 'buy', size: 1, text: 'Buy' },
      { time: 0, kind: 'sell', size: 1, text: 'Sell' },
      { time: HOUR, kind: 'sell', size: 1, text: 'Sell' },
      { time: HOUR, kind: 'buy', size: 1, text: 'Buy' },
    ]);
  });

  it('puts an arrow on the candle its time falls in and leaves out a time off the candles', () => {
    const late: TradeRow = { ...many(1)[0]!, openTs: 9 * HOUR + 600, closeTs: 11 * HOUR };
    expect(marksIn([late], { from: 9 * HOUR, to: 12 * HOUR }, CANDLES, null).marks).toEqual([
      { time: 9 * HOUR, kind: 'buy', size: 1, text: 'Buy' },
    ]);
  });

  it('drops the words, then shrinks the arrows, then shows none as the trades in view grow', () => {
    const view = (count: number) =>
      marksIn(
        many(count),
        { from: 0, to: count * HOUR },
        { times: many(count).map((t) => t.openTs), end: count * HOUR },
        null,
      );
    expect(view(TEXT_MOST).marks[0]).toMatchObject({ size: 1, text: 'Buy' });
    expect(view(TEXT_MOST + 1).marks[0]).toMatchObject({ size: 1, text: '' });
    expect(view(MARK_MOST + 1).marks[0]).toMatchObject({ size: 0.6, text: '' });
    expect(view(DENSE_MOST).marks).toHaveLength(2 * DENSE_MOST);
    expect(view(DENSE_MOST + 1)).toEqual({ marks: [], inView: DENSE_MOST + 1 });
  });

  it('makes the highlighted trade bigger with its return on the exit, also among too many to show', () => {
    const count = DENSE_MOST + 1;
    const list = many(count);
    const { marks } = marksIn(
      list,
      { from: 0, to: count * HOUR },
      { times: list.map((t) => t.openTs), end: count * HOUR },
      3,
    );
    expect(marks).toEqual([
      { time: 3 * HOUR, kind: 'sell', size: 1.6, text: 'Sell' },
      { time: 3 * HOUR, kind: 'buy', size: 1.6, text: 'Buy +1.0 %' },
    ]);
  });

  it('shows a trade picked outside the view too', () => {
    const { marks, inView } = marksIn(many(5), { from: 0, to: HOUR }, CANDLES, 4);
    expect(inView).toBe(1);
    expect(marks.filter((mark) => mark.size === 1.6)).toHaveLength(2);
  });
});

describe('in a position', () => {
  it('shades the candles of a long and of a short, from the candle it opens in to the one it closes in', () => {
    const list: TradeRow[] = [
      { ...many(1)[0]!, long: true, openTs: HOUR + 600, closeTs: 3 * HOUR + 600 },
      { ...many(1)[0]!, index: 1, long: false, openTs: 5 * HOUR, closeTs: 5 * HOUR + 1800 },
    ];
    const bands = positionBands(CANDLES, list);
    expect(bands.map((band) => band.long)).toEqual([0, 1, 1, 1, 0, 0, 0, 0, 0, 0]);
    expect(bands.map((band) => band.short)).toEqual([0, 0, 0, 0, 0, 1, 0, 0, 0, 0]);
    expect(bands[0]?.time).toBe(0);
  });
});

describe('the returns', () => {
  it('takes the last row of each candle, the lowest for the drawdown, and none for a candle without rows', () => {
    const candles = { times: [0, 2 * HOUR], end: 4 * HOUR };
    const ts = [0, HOUR, 2 * HOUR];
    expect(onCandles(candles, ts, [1, 3, 2])).toEqual([3, 2]);
    expect(onCandles(candles, ts, [-0.1, -0.3, 0], 'min')).toEqual([-0.3, 0]);
    expect(onCandles({ times: [0, HOUR, 5 * HOUR], end: 6 * HOUR }, [HOUR], [7])).toEqual([undefined, 7, undefined]);
  });

  it('reads the strategy, buy and hold and the drawdown of the series as fractions on the candles', () => {
    const candles = { times: TS, end: TS.at(-1)! + 1800 };
    const returns = returnsOn(candles, series);
    expect(returns.strategy.map((value) => Number(value?.toFixed(4)))).toEqual([0, 0, 0.0088, -0.052, -0.052]);
    expect(returns.benchmark?.map((value) => Number(value?.toFixed(2)))).toEqual([0, 0.01, 0.03, -0.03, -0.02]);
    expect(returns.drawdown.at(-1)).toBe(-0.060224);
    expect(returnsOn(candles, { ...series, benchmarks: {} }).benchmark).toBeNull();
  });

  it('draws the candles with a row in percent and carries the last value for the legend', () => {
    expect(percentPoints([0, HOUR, 2 * HOUR], [0.1, undefined, -0.05])).toEqual([
      { time: 0, value: 10 },
      { time: 2 * HOUR, value: -5 },
    ]);
    expect(valueAt([0.1, undefined, undefined], 2)).toBe(0.1);
    expect(valueAt([undefined, 0.2], 0)).toBeUndefined();
  });
});

describe('the view', () => {
  it('goes to a picked trade with room on both sides, within the period', () => {
    const trade = { ...many(1)[0]!, openTs: 100 * HOUR, closeTs: 110 * HOUR };
    expect(tradeView(trade, HOUR, { start: 0, end: 1000 * HOUR })).toEqual({ from: 80 * HOUR, to: 131 * HOUR });
    expect(tradeView({ ...trade, openTs: HOUR, closeTs: HOUR }, HOUR, { start: 0, end: 2 * HOUR })).toEqual({
      from: 0,
      to: 2 * HOUR,
    });
  });

  it('turns a logical position into a time, past the candles a candle at a time', () => {
    expect(timeAtLogical(CANDLES.times, 3.4, HOUR)).toBe(3 * HOUR);
    expect(timeAtLogical(CANDLES.times, -2, HOUR)).toBe(-2 * HOUR);
    expect(timeAtLogical(CANDLES.times, 12, HOUR)).toBe(12 * HOUR);
    expect(timeAtLogical([], 5, HOUR)).toBe(5 * HOUR);
  });

  it('turns a time into a logical position, between candles in proportion and past them', () => {
    expect(logicalAtTime(CANDLES.times, 3 * HOUR, HOUR)).toBe(3);
    expect(logicalAtTime(CANDLES.times, -4 * HOUR, HOUR)).toBe(-4);
    expect(logicalAtTime(CANDLES.times, 15 * HOUR, HOUR)).toBe(15);
    // A night between two sessions: halfway through it is halfway between the candles.
    expect(logicalAtTime([0, HOUR, 13 * HOUR], 7 * HOUR, HOUR)).toBe(1.5);
    expect(logicalAtTime([], 5 * HOUR, HOUR)).toBe(5);
  });

  it('keeps a view with blank parts through the logical range and back', () => {
    // The left half of the chart before the first candle.
    const range = { from: -10, to: 9 };
    const view = timesOfLogical(CANDLES.times, range, HOUR);
    expect(view).toEqual({ from: -10 * HOUR, to: 10 * HOUR });
    expect(logicalOfTimes(CANDLES.times, view, HOUR)).toEqual(range);
    // On other candles, the same part of time.
    const finer = Array.from({ length: 40 }, (_, index) => (index - 20) * HOUR);
    expect(logicalOfTimes(finer, view, HOUR)).toEqual({ from: 10, to: 29 });
    // A view shorter than a candle is one candle wide, not reversed.
    expect(logicalOfTimes(CANDLES.times, { from: 3 * HOUR, to: 3 * HOUR + 60 }, HOUR)).toEqual({ from: 3, to: 3 });
  });
});
