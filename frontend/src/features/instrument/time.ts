import type { Market } from '@/api';
import { NEW_YORK, calendarDay, formatDate, formatShortDay, formatTime, monthName, type DateStyle } from '@/lib/dates';

// Under the chart, so nobody has to guess which clock the axis uses. Any zone but New York is the
// viewer's own.
export function timeZoneNote(timeZone: string): string {
  return timeZone === NEW_YORK ? 'Times are New York time.' : `Times are your time (${timeZone}).`;
}

// Under a daily chart, which writes each candle's day in the market's zone (dayZone), not times.
export function dayNote(market: Market): string {
  return market === 'stock' ? 'Days are New York trading days.' : 'Days are UTC days.';
}

// The kinds of tick marks lightweight-charts asks a label for (its TickMarkType enum).
export const Tick = { Year: 0, Month: 1, Day: 2, Time: 3, TimeWithSeconds: 4 } as const;

// Labels on the time axis: a year, a month, a day or a time of day in the user's time format, in
// the style's zone. The day stays short (3 Jun) in every date format, to fit between the ticks.
export function tickFormatter(style: DateStyle) {
  return (seconds: number, tick: number): string => {
    const date = new Date(seconds * 1000);
    if (tick === Tick.Time || tick === Tick.TimeWithSeconds) return formatTime(date, style);
    if (tick === Tick.Day) return formatShortDay(date, style);
    const { year, month } = calendarDay(date, style.timeZone);
    return tick === Tick.Year ? year : monthName(month);
  };
}

// The label under the crosshair: the date and the time as the user writes them, in the style's
// zone.
export function crosshairFormatter(style: DateStyle) {
  return (seconds: number): string => {
    const date = new Date(seconds * 1000);
    return `${formatDate(date, style)}, ${formatTime(date, style)}`;
  };
}

// The label under the crosshair of a chart of days: the date alone.
export function dayFormatter(style: DateStyle) {
  return (seconds: number): string => formatDate(new Date(seconds * 1000), style);
}

// The zone of a market's days, in which a daily candle or the first day of data is written: a
// stock's session is a New York day, a crypto day runs 00:00-24:00 UTC (docs/data.md, 4h and 1d).
// In the user's zone, a crypto day would read as the day before west of UTC.
export function dayZone(market: Market): string {
  return market === 'stock' ? NEW_YORK : 'UTC';
}
