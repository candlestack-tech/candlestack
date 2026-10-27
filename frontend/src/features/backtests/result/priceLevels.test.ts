import { describe, expect, it } from 'vitest';

import {
  blocksAround,
  blocksOf,
  candlesIn,
  levelLabel,
  levelsOf,
  nextWant,
  pickLevel,
  sourceOf,
  weekOf,
  weekly,
} from './priceLevels';

const DAY = 86_400;
// Monday 6 Jan 2025, 00:00 UTC.
const MONDAY = Date.UTC(2025, 0, 6) / 1000;
const YEAR = 365 * DAY;

describe('the sizes of the candles', () => {
  it("goes from the run's own candles to 4 hours, days and weeks, with hours for runs under 30 minutes", () => {
    expect(levelsOf('30m')).toEqual(['30m', '4h', '1d', '1w']);
    expect(levelsOf('1h')).toEqual(['1h', '4h', '1d', '1w']);
    expect(levelsOf('4h')).toEqual(['4h', '1d', '1w']);
    expect(levelsOf('1d')).toEqual(['1d', '1w']);
    expect(levelsOf('5m')).toEqual(['5m', '1h', '4h', '1d', '1w']);
    expect(levelsOf('unknown')).toEqual(['1d', '1w']);
  });

  it('names days and weeks as TradingView does and asks the API for days for a week', () => {
    expect([levelLabel('1d'), levelLabel('1w'), levelLabel('30m')]).toEqual(['1D', '1W', '30m']);
    expect([sourceOf('1w'), sourceOf('4h')]).toEqual(['1d', '4h']);
  });

  it('counts the candles of crypto around the clock and of stocks in their sessions only', () => {
    expect(candlesIn(DAY, '30m', 'crypto')).toBe(48);
    expect(candlesIn(YEAR, '1d', 'stock')).toBeCloseTo(252);
    // 13 half hours a session (the last one shorter).
    expect(candlesIn(YEAR, '30m', 'stock')).toBeCloseTo(252 * 13);
    expect(candlesIn(7 * DAY, '1w', 'stock')).toBe(1);
  });

  it("picks the finest size that fits the span: weeks for years, then days, 4 hours, the run's candles", () => {
    const levels = levelsOf('30m');
    expect(pickLevel(10 * YEAR, levels, '1w', 'crypto')).toBe('1w');
    expect(pickLevel(YEAR, levels, '1w', 'crypto')).toBe('1d');
    expect(pickLevel(30 * DAY, levels, '1d', 'crypto')).toBe('4h');
    expect(pickLevel(5 * DAY, levels, '4h', 'crypto')).toBe('30m');
    // Never finer than the run.
    expect(pickLevel(3600, levels, '30m', 'crypto')).toBe('30m');
  });

  it('does not flip at the edge: a finer size needs room to spare, the current one keeps its own', () => {
    const levels = levelsOf('30m');
    // 450 four-hour candles: within 480, but not within 85 % of it.
    const span = 450 * 4 * 3600;
    expect(pickLevel(span, levels, '1d', 'crypto')).toBe('1d');
    expect(pickLevel(span, levels, '4h', 'crypto')).toBe('4h');
    expect(pickLevel(400 * 4 * 3600, levels, '1d', 'crypto')).toBe('4h');
  });
});

describe('the blocks asked for', () => {
  const period = { start: Date.UTC(2023, 0, 1) / 1000, end: Date.UTC(2024, 0, 1) / 1000 };

  it('cuts the period into blocks on a fixed grid of about 2000 candles, days in blocks of 5000', () => {
    const blocks = blocksOf('30m', period, period, 'crypto');
    // 42 days of 48 candles.
    expect(blocks[1]!.end - blocks[1]!.start).toBe(42 * DAY);
    expect(blocks[0]!.start).toBe(period.start);
    expect(blocks.at(-1)!.end).toBe(period.end);
    expect(blocks.every((block, index) => index === 0 || block.start === blocks[index - 1]!.end)).toBe(true);
    expect(blocksOf('1w', period, period, 'crypto')).toHaveLength(1);
  });

  it('asks for the same block whatever the view inside it, and nothing outside the period', () => {
    const a = blocksOf('4h', { start: period.start + DAY, end: period.start + 2 * DAY }, period, 'crypto');
    const b = blocksOf('4h', { start: period.start + 3 * DAY, end: period.start + 4 * DAY }, period, 'crypto');
    expect(a).toEqual(b);
    expect(blocksOf('4h', { start: period.end, end: period.end + DAY }, period, 'crypto')).toEqual([]);
  });

  it('covers half the view more on either side', () => {
    const view = { start: period.start + 100 * DAY, end: period.start + 104 * DAY };
    const blocks = blocksAround('30m', view, period, 'crypto');
    expect(blocks[0]!.start).toBeLessThanOrEqual(view.start - 2 * DAY);
    expect(blocks.at(-1)!.end).toBeGreaterThanOrEqual(view.end + 2 * DAY);
  });

  it('asks for other candles only when the size changes or the view leaves the blocks shown', () => {
    const levels = levelsOf('30m');
    const source = { period, market: 'crypto' };
    const whole = { level: '1d' as const, start: period.start, end: period.end };
    expect(nextWant({ start: period.start, end: period.end }, whole, levels, source)).toBeNull();
    const month = { start: period.start + 30 * DAY, end: period.start + 60 * DAY };
    expect(nextWant(month, whole, levels, source)).toEqual({ level: '4h', view: month });
    const [block] = blocksAround('4h', month, period, 'crypto');
    const shown = { level: '4h' as const, start: block!.start, end: block!.end };
    expect(nextWant(month, shown, levels, source)).toBeNull();
    const later = { start: period.end - 30 * DAY, end: period.end };
    expect(nextWant(later, shown, levels, source)).toEqual({ level: '4h', view: later });
  });
});

describe('weeks', () => {
  it('start on Monday 00:00 UTC', () => {
    expect(weekOf(MONDAY)).toBe(MONDAY);
    expect(weekOf(MONDAY + 6 * DAY + 3600)).toBe(MONDAY);
    expect(weekOf(MONDAY - 1)).toBe(MONDAY - 7 * DAY);
  });

  it('are made of days: the first open, the highest high, the lowest low, the last close, all the volume', () => {
    const day = (offset: number, open: number, high: number, low: number, close: number) => ({
      time: MONDAY + offset * DAY,
      open,
      high,
      low,
      close,
      volume: 10,
    });
    const days = [day(0, 10, 12, 9, 11), day(1, 11, 15, 10, 14), day(6, 14, 14, 8, 9), day(7, 9, 10, 9, 10)];
    expect(weekly(days)).toEqual([
      { time: MONDAY, open: 10, high: 15, low: 8, close: 9, volume: 30 },
      { time: MONDAY + 7 * DAY, open: 9, high: 10, low: 9, close: 10, volume: 10 },
    ]);
    expect(days[0]).toEqual(day(0, 10, 12, 9, 11));
  });
});
