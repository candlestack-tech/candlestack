// @vitest-environment jsdom
import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Instrument } from '@/api';
import {
  ARSEN,
  DEFAULT_PREFERENCES,
  model,
  modelsAnswer,
  problem,
  rawCandlesSummary,
  sentBody,
  sessionWithPreferences,
  stubApi,
} from '@/test/api';
import { queuedDocument, runningDocument } from '@/test/runs';
import { renderApp } from '@/test/app';

const CRYPTO = model({
  id: 'mdl_c1r2y3p4t5',
  name: 'btc-1h.keras',
  state: 'ready',
  input_shape: [25, 5],
  manifest_summary: { ...rawCandlesSummary(), timeframe: '1h', session: '24/7' },
});
const STOCKS = model({
  id: 'mdl_s1t2o3c4k5',
  name: 'stocks-30m.keras',
  state: 'verified',
  input_shape: [25, 5],
  manifest_summary: rawCandlesSummary(),
});
const DRAFT = model({ id: 'mdl_d1r2a3f4t5', name: 'draft.keras' });

const instrument = (fields: Partial<Instrument>): Instrument => ({
  id: 'crypto:BTCUSDT',
  symbol: 'BTCUSDT',
  name: 'Bitcoin / TetherUS',
  market: 'crypto',
  exchange: null,
  source: 'binance',
  feed: 'spot',
  base: 'BTC',
  quote: 'USDT',
  price: null,
  change_24h: null,
  volume_24h: null,
  spark: null,
  available_from: 1_502_942_400, // 2017-08-17 04:00 UTC
  ...fields,
});
const BTC = instrument({});
const ETH = instrument({ id: 'crypto:ETHUSDT', symbol: 'ETHUSDT', name: 'Ethereum / TetherUS' });
const AAPL = instrument({
  id: 'stock:AAPL',
  symbol: 'AAPL',
  name: 'Apple Inc.',
  market: 'stock',
  source: 'alpaca',
  feed: 'sip',
  available_from: 1_451_917_800, // 2016-01-04 14:30 UTC
});

const search = (items: Instrument[]) => ({
  status: 200,
  body: { count: items.length, items, offset: 0, total: items.length, unavailable: [] },
});
const LISTS = {
  'GET /api/v1/data/instruments?limit=20&offset=0&market=crypto': search([BTC, ETH]),
  'GET /api/v1/data/instruments?limit=20&offset=0&q=eth&market=crypto': search([ETH]),
  'GET /api/v1/data/instruments?limit=20&offset=0&market=stock': search([AAPL]),
};
// A user whose default benchmark is SPY.
const SPY_DEFAULT = sessionWithPreferences(ARSEN, {
  ...DEFAULT_PREFERENCES,
  backtest_defaults: { ...DEFAULT_PREFERENCES.backtest_defaults, benchmark: 'SPY' },
});
const RUN = 'POST /api/v1/backtests';
const started = runningDocument();
const STARTED = {
  status: 201,
  body: {
    ...started,
    backtest: {
      ...started.backtest,
      id: 'bt_n3w8a9c4k2',
      number: 13,
      name: 'Silent Orion',
      generated_name: 'Silent Orion',
    },
  },
};

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-17T12:00:00Z'));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function openBuilder(url = '/backtests/new', answers: Parameters<typeof stubApi>[0] = {}) {
  const fetch = stubApi({
    ...sessionWithPreferences(ARSEN),
    ...modelsAnswer([DRAFT, CRYPTO, STOCKS]),
    ...LISTS,
    [RUN]: STARTED,
    ...answers,
  });
  const app = await renderApp(url);
  await screen.findByRole('button', { name: /^btc-1h\.keras/ });
  return { ...app, fetch, user: userEvent.setup() };
}

const step = () => screen.getByRole('heading', { level: 2 }).textContent;
const next = () => screen.getByRole('button', { name: 'Next' });
const alert = () => screen.queryByRole('alert')?.textContent;
const modelRow = (name: string) => screen.getByRole('button', { name: new RegExp(`^${name.replace('.', '\\.')}`) });

async function goTo(user: ReturnType<typeof userEvent.setup>, title: string) {
  while (step() !== title) await user.click(next());
}

describe('the backtest builder', () => {
  it('lists the models with their preprocessing, those without a manifest dimmed with Add manifest', async () => {
    await openBuilder();
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('New backtest');
    expect(step()).toBe('Model');
    const rows = within(screen.getByRole('list', { name: 'Models' })).getAllByRole('listitem');
    expect(rows.map((row) => row.textContent)).toEqual([
      'draft.kerasNeeds a manifestAdd manifest',
      'btc-1h.kerasCandles · 1h · 5 features · window 25',
      'stocks-30m.kerasCandles · 30m · 5 features · window 25',
    ]);
    expect(modelRow('draft.keras')).toHaveProperty('disabled', true);
    // The first model that can run is chosen.
    expect(modelRow('btc-1h.keras').getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: 'Add manifest to draft.keras' })).toBeDefined();
    expect(screen.getByRole('button', { name: 'Upload model' })).toBeDefined();
    expect(screen.queryByRole('button', { name: 'Back' })).toBeNull();
    expect(screen.queryByRole('navigation', { name: 'Workspace' })).toBeNull();
  });

  it('starts with the model of the link, and opens Add manifest and the upload from the step', async () => {
    const { user } = await openBuilder(`/backtests/new?model=${STOCKS.id}`);
    expect(modelRow('stocks-30m.keras').getAttribute('aria-pressed')).toBe('true');
    await user.click(screen.getByRole('button', { name: 'Add manifest to draft.keras' }));
    expect(await screen.findByRole('dialog', { name: 'Add a manifest to draft.keras' })).toBeDefined();
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('button', { name: 'Upload model' }));
    expect(await screen.findByRole('dialog', { name: 'Add a model' })).toBeDefined();
  });

  it("takes the instrument from the model's market and the timeframe from the model", async () => {
    const { user } = await openBuilder();
    await user.click(next());
    expect(step()).toBe('Data');
    expect(screen.getByRole('button', { name: 'Instrument: BTCUSDT' }).textContent).toBe('BTCUSDTBitcoin / TetherUS');
    expect(screen.getByText('1h').nextSibling?.textContent).toBe('from the model');
    expect([
      screen.getByLabelText<HTMLInputElement>('From').value,
      screen.getByLabelText<HTMLInputElement>('To').value,
    ]).toEqual(['2021-01-04', '2024-12-31']);
    await user.click(screen.getByRole('button', { name: 'Instrument: BTCUSDT' }));
    // #318: the button and every option name the instrument with its icon.
    const icon = (element: Element | null | undefined) => element?.querySelector('img')?.getAttribute('src');
    expect(icon(screen.getByRole('button', { name: 'Instrument: BTCUSDT' }))).toBe(
      '/api/v1/data/instruments/crypto%3ABTCUSDT/icon',
    );
    const options = within(await screen.findByRole('list', { name: 'Instruments' })).getAllByRole('button');
    expect(options.map(icon)).toEqual([
      '/api/v1/data/instruments/crypto%3ABTCUSDT/icon',
      '/api/v1/data/instruments/crypto%3AETHUSDT/icon',
    ]);
    await user.type(screen.getByRole('textbox', { name: 'Search instruments' }), 'eth');
    await user.click(await screen.findByRole('button', { name: /^ETHUSDT/ }));
    expect(screen.getByRole('button', { name: 'Instrument: ETHUSDT' })).toBeDefined();

    // A stock model lists stocks.
    await user.click(screen.getByRole('button', { name: 'Model' }));
    await user.click(modelRow('stocks-30m.keras'));
    await user.click(next());
    expect(await screen.findByRole('button', { name: 'Instrument: AAPL' })).toBeDefined();
  });

  it('keeps Next off while the period cannot run, and says why', async () => {
    const { user } = await openBuilder();
    await user.click(next());
    const from = screen.getByLabelText('From');
    await user.clear(from);
    await user.type(from, '2017-08-18');
    expect(alert()).toBe(
      'Start on 19 Aug 2017 or later. BTCUSDT has candles from 17 Aug 2017 and the model needs some before the start.',
    );
    expect(next()).toHaveProperty('disabled', true);
    await user.click(screen.getByRole('button', { name: 'All data' }));
    expect(screen.getByLabelText<HTMLInputElement>('From').value).toBe('2017-08-19');
    expect(screen.getByLabelText<HTMLInputElement>('To').value).toBe('2026-10-17');
    expect(alert()).toBeUndefined();
    expect(next()).toHaveProperty('disabled', false);
  });

  it('keeps Next off while a window of Advanced is wrong', async () => {
    const { user } = await openBuilder();
    await goTo(user, 'Decision');
    expect(screen.getByRole('slider', { name: 'Threshold' }).getAttribute('aria-valuetext')).toBe('0.05');
    expect(screen.getByRole('radio', { name: /^Long only/ }).getAttribute('aria-checked')).toBe('true');
    await user.click(screen.getByRole('radio', { name: 'K-means' }));
    expect(screen.queryByRole('slider', { name: 'Threshold' })).toBeNull();
    const refit = screen.getByLabelText('Refit every');
    await user.clear(refit);
    await user.type(refit, '10');
    expect(alert()).toBe('Refit every 25 rows or more.');
    expect(next()).toHaveProperty('disabled', true);
  });

  it('starts a position at the whole capital, in Advanced', async () => {
    const { user } = await openBuilder();
    await goTo(user, 'Decision');
    expect(screen.getByRole('slider', { name: 'Position size' }).getAttribute('aria-valuetext')).toBe('100 %');
  });

  it('offers buy and hold as the benchmark of a crypto backtest, the ETFs for stocks only', async () => {
    const { user } = await openBuilder();
    await goTo(user, 'Execution');
    expect(screen.getByRole('switch', { name: 'Honest fill' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByText("Next candle's open")).toBeDefined();
    await user.click(screen.getByRole('combobox', { name: 'Benchmark: Buy and hold' }));
    const options = await screen.findAllByRole('option');
    expect(options.map((option) => [option.textContent, option.getAttribute('aria-disabled')])).toEqual([
      ['B&HBuy and holdThe same instruments, bought and kept', null],
      ['SPYSPDR S&P 500 ETFFor stocks only', 'true'],
      ['QQQInvesco QQQFor stocks only', 'true'],
      ['DIASPDR Dow Jones ETFFor stocks only', 'true'],
    ]);
  });

  it('starts a stock backtest with the default ETF, sums it up and sends it', async () => {
    const { user, fetch, router } = await openBuilder(`/backtests/new?model=${STOCKS.id}`, SPY_DEFAULT);
    await goTo(user, 'Execution');
    await user.click(screen.getByRole('combobox', { name: 'Benchmark: SPDR S&P 500 ETF' }));
    const options = await screen.findAllByRole('option');
    expect(options.map((option) => option.getAttribute('aria-disabled'))).toEqual([null, null, null, null]);
    await user.keyboard('{Escape}');
    await goTo(user, 'Run');
    expect(screen.getByText('Execution', { selector: 'dt' }).nextSibling?.textContent).toBe(
      "Next candle's openfees 0.1 % · slippage 0.02 % · vs SPY",
    );
    await user.click(screen.getByRole('button', { name: 'Run backtest' }));
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/backtests/bt_n3w8a9c4k2'));
    expect(sentBody(fetch, RUN)).toMatchObject({
      data: { instrument: { id: 'stock:AAPL' } },
      benchmarks: [{ id: 'spy', kind: 'buy_and_hold', instrument: 'stock:SPY' }],
    });
  });

  it('falls back to buy and hold on a crypto model, and keeps it', async () => {
    const { user } = await openBuilder('/backtests/new', SPY_DEFAULT);
    // The first model is crypto: the default ETF cannot run.
    await goTo(user, 'Execution');
    expect(screen.getByRole('combobox', { name: 'Benchmark: Buy and hold' })).toBeDefined();

    await user.click(screen.getByRole('button', { name: 'Model' }));
    await user.click(modelRow('stocks-30m.keras'));
    await goTo(user, 'Execution');
    await user.click(screen.getByRole('combobox', { name: 'Benchmark: Buy and hold' }));
    await user.click(await screen.findByRole('option', { name: 'Invesco QQQ' }));
    expect(screen.getByRole('combobox', { name: 'Benchmark: Invesco QQQ' })).toBeDefined();

    // Back to crypto, then stocks again: buy and hold stays.
    await user.click(screen.getByRole('button', { name: 'Model' }));
    await user.click(modelRow('btc-1h.keras'));
    await goTo(user, 'Execution');
    expect(screen.getByRole('combobox', { name: 'Benchmark: Buy and hold' })).toBeDefined();
    await user.click(screen.getByRole('button', { name: 'Model' }));
    await user.click(modelRow('stocks-30m.keras'));
    await goTo(user, 'Execution');
    expect(screen.getByRole('combobox', { name: 'Benchmark: Buy and hold' })).toBeDefined();
  });

  it('sums the steps up with Edit, runs the backtest and opens its page', async () => {
    const { user, fetch, router } = await openBuilder();
    await goTo(user, 'Run');
    const terms = screen.getAllByRole('term').map((term) => `${term.textContent}: ${term.nextSibling?.textContent}`);
    expect(terms).toEqual([
      'Model: btc-1h.kerasCandles · 1h · 5 features · window 25',
      'Data: BTCUSDTBitcoin / TetherUS4 Jan 2021 – 31 Dec 2024',
      'Decision: threshold 0.05 · long only · stop 7 %',
      "Execution: Next candle's openfees 0.1 % · slippage 0.02 % · vs buy and hold",
    ]);
    expect(
      screen.getByText('Data', { selector: 'dt' }).nextElementSibling?.querySelector('img')?.getAttribute('src'),
    ).toBe('/api/v1/data/instruments/crypto%3ABTCUSDT/icon');
    await user.click(screen.getByRole('button', { name: 'Edit decision' }));
    expect(step()).toBe('Decision');
    await user.click(screen.getByRole('button', { name: 'Run' }));
    await user.click(screen.getByRole('button', { name: 'Run backtest' }));
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/backtests/bt_n3w8a9c4k2'));
    // The answer is on the backtest's page at once, running.
    // It has its generated name from the start (#376).
    expect((await screen.findByRole('heading', { level: 1, name: 'Silent Orion' })).textContent).toBe('Silent Orion');
    expect(screen.getByText('#13')).toBeDefined();
    expect(screen.getByText(/^Running · /, { selector: 'span' })).toBeDefined();
    expect(sentBody(fetch, RUN)).toEqual({
      model: { id: CRYPTO.id },
      data: {
        instrument: { id: 'crypto:BTCUSDT' },
        period: { start: 1_609_718_400, end: 1_735_689_600 },
      },
      decision: {
        calibrate: null,
        ema: null,
        rule: { name: 'threshold', limit: 0.05 },
        long_only: true,
        confirm: null,
        min_hold: null,
      },
      execution: {
        fill: 'next_open',
        stop_loss: 0.07,
        drawdown_stop: null,
        position_size: 1,
        fee_bps: 10,
        slippage_bps: 2,
      },
      benchmarks: [{ id: 'bh', kind: 'buy_and_hold', instrument: 'crypto:BTCUSDT' }],
    });
  });

  it("goes back to the step the API's problem is about and says it in plain words", async () => {
    const { user } = await openBuilder('/backtests/new', {
      [RUN]: problem(422, 'warm-up-out-of-range', 'The model needs 24 1h candles...', {
        candles: 24,
        earliest_start: 1_610_000_000,
      }),
    });
    await goTo(user, 'Run');
    await user.click(screen.getByRole('button', { name: 'Run backtest' }));
    await vi.waitFor(() => expect(step()).toBe('Data'));
    expect(alert()).toBe('Start on 7 Jan 2021 or later. The model needs 24 candles before the start.');
    // A change clears it.
    await user.click(screen.getByRole('button', { name: '2025' }));
    expect(alert()).toBeUndefined();
  });

  it('stays on Run when the queue is full', async () => {
    const { user } = await openBuilder('/backtests/new', {
      [RUN]: problem(409, 'queue-full', 'You have 20 backtests waiting already.', { limit: 20 }),
    });
    await goTo(user, 'Run');
    await user.click(screen.getByRole('button', { name: 'Run backtest' }));
    await vi.waitFor(() =>
      expect(alert()).toBe(
        'Queue full. 20 backtests are waiting already. Run this one when one of them has started, or cancel one.',
      ),
    );
    expect(step()).toBe('Run');
  });

  it('says how many are ahead of a queued backtest and stays, with a link to its page', async () => {
    const queued = queuedDocument(2);
    const { user, router, queryClient } = await openBuilder('/backtests/new', {
      [RUN]: {
        status: 201,
        body: { ...queued, backtest: { ...queued.backtest, id: 'bt_q2u3e4u5e6', name: 'Quiet Lyra' } },
      },
    });
    await goTo(user, 'Run');
    await user.click(screen.getByRole('button', { name: 'Run backtest' }));
    const note = await screen.findByRole('status');
    expect(note.textContent).toBe('Queued, 2 aheadQuiet Lyra starts on its own. You can run another one.Open it');
    expect(router.state.location.pathname).toBe('/backtests/new');
    expect(alert()).toBeUndefined();
    // Its page has the answer already.
    expect(queryClient.getQueryData(['account', 'backtest', 'bt_q2u3e4u5e6'])).toBeDefined();
    expect(within(note).getByRole('link', { name: 'Open it' }).getAttribute('href')).toBe('/backtests/bt_q2u3e4u5e6');
    // Going to another step keeps it; a change, for the next one, clears it.
    await user.click(screen.getByRole('button', { name: 'Edit data' }));
    await user.click(screen.getByRole('button', { name: 'Run' }));
    expect(screen.getByRole('status')).toBeDefined();
    await user.click(screen.getByRole('button', { name: 'Edit data' }));
    await user.click(screen.getByRole('button', { name: '2025' }));
    await goTo(user, 'Run');
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('sends one start for a quick double click, and says Queued without a place the API did not give', async () => {
    const queued = queuedDocument();
    let answer: ((value: { status: number; body: unknown }) => void) | undefined;
    const { user, fetch } = await openBuilder('/backtests/new', {
      [RUN]: () => new Promise((resolve) => (answer = resolve)),
    });
    const starts = () =>
      fetch.mock.calls.filter(([path, init]) => path === '/api/v1/backtests' && init?.method === 'POST').length;
    await goTo(user, 'Run');
    const button = screen.getByRole('button', { name: 'Run backtest' });
    fireEvent.click(button);
    fireEvent.click(button);
    await vi.waitFor(() => expect(answer).toBeDefined());
    answer?.({ status: 201, body: { ...queued, backtest: { ...queued.backtest, queue_position: null } } });
    const note = await screen.findByRole('status');
    expect(note.textContent).toBe('QueuedBright Vega starts on its own. You can run another one.Open it');
    expect(starts()).toBe(1);
  });

  it('goes back to the page it was opened from', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), ...modelsAnswer([]) });
    await renderApp('/backtests/new');
    expect((await screen.findByRole('link', { name: '← Dashboard' })).getAttribute('href')).toBe('/dashboard');
    expect(await screen.findByText('No models yet')).toBeDefined();
    expect(next()).toHaveProperty('disabled', true);
  });
});
