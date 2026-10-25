import {
  AreaSeries,
  BaselineSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import { useEffect, useMemo, useRef } from 'react';

import { crosshairFormatter, dayFormatter, tickFormatter } from '@/features/instrument';
import type { DateStyle } from '@/lib/dates';
import { formatPriceWith, priceDecimals } from '@/lib/numbers';
import { cn } from '@/lib/utils';
import { useTheme } from '@/theme';

import {
  benchmarkPoints,
  drawdownPoints,
  equityPoints,
  pricePoints,
  showsMarkers,
  tradeMarkers,
  type Point,
} from './chartData';
import type { Series, Trades } from './chartData';

// The read-only TradingView charts of a backtest (mockups/app, mountRunCharts): no scrolling or
// zooming, the whole test period in view, the colours of the theme.

type Colors = { ink: string; muted: string; grid: string; up: string; down: string; accent: string; font: string };

function readColors(): Colors {
  const css = getComputedStyle(document.documentElement);
  const read = (name: string) => css.getPropertyValue(name).trim();
  return {
    ink: read('--ink'),
    muted: read('--muted'),
    grid: read('--rule'),
    up: read('--up'),
    down: read('--down'),
    accent: read('--accent'),
    // The numbers in Geist Mono, as everywhere in the app.
    font: read('--font-mono'),
  };
}

// '#1a7f37' at 22 %, for the fills under the lines.
function withAlpha(hex: string, alpha: number): string {
  const value = Number.parseInt(hex.replace('#', ''), 16);
  if (hex.length !== 7 || Number.isNaN(value)) return hex;
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${alpha})`;
}

const TRANSPARENT = 'rgba(0, 0, 0, 0)';

// The axis and the crosshair in the user's zone and formats; a run of days shows no time of day.
type TimeStyle = { dateStyle: DateStyle; daily: boolean };

function create(container: HTMLElement, colors: Colors, { dateStyle, daily }: TimeStyle): IChartApi {
  const crosshair = daily ? dayFormatter(dateStyle) : crosshairFormatter(dateStyle);
  const ticks = tickFormatter(dateStyle);
  const line = { color: colors.muted, labelBackgroundColor: colors.ink };
  return createChart(container, {
    autoSize: true,
    // The TradingView attribution is a line in the footer instead (#166).
    layout: {
      attributionLogo: false,
      background: { type: ColorType.Solid, color: TRANSPARENT },
      textColor: colors.muted,
      ...(colors.font && { fontFamily: colors.font }),
      fontSize: 11,
    },
    grid: { vertLines: { visible: false }, horzLines: { color: colors.grid } },
    rightPriceScale: { borderVisible: false },
    timeScale: {
      borderVisible: false,
      fixLeftEdge: true,
      fixRightEdge: true,
      // The whole period stays in view when the box gets narrower or wider (a phone turned).
      lockVisibleTimeRangeOnResize: true,
      timeVisible: !daily,
      tickMarkFormatter: (time: Time, tick: number) => ticks(time as number, tick),
    },
    localization: { timeFormatter: (time: Time) => crosshair(time as number) },
    handleScroll: false,
    handleScale: false,
    crosshair: { mode: CrosshairMode.Magnet, vertLine: line, horzLine: line },
  });
}

const asData = (list: readonly Point[]) => list.map(({ time, value }) => ({ time: time as UTCTimestamp, value }));

// A return in percent with its sign: +12.3%, −4.0%.
const PERCENT = {
  type: 'custom',
  formatter: (value: number) => `${value < 0 ? '−' : '+'}${Math.abs(value).toFixed(1)}%`,
} as const;

type Draw = (chart: IChartApi, colors: Colors) => void;

// One chart in its box, made again when what it draws or the theme changes.
function ReadOnlyChart({
  draw,
  time,
  label,
  className,
}: {
  draw: Draw;
  time: TimeStyle;
  label: string;
  className: string;
}) {
  const box = useRef<HTMLElement>(null);
  const theme = useTheme();
  const { dateStyle, daily } = time;
  const { timeZone, dateFormat, timeFormat } = dateStyle;
  useEffect(() => {
    if (!box.current) return;
    const colors = readColors();
    const chart = create(box.current, colors, { dateStyle: { timeZone, dateFormat, timeFormat }, daily });
    draw(chart, colors);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [draw, theme, timeZone, dateFormat, timeFormat, daily]);
  return <figure ref={box} aria-label={label} className={cn('relative min-w-0', className)} />;
}

// The strategy's return as a baseline at the start, green above and red below, with buy and hold
// dashed.
const drawEquity =
  (series: Series): Draw =>
  (chart, colors) => {
    chart
      .addSeries(BaselineSeries, {
        baseValue: { type: 'price', price: 0 },
        topLineColor: colors.up,
        topFillColor1: withAlpha(colors.up, 0.22),
        topFillColor2: TRANSPARENT,
        bottomLineColor: colors.down,
        bottomFillColor1: TRANSPARENT,
        bottomFillColor2: withAlpha(colors.down, 0.22),
        lineWidth: 2,
        priceFormat: PERCENT,
        title: 'Strategy',
      })
      .setData(asData(equityPoints(series)));
    const benchmark = benchmarkPoints(series);
    if (benchmark.length === 0) return;
    chart
      .addSeries(LineSeries, {
        color: colors.muted,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceFormat: PERCENT,
        lastValueVisible: false,
        priceLineVisible: false,
        title: 'Buy and hold',
      })
      .setData(asData(benchmark));
  };

// The fall from the peak as a red area, on the dates of the chart above it.
const drawDrawdown =
  (series: Series): Draw =>
  (chart, colors) => {
    chart.applyOptions({ timeScale: { visible: false } });
    chart
      .addSeries(AreaSeries, {
        lineColor: colors.down,
        topColor: withAlpha(colors.down, 0.06),
        bottomColor: withAlpha(colors.down, 0.26),
        lineWidth: 1,
        priceFormat: PERCENT,
        priceLineVisible: false,
        title: 'Drawdown',
      })
      .setData(asData(drawdownPoints(series)));
  };

// The price as a line over a light fill, a green arrow under every buy and a red one over every
// sell.
const drawPrice =
  (series: Series, trades: Trades): Draw =>
  (chart, colors) => {
    const decimals = priceDecimals(series.columns.close.at(-1) ?? 0);
    const price = chart.addSeries(AreaSeries, {
      lineColor: colors.ink,
      topColor: withAlpha(colors.accent, 0.14),
      bottomColor: TRANSPARENT,
      lineWidth: 2,
      priceLineVisible: false,
      // 83,249.35 as the instrument list writes it.
      priceFormat: {
        type: 'custom',
        minMove: 10 ** -decimals,
        formatter: (value: number) => formatPriceWith(value, decimals),
      },
    });
    price.setData(asData(pricePoints(series)));
    // Hundreds of trades would make a band of arrows that hides the price: the table has them.
    if (!showsMarkers(trades)) return;
    createSeriesMarkers(
      price,
      tradeMarkers(series, trades).map(({ time, kind }) =>
        kind === 'buy'
          ? { time: time as UTCTimestamp, position: 'belowBar', color: colors.up, shape: 'arrowUp' }
          : { time: time as UTCTimestamp, position: 'aboveBar', color: colors.down, shape: 'arrowDown' },
      ),
    );
  };

type Props = { series: Series; time: TimeStyle };

// The equity with the drawdown under it.
export function EquityCharts({ series, time }: Props) {
  const equity = useMemo(() => drawEquity(series), [series]);
  const drawdown = useMemo(() => drawDrawdown(series), [series]);
  return (
    <>
      <ReadOnlyChart draw={equity} time={time} label="Equity of the strategy and buy and hold" className="h-60" />
      <ReadOnlyChart draw={drawdown} time={time} label="Drawdown" className="h-[90px]" />
    </>
  );
}

export function PriceChart({ series, trades, time, symbol }: Props & { trades: Trades; symbol: string }) {
  const draw = useMemo(() => drawPrice(series, trades), [series, trades]);
  return <ReadOnlyChart draw={draw} time={time} label={`${symbol} price with the trades`} className="h-[220px]" />;
}
