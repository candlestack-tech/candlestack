import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LogicalRange,
  type MouseEventParams,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import { ChevronsRight } from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';

import type { Market } from '@/api';
import { Button } from '@/components/ui/button';
import type { DateStyle } from '@/lib/dates';
import { priceDecimals } from '@/lib/numbers';
import { useTheme } from '@/theme';

import type { Bar } from './bars';
import { ChartLegend } from './ChartLegend';
import { legendFor } from './legend';
import { crosshairFormatter, dayFormatter, tickFormatter } from './time';

// Start loading the older page this many bars before the left edge, so it is there in time.
const PRELOAD_BARS = 30;
// Further than this from the newest candle, the "go to the latest candle" button shows.
const AWAY_BARS = 3;
// The chart opens on this many of the newest candles, as the mockup does (about four months of
// daily candles); fewer fill it at the default spacing from the right.
const OPEN_BARS = 120;

type Series = { candles: ISeriesApi<'Candlestick'>; volume: ISeriesApi<'Histogram'> };

// '#43b049' at 40%: the volume bars are a softer shade of their candle's color.
function withAlpha(hex: string, alpha: number): string {
  const value = Number.parseInt(hex.replace('#', ''), 16);
  if (hex.length !== 7 || Number.isNaN(value)) return hex;
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${alpha})`;
}

// The chart's colors come from the theme in src/index.css, like everything else.
function themeColors() {
  const css = getComputedStyle(document.documentElement);
  const read = (name: string) => css.getPropertyValue(name).trim();
  return { text: read('--muted'), grid: read('--rule'), up: read('--candle-up'), down: read('--candle-down') };
}

function applyTheme(chart: IChartApi, series: Series) {
  const colors = themeColors();
  chart.applyOptions({
    layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: colors.text },
    grid: { vertLines: { color: colors.grid }, horzLines: { color: colors.grid } },
    rightPriceScale: { borderColor: colors.grid },
    timeScale: { borderColor: colors.grid },
  });
  series.candles.applyOptions({
    upColor: colors.up,
    downColor: colors.down,
    wickUpColor: colors.up,
    wickDownColor: colors.down,
  });
  return colors;
}

function create(container: HTMLElement) {
  const chart = createChart(container, {
    autoSize: true,
    // The TradingView attribution is a line in the footer instead (#166).
    layout: { attributionLogo: false },
    timeScale: { timeVisible: true },
  });
  const candles = chart.addSeries(CandlestickSeries, { borderVisible: false });
  const volume = chart.addSeries(HistogramSeries, { priceScaleId: '', priceFormat: { type: 'volume' } });
  volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
  return { chart, series: { candles, volume } };
}

function setBars(series: Series, bars: readonly Bar[], colors: { up: string; down: string }) {
  series.candles.setData(
    bars.map(({ time, open, high, low, close }) => ({ time: time as UTCTimestamp, open, high, low, close })),
  );
  series.volume.setData(
    bars.map((bar) => ({
      time: bar.time as UTCTimestamp,
      value: bar.volume,
      color: withAlpha(bar.close >= bar.open ? colors.up : colors.down, 0.4),
    })),
  );
}

// The axis and the crosshair write dates and times as the user does, in the style's zone; a chart
// of days shows no time of day under the crosshair.
function applyTimeFormats(chart: IChartApi, style: DateStyle, daily: boolean) {
  const ticks = tickFormatter(style);
  const crosshair = daily ? dayFormatter(style) : crosshairFormatter(style);
  chart.applyOptions({
    localization: { timeFormatter: (time: Time) => crosshair(time as number) },
    timeScale: { tickMarkFormatter: (time: Time, tick: number) => ticks(time as number, tick) },
  });
}

type Instance = { chart: IChartApi; series: Series };

// The candle under the cursor, by its index in the bars; null when the cursor is off the chart.
function hoveredIndex(event: MouseEventParams): number | null {
  return event.point && event.logical !== undefined ? Math.round(event.logical) : null;
}

// Puts the bars into the chart, in the theme's colors and with enough decimals for the price.
// The first bars of a chart show their newest OPEN_BARS; older bars added on the left later shift
// the view by as many, so it does not jump. Returns the time of the first bar, to compare with on
// the next call.
function showBars(instance: Instance, bars: readonly Bar[], firstTime: number | undefined) {
  const colors = applyTheme(instance.chart, instance.series);
  const decimals = priceDecimals(bars.at(-1)?.close ?? 0);
  instance.series.candles.applyOptions({
    priceFormat: { type: 'price', precision: decimals, minMove: 10 ** -decimals },
  });
  const timeScale = instance.chart.timeScale();
  const range = timeScale.getVisibleLogicalRange();
  const added = firstTime === undefined ? 0 : bars.findIndex((bar) => bar.time === firstTime);
  setBars(instance.series, bars, colors);
  if (firstTime === undefined && bars.length > OPEN_BARS) {
    // Logical indexes are the bars' centres: half a bar more on each side shows them whole.
    timeScale.setVisibleLogicalRange({ from: bars.length - OPEN_BARS - 0.5, to: bars.length - 0.5 });
  } else if (range && added > 0) {
    timeScale.setVisibleLogicalRange({ from: range.from + added, to: range.to + added });
  }
  return bars[0]?.time;
}

// How the chart writes time: the user's zone and formats, and whether its candles are days.
type TimeStyle = { dateStyle: DateStyle; daily: boolean };

// The axis and the crosshair follow the user's zone and formats once the chart is there (a new one
// for each market), and on a change.
function useTimeFormats(
  chart: RefObject<Instance | null>,
  market: Market,
  { dateStyle: { timeZone, dateFormat, timeFormat }, daily }: TimeStyle,
) {
  useEffect(() => {
    if (chart.current) applyTimeFormats(chart.current.chart, { timeZone, dateFormat, timeFormat }, daily);
  }, [chart, market, timeZone, dateFormat, timeFormat, daily]);
}

// Creates the chart and follows it: loads older pages near the left edge, tracks the candle under
// the cursor for the legend, and whether the newest candle is out of view.
function useCandleChart(market: Market, time: TimeStyle, bars: readonly Bar[], onNearLeftEdge: () => void) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<Instance | null>(null);
  const lastIndex = useRef(-1);
  const firstTime = useRef<number | undefined>(undefined);
  const nearEdge = useRef(onNearLeftEdge);
  const [hovered, setHovered] = useState<number | null>(null);
  const [awayFromLatest, setAwayFromLatest] = useState(false);
  const theme = useTheme();

  // loadOlder changes when a fetch ends or a page arrives. It skips while a page is loading, so
  // if the view still sits at the left edge, ask again now: a user who stopped there gets the
  // older page without moving the chart.
  useEffect(() => {
    nearEdge.current = onNearLeftEdge;
    const range = chart.current?.chart.timeScale().getVisibleLogicalRange();
    if (range && range.from < PRELOAD_BARS) onNearLeftEdge();
  }, [onNearLeftEdge]);

  // One chart per market; the bars and the time formats only get replaced.
  useEffect(() => {
    if (!container.current) return;
    const created = create(container.current);
    chart.current = created;
    created.chart.timeScale().subscribeVisibleLogicalRangeChange((range: LogicalRange | null) => {
      if (!range) return;
      if (range.from < PRELOAD_BARS) nearEdge.current();
      setAwayFromLatest(Math.abs(range.to - lastIndex.current) > AWAY_BARS);
    });
    created.chart.subscribeCrosshairMove((event) => setHovered(hoveredIndex(event)));
    return () => {
      created.chart.remove();
      chart.current = null;
      firstTime.current = undefined;
    };
  }, [market]);

  useTimeFormats(chart, market, time);

  useEffect(() => {
    if (!chart.current) return;
    lastIndex.current = bars.length - 1;
    firstTime.current = showBars(chart.current, bars, firstTime.current);
  }, [bars, theme, market]);

  const scrollToLatest = useCallback(() => chart.current?.chart.timeScale().scrollToRealTime(), []);
  return { container, hovered, awayFromLatest, scrollToLatest };
}

type Props = {
  bars: readonly Bar[];
  market: Market;
  dateStyle: DateStyle;
  // Daily candles: the crosshair shows the date alone.
  daily: boolean;
  onNearLeftEdge: () => void;
};

export function CandleChart({ bars, market, dateStyle, daily, onNearLeftEdge }: Props) {
  const { container, hovered, awayFromLatest, scrollToLatest } = useCandleChart(
    market,
    { dateStyle, daily },
    bars,
    onNearLeftEdge,
  );
  const legend = legendFor(bars, hovered, priceDecimals(bars.at(-1)?.close ?? 0));

  // As tall as the rest of the page leaves room for on the screen, however many lines the name,
  // the facts or the status take: the page is a flex column (Layout.tsx) and the chart its part
  // that grows, up to where its section stops (InstrumentCandles.tsx). Never under 16rem on a phone
  // (a long name takes three lines there) and 20rem from sm up (phones in landscape): then the
  // page scrolls.
  return (
    <div className="relative min-h-64 flex-1 sm:min-h-80">
      <div ref={container} className="absolute inset-0" />
      {legend && <ChartLegend legend={legend} />}
      {awayFromLatest && (
        <Button
          variant="outline"
          size="icon-sm"
          className="absolute right-20 bottom-10 z-10"
          aria-label="Go to the latest candle"
          title="Go to the latest candle"
          onClick={scrollToLatest}
        >
          <ChevronsRight />
        </Button>
      )}
    </div>
  );
}
