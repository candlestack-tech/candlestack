// @vitest-environment jsdom
import { act, cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { addToCompare, clearCompare } from '@/features/backtests/compare/selection';
import { valueAt } from '@/features/backtests/compare/chartLines';
import { ADA, backtestDocument, backtestsAnswer, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp } from '@/test/app';
import { compareAnswers, compareMissing } from '@/test/compare';
import { othersDocument } from '@/test/experiments';
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

// The names of the user's backtests here (#376).
const NAMES: Record<number, string> = { 12: 'Bright Vega', 11: 'Frozen Pulsar', 10: 'Crimson Nova' };

// A finished backtest's document with these results.
function doc(number: number, stats: Partial<Stats> = {}) {
  const base = backtestDocument();
  const name = NAMES[number] ?? `Backtest ${String(number)}`;
  return backtestDocument({
    backtest: { ...base.backtest, id: `bt_${String(number)}`, number, name, generated_name: name },
    results: base.results && { ...base.results, stats: { ...STATS, ...stats } },
  });
}

const BETTER = doc(12, { total_return: 0.9, max_drawdown: -0.05, sharpe: null, fees_paid: 0.01, num_trades: 99 });
const WORSE = doc(11, { total_return: 0.1, max_drawdown: -0.3, sharpe: null, fees_paid: 0.05, num_trades: 1 });
const MIDDLE = doc(10);
// How the server marks these three (GET /api/v1/compare): the best and the worst return, fall and
// fees; the rest are equal, better neither way, or known for one only.
const MARKED = compareAnswers([BETTER, WORSE, MIDDLE], {
  total_return: ['best', 'worst', null],
  max_drawdown: ['best', 'worst', null],
  fees_paid: ['best', 'worst', null],
});

const answer = (body: unknown) => ({ status: 200, body });
const LISTS = {
  ...sessionWithPreferences(ADA),
  ...backtestsAnswer({ backtests: [], counts: { all: 0, starred: 0, failed: 0, public: 0 }, models: [] }),
};

// Chooses the backtests of these documents: their pages, and the comparisons the page asks for.
const choose = (...docs: ReturnType<typeof doc>[]) => {
  docs.forEach(({ backtest }) => {
    addToCompare({ id: backtest.id, number: backtest.number, name: backtest.name, symbol: 'AAPL' });
  });
  return {
    ...Object.fromEntries(docs.map((each) => [`GET /api/v1/backtests/${each.backtest.id}`, answer(each)])),
    ...compareAnswers(docs),
  };
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
    stubApi({ ...LISTS, ...choose(BETTER, WORSE, MIDDLE), ...MARKED });
    await renderApp('/backtests/compare');
    await screen.findByRole('rowheader', { name: 'Total return' });
    expect(marks('Total return')).toEqual(['best', 'worst', 'plain']);
    // A fall is better the smaller it is, however it is signed; so are the fees.
    expect(marks('Max drawdown')).toEqual(['best', 'worst', 'plain']);
    expect(marks('Fees paid')).toEqual(['best', 'worst', 'plain']);
  });

  it('marks nothing when the values are equal or none is better, and a dash for a missing one', async () => {
    stubApi({ ...LISTS, ...choose(BETTER, WORSE, MIDDLE), ...MARKED });
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
    await user.click(await screen.findByRole('button', { name: 'Remove Frozen Pulsar from compare' }));
    expect(screen.queryByRole('link', { name: 'Frozen Pulsar' })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Clear all' }));
    expect(await screen.findByRole('heading', { name: 'Nothing to compare yet' })).toBeDefined();
  });

  it('drops a backtest that no longer exists from the choice', async () => {
    stubApi({
      ...LISTS,
      ...choose(BETTER, WORSE),
      ...compareMissing([BETTER, WORSE], 'bt_11'),
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
    stubApi({
      ...LISTS,
      ...choose(small, close),
      ...compareAnswers([small, close], { avg_trade_return: ['worst', 'best'] }),
    });
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
    await user.click(screen.getByRole('button', { name: 'Remove Frozen Pulsar from compare' }));
    await vi.waitFor(() => {
      expect(charts.of('Line')).toHaveLength(6);
    });
  });

  it('opens a backtest and goes back to the comparison', async () => {
    stubApi({ ...LISTS, ...choose(BETTER, WORSE) });
    await renderApp('/backtests/compare');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('link', { name: 'Bright Vega' }));
    // The page loads its document now: the comparison did not.
    await screen.findByRole('button', { name: 'Download JSON' });
    await user.click(screen.getByRole('button', { name: '← Compare backtests' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Compare backtests' })).toBeDefined();
  });

  it('reads the server’s comparison, never the backtests’ documents (#401)', async () => {
    const fetch = stubApi({ ...LISTS, ...choose(BETTER, WORSE) });
    await renderApp('/backtests/compare');
    await screen.findByRole('rowheader', { name: 'Total return' });
    const asked = fetch.mock.calls.map(([path]) => path);
    expect(asked).toContain('/api/v1/compare?ids=bt_12,bt_11');
    expect(asked.filter((path) => path.startsWith('/api/v1/backtests/bt_'))).toEqual([]);
    // The setup as each stored it: the model's input from its manifest, the candles it ran on.
    expect(row('Input').map((cell) => cell.textContent)).toEqual(['25 × 5', '25 × 5', 'No backtest']);
    expect(row('Candles').map((cell) => cell.textContent)).toEqual(['5', '5', 'No backtest']);
  });

  // #465: two backtests fit a 360 px phone beside the labels, which stay while the table scrolls.
  it('fits two backtests beside the labels on a phone, inside the box that scrolls', async () => {
    stubApi({ ...LISTS, ...choose(BETTER, WORSE, MIDDLE), ...MARKED });
    await renderApp('/backtests/compare');
    const label = await screen.findByRole('rowheader', { name: 'Total return' });
    const table = label.closest('table') as HTMLElement;
    // Three backtests and the column to add one: 96 px and 120 px a column up to 640 px.
    expect(table.style.getPropertyValue('--columns')).toBe('4');
    expect(table.className).toContain('max-sm:min-w-[calc(96px+var(--columns)*120px)]');
    expect(table.parentElement?.className.split(' ')).toEqual(expect.arrayContaining(['relative', 'overflow-x-auto']));
    expect(label.className.split(' ')).toEqual(
      expect.arrayContaining(['max-sm:sticky', 'max-sm:left-0', 'max-sm:bg-background']),
    );
  });

  it('shows no panel on its own page', async () => {
    stubApi({ ...LISTS, ...choose(BETTER, WORSE) });
    await renderApp('/backtests/compare');
    await screen.findByRole('rowheader', { name: 'Total return' });
    expect(screen.queryByRole('complementary', { name: 'Compare' })).toBeNull();
  });
});

describe('comparing with another account’s backtests', () => {
  const theirs = () => {
    const doc = othersDocument();
    addToCompare({
      id: doc.backtest.id,
      number: doc.backtest.number,
      name: doc.backtest.name,
      symbol: 'AAPL',
      author: 'Alan Turing',
    });
    return { [`GET /api/v1/backtests/${doc.backtest.id}`]: answer(doc), ...compareAnswers([BETTER, doc]) };
  };

  it('counts whose they are, and names the author in the head, the legends and the Author row', async () => {
    stubApi({ ...LISTS, ...choose(BETTER), ...theirs() });
    await renderApp('/backtests/compare');
    expect(await screen.findByText('2 of 4 backtests · 1 yours, 1 from Experiments')).toBeDefined();
    await screen.findByRole('rowheader', { name: 'Author' });
    expect(row('Author').map((cell) => cell.textContent)).toEqual(['ALYou', 'ATAlan Turing', 'No backtest']);
    // Each head and legend names its backtest on two lines, without its number (#376).
    const head = screen.getByRole('link', { name: 'Velvet Deneb' }).closest('th')!;
    expect(within(head).getByText('Alan Turing').previousElementSibling?.textContent).toBe('AT');
    expect(within(head).getByText('AAPL · Candles · 30m')).toBeDefined();
    expect(within(head).queryByText('#9')).toBeNull();
    const legend = within(screen.getByRole('region', { name: 'Equity' }));
    expect(legend.getByText('Bright Vega').parentElement?.textContent).toBe('Bright VegaAAPL · Candles · 30m');
    expect(legend.getByTitle('Alan Turing').parentElement?.textContent).toBe('ATVelvet Deneb');
    expect(legend.queryByText(/#/)).toBeNull();
  });

  it('has no Author row with only the user’s own', async () => {
    stubApi({ ...LISTS, ...choose(BETTER, WORSE) });
    await renderApp('/backtests/compare');
    await screen.findByRole('rowheader', { name: 'Total return' });
    expect(screen.queryByRole('rowheader', { name: 'Author' })).toBeNull();
    expect(screen.getByText('2 of 4 backtests')).toBeDefined();
  });

  it('says which one its author unpublished until it is dismissed', async () => {
    const doc = othersDocument();
    addToCompare({ id: doc.backtest.id, number: 9, name: 'Velvet Deneb', symbol: 'AAPL', author: 'Alan Turing' });
    stubApi({
      ...LISTS,
      ...choose(BETTER, WORSE),
      ...compareMissing([doc, BETTER, WORSE], doc.backtest.id),
    });
    await renderApp('/backtests/compare');
    const note = (await screen.findByText(/, so it left your Compare\.$/)).parentElement!;
    expect(note.textContent).toBe('ATAlan Turing unpublished Velvet Deneb, so it left your Compare.');
    expect(screen.getByText('2 of 4 backtests')).toBeDefined();
    await userEvent.click(within(note).getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByText(/so it left your Compare/)).toBeNull();
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

describe('the charts of backtests whose periods do not overlap (#466)', () => {
  const YEAR = 365 * 86_400;
  // The backtest of this number, its series moved by `shift` seconds.
  const moved = (number: number, shift: number) => {
    const base = doc(number);
    if (!base.results) throw new Error('A finished backtest has results');
    const { series } = base.results;
    const ts = series.columns.ts.map((time) => time + shift);
    return { ...base, results: { ...base.results, series: { ...series, columns: { ...series.columns, ts } } } };
  };

  it('draw each over its whole period from 0 %, and fit both charts once both have their lines', async () => {
    const earlier = moved(12, -2 * YEAR);
    const later = moved(11, 0);
    stubApi({ ...LISTS, ...choose(earlier, later) });
    await renderApp('/backtests/compare');
    await vi.waitFor(() => {
      expect(charts.of('Line')).toHaveLength(4);
    });
    const [early, late, earlyFall, lateFall] = charts.of('Line');
    const span = (points: { time: number }[] | undefined) => [points?.[0]?.time, points?.at(-1)?.time];
    const ts = (each: typeof earlier) => each.results?.series.columns.ts ?? [];
    expect(span(early?.data)).toEqual([ts(earlier)[0], ts(earlier).at(-1)]);
    expect(span(late?.data)).toEqual([ts(later)[0], ts(later).at(-1)]);
    expect(span(earlyFall?.data)).toEqual(span(early?.data));
    expect(span(lateFall?.data)).toEqual(span(late?.data));
    for (const line of [early, late, earlyFall, lateFall]) expect(line?.data[0]).toMatchObject({ value: 0 });
    // A chart's fit waits for its next frame: fitted before the other had its lines, the view the
    // other copied back undid it.
    const last = charts.steps.slice(-6);
    expect(last.map((step) => step.split(' ')[0])).toEqual(['data', 'data', 'data', 'data', 'fit', 'fit']);
    expect(new Set(last.slice(-2)).size).toBe(2);
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
