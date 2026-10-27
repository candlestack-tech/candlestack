import { TIMEFRAMES, type Timeframe } from '@/api';
import type { Bar } from '@/features/instrument';

// The candle sizes of a backtest's price chart (#309). Like TradingView's Auto, the candles grow
// as the chart zooms out: weeks for the whole of a long run, then days, 4 hours and the run's own
// candles once zoomed in, never finer than the run. Weeks are built here from the days; the rest
// comes from the data API, the coarse size for the whole period and a fine one only around the
// part in view.

export type Level = Timeframe | '1w';
type Market = string;

export const LEVEL_SECONDS: Record<Level, number> = {
  '1m': 60,
  '5m': 300,
  '15m': 900,
  '30m': 1800,
  '1h': 3600,
  '4h': 14_400,
  '1d': 86_400,
  '1w': 604_800,
};

// The most candles of a size in view before the next coarser size takes over.
const MOST: Record<Level, number> = {
  '1m': 420,
  '5m': 420,
  '15m': 420,
  '30m': 420,
  '1h': 480,
  '4h': 480,
  '1d': 1100,
  '1w': Number.POSITIVE_INFINITY,
};

// Going to a finer size needs this much more room than staying, so the size does not flip back
// and forth at the edge.
const HYSTERESIS = 0.85;

// How the chart names a size: 1D and 1W, the others as the app writes them (30m, 4h).
export function levelLabel(level: Level): string {
  return level === '1d' ? '1D' : level === '1w' ? '1W' : level;
}

// The sizes a run's chart uses, finest first: its own candles, 4 hours, days and weeks. A run of
// candles under 30 minutes also gets hours, else 4-hour candles would come right after a few
// hundred minutes.
export function levelsOf(timeframe: string): Level[] {
  const own: Timeframe = TIMEFRAMES.includes(timeframe as Timeframe) ? (timeframe as Timeframe) : '1d';
  const seconds = LEVEL_SECONDS[own];
  const coarser: Level[] = seconds < 1800 ? ['1h', '4h', '1d', '1w'] : ['4h', '1d', '1w'];
  return [own, ...coarser.filter((level) => LEVEL_SECONDS[level] > seconds)];
}

const STOCK_DAYS = 252 / 365;
const SESSION = 23_400;

// About how many candles of a size fit in a span of seconds: every bin for crypto, which trades
// around the clock; for stocks only the sessions (252 a year of 6.5 hours, the last bin of a
// session shorter).
export function candlesIn(span: number, level: Level, market: Market): number {
  const seconds = LEVEL_SECONDS[level];
  if (market !== 'stock' || level === '1w') return span / seconds;
  const sessions = (span / 86_400) * STOCK_DAYS;
  return level === '1d' ? sessions : sessions * Math.ceil(SESSION / seconds);
}

// The size for a span in view: the finest that shows at most its MOST candles, a finer one than
// the current only with HYSTERESIS to spare.
export function pickLevel(span: number, levels: readonly Level[], current: Level, market: Market): Level {
  const at = levels.indexOf(current);
  for (const [index, level] of levels.entries()) {
    const most = index === levels.length - 1 ? Number.POSITIVE_INFINITY : MOST[level];
    const room = at >= 0 && index < at ? most * HYSTERESIS : most;
    if (candlesIn(span, level, market) <= room) return level;
  }
  return levels.at(-1) ?? current;
}

// What the data API is asked for: weeks are made of days.
export const sourceOf = (level: Level): Timeframe => (level === '1w' ? '1d' : level);

export type Span = { start: number; end: number };

// About this many candles in one request: the view of a size is at most a quarter of it.
const BLOCK_CANDLES = 2000;
const BLOCK_DAYS_CANDLES = 5000;

// A request's length in whole days, the same for every view, so each block is asked for once and
// then comes from the cache.
function blockSeconds(source: Timeframe, market: Market): number {
  const candles = source === '1d' ? BLOCK_DAYS_CANDLES : BLOCK_CANDLES;
  return Math.max(1, Math.ceil(candles / candlesIn(86_400, source, market))) * 86_400;
}

// The blocks of a size that cover a span, within the test period: whole blocks on a fixed grid,
// cut at the period's start and end.
export function blocksOf(level: Level, { start, end }: Span, period: Span, market: Market): Span[] {
  const from = Math.max(start, period.start);
  const to = Math.min(end, period.end);
  if (from >= to) return [];
  const size = blockSeconds(sourceOf(level), market);
  const blocks: Span[] = [];
  for (let k = Math.floor(from / size); k * size < to; k += 1) {
    blocks.push({ start: Math.max(k * size, period.start), end: Math.min((k + 1) * size, period.end) });
  }
  return blocks;
}

// The blocks around a view: the view and half of it on either side, so a little scrolling needs
// no request.
export function blocksAround(level: Level, view: Span, period: Span, market: Market): Span[] {
  const half = (view.end - view.start) / 2;
  return blocksOf(level, { start: view.start - half, end: view.end + half }, period, market);
}

// A week starts on Monday 00:00 UTC (the epoch was a Thursday).
const MONDAY = 4 * 86_400;
export const weekOf = (time: number) => Math.floor((time - MONDAY) / 604_800) * 604_800 + MONDAY;

// Weeks from days: the first open, the highest high, the lowest low, the last close, the volumes
// summed, at the week's Monday.
export function weekly(days: readonly Bar[]): Bar[] {
  const weeks: Bar[] = [];
  for (const day of days) {
    const time = weekOf(day.time);
    const week = weeks.at(-1);
    if (week?.time === time) {
      week.high = Math.max(week.high, day.high);
      week.low = Math.min(week.low, day.low);
      week.close = day.close;
      week.volume += day.volume;
    } else {
      weeks.push({ ...day, time });
    }
  }
  return weeks;
}

export type Shown = { level: Level; start: number; end: number };

// Where the chart goes once it stopped moving: the size for the span in view and the blocks
// around it, or null while the candles shown already cover them.
export function nextWant(
  view: Span,
  shown: Shown,
  levels: readonly Level[],
  { period, market }: { period: Span; market: Market },
): { level: Level; view: Span } | null {
  const level = pickLevel(view.end - view.start, levels, shown.level, market);
  const blocks = blocksAround(level, view, period, market);
  const first = blocks[0];
  const last = blocks.at(-1);
  if (!first || !last) return null;
  const covered = level === shown.level && first.start >= shown.start && last.end <= shown.end;
  return covered ? null : { level, view };
}
