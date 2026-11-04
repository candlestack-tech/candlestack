import { describe, expect, it } from 'vitest';

import { isFiltered, validateExperimentsSearch, withSortBy, withSortPick, withoutFilters } from './experimentsSearch';

type RawSearch = Parameters<typeof validateExperimentsSearch>[0];
const validate = (search: Record<string, unknown>) => validateExperimentsSearch(search as RawSearch);

describe('validateExperimentsSearch', () => {
  it('keeps every value that is valid', () => {
    expect(
      validate({
        q: ' nvda ',
        mine: true,
        author: 'usr_t9m8r7k6j5',
        market: 'crypto',
        representation: 'kagi',
        timeframe: '1h',
        sort: 'sharpe',
        order: 'asc',
        page: '2',
      }),
    ).toEqual({
      q: 'nvda',
      mine: true,
      author: 'usr_t9m8r7k6j5',
      market: 'crypto',
      representation: 'kagi',
      timeframe: '1h',
      sort: 'sharpe',
      order: 'asc',
      page: 2,
    });
  });

  it('drops defaults and broken values', () => {
    expect(validate({ q: '  ', mine: false, market: 'bonds', representation: 'heikin', timeframe: '2h' })).toEqual({});
    expect(validate({ sort: 'newest', order: 'up', page: '1' })).toEqual({});
    expect(validate({ sort: 'trades', page: '-3' })).toEqual({});
  });

  it('keeps every key, undefined when dropped, so it covers the raw value of the URL', () => {
    expect(Object.keys(validate({ market: 'bonds' })).sort()).toEqual(
      ['author', 'market', 'mine', 'order', 'page', 'q', 'representation', 'sort', 'symbol', 'timeframe'].sort(),
    );
  });

  it('reads Mine from the URL as the router writes it', () => {
    expect(validate({ mine: 'true' }).mine).toBe(true);
    expect(validate({ mine: 'yes' }).mine).toBeUndefined();
  });

  it('takes an instrument over a market, in capitals', () => {
    expect(validate({ market: 'stock', symbol: 'aapl' })).toMatchObject({ market: undefined, symbol: 'AAPL' });
  });

  it('sorts a column down unless the URL says up', () => {
    expect(validate({ sort: 'max_drawdown' })).toMatchObject({ sort: 'max_drawdown', order: 'desc' });
    expect(validate({ order: 'asc' })).toMatchObject({ sort: undefined, order: undefined });
  });
});

describe('withSortBy', () => {
  it('sorts a column down, then up, then back to newest first, from page 1', () => {
    const down = withSortBy({ q: 'nvda', page: 3 }, 'return');
    expect(down).toEqual({ q: 'nvda', page: undefined, sort: 'return', order: 'desc' });
    const up = withSortBy(down, 'return');
    expect(up).toMatchObject({ sort: 'return', order: 'asc' });
    expect(withSortBy(up, 'return')).toMatchObject({ sort: undefined, order: undefined });
  });

  it('goes back to newest first from Published', () => {
    expect(withSortBy({ sort: 'sharpe', order: 'asc', page: 2 }, undefined)).toEqual({
      sort: undefined,
      order: undefined,
      page: undefined,
    });
  });
});

describe('withSortPick', () => {
  it('sorts the column picked from the top, and Newest without one', () => {
    expect(withSortPick({ sort: 'return', order: 'asc', page: 2 }, 'return')).toEqual({
      sort: 'return',
      order: 'desc',
      page: undefined,
    });
    expect(withSortPick({ sort: 'return', order: 'desc' }, undefined)).toEqual({
      sort: undefined,
      order: undefined,
      page: undefined,
    });
  });
});

describe('the filters of Experiments', () => {
  it('count a search, an author and every chip, not All or Mine', () => {
    expect(isFiltered({ mine: true, sort: 'sharpe' })).toBe(false);
    expect(isFiltered({ q: 'kagi' })).toBe(true);
    expect(isFiltered({ author: 'usr_t9m8r7k6j5' })).toBe(true);
    expect(isFiltered({ symbol: 'AAPL' })).toBe(true);
    expect(isFiltered({ timeframe: '1h' })).toBe(true);
  });

  it('clear all of them, keeping Mine and the order', () => {
    expect(
      withoutFilters({ q: 'x', mine: true, market: 'stock', representation: 'renko', sort: 'sharpe', page: 2 }),
    ).toEqual({
      q: undefined,
      mine: true,
      author: undefined,
      market: undefined,
      symbol: undefined,
      representation: undefined,
      timeframe: undefined,
      sort: 'sharpe',
      page: undefined,
    });
  });
});
