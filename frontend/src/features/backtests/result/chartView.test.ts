import { afterEach, describe, expect, it, vi } from 'vitest';

import { charts } from '@/test/lightweightCharts';

import { TradesChartView, type ChartColors, type Frame } from './chartView';

vi.mock('lightweight-charts', () => import('@/test/lightweightCharts'));

const HOUR = 3600;

const DARK: ChartColors = {
  ink: '#ffffff',
  muted: '#999999',
  rule: '#333333',
  accent: '#3cc7a6',
  down: '#f0616d',
  candleUp: '#3cc7a6',
  candleDown: '#f0616d',
  price: '#ffffff',
  font: '',
};
const LIGHT: ChartColors = { ...DARK, accent: '#0f766e', down: '#b42318' };

// Hourly candles from `from` on, `count` of them.
function frame(from: number, count: number): Frame {
  const times = Array.from({ length: count }, (_, index) => from + index * HOUR);
  return {
    bars: times.map((time) => ({ time, open: 1, high: 2, low: 0.5, close: 1.5, volume: 7 })),
    times,
    seconds: HOUR,
    bands: times.map((time) => ({ time, long: 0, short: 0 })),
    strategy: [],
    benchmark: null,
    decimals: 2,
  };
}

// The fake chart never looks at its box.
const box = () => ({}) as HTMLElement;

afterEach(() => charts.reset());

describe("the backtest page's chart", () => {
  it('keeps the blank part of the view when new candles come in', () => {
    const view = new TradesChartView(box(), DARK);
    view.setFrame(frame(0, 10), null);
    // Dragged left past the candles: the left half of the chart is blank.
    view.chart.timeScale().setVisibleLogicalRange({ from: -10, to: 9 });
    view.setFrame(frame(-20 * HOUR, 30), null);
    expect(charts.shown.at(-1)).toEqual({ from: -10 * HOUR, to: 10 * HOUR });
  });

  it('draws the arrows and the link again in the colours of a new theme', () => {
    const view = new TradesChartView(box(), DARK);
    view.setFrame(frame(0, 10), null);
    view.setMarks([
      { time: 0, kind: 'buy', size: 1, text: 'Buy' },
      { time: 2 * HOUR, kind: 'sell', size: 1, text: 'Sell' },
    ]);
    const trade = {
      index: 0,
      long: true,
      openTs: 0,
      openPrice: 1,
      closeTs: 2 * HOUR,
      closePrice: 2,
      return: 1,
      candles: 3,
      reason: 'signal',
    };
    view.setLink(trade, { open: 0, close: 2 * HOUR });
    view.paint(LIGHT);
    const candles = charts.of('Candlestick')[0]!;
    expect(candles.markers.map((marker) => marker.color)).toEqual([LIGHT.accent, LIGHT.down]);
    const link = charts.of('Line').at(-1)!;
    expect(link.options.color).toBe(LIGHT.accent);
    expect(link.data).toHaveLength(2);
  });
});
