import { QueryClient, skipToken } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { candles } from '@/test/candles';

import {
  BRIDGE_CANDLES,
  PAGE_SIZE,
  candleBridgeQueryOptions,
  candlePageQueryOptions,
  candleSpanQueryOptions,
  getCandles,
} from './candles';

const END = 1_780_000_000;
const URL = '/api/v1/data/instruments/crypto%3ABTCUSDT/candles';

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch() {
  const fetch = vi.fn(() => Promise.resolve(new Response(JSON.stringify(candles([END - 60])))));
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

describe('getCandles', () => {
  it('asks the candles endpoint of the instrument for the last candles before an end, or now', async () => {
    const fetch = stubFetch();
    await getCandles('crypto:BTCUSDT', '1h', { limit: 500 });
    await getCandles('crypto:BTCUSDT', '1h', { limit: 500, end: END });
    expect(fetch.mock.calls.map((call: unknown[]) => call[0])).toEqual([
      `${URL}?timeframe=1h&limit=500`,
      `${URL}?timeframe=1h&end=${END}&limit=500`,
    ]);
  });

  it('asks for every candle between a start and an end', async () => {
    const fetch = stubFetch();
    await getCandles('crypto:BTCUSDT', '4h', { start: END - 86_400, end: END });
    expect(fetch).toHaveBeenCalledWith(`${URL}?timeframe=4h&start=${END - 86_400}&end=${END}`, expect.anything());
  });
});

describe('candleSpanQueryOptions', () => {
  it('keys a span by its start and end and keeps it for good', async () => {
    const fetch = stubFetch();
    const span = candleSpanQueryOptions({ instrument: 'crypto:BTCUSDT', timeframe: '1d', start: 0, end: END });
    expect(span.queryKey).toEqual(['candles', 'crypto:BTCUSDT', '1d', 'span', 0, END]);
    expect(span.staleTime).toBe(Infinity);
    const client = new QueryClient();
    await client.fetchQuery(span);
    await client.fetchQuery(span);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith(`${URL}?timeframe=1d&start=0&end=${END}`, expect.anything());
  });
});

describe('candlePageQueryOptions', () => {
  const base = { instrument: 'crypto:BTCUSDT', timeframe: '1h', maxCandles: 50_000 } as const;

  it('asks for the latest PAGE_SIZE candles and refreshes them after a minute', async () => {
    const fetch = stubFetch();
    const latest = candlePageQueryOptions(base);
    expect(latest.queryKey).toEqual(['candles', 'crypto:BTCUSDT', '1h', 'latest']);
    expect(latest.staleTime).toBe(60_000);
    await new QueryClient().fetchQuery(latest);
    expect(fetch).toHaveBeenCalledWith(`${URL}?timeframe=1h&limit=${PAGE_SIZE}`, expect.anything());
  });

  it('keys an older page by its end only and keeps it for good, so a refresh of the latest page reuses it', async () => {
    const fetch = stubFetch();
    const older = candlePageQueryOptions({ ...base, end: END });
    expect(older.queryKey).toEqual(['candles', 'crypto:BTCUSDT', '1h', END]);
    expect(older.staleTime).toBe(Infinity);
    const client = new QueryClient();
    await client.fetchQuery(older);
    await client.fetchQuery(candlePageQueryOptions(base));
    await client.fetchQuery(candlePageQueryOptions({ ...base, end: END }));
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch).toHaveBeenNthCalledWith(1, `${URL}?timeframe=1h&end=${END}&limit=${PAGE_SIZE}`, expect.anything());
  });

  it('never asks for more candles than the server allows', async () => {
    const fetch = stubFetch();
    await new QueryClient().fetchQuery(candlePageQueryOptions({ ...base, maxCandles: 200 }));
    expect(fetch).toHaveBeenCalledWith(`${URL}?timeframe=1h&limit=200`, expect.anything());
  });
});

describe('candleBridgeQueryOptions', () => {
  const base = { instrument: 'crypto:BTCUSDT', timeframe: '1h', maxCandles: 50_000 } as const;

  it('asks once for the BRIDGE_CANDLES candles before the start of the latest page', async () => {
    const fetch = stubFetch();
    const bridge = candleBridgeQueryOptions({ ...base, end: END });
    expect(bridge.queryKey).toEqual(['candles', 'crypto:BTCUSDT', '1h', 'bridge', END]);
    expect(bridge.staleTime).toBe(Infinity);
    // A bridge left behind by a longer one goes after a minute.
    expect(bridge.gcTime).toBe(60_000);
    await new QueryClient().fetchQuery(bridge);
    expect(fetch).toHaveBeenCalledWith(`${URL}?timeframe=1h&end=${END}&limit=${BRIDGE_CANDLES}`, expect.anything());
  });

  it('never asks for more candles than the server allows', async () => {
    const fetch = stubFetch();
    await new QueryClient().fetchQuery(candleBridgeQueryOptions({ ...base, maxCandles: 600, end: END }));
    expect(fetch).toHaveBeenCalledWith(`${URL}?timeframe=1h&end=${END}&limit=600`, expect.anything());
  });

  it('asks nothing without a gap', () => {
    expect(candleBridgeQueryOptions(base).queryFn).toBe(skipToken);
  });
});
