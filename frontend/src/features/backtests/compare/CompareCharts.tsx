import {
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  type AutoscaleInfo,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type LogicalRange,
  type MouseEventParams,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import { useEffect, useRef, useState, type RefObject } from 'react';

import { dayFormatter, tickFormatter } from '@/features/instrument';
import { useViewerDateStyle } from '@/features/preferences';
import { formatDate } from '@/lib/dates';
import { useTheme } from '@/theme';

import { valueAt, type Line } from './chartLines';

// The Equity and Drawdown charts of the comparison, on TradingView Lightweight Charts: one line per
// backtest in its slot's colour, one crosshair and one view of time across both, and a box with
// each backtest's value at the crosshair.

type Field = 'equity' | 'drawdown';
const FIELDS: readonly { field: Field; title: string }[] = [
  { field: 'equity', title: 'Equity' },
  { field: 'drawdown', title: 'Drawdown' },
];
const HEIGHT = 240;

type Pane = { chart: IChartApi; series: ISeriesApi<'Line'>[]; zero: IPriceLine | null; field: Field };
type Hover = { time: number; x: number; width: number };
type Boxes = Partial<Record<Field, HTMLDivElement>>;

const withSign = (value: number) => `${value > 0 ? '+' : value < 0 ? '−' : ''}${Math.abs(value).toFixed(1)} %`;
const cssVar = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
// A slot's colour is `var(--compare-1)`; the charts' canvas needs the value itself.
const resolved = (color: string) => cssVar(color.slice(4, -1));

function makeChart(container: HTMLElement): IChartApi {
  return createChart(container, {
    autoSize: true,
    // The TradingView attribution is a line in the footer instead (#166).
    layout: { attributionLogo: false, fontSize: 11, background: { type: ColorType.Solid, color: 'transparent' } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false, rightOffset: 2, minBarSpacing: 0.02 },
    crosshair: { mode: CrosshairMode.Magnet, horzLine: { visible: false, labelVisible: false } },
    localization: { priceFormatter: withSign },
  });
}

// The theme's colours on a chart and on what is drawn in it.
function paint(pane: Pane, lines: readonly Line[]) {
  const font = cssVar('--font-mono');
  const muted = cssVar('--muted');
  pane.chart.applyOptions({
    layout: { textColor: muted, ...(font && { fontFamily: font }) },
    grid: { vertLines: { visible: false }, horzLines: { color: cssVar('--rule') } },
    crosshair: { vertLine: { color: muted, style: LineStyle.Dashed, labelBackgroundColor: cssVar('--ink') } },
  });
  pane.series.forEach((series, index) => {
    const line = lines[index];
    if (line) series.applyOptions({ color: resolved(line.color) });
  });
  pane.zero?.applyOptions({ color: muted });
}

type Autoscale = () => AutoscaleInfo | null;
const upToZero = (original: Autoscale) => {
  const info = original();
  return info?.priceRange ? { ...info, priceRange: { ...info.priceRange, maxValue: 0 } } : info;
};

function draw(pane: Pane, lines: readonly Line[]) {
  pane.series.forEach((series) => {
    pane.chart.removeSeries(series);
  });
  pane.zero = null;
  pane.series = lines.map((line, index) => {
    const series = pane.chart.addSeries(LineSeries, {
      color: resolved(line.color),
      lineWidth: 2,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerRadius: 3,
      // A fall never goes above zero, so its scale stops there.
      ...(pane.field === 'drawdown' && { autoscaleInfoProvider: upToZero }),
    });
    series.setData(line[pane.field].map(({ time, value }) => ({ time: time as UTCTimestamp, value })));
    if (index === 0) {
      pane.zero = series.createPriceLine({ price: 0, color: cssVar('--muted'), lineWidth: 1, axisLabelVisible: false });
    }
    return series;
  });
  pane.chart.timeScale().fitContent();
}

// What happens on a chart is copied to the other, and the copy is not copied back.
function linkPane(pane: Pane, other: Pane, onHover: (hover: Hover | null) => void, guard: { copying: boolean }) {
  const copy = (action: () => void) => {
    guard.copying = true;
    action();
    guard.copying = false;
  };
  const onCrosshair = ({ time, point }: MouseEventParams<Time>) => {
    if (guard.copying) return;
    if (time === undefined || point === undefined) {
      onHover(null);
      copy(() => {
        other.chart.clearCrosshairPosition();
      });
      return;
    }
    onHover({ time: time as number, x: point.x, width: pane.chart.paneSize().width });
    const series = other.series[0];
    if (series) {
      copy(() => {
        other.chart.setCrosshairPosition(0, time, series);
      });
    }
  };
  const onRange = (range: LogicalRange | null) => {
    if (guard.copying || !range) return;
    copy(() => {
      other.chart.timeScale().setVisibleLogicalRange(range);
    });
  };
  pane.chart.subscribeCrosshairMove(onCrosshair);
  pane.chart.timeScale().subscribeVisibleLogicalRangeChange(onRange);
}

// The two charts, linked, for as long as the page shows them.
function usePanes(boxes: RefObject<Boxes>, onHover: (hover: Hover | null) => void) {
  const panes = useRef<Pane[]>([]);
  useEffect(() => {
    const created = FIELDS.flatMap(({ field }) => {
      const box = boxes.current[field];
      return box ? [{ chart: makeChart(box), series: [], zero: null, field }] : [];
    });
    const [first, second] = created;
    if (first && second) {
      const guard = { copying: false };
      linkPane(first, second, onHover, guard);
      linkPane(second, first, onHover, guard);
    }
    panes.current = created;
    return () => {
      created.forEach(({ chart }) => {
        chart.remove();
      });
    };
  }, [boxes, onHover]);
  return panes;
}

// After usePanes in a component, so the charts exist by the time these run.
function useDrawn(panes: RefObject<Pane[]>, lines: readonly Line[]) {
  const theme = useTheme();
  const { timeZone, dateFormat, timeFormat } = useViewerDateStyle();
  useEffect(() => {
    const style = { timeZone, dateFormat, timeFormat };
    panes.current.forEach(({ chart }) => {
      chart.applyOptions({
        localization: { timeFormatter: dayFormatter(style) },
        timeScale: { tickMarkFormatter: (time: Time, tick: number) => tickFormatter(style)(time as number, tick) },
      });
    });
  }, [panes, timeZone, dateFormat, timeFormat]);
  // A backtest's document never changes once it finished, so this says when the lines did, whatever
  // made the page draw again; the view of time stays until then.
  const signature = lines.map(({ id, color, equity }) => `${id} ${color} ${String(equity.length)}`).join();
  const latest = useRef(lines);
  useEffect(() => {
    latest.current = lines;
  });
  useEffect(() => {
    panes.current.forEach((pane) => {
      draw(pane, latest.current);
    });
  }, [panes, signature]);
  useEffect(() => {
    panes.current.forEach((pane) => {
      paint(pane, latest.current);
    });
  }, [panes, signature, theme]);
}

type ChartProps = { lines: readonly Line[]; field: Field };

// Each backtest's value at the crosshair, on the side of the chart away from it.
function HoverBox({ lines, field, hover }: ChartProps & { hover: Hover }) {
  const style = useViewerDateStyle();
  return (
    <div
      className="pointer-events-none absolute top-1.5 z-10 min-w-[150px] rounded-md border bg-background px-2.5 py-2 shadow-[0_2px_8px_var(--shadow)]"
      style={hover.x > hover.width / 2 ? { left: 8 } : { right: 60 }}
    >
      <div className="mb-1 text-[11px] text-muted-foreground">{formatDate(new Date(hover.time * 1000), style)}</div>
      {lines.map((line) => {
        const value = valueAt(line[field], hover.time);
        return (
          <div key={line.id} className="flex items-center justify-between gap-3.5 text-xs">
            <span className="inline-flex items-center gap-1.5 font-mono">
              <span aria-hidden="true" className="size-2 rounded-full" style={{ background: line.color }} />#
              {line.number}
            </span>
            <span className="font-mono font-medium">{value === null ? '–' : withSign(value)}</span>
          </div>
        );
      })}
    </div>
  );
}

type BoxProps = ChartProps & {
  title: string;
  hover: Hover | null;
  box: (element: HTMLDivElement | null) => void;
};

function ChartBox({ lines, hover, field, title, box }: BoxProps) {
  return (
    <section aria-label={title} className="min-w-0 rounded-lg border bg-background p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-[15px] font-semibold">{title}</h2>
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          {lines.map(({ id, number, color }) => (
            <span key={id} className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              <span aria-hidden="true" className="h-[3px] w-3.5 rounded-sm" style={{ background: color }} />
              <span className="font-mono">#{number}</span>
            </span>
          ))}
        </div>
      </div>
      <div className="relative" style={{ height: HEIGHT }}>
        <div ref={box} className="absolute inset-0" />
        {hover && <HoverBox lines={lines} field={field} hover={hover} />}
      </div>
    </section>
  );
}

export function CompareCharts({ lines }: { lines: readonly Line[] }) {
  const boxes = useRef<Boxes>({});
  const [hover, setHover] = useState<Hover | null>(null);
  const panes = usePanes(boxes, setHover);
  useDrawn(panes, lines);
  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,480px),1fr))] gap-4">
      {FIELDS.map(({ field, title }) => (
        <ChartBox
          key={field}
          field={field}
          title={title}
          lines={lines}
          hover={hover}
          box={(element) => {
            boxes.current[field] = element ?? undefined;
          }}
        />
      ))}
    </div>
  );
}
