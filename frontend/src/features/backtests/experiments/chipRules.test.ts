import { describe, expect, it } from 'vitest';

import {
  marketOptions,
  marketValue,
  representationOptions,
  representationValue,
  sortValue,
  timeframeOptions,
} from './chipRules';

const FACETS = {
  markets: [
    { value: 'stock', count: 17 },
    { value: 'crypto', count: 8 },
  ],
  instruments: [
    { market: 'stock' as const, symbol: 'NVDA', count: 5 },
    { market: 'crypto' as const, symbol: 'BTCUSDT', count: 4 },
  ],
  representations: [
    { value: 'renko', count: 9 },
    { value: 'windows', count: 16 },
  ],
  timeframes: [
    { value: '30m', count: 10 },
    { value: '1h', count: 15 },
  ],
};

const rows = (options: readonly { label: string; count: number; selected: boolean }[]) =>
  options.map(({ label, count, selected }) => [label, count, selected]);

describe('the Market chip', () => {
  it('offers every market with its count, then the instruments, the most published first', () => {
    const { markets, instruments } = marketOptions({}, FACETS);
    expect(rows(markets)).toEqual([
      ['All markets', 25, true],
      ['Stocks', 17, false],
      ['Crypto', 8, false],
    ]);
    expect(rows(instruments)).toEqual([
      ['NVDA', 5, false],
      ['BTCUSDT', 4, false],
    ]);
    expect(instruments[1]?.icon).toBe('crypto:BTCUSDT');
  });

  it('picks a market or an instrument, never both, from page 1', () => {
    const { markets, instruments } = marketOptions({ symbol: 'NVDA', page: 2 }, FACETS);
    expect(instruments[0]?.selected).toBe(true);
    expect(markets[2]?.apply({ symbol: 'NVDA', page: 2 })).toEqual({
      market: 'crypto',
      symbol: undefined,
      page: undefined,
    });
    expect(instruments[1]?.apply({ market: 'stock' })).toEqual({
      market: undefined,
      symbol: 'BTCUSDT',
      page: undefined,
    });
    expect(markets[0]?.apply({ market: 'stock', q: 'x' })).toEqual({
      market: undefined,
      symbol: undefined,
      q: 'x',
      page: undefined,
    });
  });

  it('says what it is set to', () => {
    expect(marketValue({})).toBeUndefined();
    expect(marketValue({ market: 'crypto' })).toBe('Crypto');
    expect(marketValue({ symbol: 'AAPL' })).toBe('AAPL');
  });
});

describe('the Representation chip', () => {
  it('offers Any and each representation, one nobody published with 0', () => {
    expect(rows(representationOptions({ representation: 'kagi' }, FACETS))).toEqual([
      ['Any', 25, false],
      ['Renko', 9, false],
      ['Kagi', 0, true],
      ['Candles', 16, false],
    ]);
    expect(representationValue({ representation: 'windows' })).toBe('Candles');
    expect(representationValue({})).toBeUndefined();
  });
});

describe('the Timeframe chip', () => {
  it('offers Any and the published timeframes, and keeps the one chosen', () => {
    expect(rows(timeframeOptions({}, FACETS))).toEqual([
      ['Any', 25, true],
      ['30m', 10, false],
      ['1h', 15, false],
    ]);
    expect(rows(timeframeOptions({ timeframe: '4h' }, FACETS)).at(-1)).toEqual(['4h', 0, true]);
    expect(timeframeOptions({}, FACETS)[2]?.apply({ page: 3 })).toEqual({ timeframe: '1h', page: undefined });
  });
});

describe('the Sort chip', () => {
  it('says Newest, the column, and an arrow up for the other way', () => {
    expect(sortValue({})).toBe('Newest');
    expect(sortValue({ sort: 'sharpe', order: 'desc' })).toBe('Sharpe');
    expect(sortValue({ sort: 'return', order: 'asc' })).toBe('Return ↑');
  });
});
