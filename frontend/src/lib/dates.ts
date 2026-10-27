import type { Appearance, DateFormat, Market, TimeFormat, TimeZone } from '@/api';

// How the user wants dates and times written (Settings, Appearance), as the API stores it and
// resolved to a zone. Every date the app shows goes through formatDate and formatTime, so a
// change in Settings reaches all of them.

export const NEW_YORK = 'America/New_York';

// The viewer's own zone from the browser, e.g. 'Europe/Bratislava'.
export function viewerTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
}

// An instrument's zone for a visitor, who has no time zone setting (signed in, the setting
// decides): the chart's and the list's. Stocks trade on New York time (the session opens at 09:30
// there all year). Crypto trades around the clock with no home exchange, so it shows the viewer's
// own time. The API sends UTC epoch seconds; without a zone the chart would show UTC.
export function marketTimeZone(market: Market, viewer = viewerTimeZone()): string {
  return market === 'stock' ? NEW_YORK : viewer;
}

// The zone of the time zone setting: New York (exchange time) or the browser's own.
export function zoneOf(setting: TimeZone, viewer = viewerTimeZone()): string {
  return setting === 'ny' ? NEW_YORK : viewer;
}

// A zone as people name it: its city, 'Europe/Bratislava' → 'Bratislava', 'America/New_York' →
// 'New York'.
export function cityOf(zone: string): string {
  return (zone.split('/').at(-1) ?? zone).replaceAll('_', ' ');
}

export type DateStyle = { timeZone: string; dateFormat: DateFormat; timeFormat: TimeFormat };

// The API's defaults, for a visitor who is not signed in.
const DEFAULT_APPEARANCE = { time_zone: 'ny', date_format: 'dmy', time_format: '24' } as const;

export function dateStyleOf(
  appearance: Pick<Appearance, 'time_zone' | 'date_format' | 'time_format'> = DEFAULT_APPEARANCE,
  viewer = viewerTimeZone(),
): DateStyle {
  return {
    timeZone: zoneOf(appearance.time_zone, viewer),
    dateFormat: appearance.date_format,
    timeFormat: appearance.time_format,
  };
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// Intl.DateTimeFormat is slow to make, so each one is made once and kept by what it formats.
const formats = new Map<string, Intl.DateTimeFormat>();

function partsOf(date: Date, key: string, options: Intl.DateTimeFormatOptions): Record<string, string> {
  let format = formats.get(key);
  if (format === undefined) {
    format = new Intl.DateTimeFormat('en-US', options);
    formats.set(key, format);
  }
  return Object.fromEntries(format.formatToParts(date).map((part) => [part.type, part.value]));
}

// The calendar day of `date` in `timeZone`, as written in a date: '2024', '06', '03'.
export function calendarDay(date: Date, timeZone: string): { year: string; month: string; day: string } {
  const {
    year = '',
    month = '',
    day = '',
  } = partsOf(date, `date ${timeZone}`, {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
  return { year, month, day };
}

// The short name of a month written as in a date: '06' → 'Jun'.
export function monthName(month: string): string {
  return MONTHS[Number(month) - 1] ?? '';
}

// 12 Oct 2024 (dmy), 2024-10-12 (iso) or 12.10.2024 (dot), the day in `timeZone`. Built from the
// parts, not a locale's pattern, so it reads the same in every browser.
export function formatDate(date: Date, { timeZone, dateFormat }: Pick<DateStyle, 'timeZone' | 'dateFormat'>): string {
  const { year, month, day } = calendarDay(date, timeZone);
  if (dateFormat === 'iso') return `${year}-${month}-${day}`;
  if (dateFormat === 'dot') return `${day}.${month}.${year}`;
  return `${Number(day)} ${monthName(month)} ${year}`;
}

// A day without its year, short enough for the chart's axis between its ticks: 3 Jun, the day in
// `timeZone`, the same in every date format.
export function formatShortDay(date: Date, { timeZone }: Pick<DateStyle, 'timeZone'>): string {
  const { month, day } = calendarDay(date, timeZone);
  return `${Number(day)} ${monthName(month)}`;
}

// A month and its year, short for a column: Aug 2017, the month in `timeZone`, the same in every
// date format.
export function formatMonth(date: Date, { timeZone }: Pick<DateStyle, 'timeZone'>): string {
  const { year, month } = calendarDay(date, timeZone);
  return `${monthName(month)} ${year}`;
}

// Days since 1970 of a calendar day as calendarDay writes it, to count the days between two.
const dayNumber = ({ year, month, day }: ReturnType<typeof calendarDay>) =>
  Date.UTC(Number(year), Number(month) - 1, Number(day)) / 86_400_000;

// A recent day as the app's lists write it (mockups/app), in the user's zone: "Today", with
// `yesterday` also "Yesterday", or the day in the user's date format (13 Oct 2025, 2025-10-13,
// 13.10.2025). The default format keeps the year out while it is this year's (13 Oct); the
// numeric formats always write the whole date.
export function formatRecentDay(
  date: Date,
  style: Pick<DateStyle, 'timeZone' | 'dateFormat'>,
  now = new Date(),
  yesterday = false,
): string {
  const day = calendarDay(date, style.timeZone);
  const today = calendarDay(now, style.timeZone);
  const ago = dayNumber(today) - dayNumber(day);
  if (ago === 0) return 'Today';
  if (yesterday && ago === 1) return 'Yesterday';
  if (style.dateFormat === 'dmy' && day.year === today.year) return formatShortDay(date, style);
  return formatDate(date, style);
}

// A recent day in the middle of a sentence, in lowercase where it is a word: "added today",
// "added yesterday", "added 13 Oct". Where it starts a cell, formatRecentDay keeps the capital.
export function formatRecentDayInline(...args: Parameters<typeof formatRecentDay>): string {
  const day = formatRecentDay(...args);
  return day === 'Today' || day === 'Yesterday' ? day.toLowerCase() : day;
}

// When something ran, day and time: "Today 16:05", "Yesterday 18:10", "12 Oct 19:44".
export function formatRecentTime(date: Date, style: DateStyle, now = new Date()): string {
  return `${formatRecentDay(date, style, now, true)} ${formatTime(date, style)}`;
}

// 16:05 (24) or 4:05 PM (12), in `timeZone`. A plain space before AM and PM: newer browsers put a
// narrow one there, which some fonts draw too thin.
export function formatTime(date: Date, { timeZone, timeFormat }: Pick<DateStyle, 'timeZone' | 'timeFormat'>): string {
  const twelve = timeFormat === '12';
  const {
    hour = '',
    minute = '',
    dayPeriod = '',
  } = partsOf(date, `time ${timeFormat} ${timeZone}`, {
    timeZone,
    hour: twelve ? 'numeric' : '2-digit',
    minute: '2-digit',
    hourCycle: twelve ? 'h12' : 'h23',
  });
  return twelve ? `${hour}:${minute} ${dayPeriod}` : `${hour}:${minute}`;
}
