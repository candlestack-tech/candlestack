import { vi } from 'vitest';

// A stand-in for lightweight-charts, which draws on a canvas jsdom has not: it keeps what the
// page gave each chart and series, and lets a test move the crosshair. Use it as
// `vi.mock('lightweight-charts', () => import('@/test/lightweightCharts'))`.

type Point = { time: number };
type Options = Record<string, unknown>;

export type FakeSeries = {
  type: string;
  pane: number;
  options: Options;
  data: Point[];
  markers: Options[];
};

type Handler = (...args: never[]) => void;
type RangeHandler = () => void;

export const charts = {
  made: 0,
  options: [] as Options[],
  series: [] as FakeSeries[],
  shown: [] as { from: number; to: number }[],
  crosshair: [] as Handler[],
  reset() {
    Object.assign(charts, { made: 0, options: [], series: [], shown: [], crosshair: [] });
  },
  // The series of a type, in the order the chart added them.
  of(type: string) {
    return charts.series.filter((series) => series.type === type);
  },
  // The cursor over the candle that opens at `time`, or off the chart.
  hover(time: number | null) {
    const event = time === null ? {} : { time, point: { x: 10, y: 10 }, logical: 0 };
    for (const handler of charts.crosshair) (handler as (event: unknown) => void)(event);
  },
};

export const AreaSeries = 'Area';
export const CandlestickSeries = 'Candlestick';
export const HistogramSeries = 'Histogram';
export const LineSeries = 'Line';
export const ColorType = { Solid: 'solid' };
export const CrosshairMode = { Normal: 0, Magnet: 1 };
export const LineStyle = { Solid: 0, Dashed: 2 };
export const LineType = { Simple: 0, WithSteps: 1 };

function addSeries(type: string, options: Options = {}, pane = 0) {
  const series: FakeSeries = { type, pane, options: { visible: true, ...options }, data: [], markers: [] };
  charts.series.push(series);
  const lines: { applyOptions: () => void }[] = [];
  return {
    fake: series,
    setData: (data: Point[]) => (series.data = data),
    applyOptions: (more: Options) => Object.assign(series.options, more),
    priceScale: () => ({ applyOptions: vi.fn() }),
    createPriceLine: () => lines.push({ applyOptions: vi.fn() }),
    priceLines: () => lines,
  };
}

export function createChart(_box: HTMLElement, options: Options) {
  charts.made += 1;
  charts.options.push(options);
  const ranges: RangeHandler[] = [];
  let shown: { from: number; to: number } | null = null;
  const candles = () => charts.series.find((series) => series.type === 'Candlestick')?.data ?? [];
  // The candle at or after a time, the last one past them.
  const indexOf = (time: number) => {
    const index = candles().findIndex((point) => point.time >= time);
    return index < 0 ? candles().length - 1 : index;
  };
  return {
    applyOptions: vi.fn(),
    addSeries,
    priceScale: () => ({ applyOptions: vi.fn() }),
    panes: () => [
      { setHeight: vi.fn(), getHeight: () => 300 },
      { setHeight: vi.fn(), getHeight: () => 110 },
    ],
    subscribeCrosshairMove: (handler: Handler) => charts.crosshair.push(handler),
    unsubscribeCrosshairMove: vi.fn(),
    timeScale: () => ({
      getVisibleRange: () => shown,
      getVisibleLogicalRange: () => shown && { from: indexOf(shown.from), to: indexOf(shown.to) },
      setVisibleRange: (range: { from: number; to: number }) => {
        shown = range;
        charts.shown.push(range);
        for (const handler of ranges) handler();
      },
      subscribeVisibleLogicalRangeChange: (handler: RangeHandler) => ranges.push(handler),
      unsubscribeVisibleLogicalRangeChange: vi.fn(),
      subscribeSizeChange: vi.fn(),
      unsubscribeSizeChange: vi.fn(),
    }),
    remove: vi.fn(),
  };
}

export function createSeriesMarkers(series: { fake: FakeSeries }, markers: Options[]) {
  series.fake.markers = markers;
  return { setMarkers: (list: Options[]) => (series.fake.markers = list) };
}
