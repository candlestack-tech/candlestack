import type { LogicalRange, MouseEventParams, Time } from 'lightweight-charts';
import { useEffect, useRef, useState } from 'react';

import type { DateStyle } from '@/lib/dates';
import { useTheme } from '@/theme';

import { timesOfLogical, type Candles } from './chartData';
import { TradesChartView, readColors } from './chartView';

// The chart of a backtest's page as React sees it: made once, painted with the theme, writing
// times in the user's style, and telling the page what is in view and under the cursor.

export type View = { from: number; to: number };

// The chart in its box. `daily` drops the time of day for days and weeks.
export function useChartView(style: DateStyle, daily: boolean) {
  const container = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<TradesChartView | null>(null);
  const theme = useTheme();
  useEffect(() => {
    if (!container.current) return;
    const created = new TradesChartView(container.current, readColors());
    setView(created);
    return () => {
      created.remove();
      setView(null);
    };
  }, []);
  useEffect(() => {
    view?.paint(readColors());
  }, [view, theme]);
  const { timeZone, dateFormat, timeFormat } = style;
  useEffect(() => {
    view?.setTimeFormat({ timeZone, dateFormat, timeFormat }, daily);
  }, [view, timeZone, dateFormat, timeFormat, daily]);
  return { container, view };
}

// The part of time in view, `[from, to)`, past the loaded candles too.
function viewOf(range: LogicalRange | null, candles: Candles, seconds: number): View | null {
  if (!range || candles.times.length === 0) return null;
  return timesOfLogical(candles.times, range, seconds);
}

type Events = {
  candles: Candles | undefined;
  // The length of one candle on the chart.
  seconds: number;
};

// What is in view, once a frame (the arrows follow it), and the candle under the cursor by its
// time, null off the candles. Also how high the price pane is, for the legend under it.
export function useChartEvents(view: TradesChartView | null, { candles, seconds }: Events) {
  const [visible, setVisible] = useState<View | null>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const [paneHeight, setPaneHeight] = useState(0);
  const latest = useRef({ candles, seconds });
  useEffect(() => {
    latest.current = { candles, seconds };
  }, [candles, seconds]);
  useEffect(() => {
    if (!view) return;
    let frame = 0;
    const onRange = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const { candles: now, seconds: length } = latest.current;
        // Drawing the arrows tells of a range change too: only a real move makes a new view.
        const next = now && viewOf(view.chart.timeScale().getVisibleLogicalRange(), now, length);
        if (next) setVisible((last) => (last?.from === next.from && last.to === next.to ? last : next));
        setPaneHeight(view.pricePaneHeight());
      });
    };
    const onMove = (event: MouseEventParams<Time>) =>
      setCursor(event.point && event.time !== undefined ? (event.time as number) : null);
    view.chart.timeScale().subscribeVisibleLogicalRangeChange(onRange);
    view.chart.timeScale().subscribeSizeChange(onRange);
    view.chart.subscribeCrosshairMove(onMove);
    return () => {
      cancelAnimationFrame(frame);
      view.chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRange);
      view.chart.timeScale().unsubscribeSizeChange(onRange);
      view.chart.unsubscribeCrosshairMove(onMove);
    };
  }, [view]);
  return { visible, cursor, paneHeight };
}
