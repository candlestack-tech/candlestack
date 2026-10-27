// @vitest-environment jsdom
import { act, cleanup, screen, within } from '@testing-library/react';
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
import { renderApp, viewerIn } from '@/test/app';
import { candles } from '@/test/candles';
import { charts } from '@/test/lightweightCharts';
import { step } from '@/test/steps';

vi.mock('lightweight-charts', () => import('@/test/lightweightCharts'));

const ID = 'bt_7f3k2m9qx4';
const PAGE = `/backtests/${ID}`;
const DOCUMENT = `GET /api/v1/backtests/${ID}`;
// The run's 30-minute candles of AAPL, the whole test period in one request.
const CANDLES = 'GET /api/v1/data/instruments/stock%3AAAPL/candles?timeframe=30m&start=1717372800&end=1717459200';
const TIMES = backtestDocument().results!.series.columns.ts;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-17T16:00:00Z'));
  // A viewer in Europe with the New York setting: when it ran is on their clock, the trades on
  // New York's.
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
  const app = await renderApp(PAGE);
  return { ...app, fetch, all };
}

// How many candle requests the page sent.
const fetchedCandles = (fetch: ReturnType<typeof stubApi>) =>
  fetch.mock.calls.filter(([path]) => String(path).includes('/candles')).length;

const box = (name: string) => within(screen.getByRole('heading', { level: 2, name }).closest('section')!);

describe("a finished backtest's page", () => {
  it('names the backtest and what it ran on, with what, over which days and when, without the sidebar', async () => {
    await openPage({ status: 200, body: backtestDocument() });
    expect((await screen.findByRole('heading', { level: 1 })).textContent).toBe('Backtest #12');
    expect(document.title).toBe('Backtest #12 · CandleStack');
    expect(screen.getByText('Finished')).toBeDefined();
    expect(screen.getByRole('link', { name: 'raw-candles.keras' }).getAttribute('href')).toBe('/models/mdl_7f3k2m9qx4');
    expect(screen.getByText('3 Jun 2024 – 3 Jun 2024')).toBeDefined();
    expect(screen.getByText('Today 16:05 · 2 min 42 s')).toBeDefined();
    expect(screen.getByRole('link', { name: '← Backtests' }).getAttribute('href')).toBe('/backtests');
    // The page takes the whole width, as in the design.
    expect(screen.queryByRole('navigation', { name: 'Workspace' })).toBeNull();
  });

  // #318: the instrument with its icon in the header and the Setup, the model with its mark.
  it('names its instrument with the icon in the header and the setup, and the model with its mark', async () => {
    await openPage({ status: 200, body: backtestDocument() });
    const title = await screen.findByRole('heading', { level: 1 });
    const icon = '/api/v1/data/instruments/stock%3AAAPL/icon';
    const header = title.parentElement!.parentElement!.nextElementSibling!;
    expect(header.querySelector('img')?.getAttribute('src')).toBe(icon);
    expect(header.firstElementChild?.textContent).toBe('AAPL');
    const setup = screen.getByRole('heading', { level: 2, name: 'Setup' }).closest('section')!;
    const data = within(setup).getByText('Data').parentElement!;
    expect(data.querySelector('img')?.getAttribute('src')).toBe(icon);
    expect(within(data).getByText('Apple Inc.').getAttribute('title')).toBe('Apple Inc.');
    const model = screen.getByRole('link', { name: 'raw-candles.keras' }).parentElement!;
    expect(model.querySelector('[title="Keras"]')).not.toBeNull();
    expect(within(setup).getByText('Model').parentElement!.querySelector('[title="Keras"]')).not.toBeNull();
  });

  it('saves the whole document under its own name and links its format', async () => {
    const createObjectURL = vi.fn(() => 'blob:document');
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
    const saved: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.download);
    });
    const { fetch } = await openPage(
      { status: 200, body: backtestDocument() },
      { [`GET /api/v1/backtests/${ID}/download`]: { status: 200, body: { version: 1 } } },
    );
    await userEvent.click(await screen.findByRole('button', { name: 'Download JSON' }));
    expect(fetch.mock.calls.some(([path]) => path === `/api/v1/backtests/${ID}/download`)).toBe(true);
    expect(saved).toEqual(['candlestack-backtest-12.json']);
    click.mockRestore();
    const docs = screen.getByRole('link', { name: 'About the JSON format' });
    expect(docs.getAttribute('href')).toBe('https://candlestack.tech/docs/backtest-json');
    expect(docs.getAttribute('target')).toBe('_blank');
    expect(screen.queryByRole('button', { name: /run again/i })).toBeNull();
  });

  it('says so when the file does not download', async () => {
    await openPage(
      { status: 200, body: backtestDocument() },
      { [`GET /api/v1/backtests/${ID}/download`]: { status: 503 } },
    );
    await userEvent.click(await screen.findByRole('button', { name: 'Download JSON' }));
    expect((await screen.findByText('The file did not download. Try again.')).getAttribute('role')).toBe('alert');
  });

  it('opens Compare as Coming soon', async () => {
    await openPage({ status: 200, body: backtestDocument() });
    await userEvent.click(await screen.findByRole('button', { name: 'Compare' }));
    expect((await screen.findByRole('dialog')).textContent).toContain('Compare backtests');
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
    const rows = setup
      .getAllByRole('term')
      .map((term) => `${term.textContent} ${term.nextElementSibling?.textContent}`);
    expect(rows.slice(0, 13)).toEqual([
      'Reads Candles',
      'Timeframe 30m',
      'Input 25 × 5',
      'From 3 Jun 2024',
      'To 3 Jun 2024',
      'Candles 5',
      'Threshold 0.05',
      'Positions Long and short',
      "Fills at Next candle's open",
      'Stop loss 5 %',
      'Fees 0.1 %',
      'Slippage 0 %',
      'Benchmark Buy and hold',
    ]);
    expect(setup.getAllByText(/^(Model|Data|Decision|Execution|Run)$/).map((label) => label.textContent)).toEqual([
      'Model',
      'Data',
      'Decision',
      'Execution',
      'Run',
    ]);
  });

  it('shows the newest six trades of a long-only run, then all of them', async () => {
    const doc = backtestDocument({ decision: { ...backtestDocument().decision, long_only: true } });
    const count = 8;
    const ts = Array.from({ length: count }, (_, index) => 1717425000 + index * 1800);
    // A copy: the fixture's results are shared by every test.
    doc.results = {
      ...doc.results!,
      trades: {
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

  // #316: how long the run took, in total and by step.
  it('says in the setup how long it ran, in total and each step', async () => {
    await openPage({ status: 200, body: backtestDocument() });
    await screen.findByRole('heading', { level: 1 });
    const run = box('Setup').getByText('Run').parentElement!;
    expect(within(run).getByText('2 min 42 s in total')).toBeDefined();
    expect(
      within(run)
        .getAllByRole('term')
        .map((term) => `${term.textContent} ${term.nextElementSibling?.textContent}`),
    ).toEqual(['Load candles 2 min 30 s', 'Wait in the queue 0.4 s', 'Run the model 9.8 s', 'Simulate trades 1.6 s']);
    expect(screen.queryByRole('region', { name: 'Steps' })).toBeNull();
  });
});

describe("a backtest's page before it finished", () => {
  it('shows a running backtest with its steps and setup, asks again until it ends, then refreshes the lists', async () => {
    const { all, queryClient, fetch } = await openPage({ status: 200, body: runningDocument() });
    const steps = await screen.findByRole('region', { name: 'Steps' });
    expect(
      within(steps)
        .getAllByRole('listitem')
        .map((item) => item.querySelector('div > span')?.textContent),
    ).toEqual([
      'Load candles, running',
      'Wait in the queue, waiting to start',
      'Run the model, waiting to start',
      'Simulate trades, waiting to start',
    ]);
    expect(screen.getByText('You can close the page. The backtest keeps going.')).toBeDefined();
    expect(box('Setup').getByText('raw-candles.keras')).toBeDefined();
    expect(box('Setup').queryByText('Run')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Download JSON' })).toBeNull();
    expect(charts.made).toBe(0);
    expect(fetchedCandles(fetch)).toBe(0);

    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    all[DOCUMENT] = { status: 200, body: backtestDocument() };
    await queryClient.refetchQueries({ queryKey: ['account', 'backtest', ID] });
    expect(await screen.findByText('Finished')).toBeDefined();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['account', 'backtests'] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['account', 'dashboard'] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['account', 'models'] });
  });

  it('counts how long it runs in its status each second', async () => {
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
    vi.setSystemTime(new Date('2026-10-17T16:00:00Z'));
    const running = runningDocument([
      step('candles', { state: 'running', started_at: '2026-10-17T15:58:55Z' }),
      step('queue'),
      step('model'),
      step('trades'),
    ]);
    running.backtest.created_at = '2026-10-17T15:58:55Z';
    await openPage({ status: 200, body: running });
    expect(await screen.findByText('Running · 1 min 5 s')).toBeDefined();
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByText('Running · 1 min 6 s')).toBeDefined();
  });

  it('shows the step a failed backtest failed at, its error and what did not run, with its setup', async () => {
    const running = runningDocument();
    const failed = {
      ...running,
      backtest: {
        ...running.backtest,
        status: 'failed',
        error: 'No prediction in the period.',
        steps: [
          step('candles', { state: 'done', duration_s: 1, candles: 29 }),
          step('queue', { state: 'done', duration_s: 0.2 }),
          step('model', { state: 'done', duration_s: 3 }),
          step('trades', { state: 'failed', duration_s: 0.1 }),
        ],
      },
    } as const;
    await openPage({ status: 200, body: failed });
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toBe('No prediction in the period.');
    expect(alert.closest('li')?.textContent).toContain('Simulate trades');
    expect(screen.getByText('Failed')).toBeDefined();
    expect(box('Setup')).toBeDefined();
  });

  it('shows why a failed backtest from before the steps failed, with its setup', async () => {
    const running = runningDocument([]);
    const failed = {
      ...running,
      backtest: { ...running.backtest, status: 'failed', error: 'No prediction in the period.' },
    } as const;
    await openPage({ status: 200, body: failed });
    expect((await screen.findByRole('alert')).textContent).toBe('No prediction in the period.');
    expect(screen.getAllByText('Failed').length).toBeGreaterThan(0);
    expect(box('Setup')).toBeDefined();
    expect(screen.queryByRole('button', { name: 'Download JSON' })).toBeNull();
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
