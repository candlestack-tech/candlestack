import { describe, expect, it } from 'vitest';

import { validateBacktestsSearch, withSortBy } from './backtestsSearch';

type RawSearch = Parameters<typeof validateBacktestsSearch>[0];
const validate = (search: Record<string, unknown>) => validateBacktestsSearch(search as RawSearch);

describe('validateBacktestsSearch', () => {
  it('keeps every value that is valid', () => {
    expect(
      validate({
        q: ' aapl ',
        show: 'starred',
        model: 'lstm.keras',
        sort: 'sharpe',
        order: 'asc',
        page: '2',
      }),
    ).toEqual({ q: 'aapl', show: 'starred', model: 'lstm.keras', sort: 'sharpe', order: 'asc', page: 2 });
  });

  it('keeps Public only, the backtests in Experiments', () => {
    expect(validate({ show: 'public' })).toEqual({ show: 'public' });
  });

  it('drops defaults and broken values', () => {
    expect(validate({ q: '  ', show: 'all', model: '', sort: 'default', page: '1' })).toEqual({});
    expect(validate({ show: 'mine', sort: 'drawdown', order: 'up', page: '-3' })).toEqual({});
    expect(validate({ page: '1e20' })).toEqual({});
  });

  it('keeps every key, undefined when dropped, so it covers the raw value of the URL', () => {
    expect(validate({ show: 'bar', sort: 'foo', order: 'x', page: '0' })).toStrictEqual({
      q: undefined,
      show: undefined,
      model: undefined,
      sort: undefined,
      order: undefined,
      page: undefined,
    });
  });

  it('drops an order without a column and gives a column its first order', () => {
    expect(validate({ order: 'asc' })).toEqual({});
    expect(validate({ sort: 'return' })).toEqual({ sort: 'return', order: 'desc' });
    expect(validate({ sort: 'model' })).toEqual({ sort: 'model', order: 'asc' });
    expect(validate({ sort: 'name' })).toEqual({ sort: 'name', order: 'asc' });
  });

  it('caps the page at the most the API takes', () => {
    expect(validate({ page: '100000' })).toEqual({ page: 100_000 });
    expect(validate({ page: '250000' })).toEqual({ page: 100_000 });
  });

  it('cuts a search longer than the API takes', () => {
    expect(validate({ q: 'a'.repeat(150) }).q).toHaveLength(100);
  });
});

describe('withSortBy', () => {
  it('sorts a metric down, then up, then back to newest first', () => {
    const down = withSortBy({ q: 'aapl', page: 3 }, 'sharpe');
    expect(down).toEqual({ q: 'aapl', page: undefined, sort: 'sharpe', order: 'desc' });
    const up = withSortBy(down, 'sharpe');
    expect(up).toMatchObject({ sort: 'sharpe', order: 'asc' });
    expect(withSortBy(up, 'sharpe')).toMatchObject({ sort: undefined, order: undefined });
  });

  it.each(['model', 'name'] as const)('sorts the %s A to Z first', (column) => {
    const first = withSortBy({}, column);
    expect(first).toMatchObject({ sort: column, order: 'asc' });
    const second = withSortBy(first, column);
    expect(second).toMatchObject({ sort: column, order: 'desc' });
    expect(withSortBy(second, column)).toMatchObject({ sort: undefined });
  });

  it('starts another column over at its first order', () => {
    expect(withSortBy({ sort: 'sharpe', order: 'asc' }, 'trades')).toMatchObject({ sort: 'trades', order: 'desc' });
  });
});
