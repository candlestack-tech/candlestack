// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { setExperimentsView } from '@/features/backtests/experiments/view';
import { ARSEN, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp, viewerIn } from '@/test/app';
import { TYMUR, experiment, experimentsAnswer, othersDocument } from '@/test/experiments';
import { charts } from '@/test/lightweightCharts';

vi.mock('lightweight-charts', () => import('@/test/lightweightCharts'));

const NINE = experiment();
const MINE = experiment({
  id: 'bt_7f3k2m9qx4',
  number: 12,
  instruments: [{ symbol: 'AAPL', market: 'stock' }],
  representation: 'kagi',
  author: { id: ARSEN.id, name: ARSEN.display_name, mine: true },
  published_at: '2026-10-16T09:12:00Z',
  total_return: -0.084,
});
const FACETS = {
  markets: [
    { value: 'stock', count: 17 },
    { value: 'crypto', count: 8 },
  ],
  instruments: [{ market: 'stock' as const, symbol: 'NVDA', count: 5 }],
  representations: [{ value: 'renko', count: 25 }],
  timeframes: [{ value: '30m', count: 25 }],
};
const PAGE = {
  experiments: [NINE, MINE],
  total: 25,
  pages: 3,
  counts: { all: 25, mine: 2, people: 7 },
  facets: FACETS,
};

type Answers = Parameters<typeof stubApi>[0];

async function open(url: string, answers: Answers) {
  const fetch = stubApi({ ...sessionWithPreferences(ARSEN), ...answers });
  const app = await renderApp(url);
  return { ...app, fetch, user: userEvent.setup() };
}

const asked = (fetch: ReturnType<typeof stubApi>) =>
  fetch.mock.calls.map(([path]) => String(path)).filter((path) => path.startsWith('/api/v1/experiments'));
const cards = () => screen.queryAllByRole('article');
const segment = (name: string) => screen.getByRole('radio', { name: new RegExp(`^${name}`) });

beforeEach(() => {
  charts.reset();
  localStorage.clear();
  setExperimentsView('cards');
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-17T16:00:00Z'));
  viewerIn('Europe/Bratislava');
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('Experiments as cards', () => {
  it('shows everyone’s published backtests as cards, with who published how many', async () => {
    await open('/experiments', experimentsAnswer(PAGE, '?page=1'));
    expect(await screen.findByRole('heading', { level: 1, name: 'Experiments' })).toBeDefined();
    expect(
      screen.getByText('Backtests people on the CandleStack beta published. Open one or add it to your Compare.'),
    ).toBeDefined();
    expect(await screen.findByText('25 backtests from 7 people')).toBeDefined();
    expect(segment('All').getAttribute('aria-checked')).toBe('true');
    expect(segment('All').textContent).toBe('All25');
    expect(segment('Mine').textContent).toBe('Mine2');
    expect(segment('Cards').getAttribute('aria-checked')).toBe('true');
    expect(cards()).toHaveLength(2);
  });

  it('draws a card with the model, what it reads, the return against its benchmark and the author', async () => {
    await open('/experiments', experimentsAnswer(PAGE, '?page=1'));
    const [nine, mine] = await screen.findAllByRole('article');
    const card = within(nine!);
    const link = card.getByRole('link', { name: 'Backtest #9, NVDA, by Tymur Tkach' });
    expect(link.getAttribute('href')).toBe('/backtests/bt_t9n8v7d6a5');
    expect(card.getByText('#9')).toBeDefined();
    expect(card.getByText('blesk-novol-8.keras')).toBeDefined();
    expect(['Renko', '30m', 'in 25 × 23', 'SPY +14.3 %'].map((text) => card.getByText(text).textContent)).toHaveLength(
      4,
    );
    expect(
      card.getAllByRole('term').map((term) => `${term.textContent} ${term.nextElementSibling?.textContent}`),
    ).toEqual(['Return +41.2 %', 'Sharpe 1.38', 'Max drawdown −12.4 %', 'Trades 188']);
    expect(card.getByText('Tymur Tkach').previousElementSibling?.textContent).toBe('TT');
    expect(card.getByText('Today')).toBeDefined();
    // The user's own says so, and its day is yesterday.
    expect(within(mine!).getByText('you')).toBeDefined();
    expect(within(mine!).getByText('Yesterday')).toBeDefined();
    // The return on a baseline at zero, its benchmark dashed under it.
    expect(charts.of('Baseline')).toHaveLength(2);
    expect(charts.of('Line')[0]?.options.lineStyle).toBe(2);
    expect(charts.of('Line')[0]?.data.at(-1)).toEqual({ time: 3 * 86400, value: expect.closeTo(14.3, 5) as number });
  });

  it('loads the next 12 with Load more, and says when they are all there', async () => {
    const twelve = Array.from({ length: 12 }, (_, index) =>
      experiment({ id: `bt_${String(index)}`, number: index + 1 }),
    );
    const { user, fetch } = await open('/experiments', {
      ...experimentsAnswer({ ...PAGE, experiments: twelve, total: 13, pages: 2 }, '?page=1'),
      ...experimentsAnswer(
        { ...PAGE, experiments: [experiment({ id: 'bt_last', number: 13 })], page: 2, total: 13, pages: 2 },
        '?page=2',
      ),
    });
    expect(await screen.findByText('Showing 12 of 13')).toBeDefined();
    await user.click(screen.getByRole('button', { name: 'Load more' }));
    expect(await screen.findByText('That is all 13')).toBeDefined();
    expect(cards()).toHaveLength(13);
    expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull();
    expect(asked(fetch)).toEqual(['/api/v1/experiments?page=1', '/api/v1/experiments?page=2']);
  });

  it('adds another account’s backtest to compare, with its author’s initials in the panel', async () => {
    const { user } = await open('/experiments', {
      ...experimentsAnswer(PAGE, '?page=1'),
      'GET /api/v1/backtests/bt_t9n8v7d6a5': { status: 200, body: othersDocument() },
    });
    await user.click(await screen.findByRole('button', { name: 'Add to compare: backtest 9' }));
    const panel = screen.getByRole('complementary', { name: 'Compare' });
    expect(within(panel).getByRole('link', { name: '#9' })).toBeDefined();
    expect(within(panel).getByTitle('Tymur Tkach').textContent).toBe('TT');
    expect(JSON.parse(localStorage.getItem('compare-backtests') ?? '{}')).toMatchObject({
      slots: [{ id: 'bt_t9n8v7d6a5', number: 9, symbol: 'NVDA', author: 'Tymur Tkach' }, null, null, null],
    });
  });
});

describe('Experiments as a table', () => {
  it('remembers Table in this browser, and pages it', async () => {
    const { user } = await open('/experiments', {
      ...experimentsAnswer(PAGE, '?page=1'),
      ...experimentsAnswer(PAGE),
    });
    await screen.findByText('25 backtests from 7 people');
    await user.click(segment('Table'));
    expect(localStorage.getItem('experiments-view')).toBe('table');
    const table = await screen.findByRole('table');
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((head) => head.textContent),
    ).toEqual([
      'Backtest',
      'Author',
      'Instrument',
      'Model',
      'Return',
      'Sharpe',
      'Max drawdown',
      'Published ↓',
      'Compare',
    ]);
    const [, row] = within(table).getAllByRole('row');
    expect(
      within(row!)
        .getAllByRole('cell')
        .map((cell) => cell.textContent),
    ).toEqual([
      '#9',
      'TTTymur Tkach',
      'NVDA',
      'blesk-novol-8.kerasRenko · 30m · 25 × 23',
      '+41.2 %',
      '1.38',
      '−12.4 %',
      'Today',
      '',
    ]);
    // Its model is a name, not a link: the file stays with its author.
    expect(within(row!).queryByRole('link', { name: /blesk/ })).toBeNull();
    expect(screen.getByText('Page 1 of 3')).toBeDefined();
    expect(screen.getByRole('link', { name: 'Next' }).getAttribute('href')).toBe('/experiments?page=2');
  });

  it('opens as a table once Table was picked', async () => {
    setExperimentsView('table');
    await open('/experiments', experimentsAnswer(PAGE));
    expect(await screen.findByRole('table')).toBeDefined();
    expect(segment('Table').getAttribute('aria-checked')).toBe('true');
  });

  it('sorts by a column down, then up, then newest first again', async () => {
    setExperimentsView('table');
    const { user, router } = await open('/experiments', {
      ...experimentsAnswer(PAGE),
      ...experimentsAnswer(PAGE, '?sort=return&order=desc'),
      ...experimentsAnswer(PAGE, '?sort=return&order=asc'),
    });
    await user.click(await screen.findByRole('button', { name: 'Return' }));
    expect(await screen.findByRole('button', { name: 'Return ↓' })).toBeDefined();
    expect(screen.getByRole('button', { name: 'Sort Return' })).toBeDefined();
    await user.click(screen.getByRole('button', { name: 'Return ↓' }));
    expect(await screen.findByRole('button', { name: 'Return ↑' })).toBeDefined();
    expect(screen.getByRole('columnheader', { name: 'Return ↑' }).getAttribute('aria-sort')).toBe('ascending');
    await user.click(screen.getByRole('button', { name: 'Published' }));
    expect(router.state.location.search).toEqual({});
  });
});

describe('the search and the chips of Experiments', () => {
  it('filter by a market with the counts the API gives, and Clear filters shows all again', async () => {
    const { user, fetch } = await open('/experiments', {
      ...experimentsAnswer(PAGE, '?page=1'),
      ...experimentsAnswer({ ...PAGE, experiments: [NINE], total: 8 }, '?market=crypto&page=1'),
    });
    await user.click(await screen.findByRole('button', { name: 'Market' }));
    const menu = await screen.findByRole('menu');
    expect(
      within(menu)
        .getAllByRole('menuitemradio')
        .map((item) => item.textContent),
    ).toEqual(['All markets25', 'Stocks17', 'Crypto8', 'NVDA5']);
    expect(within(menu).getByText('Instrument')).toBeDefined();
    await user.click(within(menu).getByRole('menuitemradio', { name: /^Crypto/ }));
    expect(await screen.findByRole('button', { name: 'Market Crypto' })).toBeDefined();
    expect(await screen.findByText('8 of 25')).toBeDefined();
    expect(asked(fetch).at(-1)).toBe('/api/v1/experiments?market=crypto&page=1');
    await user.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(await screen.findByText('25 backtests from 7 people')).toBeDefined();
  });

  it('sort from the Sort menu, Newest by default', async () => {
    const { user, fetch } = await open('/experiments', {
      ...experimentsAnswer(PAGE, '?page=1'),
      ...experimentsAnswer(PAGE, '?sort=sharpe&order=desc&page=1'),
    });
    await user.click(await screen.findByRole('button', { name: 'Sort Newest' }));
    const menu = await screen.findByRole('menu');
    expect(
      within(menu)
        .getAllByRole('menuitemradio')
        .map((item) => item.textContent),
    ).toEqual(['SharpeHigh to low', 'ReturnHigh to low', 'Max drawdownSmallest first', 'NewestLast published first']);
    await user.click(within(menu).getByRole('menuitemradio', { name: /^Sharpe/ }));
    expect(await screen.findByRole('button', { name: 'Sort Sharpe' })).toBeDefined();
    expect(asked(fetch).at(-1)).toBe('/api/v1/experiments?sort=sharpe&order=desc&page=1');
  });

  it('shows the user’s own under Mine, and how to publish one when there is none', async () => {
    const { user } = await open('/experiments', {
      ...experimentsAnswer(PAGE, '?page=1'),
      ...experimentsAnswer(
        { ...PAGE, experiments: [], total: 0, counts: { all: 25, mine: 0, people: 7 } },
        '?mine=true&page=1',
      ),
    });
    await user.click(await screen.findByRole('radio', { name: /^Mine/ }));
    expect(await screen.findByRole('heading', { name: 'You have not published a backtest yet' })).toBeDefined();
    expect(screen.getByText('Open a finished backtest and click Publish. It shows up here and in All.')).toBeDefined();
    expect(screen.getByRole('link', { name: 'Go to Backtests' }).getAttribute('href')).toBe('/backtests');
  });

  it('counts the user’s own under Mine', async () => {
    await open(
      '/experiments?mine=true',
      experimentsAnswer({ ...PAGE, experiments: [MINE], total: 2 }, '?mine=true&page=1'),
    );
    expect(await screen.findByText('2 of your backtests are in Experiments')).toBeDefined();
  });

  it('says nothing matches a search, and clears it', async () => {
    const { user, router } = await open('/experiments?q=dogecoin', {
      ...experimentsAnswer({ ...PAGE, experiments: [], total: 0 }, '?q=dogecoin&page=1'),
      ...experimentsAnswer(PAGE, '?page=1'),
    });
    expect(await screen.findByRole('heading', { name: 'Nothing matches “dogecoin”' })).toBeDefined();
    expect(screen.getByText('Search by instrument, model or author, like NVDA, kagi or Tymur.')).toBeDefined();
    await user.click(screen.getByRole('button', { name: 'Clear search and filters' }));
    expect(router.state.location.search).toEqual({});
    expect(await screen.findByText('25 backtests from 7 people')).toBeDefined();
  });

  it('shows the author of More by in a chip, whose cross shows everyone again', async () => {
    const { user, router } = await open(`/experiments?author=${TYMUR.id}`, {
      ...experimentsAnswer({ ...PAGE, experiments: [NINE], total: 3 }, `?author=${TYMUR.id}&page=1`),
      ...experimentsAnswer(PAGE, '?page=1'),
    });
    const chip = await screen.findByRole('button', { name: 'Remove the author filter' });
    await vi.waitFor(() => expect(chip.textContent).toBe('TTTymur Tkach'));
    expect(chip.getAttribute('title')).toBe('Show everyone');
    expect(screen.getByText('3 of 25')).toBeDefined();
    await user.click(chip);
    expect(router.state.location.search).toEqual({});
  });

  it('says so when it cannot load', async () => {
    await open('/experiments', { 'GET /api/v1/experiments?page=1': { status: 503, body: { detail: 'Down.' } } });
    expect((await screen.findByRole('alert')).textContent).toBe('Down.');
  });
});
