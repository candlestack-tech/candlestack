import { describe, expect, it } from 'vitest';

import { marketTimeZone, viewerTimeZone, type DateStyle } from '@/lib/dates';

import { Tick, crosshairFormatter, dayFormatter, tickFormatter, timeZoneNote } from './time';

// 2024-06-03 13:30 UTC: the NYSE open in summer time; 2024-01-08 14:30 UTC: the open in winter.
const SUMMER_OPEN = Date.UTC(2024, 5, 3, 13, 30) / 1000;
const WINTER_OPEN = Date.UTC(2024, 0, 8, 14, 30) / 1000;

// The API's defaults: 3 Jun 2024, 09:30.
const DEFAULTS = { dateFormat: 'dmy', timeFormat: '24' } as const;
type Formats = Omit<DateStyle, 'timeZone'>;
const inZone = (timeZone: string, formats: Formats = DEFAULTS) => ({ ...formats, timeZone });

describe('chart time', () => {
  it('shows a visitor stocks in New York time wherever they are, so the session opens at 09:30 all year', () => {
    const ticks = tickFormatter(inZone(marketTimeZone('stock', 'Europe/Bratislava')));
    expect(ticks(SUMMER_OPEN, Tick.Time)).toBe('09:30');
    expect(ticks(WINTER_OPEN, Tick.Time)).toBe('09:30');
  });

  it('shows a visitor crypto in their own time, with summer time', () => {
    const ticks = tickFormatter(inZone(marketTimeZone('crypto', 'Europe/Bratislava')));
    expect(ticks(SUMMER_OPEN, Tick.Time)).toBe('15:30');
    expect(ticks(WINTER_OPEN, Tick.Time)).toBe('15:30');
  });

  it("takes the viewer's zone from the browser", () => {
    expect(marketTimeZone('crypto')).toBe(viewerTimeZone());
    expect(viewerTimeZone()).not.toBe('');
  });

  it('says under the chart which clock the axis uses', () => {
    expect(timeZoneNote('America/New_York')).toBe('Times are New York time.');
    expect(timeZoneNote('Europe/Bratislava')).toBe('Times are your time (Europe/Bratislava).');
  });

  it('labels years, months and days on the axis', () => {
    const ticks = tickFormatter(inZone('UTC'));
    expect(ticks(SUMMER_OPEN, Tick.Year)).toBe('2024');
    expect(ticks(SUMMER_OPEN, Tick.Month)).toBe('Jun');
    expect(ticks(SUMMER_OPEN, Tick.Day)).toBe('3 Jun');
  });

  it('gives the crosshair the full date and time', () => {
    expect(crosshairFormatter(inZone('America/New_York'))(SUMMER_OPEN)).toBe('3 Jun 2024, 09:30');
  });

  it("writes the crosshair and the axis's times in the user's formats", () => {
    const formats = { dateFormat: 'iso', timeFormat: '12' } as const;
    expect(crosshairFormatter(inZone('America/New_York', formats))(SUMMER_OPEN)).toBe('2024-06-03, 9:30 AM');
    expect(crosshairFormatter(inZone('UTC', { dateFormat: 'dot', timeFormat: '24' }))(SUMMER_OPEN)).toBe(
      '03.06.2024, 13:30',
    );
    expect(tickFormatter(inZone('America/New_York', formats))(SUMMER_OPEN, Tick.Time)).toBe('9:30 AM');
  });

  it('gives a chart of days the date alone under the crosshair', () => {
    expect(dayFormatter(inZone('America/New_York'))(SUMMER_OPEN)).toBe('3 Jun 2024');
  });
});
