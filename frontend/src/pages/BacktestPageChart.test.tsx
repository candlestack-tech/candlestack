// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { ADA, backtestDocument, problem, sessionWithPreferences, spyDocument, stubApi } from '@/test/api';
import { renderApp, viewerIn } from '@/test/app';
import { candles, times } from '@/test/candles';
import { charts } from '@/test/lightweightCharts';

vi.mock('lightweight-charts', () => import('@/test/lightweightCharts'));

const ID = 'bt_7f3k2m9qx4';
const DOCUMENT = `GET /api/v1/backtests/${ID}`;
// The run's 30-minute candles of AAPL, the whole test period in one request.
const CANDLES = 'GET /api/v1/data/instruments/stock%3AAAPL/candles?timeframe=30m&start=1717372800&end=1717459200';
const TIMES = backtestDocument().results!.series.columns.ts;

// The backtest page once, before the tests: the first page of a test file loads the app's code on
// demand, which takes seconds while every test file starts at once (as warmUpSettings does).
beforeAll(async () => {
  stubApi({ ...sessionWithPreferences(ADA), [DOCUMENT]: { status: 200, body: backtestDocument() } });
  await renderApp(`/backtests/${ID}`);
  cleanup();
  vi.unstubAllGlobals();
  charts.reset();
});

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
    ...sessionWithPreferences(ADA),
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

  // #465: the candles' own red reads 3.7:1 on the light page; the legend's numbers are text.
  it("writes the candle's numbers in the text colours, and keeps the legend's room over the price", async () => {
    await openChart();
    const card = box('Price and trades');
    for (const label of ['O', 'H', 'L', 'C', 'Vol']) {
      const value = card.getByText(label, { selector: 'span' }).nextElementSibling;
      expect(value?.className).toBe('text-up');
    }
    // jsdom lays nothing out: the legend takes no height, the price keeps its usual 24 %.
    expect(charts.scales.right).toEqual({ scaleMargins: { top: 0.24, bottom: 0.2 } });
  });

  it('names the line of an ETF benchmark by its symbol', async () => {
    await openChart(spyDocument());
    const [benchmark] = charts.of('Line').filter((series) => series.pane === 1);
    expect(benchmark?.data.at(-1)).toEqual({ time: TIMES[4], value: expect.closeTo(1.3, 5) });
    const card = box('Price and trades');
    expect(card.getByRole('button', { name: 'SPY' })).toBeDefined();
    expect(card.queryByRole('button', { name: /Buy and hold/ })).toBeNull();
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

  it('lets go of the picked trade, in the table too, once a range is picked', async () => {
    await openChart();
    const row = () => box('Trades 1').getAllByRole('row')[1]!;
    await userEvent.click(row());
    await vi.waitFor(() => expect(row().getAttribute('aria-current')).toBe('true'));
    await userEvent.click(box('Price and trades').getByRole('button', { name: '1M' }));
    await vi.waitFor(() => expect(row().getAttribute('aria-current')).toBeNull());
    expect(candleMarkers().every((marker) => marker.size !== 1.6)).toBe(true);
  });

  it('says when the candles did not load and asks again', async () => {
    const { all } = await openPage(
      { status: 200, body: backtestDocument() },
      { [CANDLES]: problem(503, 'source-unavailable', 'Down.') },
    );
    await screen.findByRole('heading', { level: 2, name: 'Price and trades' });
    const again = await box('Price and trades').findByRole('button', { name: 'Try again' });
    expect(charts.of('Candlestick')[0]?.data).toEqual([]);
    // The strategy and buy and hold come from the document alone.
    const [benchmark, strategy] = charts.of('Line').filter((series) => series.pane === 1);
    await vi.waitFor(() => expect(strategy?.data).toHaveLength(5));
    expect(benchmark?.data.at(-1)).toEqual({ time: TIMES[4], value: expect.closeTo(-2, 5) });
    all[CANDLES] = { status: 200, body: candles(TIMES) };
    await userEvent.click(again);
    await vi.waitFor(() => expect(charts.of('Candlestick')[0]?.data).toHaveLength(5));
    expect(box('Price and trades').queryByRole('button', { name: 'Try again' })).toBeNull();
  });
});

// #309: a run of two years opens on its days, then goes to its last month once the 4-hour candles
// are in, unless the user moved the chart first.
describe("a long backtest's chart", () => {
  const [START, END] = [1672531200, 1735689600];
  const MONTH = { from: END - 30 * 86_400, to: END };
  const SECONDS: Record<string, number> = { '1h': 3600, '4h': 14_400, '1d': 86_400 };
  const base = backtestDocument().data;
  const LONG = backtestDocument({
    data: {
      ...base,
      instrument: {
        ...base.instrument,
        id: 'crypto:BTCUSDT',
        market: 'crypto',
        symbol: 'BTCUSDT',
        exchange: 'BINANCE',
      },
      timeframe: '1h',
      timeframe_seconds: 3600,
      period: { start: START, end: END },
      rows: { first_ts: START, last_ts: END - 3600, count: (END - START) / 3600 },
    },
  });

  // The candles of any request, those of `held` sizes only once released.
  async function openLong(held: string[], { settle = true } = {}) {
    const waiting: (() => void)[] = [];
    const api = stubApi({ ...sessionWithPreferences(ADA), [DOCUMENT]: { status: 200, body: LONG } });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string, init?: RequestInit) => {
        if (!path.includes('/candles?')) return api(path, init);
        const query = new URL(path, 'http://localhost').searchParams;
        const timeframe = query.get('timeframe') ?? '1d';
        const [start, end] = [Number(query.get('start')), Number(query.get('end'))];
        if (held.includes(timeframe)) await new Promise<void>((release) => waiting.push(release));
        const step = SECONDS[timeframe] ?? 86_400;
        const first = Math.ceil(start / step) * step;
        const page = candles(times(first, Math.ceil((end - first) / step), step), { start, end });
        return new Response(JSON.stringify(page), { status: 200 });
      }),
    );
    await renderApp(`/backtests/${ID}`);
    await vi.waitFor(() => expect(charts.of('Candlestick')[0]?.data.length).toBeGreaterThan(0));
    await vi.waitFor(() => expect(waiting.length).toBeGreaterThan(0));
    // The opening view settles (250 ms) while the month waits: settling later, after the month's
    // candles came in, it would ask for the days of the whole period again before the test looks.
    await vi.waitFor(() => expect(box('Price and trades').getByText(/1 trade in view/)).toBeDefined());
    if (settle) await act(() => new Promise((done) => setTimeout(done, 400)));
    const release = async () => {
      act(() => waiting.splice(0).forEach((done) => done()));
      await vi.waitFor(() => expect(box('Price and trades').queryByText(/BTCUSDT · 4h/)).not.toBeNull());
    };
    return { release };
  }

  const pressed = (name: string) =>
    box('Price and trades').getByRole('button', { name }).getAttribute('aria-pressed') === 'true';

  it('opens on the days, then goes to the last month', async () => {
    const { release } = await openLong(['4h']);
    expect(box('Price and trades').getByText('BTCUSDT · 1D · Binance')).toBeDefined();
    expect(pressed('1M')).toBe(true);
    await release();
    expect(charts.shown.at(-1)).toEqual(MONTH);
    expect(pressed('1M')).toBe(true);
  });

  it('stays where the user zoomed before the month came in', async () => {
    const { release } = await openLong(['4h']);
    fireEvent.wheel(screen.getByRole('figure', { name: /BTCUSDT price/ }));
    act(() => charts.scroll({ from: 100, to: 400 }));
    await release();
    expect(charts.shown).not.toContainEqual(MONTH);
    await vi.waitFor(() => expect(pressed('1M')).toBe(false));
  });

  it('stays on a range shown at once over one still waiting for its candles', async () => {
    const { release } = await openLong(['4h']);
    await userEvent.click(box('Price and trades').getByRole('button', { name: 'All' }));
    const all = charts.shown.at(-1);
    expect(all).toEqual({ from: START, to: END });
    await release();
    expect(charts.shown).not.toContainEqual(MONTH);
    expect(pressed('All')).toBe(true);
  });

  // The chart tells of its new view a frame after it moved (here a slow one, 300 ms): the opening
  // view settling in that frame must not ask for the days of the whole period again.
  it('stays on the month when the opening view settles just after the month came in', async () => {
    vi.stubGlobal('requestAnimationFrame', (done: FrameRequestCallback) => setTimeout(() => done(0), 300));
    vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id));
    const { release } = await openLong(['4h'], { settle: false });
    await release();
    await act(() => new Promise((done) => setTimeout(done, 900)));
    expect(box('Price and trades').getByText('BTCUSDT · 4h · Binance')).toBeDefined();
    expect(charts.shown.at(-1)).toEqual(MONTH);
  });

  // React shows the chart's own move at once but runs the effects of it a moment later; a range
  // picked in between is kept.
  it('keeps a range picked before the page caught up with a move of the chart', async () => {
    await openLong(['4h']);
    const section = screen.getByRole('heading', { level: 2, name: 'Price and trades' }).closest('section')!;
    const card = within(section);
    const picked = new Promise<void>((done) => {
      const observer = new MutationObserver(() => {
        if (!card.queryByText(/0 trades in view/)) return;
        observer.disconnect();
        fireEvent.click(card.getByRole('button', { name: 'All' }));
        done();
      });
      observer.observe(section, { subtree: true, childList: true, characterData: true });
    });
    charts.scroll({ from: 0, to: 100 });
    await picked;
    await vi.waitFor(() => expect(charts.shown.at(-1)).toEqual({ from: START, to: END }));
    expect(pressed('All')).toBe(true);
  });
});
