// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { InstrumentDetail } from '@/api';
import { ARSEN, problem, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp } from '@/test/app';
import { candles, times } from '@/test/candles';
import { charts } from '@/test/lightweightCharts';

vi.mock('lightweight-charts', () => import('@/test/lightweightCharts'));

const DAY = 86_400;
const BTC: InstrumentDetail = {
  id: 'crypto:BTCUSDT',
  symbol: 'BTCUSDT',
  name: 'Bitcoin / TetherUS',
  market: 'crypto',
  exchange: 'BINANCE',
  source: 'binance',
  feed: 'spot',
  base: 'BTC',
  quote: 'USDT',
  available_from: 1_502_942_400, // 2017-08-17 04:00 UTC
  available_to: 1_790_812_800,
  max_candles: 1000,
  timeframes: ['1h', '1d'],
};
const DETAIL = 'GET /api/v1/data/instruments/crypto%3ABTCUSDT';
const DAILY = `${DETAIL}/candles?timeframe=1d&limit=500`;
const HOURLY = `${DETAIL}/candles?timeframe=1h&limit=500`;
// Three daily candles of October 2026.
const OCTOBER = times(1_790_812_800 - 3 * DAY, 3, DAY);

beforeEach(() => charts.reset());

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const heading = () => screen.getByRole('heading', { level: 1 }).textContent;

describe('InstrumentPage', () => {
  it('shows the instrument and draws its candles of the timeframe asked for', async () => {
    stubApi({
      ...sessionWithPreferences(ARSEN),
      [DETAIL]: { status: 200, body: BTC },
      [DAILY]: { status: 200, body: candles(OCTOBER) },
    });
    await renderApp('/instrument/crypto:BTCUSDT?tf=1d');
    await vi.waitFor(() => expect(heading()).toBe('Bitcoin / TetherUS'));
    expect(screen.getByText('17 Aug 2017')).toBeDefined();
    const timeframes = within(screen.getByRole('navigation', { name: 'Timeframe' }));
    expect(timeframes.getByRole('link', { name: '1d' }).getAttribute('aria-current')).toBe('page');
    await vi.waitFor(() => expect(charts.of('Candlestick')[0]?.data.map((bar) => bar.time)).toEqual(OCTOBER));
    expect(document.title).toBe('BTCUSDT 1d · CandleStack');
  });

  it('opens on the first timeframe the instrument has when the one asked for is not among them', async () => {
    const fetch = stubApi({
      ...sessionWithPreferences(ARSEN),
      [DETAIL]: { status: 200, body: BTC },
      [HOURLY]: { status: 200, body: candles(times(1_790_812_800 - 3600, 1, 3600)) },
    });
    await renderApp('/instrument/crypto:BTCUSDT?tf=5m');
    await vi.waitFor(() => expect(document.title).toBe('BTCUSDT 1h · CandleStack'));
    const timeframes = within(screen.getByRole('navigation', { name: 'Timeframe' }));
    expect(timeframes.getByRole('link', { name: '1h' }).getAttribute('aria-current')).toBe('page');
    await vi.waitFor(() => expect(fetch.mock.calls.map(([path]) => path)).toContain(HOURLY.slice(4)));
  });

  it('shows Loading while the instrument loads, then the page', async () => {
    let answer: (value: { status: number; body: unknown }) => void = () => {};
    stubApi({
      ...sessionWithPreferences(ARSEN),
      [DETAIL]: () => new Promise((resolve) => (answer = resolve)),
      [DAILY]: { status: 200, body: candles(OCTOBER) },
    });
    const { router } = await renderApp('/settings');
    void router.navigate({ to: '/instrument/$id', params: { id: 'crypto:BTCUSDT' }, search: { tf: '1d' } });
    await vi.waitFor(() => expect(screen.getByRole('status').textContent).toBe('Loading…'), { timeout: 3000 });
    answer({ status: 200, body: BTC });
    await vi.waitFor(() => expect(heading()).toBe('Bitcoin / TetherUS'));
    expect(screen.queryByText('Loading…')).toBeNull();
  });

  it('says when the instrument does not exist, with the way back to the list', async () => {
    stubApi({
      ...sessionWithPreferences(ARSEN),
      'GET /api/v1/data/instruments/crypto%3ANOPE': problem(404, 'instrument-not-found', 'No instrument crypto:NOPE.'),
    });
    await renderApp('/instrument/crypto:NOPE');
    await vi.waitFor(() => expect(heading()).toBe('Not found'));
    expect(screen.getByText('No instrument crypto:NOPE.')).toBeDefined();
    expect(screen.getByRole('link', { name: 'Back to instruments' }).getAttribute('href')).toBe('/instruments');
  });

  it('says why the candles cannot load, under the instrument it did load', async () => {
    stubApi({
      ...sessionWithPreferences(ARSEN),
      [DETAIL]: { status: 200, body: BTC },
      [DAILY]: problem(503, 'source-unavailable', 'Binance is not answering.'),
    });
    await renderApp('/instrument/crypto:BTCUSDT?tf=1d');
    await vi.waitFor(() => expect(heading()).toBe('Bitcoin / TetherUS'));
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Try again' })).toBeDefined());
    expect(charts.of('Candlestick')[0]?.data ?? []).toEqual([]);
  });
});
