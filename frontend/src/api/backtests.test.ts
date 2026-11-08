import { QueryClient } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { backtest, backtestDocument, backtestsAnswer, dashboardAnswer, problem, stubApi } from '@/test/api';
import { queuedDocument, runningDocument } from '@/test/runs';

import {
  backtestQueryOptions,
  backtestsQueryOptions,
  cancelBacktest,
  dashboardQueryOptions,
  getBacktestFile,
  updateBacktest,
  type BacktestDocument,
  type BacktestsPage,
  type Dashboard,
} from './backtests';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('backtestsQueryOptions', () => {
  it('asks for the plain list without a query, under the account', async () => {
    const fetch = stubApi(backtestsAnswer({ backtests: [backtest()] }));
    const options = backtestsQueryOptions({});
    expect(options.queryKey).toEqual(['account', 'backtests', {}]);
    const data = await new QueryClient().fetchQuery(options);
    expect(data.backtests).toEqual([backtest()]);
    expect(fetch.mock.calls[0]?.[0]).toBe('/api/v1/backtests');
  });

  it('sends the search, the filters, the order and the page that are set', async () => {
    const query = '?q=aapl+%2312&show=starred&model=blesk-novol-8.keras&sort=sharpe&order=asc&page=2';
    const fetch = stubApi(backtestsAnswer({}, query));
    const params = {
      q: 'aapl #12',
      show: 'starred',
      model: 'blesk-novol-8.keras',
      sort: 'sharpe',
      order: 'asc',
      page: 2,
    } as const;
    await new QueryClient().fetchQuery(backtestsQueryOptions(params));
    expect(fetch.mock.calls[0]?.[0]).toBe(`/api/v1/backtests${query}`);
  });

  it('leaves out what is not set', async () => {
    const fetch = stubApi(backtestsAnswer({}, '?show=failed'));
    await new QueryClient().fetchQuery(backtestsQueryOptions({ q: '', show: 'failed', page: undefined }));
    expect(fetch.mock.calls[0]?.[0]).toBe('/api/v1/backtests?show=failed');
  });
});

describe('the lists while a backtest waits or runs', () => {
  const queued = backtest({ status: 'queued', queue_position: 2, total_return: null });
  const running = backtest({ status: 'running', total_return: null });
  const query = (data: unknown) => ({ state: { data } }) as never;

  it('ask for the Backtests page again every 5 s, so the place in the queue keeps up', () => {
    const { refetchInterval } = backtestsQueryOptions({});
    if (typeof refetchInterval !== 'function') throw new Error('Not a function');
    const page = (backtests: BacktestsPage['backtests']) => query({ backtests });
    expect(refetchInterval(page([backtest(), queued]))).toBe(5000);
    expect(refetchInterval(page([running]))).toBe(5000);
    expect(refetchInterval(page([backtest(), backtest({ status: 'failed' })]))).toBe(false);
    expect(refetchInterval(query(undefined))).toBe(false);
  });

  it('ask for the dashboard again while one of its widgets shows such a backtest', () => {
    const { refetchInterval } = dashboardQueryOptions();
    if (typeof refetchInterval !== 'function') throw new Error('Not a function');
    const dashboard = (last: Dashboard['last'], starred: Dashboard['last']) =>
      query({ last, starred: { backtests: starred, total: starred.length } });
    expect(refetchInterval(dashboard([], [queued]))).toBe(5000);
    expect(refetchInterval(dashboard([running], []))).toBe(5000);
    expect(refetchInterval(dashboard([backtest()], [backtest()]))).toBe(false);
    expect(refetchInterval(query(undefined))).toBe(false);
  });
});

describe('dashboardQueryOptions', () => {
  it('reads the dashboard under the account', async () => {
    stubApi(dashboardAnswer({ last: [backtest()] }));
    const options = dashboardQueryOptions();
    expect(options.queryKey).toEqual(['account', 'dashboard']);
    const data = await new QueryClient().fetchQuery(options);
    expect(data.last).toEqual([backtest()]);
  });
});

describe('cancelBacktest', () => {
  it('posts to its cancel path without a body and gives the document back', async () => {
    const cancelled = backtestDocument();
    const fetch = stubApi({ 'POST /api/v1/backtests/bt_7f3k2m9qx4/cancel': { status: 200, body: cancelled } });
    await expect(cancelBacktest('bt_7f3k2m9qx4')).resolves.toEqual(cancelled);
    expect(fetch.mock.calls[0]?.[1]).toMatchObject({ method: 'POST' });
    expect(fetch.mock.calls[0]?.[1]?.body).toBeUndefined();
  });

  it('passes on a backtest that began to run as backtest-not-queued', async () => {
    stubApi({
      'POST /api/v1/backtests/bt_7f3k2m9qx4/cancel': problem(409, 'backtest-not-queued', 'It runs already.'),
    });
    await expect(cancelBacktest('bt_7f3k2m9qx4')).rejects.toMatchObject({
      status: 409,
      problem: 'backtest-not-queued',
    });
  });
});

describe('updateBacktest', () => {
  it('patches the star', async () => {
    const fetch = stubApi({
      'PATCH /api/v1/backtests/bt_7f3k2m9qx4': { status: 200, body: backtest({ starred: true }) },
    });
    await expect(updateBacktest('bt_7f3k2m9qx4', { starred: true })).resolves.toMatchObject({ starred: true });
    expect(fetch.mock.calls[0]?.[1]?.body).toBe('{"starred":true}');
  });

  it('passes a backtest that is gone on as not-found', async () => {
    stubApi({ 'PATCH /api/v1/backtests/bt_7f3k2m9qx4': problem(404, 'not-found', 'No such backtest.') });
    await expect(updateBacktest('bt_7f3k2m9qx4', { starred: true })).rejects.toMatchObject({ status: 404 });
  });
});

describe('backtestQueryOptions', () => {
  it("reads a backtest's document under the account", async () => {
    stubApi({ 'GET /api/v1/backtests/bt_7f3k2m9qx4': { status: 200, body: backtestDocument() } });
    const options = backtestQueryOptions('bt_7f3k2m9qx4');
    expect(options.queryKey).toEqual(['account', 'backtest', 'bt_7f3k2m9qx4']);
    await expect(new QueryClient().fetchQuery(options)).resolves.toEqual(backtestDocument());
  });

  it('asks again every 2 s while it waits or runs and never once it ended', () => {
    const { refetchInterval, staleTime } = backtestQueryOptions('bt_7f3k2m9qx4');
    const query = (data: BacktestDocument | undefined) => ({ state: { data } }) as never;
    if (typeof refetchInterval !== 'function' || typeof staleTime !== 'function') throw new Error('Not functions');
    expect(refetchInterval(query(queuedDocument()))).toBe(2000);
    expect(refetchInterval(query(runningDocument()))).toBe(2000);
    expect(refetchInterval(query(backtestDocument()))).toBe(false);
    expect(staleTime(query(queuedDocument()))).toBe(0);
    expect(staleTime(query(runningDocument()))).toBe(0);
    expect(staleTime(query(backtestDocument()))).toBe(Infinity);
  });

  it('downloads the whole document from its own path, as a file', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{"version":1}', { status: 200 }));
    vi.stubGlobal('fetch', fetch);
    const file = await getBacktestFile('bt_7f3k2m9qx4');
    expect(fetch.mock.calls[0]?.[0]).toBe('/api/v1/backtests/bt_7f3k2m9qx4/download');
    expect(await file.text()).toBe('{"version":1}');
  });
});
