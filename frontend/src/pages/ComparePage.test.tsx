// @vitest-environment jsdom
import { act, cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { addToCompare, clearCompare } from '@/features/backtests/compare/selection';
import { valueAt } from '@/features/backtests/compare/chartLines';
import { ARSEN, backtestDocument, backtestsAnswer, problem, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp } from '@/test/app';
import { charts } from '@/test/lightweightCharts';

vi.mock('lightweight-charts', () => import('@/test/lightweightCharts'));

type Stats = NonNullable<ReturnType<typeof backtestDocument>['results']>['stats'];

const STATS: Stats = {
  total_return: 0.5,
  max_drawdown: -0.1,
  sharpe: 1,
  sortino: 1.5,
  num_trades: 10,
  win_rate: 0.5,
  avg_trade_return: 0.01,
  best_trade_return: 0.1,
  worst_trade_return: -0.07,
  exposure: 0.4,
  fees_paid: 0.02,
  buy_and_hold_return: 0.3,
};

// A finished backtest's document with these results.
function doc(number: number, stats: Partial<Stats> = {}) {
  const base = backtestDocument();
  return backtestDocument({
    backtest: { ...base.backtest, id: `bt_${String(number)}`, number },
    results: base.results && { ...base.results, stats: { ...STATS, ...stats } },
  });
}

const BETTER = doc(12, { total_return: 0.9, max_drawdown: -0.05, sharpe: null, fees_paid: 0.01, num_trades: 99 });
const WORSE = doc(11, { total_return: 0.1, max_drawdown: -0.3, sharpe: null, fees_paid: 0.05, num_trades: 1 });
const MIDDLE = doc(10);

const answer = (body: unknown) => ({ status: 200, body });
const LISTS = {
  ...sessionWithPreferences(ARSEN),
  ...backtestsAnswer({ backtests: [], counts: { all: 0, starred: 0, failed: 0 }, models: [] }),
};

const choose = (...docs: ReturnType<typeof doc>[]) => {
  docs.forEach(({ backtest }) => {
    addToCompare({ id: backtest.id, number: backtest.number, symbol: 'AAPL' });
  });
  return Object.fromEntries(docs.map((each) => [`GET /api/v1/backtests/${each.backtest.id}`, answer(each)]));
};

const row = (label: string) =>
  within(screen.getByRole('rowheader', { name: label }).closest('tr') as HTMLElement).getAllByRole('cell');
const marks = (label: string) =>
  row(label)
    .slice(0, 3)
    .map((cell) =>
      cell.className.includes('text-up') ? 'best' : cell.className.includes('text-down') ? 'worst' : 'plain',
    );

beforeEach(() => {
  clearCompare();
  charts.reset();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the compare page', () => {
  it('says how to start when nothing is chosen', async () => {
    stubApi(LISTS);
    await renderApp('/backtests/compare');
    expect(await screen.findByRole('heading', { name: 'Nothing to compare yet' })).toBeDefined();
    expect(screen.getByText('0 of 4 backtests')).toBeDefined();
  });

  it('marks the best value of a row in green and the worst in red, the rest plain', async () => {
    stubApi({ ...LISTS, ...choose(BETTER, WORSE, MIDDLE) });
    await renderApp('/backtests/compare');
    await screen.findByRole('rowheader', { name: 'Total return' });
    expect(marks('Total return')).toEqual(['best', 'worst', 'plain']);
    // A fall is better the smaller it is, however it is signed; so are the fees.
    expect(marks('Max drawdown')).toEqual(['best', 'worst', 'plain']);
    expect(marks('Fees paid')).toEqual(['best', 'worst', 'plain']);
  });

  it('marks nothing when the values are equal or none is better, and a dash for a missing one', async () => {
    stubApi({ ...LISTS, ...choose(BETTER, WORSE, MIDDLE) });
    await renderApp('/backtests/compare');
    await screen.findByRole('rowheader', { name: 'Win rate' });
    expect(marks('Win rate')).toEqual(['plain', 'plain', 'plain']);
    expect(marks('Trades')).toEqual(['plain', 'plain', 'plain']);
    expect(row('Sharpe')[0]?.textContent).toBe('–');
    // Only one value is known: nothing to compare it with.
    expect(marks('Sharpe')).toEqual(['plain', 'plain', 'plain']);
  });

  it('asks for one more with a single backtest, and marks nothing', async () => {
    stubApi({ ...LISTS, ...choose(BETTER) });
    await renderApp('/backtests/compare');
    await screen.findByRole('rowheader', { name: 'Total return' });
    expect(screen.getByText('1 of 4 backtests. Add one more to compare')).toBeDefined();
    expect(row('Total return')[0]?.className).not.toContain('text-up');
  });

  it('removes a column, and clears all', async () => {
    stubApi({ ...LISTS, ...choose(BETTER, WORSE) });
    await renderApp('/backtests/compare');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Remove backtest 11 from compare' }));
    expect(screen.queryByRole('link', { name: '#11' })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Clear all' }));
    expect(await screen.findByRole('heading', { name: 'Nothing to compare yet' })).toBeDefined();
  });

  it('drops a backtest that no longer exists from the choice', async () => {
    stubApi({
      ...LISTS,
      ...choose(BETTER, WORSE),
      'GET /api/v1/backtests/bt_11': problem(404, 'not-found', 'No such backtest.'),
    });
    await renderApp('/backtests/compare');
    expect(await screen.findByText('1 of 4 backtests. Add one more to compare')).toBeDefined();
    expect(JSON.parse(localStorage.getItem('compare-backtests') ?? '{}')).toMatchObject({
      slots: [{ id: 'bt_12' }, null, null, null],
    });
  });

  it('opens the dialog to add from the page', async () => {
    stubApi({ ...LISTS, ...choose(BETTER) });
    await renderApp('/backtests/compare');
    await userEvent.setup().click(await screen.findByRole('button', { name: '+ Add backtest' }));
    expect(within(await screen.findByRole('dialog')).getByText('No backtests match')).toBeDefined();
  });

  it('writes a small average with two decimals and no fall without a sign', async () => {
    const small = doc(12, { avg_trade_return: 0.0041, max_drawdown: 0 });
    const close = doc(11, { avg_trade_return: 0.0044, max_drawdown: -0.1 });
    stubApi({ ...LISTS, ...choose(small, close) });
    await renderApp('/backtests/compare');
    await screen.findByRole('rowheader', { name: 'Avg. trade' });
    expect(row('Avg. trade').map((cell) => cell.textContent)).toEqual(['+0.41 %', '+0.44 %', 'No backtest']);
    expect(marks('Avg. trade').slice(0, 2)).toEqual(['worst', 'best']);
    expect(row('Max drawdown')[0]?.textContent).toBe('0.0 %');
  });

  it('keeps the charts as they are when a dialog opens, and redraws when a backtest leaves', async () => {
    stubApi({ ...LISTS, ...choose(BETTER, WORSE) });
    await renderApp('/backtests/compare');
    await vi.waitFor(() => {
      expect(charts.of('Line')).toHaveLength(4);
    });
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '+ Add backtest' }));
    await screen.findByRole('dialog');
    expect(charts.of('Line')).toHaveLength(4);
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('button', { name: 'Remove backtest 11 from compare' }));
    await vi.waitFor(() => {
      expect(charts.of('Line')).toHaveLength(6);
    });
  });

  it('opens a backtest and goes back to the comparison', async () => {
    stubApi({ ...LISTS, ...choose(BETTER, WORSE) });
    await renderApp('/backtests/compare');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('link', { name: '#12' }));
    await user.click(await screen.findByRole('button', { name: '← Compare backtests' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Compare backtests' })).toBeDefined();
  });

  it('shows no panel on its own page', async () => {
    stubApi({ ...LISTS, ...choose(BETTER, WORSE) });
    await renderApp('/backtests/compare');
    await screen.findByRole('rowheader', { name: 'Total return' });
    expect(screen.queryByRole('complementary', { name: 'Compare' })).toBeNull();
  });
});

describe('the charts of the compare page', () => {
  it('draw an equity and a drawdown chart with a line per backtest, in percent', async () => {
    stubApi({ ...LISTS, ...choose(BETTER, WORSE) });
    await renderApp('/backtests/compare');
    expect(await screen.findByRole('region', { name: 'Equity' })).toBeDefined();
    expect(screen.getByRole('region', { name: 'Drawdown' })).toBeDefined();
    await vi.waitFor(() => {
      expect(charts.of('Line')).toHaveLength(4);
    });
    const [equity, , drawdown] = charts.of('Line');
    expect(equity?.data[2]).toEqual({ time: 1717425000, value: expect.closeTo(0.8804, 3) as number });
    expect(drawdown?.data[3]).toEqual({ time: 1717426800, value: expect.closeTo(-6.0224, 3) as number });
  });

  it("show each backtest's value at the crosshair on both", async () => {
    stubApi({ ...LISTS, ...choose(BETTER, WORSE) });
    await renderApp('/backtests/compare');
    await screen.findByRole('region', { name: 'Equity' });
    await vi.waitFor(() => {
      expect(charts.of('Line')).toHaveLength(4);
    });
    act(() => {
      charts.hover(1717426800);
    });
    expect(within(screen.getByRole('region', { name: 'Equity' })).getAllByText('−5.2 %')).toHaveLength(2);
    expect(within(screen.getByRole('region', { name: 'Drawdown' })).getAllByText('−6.0 %')).toHaveLength(2);
    act(() => {
      charts.hover(null);
    });
    expect(screen.queryByText('−6.0 %')).toBeNull();
  });
});

describe('a value on a line of the charts', () => {
  const points = [
    { time: 10, value: 1 },
    { time: 20, value: 2 },
    { time: 30, value: 3 },
  ];

  it('is its last point up to then, none outside its period', () => {
    expect(valueAt(points, 5)).toBeNull();
    expect(valueAt(points, 10)).toBe(1);
    expect(valueAt(points, 25)).toBe(2);
    expect(valueAt(points, 30)).toBe(3);
    expect(valueAt(points, 31)).toBeNull();
    expect(valueAt([], 10)).toBeNull();
  });
});
