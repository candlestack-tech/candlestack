// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ARSEN,
  backtest,
  backtestDocument,
  backtestsAnswer,
  problem,
  runningDocument,
  sessionWithPreferences,
  stubApi,
} from '@/test/api';
import { renderApp } from '@/test/app';

// The charts draw on a canvas, which jsdom has not: a stand-in keeps what they were given.
const charts = vi.hoisted(() => ({
  made: 0,
  series: [] as { options: { title?: string }; data: unknown[] }[],
  markers: [] as unknown[][],
  options: [] as { handleScroll?: boolean; handleScale?: boolean }[],
}));

vi.mock('lightweight-charts', () => ({
  AreaSeries: 'Area',
  BaselineSeries: 'Baseline',
  LineSeries: 'Line',
  ColorType: { Solid: 'solid' },
  CrosshairMode: { Magnet: 1 },
  LineStyle: { Dashed: 2 },
  createChart: (_box: HTMLElement, options: (typeof charts.options)[number]) => {
    charts.made += 1;
    charts.options.push(options);
    return {
      applyOptions: vi.fn(),
      addSeries: (_type: string, options: { title?: string }) => {
        const series = { options, data: [] as unknown[] };
        charts.series.push(series);
        return { setData: (data: unknown[]) => (series.data = data) };
      },
      timeScale: () => ({ fitContent: vi.fn() }),
      remove: vi.fn(),
    };
  },
  createSeriesMarkers: (_series: unknown, markers: unknown[]) => charts.markers.push(markers),
}));

const ID = 'bt_7f3k2m9qx4';
const PAGE = `/backtests/${ID}`;
const DOCUMENT = `GET /api/v1/backtests/${ID}`;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-17T16:00:00Z'));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  Object.assign(charts, { made: 0, series: [], markers: [], options: [] });
});

type Answers = Parameters<typeof stubApi>[0];

async function openPage(answer: Answers[string], answers: Answers = {}) {
  const all = { ...sessionWithPreferences(ARSEN), [DOCUMENT]: answer, ...answers };
  const fetch = stubApi(all);
  const app = await renderApp(PAGE);
  return { ...app, fetch, all };
}

const box = (name: string) => within(screen.getByRole('heading', { level: 2, name }).closest('section')!);

describe("a finished backtest's page", () => {
  it('names the backtest and what it ran on, with what, over which days and when, without the sidebar', async () => {
    await openPage({ status: 200, body: backtestDocument() });
    expect((await screen.findByRole('heading', { level: 1 })).textContent).toBe('Backtest #12');
    expect(document.title).toBe('Backtest #12 · CandleStack');
    expect(screen.getByText('Finished')).toBeDefined();
    expect(screen.getByRole('link', { name: 'raw-candles.keras' }).getAttribute('href')).toBe('/models/mdl_7f3k2m9qx4');
    expect(screen.getByText('3 Jun 2024 – 3 Jun 2024')).toBeDefined();
    expect(screen.getByText('Today 10:05 · 2 min 42 s')).toBeDefined();
    expect(screen.getByRole('link', { name: '← Backtests' }).getAttribute('href')).toBe('/backtests');
    // The page takes the whole width, as in the design.
    expect(screen.queryByRole('navigation', { name: 'Workspace' })).toBeNull();
  });

  it('downloads the whole document, links its format and shows Compare', async () => {
    await openPage({ status: 200, body: backtestDocument() });
    const download = await screen.findByRole('link', { name: 'Download JSON' });
    expect(download.getAttribute('href')).toBe(`/api/v1/backtests/${ID}/download`);
    expect(download.hasAttribute('download')).toBe(true);
    const docs = screen.getByRole('link', { name: 'About the JSON format' });
    expect(docs.getAttribute('href')).toBe('https://candlestack.tech/docs/backtest-json');
    expect(docs.getAttribute('target')).toBe('_blank');
    expect(screen.getByRole('button', { name: 'Compare' })).toBeDefined();
    expect(screen.queryByRole('button', { name: /run again/i })).toBeNull();
  });

  it('shows the first results in a strip and every metric beside the charts', async () => {
    await openPage({ status: 200, body: backtestDocument() });
    await screen.findByRole('heading', { level: 1 });
    const strip = screen.getAllByRole('term').slice(0, 6);
    expect(strip.map((term) => `${term.textContent} ${term.nextElementSibling?.textContent}`)).toEqual([
      'Return −5.2 %',
      'Buy and hold −2.0 %',
      'Sharpe –',
      'Max drawdown −6.0 %',
      'Trades 1',
      'Win rate 0.0 %',
    ]);
    expect(box('All metrics').getAllByRole('term')).toHaveLength(12);
    expect(box('All metrics').getByText('Time in market').nextElementSibling?.textContent).toBe('20.0 %');
  });

  it('draws read-only charts: the equity against buy and hold, the drawdown and the price with its trades', async () => {
    await openPage({ status: 200, body: backtestDocument() });
    await screen.findByRole('heading', { level: 1 });
    await vi.waitFor(() => expect(charts.made).toBe(3));
    expect(charts.options.every((options) => !options.handleScroll && !options.handleScale)).toBe(true);
    expect(charts.series.map((series) => series.options.title)).toEqual([
      'Strategy',
      'Buy and hold',
      'Drawdown',
      undefined,
    ]);
    expect(charts.series[0]?.data).toHaveLength(5);
    expect(charts.markers[0]).toEqual([
      expect.objectContaining({ time: 1717425000, shape: 'arrowUp', position: 'belowBar' }),
      expect.objectContaining({ time: 1717426800, shape: 'arrowDown', position: 'aboveBar' }),
    ]);
    expect(box('Equity').getByText('Buy and hold')).toBeDefined();
    expect(box('AAPL').getByText('Buy')).toBeDefined();
  });

  it('leaves buy and hold out without a benchmark', async () => {
    await openPage({ status: 200, body: backtestDocument({ benchmarks: [] }) });
    await screen.findByRole('heading', { level: 1 });
    expect(box('Equity').queryByText('Buy and hold')).toBeNull();
  });

  it('lists the trades with the setup and the manifest it ran with', async () => {
    await openPage({ status: 200, body: backtestDocument() });
    await screen.findByRole('heading', { level: 1 });
    const trades = box('Trades 1');
    const [head, row] = trades.getAllByRole('row');
    expect(
      within(head!)
        .getAllByRole('columnheader')
        .map((cell) => cell.textContent),
    ).toEqual(['Side', 'Opened', 'at', 'Closed', 'at', 'Return', 'Candles', 'Why']);
    expect(
      within(row!)
        .getAllByRole('cell')
        .map((cell) => cell.textContent),
    ).toEqual(['Long', '3 Jun 202410:30', '102.00', '3 Jun 202411:00', '96.90', '−5.2 %', '1', 'stop']);
    expect(trades.queryByRole('button', { name: 'Show all' })).toBeNull();
    const setup = box('Setup');
    expect(setup.getByText('Candles · 30m · 25 × 5')).toBeDefined();
    expect(setup.getByText('threshold 0.05 · long and short')).toBeDefined();
  });

  it('shows the newest six trades of a long-only run, then all of them', async () => {
    const doc = backtestDocument({ decision: { ...backtestDocument().decision, long_only: true } });
    const count = 8;
    const ts = Array.from({ length: count }, (_, index) => 1717425000 + index * 1800);
    doc.results!.trades = {
      count,
      total: 12,
      complete: false,
      columns: {
        signal_ts: ts,
        open_ts: ts,
        close_ts: ts,
        side: ts.map(() => 1),
        open_price: ts.map((_, index) => 100 + index),
        close_price: ts.map(() => 100),
        stop_price: ts.map(() => null),
        exit_reason: ts.map(() => 'signal'),
        return: ts.map(() => 0.01),
        fees: ts.map(() => 0),
        slippage_cost: ts.map(() => 0),
        candles_held: ts.map(() => 1),
      },
    };
    await openPage({ status: 200, body: doc });
    await screen.findByRole('heading', { level: 1 });
    const trades = box('Trades 12');
    expect(
      trades
        .getAllByRole('columnheader')
        .map((cell) => cell.textContent)
        .slice(0, 2),
    ).toEqual(['Bought', 'at']);
    expect(trades.getAllByRole('row')).toHaveLength(7);
    expect(trades.getAllByRole('row')[1]?.textContent).toContain('107.00');
    await userEvent.click(trades.getByRole('button', { name: 'Show all' }));
    expect(trades.getAllByRole('row')).toHaveLength(9);
    expect(trades.getByText('Newest 8 of 12. All in the JSON.')).toBeDefined();
    expect(trades.getByRole('button', { name: 'Show less' })).toBeDefined();
  });
});

describe("a backtest's page before it finished", () => {
  it('shows a running backtest with its setup, asks again until it ends, then refreshes the lists', async () => {
    const { all, queryClient } = await openPage({ status: 200, body: runningDocument() });
    expect(await screen.findByText('Running', { selector: 'b' })).toBeDefined();
    expect(screen.getByText('You can close the page. The backtest keeps going.')).toBeDefined();
    expect(box('Setup').getByText('raw-candles.keras')).toBeDefined();
    expect(screen.queryByRole('link', { name: 'Download JSON' })).toBeNull();
    expect(charts.made).toBe(0);

    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    all[DOCUMENT] = { status: 200, body: backtestDocument() };
    await queryClient.refetchQueries({ queryKey: ['account', 'backtest', ID] });
    expect(await screen.findByText('Finished')).toBeDefined();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['account', 'backtests'] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['account', 'dashboard'] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['account', 'models'] });
  });

  it('shows why a failed backtest failed, with its setup', async () => {
    const running = runningDocument();
    const failed = {
      ...running,
      backtest: { ...running.backtest, status: 'failed', error: 'No prediction in the period.' },
    } as const;
    await openPage({ status: 200, body: failed });
    expect((await screen.findByRole('alert')).textContent).toBe('No prediction in the period.');
    expect(screen.getAllByText('Failed').length).toBeGreaterThan(0);
    expect(box('Setup')).toBeDefined();
    expect(screen.queryByRole('link', { name: 'Download JSON' })).toBeNull();
  });
});

describe("a backtest's page without a document", () => {
  it('says when the user has no backtest with the ID', async () => {
    await openPage(problem(404, 'not-found', 'Not found.'));
    expect((await screen.findByRole('heading', { level: 1 })).textContent).toBe('Not found');
    expect(screen.getByText('You have no backtest with this ID.')).toBeDefined();
  });

  it('says why a test backtest of the seed command has nothing to show', async () => {
    const detail = 'This backtest is test data from before backtests ran in the app, so it has no result document.';
    await openPage(problem(409, 'no-document', detail));
    expect((await screen.findByRole('heading', { level: 1 })).textContent).toBe('Backtest');
    expect(screen.getByText(detail)).toBeDefined();
  });
});

describe('opening a backtest', () => {
  it('goes back to where it was opened from', async () => {
    stubApi({
      ...sessionWithPreferences(ARSEN),
      ...backtestsAnswer({ backtests: [backtest()] }),
      [DOCUMENT]: { status: 200, body: backtestDocument() },
    });
    const { router } = await renderApp('/backtests');
    await userEvent.click(await screen.findByRole('link', { name: 'Backtest #12' }));
    expect(await screen.findByRole('link', { name: '← Backtests' })).toBeDefined();
    await router.navigate({ to: '/backtests/$backtestId', params: { backtestId: ID }, state: { from: 'model' } });
    expect((await screen.findByRole('link', { name: '← raw-candles.keras' })).getAttribute('href')).toBe(
      '/models/mdl_7f3k2m9qx4',
    );
  });
});
