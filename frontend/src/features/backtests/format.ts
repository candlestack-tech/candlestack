import type { Backtest, Representation } from '@/api';
import { returnTone } from '@/lib/results';

// How the backtests' own parts are written (mockups/app: REP, instList, ichips); their results go
// through src/lib/results.ts.

const REPRESENTATIONS: Record<Representation, string> = { renko: 'Renko', kagi: 'Kagi', windows: 'Time windows' };

export function representationName(representation: Representation): string {
  return REPRESENTATIONS[representation];
}

// Whether a backtest has results to show: a finished one. A failed one shows the Failed badge and
// dashes, with no line and no Sharpe ratio.
export const hasResults = (backtest: Backtest) => backtest.status === 'finished';

// The colour of a return, the only result with one: green from zero, red below.
export const returnClass = (fraction: number) => (returnTone(fraction) === 'up' ? 'text-up' : 'text-down');

export const symbolsOf = (backtest: Backtest) => backtest.instruments.map((instrument) => instrument.symbol);

// What the comparison needs of a backtest: a backtest has one instrument now, older ones more.
export const compareTarget = (backtest: Backtest) => ({
  id: backtest.id,
  number: backtest.number,
  status: backtest.status,
  seeded: backtest.seeded,
  symbol: instrumentList(symbolsOf(backtest)),
});

// The id of an instrument of a backtest, `<market>:<symbol>`, which its icon is asked for by.
export const instrumentId = ({ market, symbol }: Backtest['instruments'][number]) => `${market}:${symbol}`;

// The instruments in a sentence: "AAPL", "MSFT and GOOGL", "AAPL, MSFT, NVDA, SPY and TSLA", and
// from six on the first three and how many more: "AAPL, MSFT, NVDA and 5 more".
export function instrumentList(symbols: readonly string[]): string {
  if (symbols.length <= 1) return symbols.join('');
  if (symbols.length <= 5) return `${symbols.slice(0, -1).join(', ')} and ${symbols.at(-1)}`;
  return `${symbols.slice(0, 3).join(', ')} and ${symbols.length - 3} more`;
}

// The instruments a row has room for: up to `max`, or `max - 1` (at least one) and how many more.
export function instrumentsShown<T>(instruments: readonly T[], max: number): { shown: T[]; more: number } {
  const count = instruments.length <= max ? instruments.length : Math.max(1, max - 1);
  return { shown: instruments.slice(0, count), more: instruments.length - count };
}
