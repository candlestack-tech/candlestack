import { QueryClient } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { backtest, sentBody, stubApi } from '@/test/api';
import { experiment, experimentsAnswer } from '@/test/experiments';

import { updateBacktest } from './backtests';
import { experimentCardsQueryOptions, experimentsQueryOptions } from './experiments';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('experimentsQueryOptions', () => {
  it('asks for the first page of everyone’s without a query, under the account', async () => {
    const fetch = stubApi(experimentsAnswer({ experiments: [experiment()] }));
    const options = experimentsQueryOptions({});
    expect(options.queryKey).toEqual(['account', 'experiments', 'page', {}]);
    const data = await new QueryClient().fetchQuery(options);
    expect(data.experiments).toEqual([experiment()]);
    expect(fetch.mock.calls[0]?.[0]).toBe('/api/v1/experiments');
  });

  it('sends the search, the scope, the filters, the order and the page that are set', async () => {
    const query =
      '?q=nvda+kagi&mine=true&author=usr_t9m8r7k6j5&symbol=NVDA&representation=kagi&timeframe=1h&sort=max_drawdown&order=asc&page=2';
    const fetch = stubApi(experimentsAnswer({}, query));
    await new QueryClient().fetchQuery(
      experimentsQueryOptions({
        q: 'nvda kagi',
        mine: true,
        author: 'usr_t9m8r7k6j5',
        market: undefined,
        symbol: 'NVDA',
        representation: 'kagi',
        timeframe: '1h',
        sort: 'max_drawdown',
        order: 'asc',
        page: 2,
      }),
    );
    expect(fetch.mock.calls[0]?.[0]).toBe(`/api/v1/experiments${query}`);
  });
});

describe('experimentCardsQueryOptions', () => {
  it('asks for one page after another while there are more', async () => {
    const fetch = stubApi({
      ...experimentsAnswer({ experiments: [experiment()], page: 1, pages: 2, total: 2 }, '?market=crypto&page=1'),
      ...experimentsAnswer(
        { experiments: [experiment({ id: 'bt_2' })], page: 2, pages: 2, total: 2 },
        '?market=crypto&page=2',
      ),
    });
    const options = experimentCardsQueryOptions({ market: 'crypto' });
    expect(options.queryKey).toEqual(['account', 'experiments', 'cards', { market: 'crypto' }]);
    const client = new QueryClient();
    await client.fetchInfiniteQuery(options);
    const data = await client.fetchInfiniteQuery({ ...options, pages: 2 });
    expect(data.pages.map((page) => page.experiments[0]?.id)).toEqual(['bt_t9n8v7d6a5', 'bt_2']);
    expect(options.getNextPageParam(data.pages[1]!, data.pages, 2, [1, 2])).toBeUndefined();
    expect(fetch.mock.calls.map(([path]) => path)).toContain('/api/v1/experiments?market=crypto&page=2');
  });
});

describe('publishing', () => {
  it('patches public, and answers with the backtest and when it was published', async () => {
    const fetch = stubApi({
      'PATCH /api/v1/backtests/bt_7f3k2m9qx4': {
        status: 200,
        body: backtest({ public: true, published_at: '2026-10-17T20:41:00Z' }),
      },
    });
    await expect(updateBacktest('bt_7f3k2m9qx4', { public: true })).resolves.toMatchObject({ public: true });
    expect(sentBody(fetch, 'PATCH /api/v1/backtests/bt_7f3k2m9qx4')).toEqual({ public: true });
  });
});
