import { describe, expect, it } from 'vitest';

import { formatRecentTime } from '@/lib/dates';
import { formatPercent } from '@/lib/results';

import { WIDTH } from './widths';

// A cell's padding, 10 px at each side (CELL), and the room it leaves for the text.
const PADDING = 20;
const room = (width: string) => Number(/^w-\[(\d+)px\]$/.exec(width)?.[1]) - PADDING;

describe('the widths of the backtest columns (#315)', () => {
  // Geist Mono draws every figure, sign and space 0.6 em wide: 7.8 px at the rows' 13 px.
  it('leave room for a return of up to +9,999.9 %', () => {
    const text = formatPercent(99.999);
    expect(text).toBe('+9,999.9 %');
    expect(room(WIDTH.return)).toBeGreaterThanOrEqual(text.length * 7.8);
  });

  // The longest When of every date and time format: a day of an earlier year in the default
  // format, with a two-digit hour on the 12-hour clock. 139.8 px in Geist at 13 px, measured in
  // Chrome over every month. The When cell wraps, so it takes two lines of the column; this year's
  // times ("12 Oct 22:34", "Yesterday 15:29") take one.
  it('leave room for the longest time a backtest ran at, on two lines', () => {
    const style = { timeZone: 'UTC', dateFormat: 'dmy', timeFormat: '12' } as const;
    const when = formatRecentTime(new Date('2025-05-28T11:58:00Z'), style, new Date('2026-10-26T12:00:00Z'));
    expect(when).toBe('28 May 2025 11:58 AM');
    // "28 May 2025" and "11:58 AM" each fit a line: about 81 and 61 px.
    expect(room(WIDTH.when)).toBeGreaterThanOrEqual(81);
  });
});
