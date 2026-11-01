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
  // The parts of time the chart was put on, `[from, to)`.
  shown: [] as { from: number; to: number }[],
  crosshair: [] as Handler[],
  // The user zooming or scrolling the last chart made to a logical range.
  scroll: (_range: { from: number; to: number }) => {},
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
export const BaselineSeries = 'Baseline';
export const CandlestickSeries = 'Candlestick';
export const HistogramSeries = 'Histogram';
export const LineSeries = 'Line';
export const ColorType = { Solid: 'solid' };
export const CrosshairMode = { Normal: 0, Magnet: 1 };
export const LineStyle = { Solid: 0, Dotted: 1, Dashed: 2 };
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
    createPriceLine: () => {
      const line = { applyOptions: vi.fn() };
      lines.push(line);
      return line;
    },
    priceLines: () => lines,
  };
}

// The time points: those of the candles, else of the strategy (the candles did not load).
function points(): Point[] {
  const candles = charts.series.find((series) => series.type === 'Candlestick')?.data ?? [];
  if (candles.length > 0) return candles;
  return charts.series.find((series) => series.pane === 1 && series.data.length > 0)?.data ?? [];
}

// A logical position as a time, on candles evenly apart as the tests have them.
function timeOf(position: number): number {
  const [first, second] = points();
  return (first?.time ?? 0) + position * ((second?.time ?? 0) - (first?.time ?? 0));
}

// One chart's time axis: the logical range in view, the parts of time it was put on recorded.
function timeAxis() {
  const ranges: RangeHandler[] = [];
  let logical: { from: number; to: number } | null = null;
  const moved = () => {
    for (const handler of ranges) handler();
  };
  const setRange = (range: { from: number; to: number }) => {
    logical = range;
    charts.shown.push({ from: timeOf(range.from), to: timeOf(range.to + 1) });
    moved();
  };
  // The first candles show all of them, as the real chart shows its last ones.
  const showFirst = (count: number) => {
    if (logical || count === 0) return;
    logical = { from: 0, to: count - 1 };
    moved();
  };
  const scroll = (range: { from: number; to: number }) => {
    logical = range;
    moved();
  };
  const api = {
    getVisibleLogicalRange: () => logical,
    setVisibleLogicalRange: setRange,
    fitContent: () => setRange({ from: 0, to: points().length - 1 }),
    subscribeVisibleLogicalRangeChange: (handler: RangeHandler) => ranges.push(handler),
    unsubscribeVisibleLogicalRangeChange: vi.fn(),
    subscribeSizeChange: vi.fn(),
    unsubscribeSizeChange: vi.fn(),
  };
  return { api, showFirst, scroll };
}

export function createChart(box: HTMLElement, options: Options) {
  charts.made += 1;
  charts.options.push(options);
  const axis = timeAxis();
  charts.scroll = axis.scroll;
  return {
    removeSeries: vi.fn(),
    setCrosshairPosition: vi.fn(),
    clearCrosshairPosition: vi.fn(),
    paneSize: () => ({ width: 100, height: 100 }),
    applyOptions: vi.fn(),
    addSeries: (type: string, options?: Options, pane?: number) => {
      const series = addSeries(type, options, pane);
      if (type !== 'Candlestick') return series;
      const setData = series.setData;
      series.setData = (data: Point[]) => {
        setData(data);
        axis.showFirst(data.length);
        return data;
      };
      return series;
    },
    chartElement: () => box,
    priceScale: () => ({ applyOptions: vi.fn() }),
    panes: () => [
      { setHeight: vi.fn(), getHeight: () => 300 },
      { setHeight: vi.fn(), getHeight: () => 110 },
    ],
    subscribeCrosshairMove: (handler: Handler) => charts.crosshair.push(handler),
    unsubscribeCrosshairMove: vi.fn(),
    timeScale: () => axis.api,
    remove: vi.fn(),
  };
}

export function createSeriesMarkers(series: { fake: FakeSeries }, markers: Options[]) {
  series.fake.markers = markers;
  return { setMarkers: (list: Options[]) => (series.fake.markers = list) };
}
