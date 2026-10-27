import { describe, expect, it } from 'vitest';

import type { DateFormat } from '@/api';

import {
  cityOf,
  dateStyleOf,
  formatDate,
  formatMonth,
  formatRecentDay,
  formatRecentDayInline,
  formatRecentTime,
  formatShortDay,
  formatTime,
  viewerTimeZone,
  zoneOf,
} from './dates';

// 2024-10-12 16:05 UTC: 12:05 in New York, 18:05 in Bratislava.
const MOMENT = new Date(Date.UTC(2024, 9, 12, 16, 5));
// 2024-01-04 02:30 UTC: still 3 January in New York.
const LATE = new Date(Date.UTC(2024, 0, 4, 2, 30));

describe('time zones', () => {
  it('turns the setting into a zone: New York, or the browser’s own', () => {
    expect(zoneOf('ny', 'Europe/Bratislava')).toBe('America/New_York');
    expect(zoneOf('local', 'Europe/Bratislava')).toBe('Europe/Bratislava');
    expect(zoneOf('local')).toBe(viewerTimeZone());
  });

  it('names a zone by its city', () => {
    expect(cityOf('Europe/Bratislava')).toBe('Bratislava');
    expect(cityOf('America/New_York')).toBe('New York');
    expect(cityOf('America/Argentina/Buenos_Aires')).toBe('Buenos Aires');
    expect(cityOf('UTC')).toBe('UTC');
  });

  it('gives a visitor the API’s defaults: New York, 12 Oct 2024, 16:05', () => {
    expect(dateStyleOf(undefined, 'Europe/Bratislava')).toEqual({
      timeZone: 'America/New_York',
      dateFormat: 'dmy',
      timeFormat: '24',
    });
    expect(dateStyleOf({ time_zone: 'local', date_format: 'iso', time_format: '12' }, 'Europe/Bratislava')).toEqual({
      timeZone: 'Europe/Bratislava',
      dateFormat: 'iso',
      timeFormat: '12',
    });
  });
});

describe('formatDate', () => {
  it('writes the three formats of Settings', () => {
    expect(formatDate(MOMENT, { timeZone: 'UTC', dateFormat: 'dmy' })).toBe('12 Oct 2024');
    expect(formatDate(MOMENT, { timeZone: 'UTC', dateFormat: 'iso' })).toBe('2024-10-12');
    expect(formatDate(MOMENT, { timeZone: 'UTC', dateFormat: 'dot' })).toBe('12.10.2024');
  });

  it('pads the day and month only where the format does', () => {
    expect(formatDate(LATE, { timeZone: 'UTC', dateFormat: 'dmy' })).toBe('4 Jan 2024');
    expect(formatDate(LATE, { timeZone: 'UTC', dateFormat: 'iso' })).toBe('2024-01-04');
    expect(formatDate(LATE, { timeZone: 'UTC', dateFormat: 'dot' })).toBe('04.01.2024');
  });

  it('takes the day in the zone', () => {
    expect(formatDate(LATE, { timeZone: 'America/New_York', dateFormat: 'iso' })).toBe('2024-01-03');
  });
});

describe('formatShortDay', () => {
  it('writes the day and the month, without padding, in the zone', () => {
    expect(formatShortDay(MOMENT, { timeZone: 'UTC' })).toBe('12 Oct');
    expect(formatShortDay(LATE, { timeZone: 'UTC' })).toBe('4 Jan');
    expect(formatShortDay(LATE, { timeZone: 'America/New_York' })).toBe('3 Jan');
  });
});

describe('formatMonth', () => {
  it('writes the month and the year, in the zone', () => {
    expect(formatMonth(MOMENT, { timeZone: 'UTC' })).toBe('Oct 2024');
    expect(formatMonth(new Date(Date.UTC(2024, 0, 1, 2)), { timeZone: 'UTC' })).toBe('Jan 2024');
    expect(formatMonth(new Date(Date.UTC(2024, 0, 1, 2)), { timeZone: 'America/New_York' })).toBe('Dec 2023');
  });
});

describe('formatTime', () => {
  it('writes 24 or 12 hours, in the zone', () => {
    expect(formatTime(MOMENT, { timeZone: 'UTC', timeFormat: '24' })).toBe('16:05');
    expect(formatTime(MOMENT, { timeZone: 'UTC', timeFormat: '12' })).toBe('4:05 PM');
    expect(formatTime(MOMENT, { timeZone: 'America/New_York', timeFormat: '24' })).toBe('12:05');
    expect(formatTime(MOMENT, { timeZone: 'America/New_York', timeFormat: '12' })).toBe('12:05 PM');
    expect(formatTime(MOMENT, { timeZone: 'Europe/Bratislava', timeFormat: '24' })).toBe('18:05');
  });

  it('writes midnight as 00 or 12 AM, with a plain space before AM', () => {
    const midnight = new Date(Date.UTC(2024, 9, 12, 0, 7));
    expect(formatTime(midnight, { timeZone: 'UTC', timeFormat: '24' })).toBe('00:07');
    expect(formatTime(midnight, { timeZone: 'UTC', timeFormat: '12' })).toBe('12:07 AM');
  });
});

describe('formatRecentDay', () => {
  const now = new Date('2026-10-17T12:00:00Z');
  const utc = (dateFormat: DateFormat) => ({ timeZone: 'UTC', dateFormat });
  const day = (iso: string, dateFormat: DateFormat = 'dmy') => formatRecentDay(new Date(iso), utc(dateFormat), now);

  it('says Today for today, in every date format', () => {
    for (const format of ['dmy', 'iso', 'dot'] as const) expect(day('2026-10-17T00:30:00Z', format)).toBe('Today');
  });

  it('writes an earlier day of this year as day and month in the default format', () => {
    expect(day('2026-10-13T09:00:00Z')).toBe('13 Oct');
    expect(day('2025-12-31T09:00:00Z')).toBe('31 Dec 2025');
  });

  it('writes the whole date in the numeric formats', () => {
    expect(day('2026-10-13T09:00:00Z', 'iso')).toBe('2026-10-13');
    expect(day('2026-10-13T09:00:00Z', 'dot')).toBe('13.10.2026');
  });

  it('says Yesterday only when asked to', () => {
    expect(day('2026-10-16T09:00:00Z')).toBe('16 Oct');
    expect(formatRecentDay(new Date('2026-10-16T09:00:00Z'), utc('iso'), now, true)).toBe('Yesterday');
    expect(formatRecentDay(new Date('2026-10-15T23:59:00Z'), utc('dmy'), now, true)).toBe('15 Oct');
  });

  it('counts days in the zone of the setting', () => {
    // 23:30 on 16 Oct in UTC is already 17 Oct in Bratislava, and still 16 Oct in New York.
    const late = new Date('2026-10-16T23:30:00Z');
    expect(formatRecentDay(late, { timeZone: 'Europe/Bratislava', dateFormat: 'dmy' }, now)).toBe('Today');
    expect(formatRecentDay(late, { timeZone: 'America/New_York', dateFormat: 'dmy' }, now)).toBe('16 Oct');
  });

  it('counts Yesterday across the turn of a month and a year', () => {
    const style = utc('dmy');
    expect(formatRecentDay(new Date('2026-12-31T20:00:00Z'), style, new Date('2027-01-01T08:00:00Z'), true)).toBe(
      'Yesterday',
    );
  });
});

describe('formatRecentDayInline', () => {
  const now = new Date('2026-10-17T12:00:00Z');
  const style = { timeZone: 'UTC', dateFormat: 'dmy' } as const;

  it('writes today and yesterday in lowercase, for the middle of a sentence', () => {
    expect(formatRecentDayInline(new Date('2026-10-17T09:00:00Z'), style, now)).toBe('today');
    expect(formatRecentDayInline(new Date('2026-10-16T09:00:00Z'), style, now, true)).toBe('yesterday');
    expect(formatRecentDayInline(new Date('2026-10-12T09:00:00Z'), style, now)).toBe('12 Oct');
  });
});

describe('formatRecentTime', () => {
  const now = new Date('2026-10-17T20:00:00Z');
  const style = { timeZone: 'UTC', dateFormat: 'dmy', timeFormat: '24' } as const;

  it('writes the day and the time as the backtest lists do', () => {
    expect(formatRecentTime(new Date('2026-10-17T16:05:00Z'), style, now)).toBe('Today 16:05');
    expect(formatRecentTime(new Date('2026-10-16T18:10:00Z'), style, now)).toBe('Yesterday 18:10');
    expect(formatRecentTime(new Date('2026-10-12T19:44:00Z'), style, now)).toBe('12 Oct 19:44');
    expect(formatRecentTime(new Date('2026-10-12T19:44:00Z'), { ...style, timeFormat: '12' }, now)).toBe(
      '12 Oct 7:44 PM',
    );
  });
});
