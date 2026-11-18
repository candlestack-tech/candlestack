import { describe, expect, it } from 'vitest';

import { formatRecentTime } from '@/lib/dates';
import { formatPercent } from '@/lib/results';

import { WIDTH } from './widths';

// A cell's padding, 8 px at each side (CELL), and the room it leaves for the text.
const PADDING = 16;
const width = (value: string) => Number(/^w-\[(\d+)px\]$/.exec(value)?.[1]);
const room = (value: string) => width(value) - PADDING;

describe('the widths of the backtest columns (#315)', () => {
  // Geist Mono draws every figure, sign and space 0.6 em wide: 7.8 px at the rows' 13 px.
  it('leave room for a return of up to +9,999.9 %', () => {
    const text = formatPercent(99.999);
    expect(text).toBe('+9,999.9 %');
    expect(room(WIDTH.return)).toBeGreaterThanOrEqual(text.length * 7.8);
  });

  // The 20 px icon, 6 px and "BTCUSDT" in Geist Mono.
  it('leave room for an instrument of seven letters with its icon', () => {
    expect(room(WIDTH.instruments)).toBeGreaterThanOrEqual(20 + 6 + 'BTCUSDT'.length * 7.8);
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

  // The Backtests table at the page's full width (1200 px and up: 928 px beside the sidebar) with
  // every column and the checkboxes before them: the backtest takes the rest, which holds the
  // star's 24 px (28 px less its margins), 6 px, a name of 120 px (150 px before the checkboxes,
  // backtest-names), 6 px, "#999" in Geist Mono at 11.5 px (6.9 px a figure), 6 px and the 14 px
  // globe of a public backtest.
  it('leave a name 120 px in the Backtests table at its full width (#376)', () => {
    const columns = [WIDTH.instruments, WIDTH.model, WIDTH.return, WIDTH.sharpe, WIDTH.maxDrawdown, WIDTH.when];
    const fixed = columns.reduce((sum, each) => sum + width(each), 0) + width(WIDTH.compare) + width(WIDTH.select);
    const rest = 928 - fixed - PADDING;
    expect(rest).toBeGreaterThanOrEqual(24 + 6 + 120 + 6 + '#999'.length * 6.9 + 6 + 14);
  });
});
