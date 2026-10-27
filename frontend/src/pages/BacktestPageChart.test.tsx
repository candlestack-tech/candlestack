// @vitest-environment jsdom
import { act, cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ARSEN, backtestDocument, problem, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp, viewerIn } from '@/test/app';
import { candles } from '@/test/candles';
import { charts } from '@/test/lightweightCharts';

vi.mock('lightweight-charts', () => import('@/test/lightweightCharts'));

const ID = 'bt_7f3k2m9qx4';
const DOCUMENT = `GET /api/v1/backtests/${ID}`;
// The run's 30-minute candles of AAPL, the whole test period in one request.
const CANDLES = 'GET /api/v1/data/instruments/stock%3AAAPL/candles?timeframe=30m&start=1717372800&end=1717459200';
const TIMES = backtestDocument().results!.series.columns.ts;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-17T16:00:00Z'));
  viewerIn('Europe/Bratislava');
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  charts.reset();
});

type Answers = Parameters<typeof stubApi>[0];

async function openPage(answer: Answers[string], answers: Answers = {}) {
  const all = {
    ...sessionWithPreferences(ARSEN),
    [DOCUMENT]: answer,
    [CANDLES]: { status: 200, body: candles(TIMES) },
    ...answers,
  };
  const fetch = stubApi(all);
  await renderApp(`/backtests/${ID}`);
  return { fetch, all };
}

// How many candle requests the page sent.
const fetchedCandles = (fetch: ReturnType<typeof stubApi>) =>
  fetch.mock.calls.filter(([path]) => String(path).includes('/candles')).length;

const box = (name: string) => within(screen.getByRole('heading', { level: 2, name }).closest('section')!);

// #309: one chart of the price and the trades over the strategy's return, linked to the table.
describe("a finished backtest's chart", () => {
  const [OPEN, CLOSE] = [1717425000, 1717426800];

  async function openChart(doc = backtestDocument()) {
    const page = await openPage({ status: 200, body: doc });
    await vi.waitFor(() => expect(charts.of('Candlestick')[0]?.data).toHaveLength(5));
    await vi.waitFor(() => expect(charts.of('Candlestick')[0]?.markers.length).toBeGreaterThan(0));
    return page;
  }

  const candleMarkers = () => charts.of('Candlestick')[0]!.markers;
  const [longBand, shortBand] = [() => charts.of('Area')[0]!, () => charts.of('Area')[1]!];

  it('draws the candles of the data API with the trades, the strategy and buy and hold under them, zoomable', async () => {
    const { fetch } = await openChart();
    expect(charts.made).toBe(1);
    expect(charts.options[0]).not.toHaveProperty('handleScroll');
    expect(charts.options[0]).not.toHaveProperty('handleScale');
    expect(fetchedCandles(fetch)).toBe(1);
    expect(candleMarkers()).toEqual([
      expect.objectContaining({ time: OPEN, shape: 'arrowUp', position: 'belowBar', text: 'Buy' }),
      expect.objectContaining({ time: CLOSE, shape: 'arrowDown', position: 'aboveBar', text: 'Sell' }),
    ]);
    expect(longBand().data.map((point) => (point as { value?: number }).value)).toEqual([0, 0, 1, 1, 0]);
    const [benchmark, strategy] = charts.of('Line').filter((series) => series.pane === 1);
    expect(strategy?.data).toHaveLength(5);
    expect(benchmark?.data.at(-1)).toEqual({ time: TIMES[4], value: expect.closeTo(-2, 5) });
    const card = box('Price and trades');
    expect(card.getByText('AAPL · 30m · NASDAQ')).toBeDefined();
    expect(card.getByText(/1 trade in view/)).toBeDefined();
    expect(card.getByRole('button', { name: /Buy and hold/ })).toBeDefined();
    expect(card.getByRole('button', { name: '1M' }).getAttribute('aria-pressed')).toBe('true');
  });

  it('leaves buy and hold out without a benchmark', async () => {
    await openChart(
      backtestDocument({
        benchmarks: [],
        results: { ...backtestDocument().results!, series: { ...backtestDocument().results!.series, benchmarks: {} } },
      }),
    );
    expect(box('Price and trades').queryByRole('button', { name: /Buy and hold/ })).toBeNull();
  });

  it('hides the arrows and the shading with the Trades switch', async () => {
    await openChart();
    const toggle = box('Price and trades').getByRole('switch', { name: /Trades/ });
    expect(toggle.getAttribute('aria-checked')).toBe('true');
    await userEvent.click(toggle);
    expect(toggle.getAttribute('aria-checked')).toBe('false');
    expect(candleMarkers()).toEqual([]);
    expect([longBand().options.visible, shortBand().options.visible]).toEqual([false, false]);
    await userEvent.click(toggle);
    expect(candleMarkers()).toHaveLength(2);
    expect(longBand().options.visible).toBe(true);
  });

  it('switches between candles and a line, the arrows going with it', async () => {
    await openChart();
    const card = box('Price and trades');
    const line = () => charts.of('Line')[0]!;
    await userEvent.click(card.getByRole('button', { name: 'Line' }));
    expect(card.getByRole('button', { name: 'Line' }).getAttribute('aria-pressed')).toBe('true');
    expect([charts.of('Candlestick')[0]!.options.visible, line().options.visible]).toEqual([false, true]);
    expect(line().markers).toHaveLength(2);
    expect(candleMarkers()).toEqual([]);
    await userEvent.click(card.getByRole('button', { name: 'Candles' }));
    expect(charts.of('Candlestick')[0]!.options.visible).toBe(true);
    expect(candleMarkers()).toHaveLength(2);
  });

  it('marks the row of the trade under the cursor and says what the trade was', async () => {
    await openChart();
    const row = box('Trades 1').getAllByRole('row')[1]!;
    expect(row.getAttribute('aria-current')).toBeNull();
    act(() => charts.hover(OPEN));
    await vi.waitFor(() => expect(row.getAttribute('aria-current')).toBe('true'));
    const card = box('Price and trades');
    expect(card.getByText('▲ Long')).toBeDefined();
    expect(card.getByText('1 candle · stop')).toBeDefined();
    expect(candleMarkers().at(-1)).toMatchObject({ size: 1.6, text: 'Sell −5.2 %' });
    act(() => charts.hover(null));
    await vi.waitFor(() => expect(row.getAttribute('aria-current')).toBeNull());
  });

  it('goes to a trade clicked in the table and highlights it', async () => {
    await openChart();
    const shown = charts.shown.length;
    await userEvent.click(box('Trades 1').getAllByRole('row')[1]!);
    await vi.waitFor(() => expect(charts.shown.length).toBeGreaterThan(shown));
    // The trade with 20 candles of room on either side, within the test period.
    expect(charts.shown.at(-1)).toEqual({ from: OPEN - 20 * 1800, to: backtestDocument().data.period.end });
    expect(candleMarkers()).toEqual([
      expect.objectContaining({ time: OPEN, size: 1.6, text: 'Buy' }),
      expect.objectContaining({ time: CLOSE, size: 1.6, text: 'Sell −5.2 %' }),
    ]);
    expect(box('Trades 1').getAllByRole('row')[1]!.getAttribute('aria-current')).toBe('true');
    expect(box('Price and trades').getByRole('button', { name: '1M' }).getAttribute('aria-pressed')).toBe('false');
  });

  it('says when the candles did not load and asks again', async () => {
    const { all } = await openPage(
      { status: 200, body: backtestDocument() },
      { [CANDLES]: problem(503, 'source-unavailable', 'Down.') },
    );
    await screen.findByRole('heading', { level: 2, name: 'Price and trades' });
    const again = await box('Price and trades').findByRole('button', { name: 'Try again' });
    expect(charts.of('Candlestick')[0]?.data).toEqual([]);
    all[CANDLES] = { status: 200, body: candles(TIMES) };
    await userEvent.click(again);
    await vi.waitFor(() => expect(charts.of('Candlestick')[0]?.data).toHaveLength(5));
    expect(box('Price and trades').queryByRole('button', { name: 'Try again' })).toBeNull();
  });
});
