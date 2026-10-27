import {
  BaselineSeries,
  ColorType,
  LineStyle,
  createChart,
  type LineWidth,
  type UTCTimestamp,
} from 'lightweight-charts';
import { useEffect, useRef } from 'react';

import { useTheme } from '@/theme';

// The points sit a day apart: the series has no dates, and the time axis is hidden anyway.
const DAY = 86400;

// '#43b049' at 18%: the fill under the line is a soft shade of the line's color.
function withAlpha(hex: string, alpha: number): string {
  const value = Number.parseInt(hex.replace('#', ''), 16);
  if (hex.length !== 7 || Number.isNaN(value)) return hex;
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${alpha})`;
}

function themeColors() {
  const css = getComputedStyle(document.documentElement);
  const read = (name: string) => css.getPropertyValue(name).trim();
  return { up: read('--up'), down: read('--down'), zero: read('--muted') };
}

// A chart with nothing but its series: no axes, grid, crosshair, scrolling or zoom.
const PICTURE = {
  autoSize: true,
  // The TradingView attribution is a line in the footer instead (#166).
  layout: { background: { type: ColorType.Solid, color: 'transparent' }, attributionLogo: false },
  grid: { vertLines: { visible: false }, horzLines: { visible: false } },
  rightPriceScale: { visible: false, scaleMargins: { top: 0.1, bottom: 0.1 } },
  leftPriceScale: { visible: false },
  timeScale: { visible: false, borderVisible: false, fixLeftEdge: true, fixRightEdge: true },
  crosshair: { vertLine: { visible: false, labelVisible: false }, horzLine: { visible: false, labelVisible: false } },
  handleScroll: false,
  handleScale: false,
} as const;

// Draws the return in percent on a baseline at zero, in the theme's colours, and the zero line.
function draw(container: HTMLElement, strategy: readonly number[]) {
  const colors = themeColors();
  const chart = createChart(container, PICTURE);
  const series = chart.addSeries(BaselineSeries, {
    baseValue: { type: 'price', price: 0 },
    // The type lists whole pixels only; the canvas draws 1.5 as the mockup has it.
    lineWidth: 1.5 as LineWidth,
    lastValueVisible: false,
    priceLineVisible: false,
    crosshairMarkerVisible: false,
    topLineColor: colors.up,
    topFillColor1: withAlpha(colors.up, 0.18),
    topFillColor2: withAlpha(colors.up, 0),
    bottomLineColor: colors.down,
    bottomFillColor1: withAlpha(colors.down, 0),
    bottomFillColor2: withAlpha(colors.down, 0.18),
  });
  series.setData(strategy.map((value, index) => ({ time: (index * DAY) as UTCTimestamp, value: value * 100 })));
  series.createPriceLine({
    price: 0,
    color: withAlpha(colors.zero, 0.6),
    lineWidth: 1,
    lineStyle: LineStyle.Dotted,
    axisLabelVisible: false,
  });
  chart.timeScale().fitContent();
  return chart;
}

// A tiny TradingView chart of a backtest's return, 64 × 28 px in a soft border: green above zero
// and red below, as the return is coloured. It is a picture; the row around it opens the backtest.
// Buy and hold is left out: on one scale with it, a strategy that lost everything while the stock
// rose tenfold is a flat line.
export function MiniChart({ strategy }: { strategy: readonly number[] }) {
  const box = useRef<HTMLDivElement>(null);
  const theme = useTheme();

  useEffect(() => {
    if (!box.current) return;
    const chart = draw(box.current, strategy);
    return () => chart.remove();
  }, [strategy, theme]);

  return (
    <div ref={box} aria-hidden="true" className="h-7 w-16 overflow-hidden rounded-[5px] border border-border/70" />
  );
}
