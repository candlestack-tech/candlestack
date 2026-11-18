import { formatCount, formatPercent, formatSharpe, formatWinRate, returnTone } from '@/lib/results';

import type { Tone } from './dom';

// How the widget writes numbers and dates: results as the app does (src/lib/results.ts), dates of
// the market's UTC days as "28 Oct 2025".

export { formatCount, formatPercent, formatSharpe, formatWinRate };

const DAY = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });

export const formatDay = (epochSeconds: number) => DAY.format(new Date(epochSeconds * 1000));

// A test period [start, end): its last day is the one before `end`.
export const formatPeriod = (start: number, end: number) => `${formatDay(start)} – ${formatDay(end - 1)}`;

export const toneOf = (fraction: number | null | undefined): Tone =>
  fraction === null || fraction === undefined ? undefined : returnTone(fraction);

// A fall is red unless there was none.
export const fallTone = (fraction: number | null | undefined): Tone => (fraction ? 'down' : undefined);

// The symbol of `<market>:<symbol>`: stock:SPY is SPY.
export const symbolOf = (instrument: string) => instrument.slice(instrument.indexOf(':') + 1);

export function formatBytes(bytes: number): string {
  if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(2)} MB`;
  return `${Math.max(1, Math.round(bytes / 1000))} kB`;
}

export function formatSeconds(seconds: number): string {
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min ${Math.round(seconds - minutes * 60)} s`;
}
