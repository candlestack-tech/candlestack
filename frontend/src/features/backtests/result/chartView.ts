import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  LineType,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';

import { crosshairFormatter, dayFormatter, tickFormatter, type Bar } from '@/features/instrument';
import type { DateStyle } from '@/lib/dates';
import { formatPriceWith } from '@/lib/numbers';
import { formatPercent } from '@/lib/results';

import type { Band, Mark, TradeRow } from './chartData';

// The backtest page's TradingView chart (#309): the price in the top pane with the volume at its
// bottom, the trades as Buy and Sell arrows over shading while in a position, and the strategy
// against buy and hold in a pane under it. One time axis and one crosshair; it zooms and scrolls.
// This file only draws: the page decides what (TradesChart.tsx).

export type ChartColors = {
  ink: string;
  muted: string;
  rule: string;
  accent: string;
  down: string;
  candleUp: string;
  candleDown: string;
  price: string;
  font: string;
};

// The colours of the theme in src/index.css, like everything else.
export function readColors(): ChartColors {
  const css = getComputedStyle(document.documentElement);
  const read = (name: string) => css.getPropertyValue(name).trim();
  return {
    ink: read('--ink'),
    muted: read('--muted'),
    rule: read('--rule'),
    accent: read('--accent'),
    down: read('--down'),
    candleUp: read('--candle-up'),
    candleDown: read('--candle-down'),
    price: read('--price-line'),
    // The numbers in Geist Mono, as everywhere in the app.
    font: read('--font-mono'),
  };
}

// '#1a7f37' at 10 %, for the shading and the volume.
export function withAlpha(hex: string, alpha: number): string {
  const value = Number.parseInt(hex.replace('#', ''), 16);
  if (hex.length !== 7 || Number.isNaN(value)) return hex;
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${alpha})`;
}

// The strategy pane's height in pixels.
export const RETURNS_PANE = 110;

const TRANSPARENT = 'rgba(0, 0, 0, 0)';
const at = (time: number) => time as UTCTimestamp;

export type Point = { time: number; value: number };

// What one size of candles draws: the candles, the shading, the returns in percent and the
// decimals of the price.
export type Frame = {
  bars: readonly Bar[];
  bands: readonly Band[];
  strategy: readonly Point[];
  benchmark: readonly Point[] | null;
  decimals: number;
};

type Series = {
  long: ISeriesApi<'Area'>;
  short: ISeriesApi<'Area'>;
  candles: ISeriesApi<'Candlestick'>;
  line: ISeriesApi<'Line'>;
  volume: ISeriesApi<'Histogram'>;
  benchmark: ISeriesApi<'Line'>;
  strategy: ISeriesApi<'Line'>;
  link: ISeriesApi<'Line'>;
};

const QUIET = { lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false } as const;

// In a position: a stepped area, 1 in it and 0 out, on a hidden 0 to 1 scale behind the rest.
function addBand(chart: IChartApi) {
  return chart.addSeries(AreaSeries, {
    ...QUIET,
    priceScaleId: 'position',
    lineType: LineType.WithSteps,
    lineVisible: false,
    autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 1 } }),
  });
}

function addSeries(chart: IChartApi): Series {
  const long = addBand(chart);
  const short = addBand(chart);
  chart.priceScale('position').applyOptions({ visible: false, scaleMargins: { top: 0, bottom: 0 } });
  const candles = chart.addSeries(CandlestickSeries, { borderVisible: false });
  const line = chart.addSeries(LineSeries, { lineWidth: 2, visible: false, crosshairMarkerRadius: 3 });
  const volume = chart.addSeries(HistogramSeries, {
    ...QUIET,
    priceScaleId: 'volume',
    priceFormat: { type: 'volume' },
  });
  chart.priceScale('volume').applyOptions({ visible: false, scaleMargins: { top: 0.86, bottom: 0 } });
  const percent = { type: 'custom', minMove: 0.01, formatter: (value: number) => formatPercent(value / 100) } as const;
  const benchmark = chart.addSeries(
    LineSeries,
    { ...QUIET, lineWidth: 1, lineStyle: LineStyle.Dashed, priceFormat: percent },
    1,
  );
  const strategy = chart.addSeries(
    LineSeries,
    { lineWidth: 2, priceFormat: percent, priceLineVisible: false, crosshairMarkerRadius: 3 },
    1,
  );
  strategy.priceScale().applyOptions({ scaleMargins: { top: 0.3, bottom: 0.08 } });
  // A trade under the cursor: its entry and exit joined by a dashed line, which never moves the
  // price scale.
  const link = chart.addSeries(LineSeries, {
    ...QUIET,
    lineWidth: 1,
    lineStyle: LineStyle.Dashed,
    pointMarkersVisible: true,
    pointMarkersRadius: 2,
    autoscaleInfoProvider: () => null,
  });
  return { long, short, candles, line, volume, benchmark, strategy, link };
}

export class TradesChartView {
  readonly chart: IChartApi;
  private readonly series: Series;
  private readonly marks: Record<'candles' | 'line', ISeriesMarkersPluginApi<Time>>;
  private colors: ChartColors;
  private frame: Frame | null = null;
  private lineMode = false;

  constructor(container: HTMLElement, colors: ChartColors) {
    this.colors = colors;
    this.chart = createChart(container, {
      autoSize: true,
      // The TradingView attribution is a line in the footer instead (#166).
      layout: { attributionLogo: false, fontSize: 11, background: { type: ColorType.Solid, color: TRANSPARENT } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.24, bottom: 0.2 } },
      timeScale: { borderVisible: false, rightOffset: 4, minBarSpacing: 0.02 },
      crosshair: { mode: CrosshairMode.Normal },
    });
    this.series = addSeries(this.chart);
    this.chart.panes()[1]?.setHeight(RETURNS_PANE);
    this.series.strategy.createPriceLine({
      price: 0,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: false,
    });
    this.marks = {
      candles: createSeriesMarkers(this.series.candles, []),
      line: createSeriesMarkers(this.series.line, []),
    };
    this.paint(colors);
  }

  // The theme's colours on everything, the volume again too.
  paint(colors: ChartColors) {
    this.colors = colors;
    const { ink, muted, rule, accent, down, candleUp, candleDown, price, font } = colors;
    const line = { color: muted, style: LineStyle.Dashed, labelBackgroundColor: ink };
    this.chart.applyOptions({
      layout: { textColor: muted, ...(font && { fontFamily: font }), panes: { separatorColor: rule } },
      grid: { vertLines: { color: rule }, horzLines: { color: rule } },
      crosshair: { vertLine: line, horzLine: line },
    });
    const { long, short, candles, benchmark, strategy } = this.series;
    long.applyOptions({ topColor: withAlpha(accent, 0.1), bottomColor: withAlpha(accent, 0.1) });
    short.applyOptions({ topColor: withAlpha(down, 0.1), bottomColor: withAlpha(down, 0.1) });
    candles.applyOptions({
      upColor: candleUp,
      downColor: candleDown,
      wickUpColor: candleUp,
      wickDownColor: candleDown,
    });
    this.series.line.applyOptions({ color: price });
    benchmark.applyOptions({ color: withAlpha(muted, 0.8) });
    strategy.applyOptions({ color: accent });
    for (const priceLine of strategy.priceLines()) priceLine.applyOptions({ color: rule });
    if (this.frame) this.drawVolume(this.frame.bars);
  }

  // The axis and the crosshair in the user's zone and formats; days and weeks without the time.
  setTimeFormat(style: DateStyle, daily: boolean) {
    const crosshair = daily ? dayFormatter(style) : crosshairFormatter(style);
    const ticks = tickFormatter(style);
    this.chart.applyOptions({
      localization: { timeFormatter: (time: Time) => crosshair(time as number) },
      timeScale: { timeVisible: !daily, tickMarkFormatter: (time: Time, tick: number) => ticks(time as number, tick) },
    });
  }

  // New candles and what lies on them, keeping the part of time in view (or showing `view`).
  setFrame(frame: Frame, view: { from: number; to: number } | null) {
    const keep = view ?? this.visibleTimes();
    this.frame = frame;
    const { long, short, candles, line, strategy, benchmark, link } = this.series;
    const format = {
      type: 'custom',
      minMove: 10 ** -frame.decimals,
      formatter: (value: number) => formatPriceWith(value, frame.decimals),
    } as const;
    candles.applyOptions({ priceFormat: format });
    line.applyOptions({ priceFormat: format });
    candles.setData(frame.bars.map(({ time, open, high, low, close }) => ({ time: at(time), open, high, low, close })));
    line.setData(frame.bars.map(({ time, close }) => ({ time: at(time), value: close })));
    this.drawVolume(frame.bars);
    long.setData(frame.bands.map(({ time, long: value }) => ({ time: at(time), value })));
    short.setData(frame.bands.map(({ time, short: value }) => ({ time: at(time), value })));
    strategy.setData(frame.strategy.map(({ time, value }) => ({ time: at(time), value })));
    benchmark.setData((frame.benchmark ?? []).map(({ time, value }) => ({ time: at(time), value })));
    link.setData([]);
    if (keep) this.show(keep);
  }

  private drawVolume(bars: readonly Bar[]) {
    const { candleUp, candleDown } = this.colors;
    this.series.volume.setData(
      bars.map(({ time, open, close, volume }) => ({
        time: at(time),
        value: volume,
        color: withAlpha(close >= open ? candleUp : candleDown, 0.25),
      })),
    );
  }

  private visibleTimes() {
    const range = this.chart.timeScale().getVisibleRange();
    return range ? { from: range.from as number, to: range.to as number } : null;
  }

  show({ from, to }: { from: number; to: number }) {
    if (!this.frame?.bars.length) return;
    this.chart.timeScale().setVisibleRange({ from: at(from), to: at(to) });
  }

  // Candles or the closes as a line; the arrows go with whichever shows.
  setKind(line: boolean) {
    this.lineMode = line;
    this.series.candles.applyOptions({ visible: !line });
    this.series.line.applyOptions({ visible: line });
  }

  setMarks(marks: readonly Mark[]) {
    const { accent, down } = this.colors;
    const list = marks.map(({ time, kind, size, text }) =>
      kind === 'buy'
        ? { time: at(time), position: 'belowBar', shape: 'arrowUp', color: accent, size, text }
        : { time: at(time), position: 'aboveBar', shape: 'arrowDown', color: down, size, text },
    ) as Parameters<ISeriesMarkersPluginApi<Time>['setMarkers']>[0];
    this.marks[this.lineMode ? 'line' : 'candles'].setMarkers(list);
    this.marks[this.lineMode ? 'candles' : 'line'].setMarkers([]);
  }

  // The shading and the link with the trades, hidden with them.
  showTrades(on: boolean) {
    this.series.long.applyOptions({ visible: on });
    this.series.short.applyOptions({ visible: on });
  }

  // The dashed line from a trade's entry to its exit, at the candles they fall in.
  setLink(trade: TradeRow | null, times: { open?: number; close?: number }) {
    const { open, close } = times;
    if (!trade || open === undefined || close === undefined) {
      this.series.link.setData([]);
      return;
    }
    this.series.link.applyOptions({ color: trade.return >= 0 ? this.colors.accent : this.colors.down });
    const points = [
      { time: at(open), value: trade.openPrice },
      { time: at(close), value: trade.closePrice },
    ];
    this.series.link.setData(open === close ? points.slice(1) : points);
  }

  setLines({ strategy, benchmark }: { strategy: boolean; benchmark: boolean }) {
    this.series.strategy.applyOptions({ visible: strategy });
    this.series.benchmark.applyOptions({ visible: benchmark });
  }

  // How high the price pane is: the strategy's legend goes right under it.
  pricePaneHeight(): number {
    return this.chart.panes()[0]?.getHeight() ?? 0;
  }

  remove() {
    this.chart.remove();
  }
}
