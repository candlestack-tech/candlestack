// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Instrument } from '@/api';
import { ARSEN, problem, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp } from '@/test/app';

vi.mock('lightweight-charts', () => import('@/test/lightweightCharts'));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const BTC: Instrument = {
  id: 'crypto:BTCUSDT',
  symbol: 'BTCUSDT',
  name: 'Bitcoin / TetherUS',
  market: 'crypto',
  exchange: 'BINANCE',
  source: 'binance',
  feed: 'spot',
  base: 'BTC',
  quote: 'USDT',
  price: 83249.35,
  change_24h: 1.51,
  volume_24h: 1258811866,
  spark: [],
  available_from: 1_502_942_400,
};
const BTC_EUR: Instrument = { ...BTC, id: 'crypto:BTCEUR', symbol: 'BTCEUR', name: 'BTC/EUR', quote: 'EUR' };

// One page of the search as the API answers it.
const page = (items: Instrument[], total: number, offset = 0) => ({
  status: 200,
  body: { items, count: items.length, total, offset, unavailable: [] },
});
const SEARCH = 'GET /api/v1/data/instruments?limit=25&offset=0&q=btc';

const heading = () => screen.getByRole('heading', { level: 1 }).textContent;
const symbols = () =>
  screen
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell')[0]?.textContent);

describe('InstrumentsPage', () => {
  it('lists what the search finds, with the query in the box and the title', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), [SEARCH]: page([BTC, BTC_EUR], 2) });
    await renderApp('/instruments?q=btc');
    expect(heading()).toBe('Instruments');
    await vi.waitFor(() =>
      expect(symbols()).toEqual([expect.stringContaining('BTCUSDT'), expect.stringContaining('BTCEUR')]),
    );
    expect(screen.getByRole('searchbox').getAttribute('value')).toBe('btc');
    expect(document.title).toBe('“btc” · Instruments · CandleStack');
  });

  it('says Loading until the list arrives', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), [SEARCH]: () => new Promise(() => {}) });
    await renderApp('/instruments?q=btc');
    expect(await screen.findByText('Loading instruments…')).toBeDefined();
  });

  it('says when nothing matches the search', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), [SEARCH]: page([], 0) });
    await renderApp('/instruments?q=btc');
    expect(
      await screen.findByText('No instruments match “btc”. Try a symbol like btc or a name like apple.'),
    ).toBeDefined();
  });

  it('says why the list cannot load and offers to try again', async () => {
    const fetch = stubApi({
      ...sessionWithPreferences(ARSEN),
      [SEARCH]: problem(500, 'internal', 'Something broke.'),
    });
    await renderApp('/instruments?q=btc');
    expect(await screen.findByText('The server ran into a problem.')).toBeDefined();
    const asked = () => fetch.mock.calls.filter(([path]) => path === SEARCH.slice(4)).length;
    const before = asked();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Try again' }));
    await vi.waitFor(() => expect(asked()).toBe(before + 1));
  });

  it('opens a later page of the search from the pager', async () => {
    const many = Array.from({ length: 25 }, (_, i) => ({ ...BTC, id: `crypto:BTC${i}`, symbol: `BTC${i}` }));
    stubApi({
      ...sessionWithPreferences(ARSEN),
      [SEARCH]: page(many, 26),
      'GET /api/v1/data/instruments?limit=25&offset=25&q=btc': page([BTC_EUR], 26, 25),
    });
    const { router } = await renderApp('/instruments?q=btc');
    await screen.findByText('Page 1 of 2');
    await userEvent
      .setup()
      .click(within(screen.getByRole('navigation', { name: 'Pages' })).getByRole('link', { name: 'Next' }));
    await vi.waitFor(() => expect(router.state.location.search).toEqual({ q: 'btc', page: 2 }));
    await vi.waitFor(() => expect(symbols()).toEqual([expect.stringContaining('BTCEUR')]));
  });
});
