// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ARSEN, backtest, backtestsAnswer, failedBacktest, problem, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp, viewerIn } from '@/test/app';

// #12 finished today at 10:05 in New York, #11 failed yesterday, #10 on three instruments.
const TWELVE = backtest();
const ELEVEN = failedBacktest({ id: 'bt_a2b3c4d5e6', number: 11, created_at: '2026-10-16T22:10:00Z' });
const TEN = backtest({
  id: 'bt_q7r8s9t2u3',
  number: 10,
  starred: true,
  instruments: [
    { symbol: 'BTCUSDT', market: 'crypto' },
    { symbol: 'ETHUSDT', market: 'crypto' },
    { symbol: 'SOLUSDT', market: 'crypto' },
  ],
  total_return: -0.241,
  sharpe: -0.184,
  created_at: '2026-10-12T23:44:00Z',
});
const COUNTS = { all: 3, starred: 1, failed: 1 };
const MODELS = [{ name: 'blesk-novol-8.keras', framework: 'keras', count: 3 }];
const PAGE = { backtests: [TWELVE, ELEVEN, TEN], counts: COUNTS, models: MODELS };

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-17T16:00:00Z'));
  viewerIn('America/New_York');
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const subtitle = () => screen.getByRole('heading', { level: 1 }).parentElement?.querySelector('p')?.textContent;
const rows = () => screen.getAllByRole('row').slice(1);
const cells = (row: HTMLElement) =>
  within(row)
    .getAllByRole('cell')
    .map((cell) => cell.textContent);
const asked = (fetch: ReturnType<typeof stubApi>) =>
  fetch.mock.calls.map(([path]) => String(path)).filter((path) => path.startsWith('/api/v1/backtests'));

describe('the Backtests page', () => {
  it('counts the backtests under the title and shows a row for each', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), ...backtestsAnswer(PAGE) });
    await renderApp('/backtests');
    await screen.findByText('#12');
    expect(subtitle()).toBe('3 backtests');
    expect(document.title).toBe('Backtests · CandleStack');
    expect(screen.getAllByRole('columnheader').map((head) => head.textContent)).toEqual([
      'Backtest',
      'Instruments',
      'Model',
      'Status',
      'Return',
      'Sharpe',
      'Max drawdown',
      'When',
    ]);
    // A failed return is a dash, with the badge for phones, where the Status column is hidden.
    expect(rows().map(cells)).toEqual([
      ['☆#12', 'AAPL+1', 'blesk-novol-8.keras', 'Finished', '+24.6 %', '0.79', '−19.7 %', 'Today 10:05'],
      ['☆#11', 'AAPL+1', 'blesk-novol-8.keras', 'Failed', 'Failed–', '–', '–', 'Yesterday 18:10'],
      ['★#10', 'BTCUSDT+2', 'blesk-novol-8.keras', 'Finished', '−24.1 %', '−0.18', '−19.7 %', '12 Oct 19:44'],
    ]);
    expect(within(rows()[0]!).getByRole('img', { name: 'Keras' })).toBeDefined();
  });

  // #318: the instruments of a row are named with their icons, the rest as +N with all of them in
  // the title.
  it('shows each instrument with its icon', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), ...backtestsAnswer(PAGE) });
    await renderApp('/backtests');
    await screen.findByText('#12');
    const icons = rows().map((row) => row.querySelector('td:nth-child(2) img')?.getAttribute('src'));
    expect(icons).toEqual([
      '/api/v1/data/instruments/stock%3AAAPL/icon',
      '/api/v1/data/instruments/stock%3AAAPL/icon',
      '/api/v1/data/instruments/crypto%3ABTCUSDT/icon',
    ]);
    expect(rows()[2]!.querySelector('td:nth-child(2) [title]')?.getAttribute('title')).toBe(
      'BTCUSDT, ETHUSDT and SOLUSDT',
    );
  });

  // #315: a long model name is cut inside its column, the numbers keep their widths and the status
  // has its own column.
  it('keeps a row with a long model name inside the table', async () => {
    const name = `${'BLESK_ATR06_R8_OHLC_LAM08_EOD_SPLIT_ATR_NO-VOL_OPT1_8_M25x23_'.repeat(2)}`.slice(0, 84) + '.keras';
    expect(name).toHaveLength(90);
    const long = failedBacktest({ model: { id: 'mdl_7f3k2m9qx4', name, framework: 'keras' } });
    stubApi({ ...sessionWithPreferences(ARSEN), ...backtestsAnswer({ ...PAGE, backtests: [long] }) });
    await renderApp('/backtests');
    await screen.findByText('#12');
    expect(screen.getByRole('table').className.split(' ')).toContain('table-fixed');

    // The full name on hover, still a link to the model.
    const model = screen.getByTitle(name);
    expect(model.tagName).toBe('A');
    expect(model.getAttribute('href')).toBe('/models/mdl_7f3k2m9qx4');
    expect(model.className.split(' ')).toEqual(expect.arrayContaining(['text-[12px]', 'max-w-full', 'min-w-0']));
    expect(within(model).getByText(name).className.split(' ')).toEqual(expect.arrayContaining(['truncate', 'min-w-0']));

    const width = (label: RegExp) =>
      screen
        .getByRole('columnheader', { name: label })
        .className.split(' ')
        .find((name) => name.startsWith('w-'));
    expect(width(/^Model/)).toBeUndefined();
    expect([/^Backtest/, /^Instruments/, /^Status/, /^Return/, /^Sharpe/, /^Max drawdown/, /^When/].map(width)).toEqual(
      ['w-[84px]', 'w-[112px]', 'w-[88px]', 'w-[100px]', 'w-[76px]', 'w-[128px]', 'w-[160px]'],
    );

    const [, , , status, result] = within(rows()[0]!).getAllByRole('cell');
    expect(status!.textContent).toBe('Failed');
    expect(within(result!).getByText('–').parentElement!.className).toBe('max-[500px]:hidden');
  });

  it('says how many match a search, and one backtest in the singular', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), ...backtestsAnswer({ ...PAGE, total: 1 }, '?q=11') });
    await renderApp('/backtests?q=11');
    expect(await screen.findByText('1 of 3')).toBeDefined();
    cleanup();

    stubApi({ ...sessionWithPreferences(ARSEN), ...backtestsAnswer({ backtests: [TWELVE] }) });
    await renderApp('/backtests');
    expect(await screen.findByText('1 backtest')).toBeDefined();
  });

  it('says No backtests yet for an account without any, and No backtests match for a search', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), ...backtestsAnswer({}) });
    await renderApp('/backtests');
    expect(await screen.findByText('No backtests yet')).toBeDefined();
    expect(subtitle()).toBe('0 backtests');
    expect(screen.queryByRole('navigation', { name: 'Pages' })).toBeNull();
    cleanup();

    stubApi({ ...sessionWithPreferences(ARSEN), ...backtestsAnswer({ counts: COUNTS, total: 0 }, '?q=zzz') });
    await renderApp('/backtests?q=zzz');
    expect(await screen.findByText('No backtests match')).toBeDefined();
    expect(subtitle()).toBe('0 of 3');
  });

  it('says why the backtests could not load', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), 'GET /api/v1/backtests': { status: 503, body: { detail: 'Down.' } } });
    await renderApp('/backtests');
    await vi.waitFor(() => expect(subtitle()).toBe('Down.'));
  });

  it("says in plain words when the API refuses the request, not in the API's", async () => {
    stubApi({
      ...sessionWithPreferences(ARSEN),
      'GET /api/v1/backtests': problem(422, 'validation', "query parameter 'page': Input should be at least 1"),
    });
    await renderApp('/backtests');
    await vi.waitFor(() => expect(subtitle()).toBe('Could not load the backtests.'));
  });

  it.each(['page=0', 'page=abc', 'page=-3', 'show=bar', 'sort=foo', 'order=x', 'show=bar&sort=foo&page=abc'])(
    'opens the plain list for ?%s, without asking the API for it',
    async (query) => {
      const fetch = stubApi({ ...sessionWithPreferences(ARSEN), ...backtestsAnswer(PAGE) });
      await renderApp(`/backtests?${query}`);
      expect(await screen.findByText('3 backtests')).toBeDefined();
      expect(asked(fetch)).toEqual(['/api/v1/backtests']);
    },
  );

  it('searches as the typing pauses, from page 1, keeping the focus in the field', async () => {
    const fetch = stubApi({
      ...sessionWithPreferences(ARSEN),
      ...backtestsAnswer({ ...PAGE, page: 2, pages: 2, total: 13 }, '?page=2'),
      ...backtestsAnswer({ ...PAGE, backtests: [TEN], total: 1 }, '?q=btc'),
    });
    const { router } = await renderApp('/backtests?page=2');
    const field = await screen.findByRole('searchbox', { name: 'Search backtests' });
    expect(field.getAttribute('placeholder')).toBe('Number, instrument or model, e.g. aapl');
    await userEvent.type(field, 'btc');
    await vi.waitFor(() => expect(router.state.location.search).toEqual({ q: 'btc' }));
    expect(await screen.findByText('1 of 3')).toBeDefined();
    expect(document.activeElement).toBe(field);
    expect(asked(fetch)).toEqual(['/api/v1/backtests?page=2', '/api/v1/backtests?q=btc']);
  });

  it('keeps the rows of the page on screen while the next one loads', async () => {
    stubApi({
      ...sessionWithPreferences(ARSEN),
      ...backtestsAnswer({ ...PAGE, pages: 2, total: 13 }),
      'GET /api/v1/backtests?page=2': () => new Promise(() => {}),
    });
    await renderApp('/backtests');
    await userEvent.click(await screen.findByRole('link', { name: 'Next' }));
    expect(screen.getByText('#12')).toBeDefined();
    expect(screen.getByText('Page 2 of 2')).toBeDefined();
    expect(screen.queryByRole('link', { name: 'Previous' })).toBeNull();
  });

  it('sorts by a column down, then up, then back to newest first', async () => {
    const fetch = stubApi({
      ...sessionWithPreferences(ARSEN),
      ...backtestsAnswer(PAGE),
      ...backtestsAnswer(PAGE, '?sort=return&order=desc'),
      ...backtestsAnswer(PAGE, '?sort=return&order=asc'),
    });
    const { router } = await renderApp('/backtests');
    await screen.findByText('#12');
    const head = () => screen.getByRole('columnheader', { name: /^Return/ });
    await userEvent.click(within(head()).getByRole('button'));
    await vi.waitFor(() => expect(head().textContent).toBe('Return ↓'));
    expect(head().getAttribute('aria-sort')).toBe('descending');
    await userEvent.click(within(head()).getByRole('button'));
    await vi.waitFor(() => expect(head().textContent).toBe('Return ↑'));
    await userEvent.click(within(head()).getByRole('button'));
    await vi.waitFor(() => expect(head().textContent).toBe('Return'));
    expect(router.state.location.search).toEqual({});
    expect(asked(fetch).slice(0, 3)).toEqual([
      '/api/v1/backtests',
      '/api/v1/backtests?sort=return&order=desc',
      '/api/v1/backtests?sort=return&order=asc',
    ]);
  });

  it('filters by Show and Model, keeping the panel open', async () => {
    const fetch = stubApi({
      ...sessionWithPreferences(ARSEN),
      ...backtestsAnswer(PAGE),
      ...backtestsAnswer({ ...PAGE, backtests: [TEN], total: 1 }, '?show=starred'),
      ...backtestsAnswer({ ...PAGE, backtests: [TEN], total: 1 }, '?show=starred&model=blesk-novol-8.keras'),
    });
    await renderApp('/backtests');
    await screen.findByText('#12');
    await userEvent.click(screen.getByRole('button', { name: 'Filters: All backtests' }));
    const panel = screen.getByRole('dialog', { name: 'Filters' });
    expect(
      within(panel)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual(['All backtests3', 'Starred1', 'Failed1', 'All models', 'blesk-novol-8.keras3']);
    await userEvent.click(within(panel).getByRole('option', { name: /^Starred/ }));
    await screen.findByRole('button', { name: 'Filters: Starred' });
    await userEvent.click(within(panel).getByRole('option', { name: /blesk-novol-8/ }));
    await screen.findByRole('button', { name: 'Filters: Starred · blesk-novol-8.keras' });
    expect(screen.getByRole('dialog', { name: 'Filters' })).toBeDefined();
    expect(asked(fetch).at(-1)).toBe('/api/v1/backtests?show=starred&model=blesk-novol-8.keras');
  });

  it('pops the Filters panel open from its corner and closes it at once, 8 px under each heading', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), ...backtestsAnswer(PAGE) });
    await renderApp('/backtests');
    await screen.findByText('#12');
    const button = screen.getByRole('button', { name: 'Filters: All backtests' });
    await userEvent.click(button);
    const panel = screen.getByRole('dialog', { name: 'Filters' });
    expect(panel.className.split(' ')).toEqual(
      expect.arrayContaining([
        'origin-top-left',
        'data-[state=open]:animate-fx-pop!',
        'data-[state=closed]:animate-none!',
      ]),
    );
    for (const name of ['Show', 'Model']) {
      const list = within(panel).getByRole('listbox', { name });
      const heading = document.getElementById(list.getAttribute('aria-labelledby') ?? '');
      expect(heading?.className.split(' ')).toContain('mb-2');
      expect(heading?.className).not.toMatch(/mb-1/);
    }
    // Nothing plays on closing: the panel is gone right after Escape, the focus back on the button.
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: 'Filters' })).toBeNull();
    expect(document.activeElement).toBe(button);
  });

  it('ends a model name too long for the Filters panel in an ellipsis, its count still beside it', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), ...backtestsAnswer(PAGE) });
    await renderApp('/backtests');
    await screen.findByText('#12');
    await userEvent.click(screen.getByRole('button', { name: 'Filters: All backtests' }));
    const option = within(screen.getByRole('dialog', { name: 'Filters' })).getByRole('option', { name: /blesk/ });
    const name = within(option).getByText('blesk-novol-8.keras');
    expect(name.className.split(' ')).toEqual(expect.arrayContaining(['min-w-0', 'truncate']));
    // The mark and the name sit in a row no wider than the entry's label, which gives way first.
    expect(name.parentElement?.className.split(' ')).toContain('max-w-full');
    expect(name.parentElement?.parentElement?.className.split(' ')).toEqual(
      expect.arrayContaining(['min-w-0', 'truncate']),
    );
    expect(within(option).getByText('3').className.split(' ')).toContain('shrink-0');
  });

  it('lights an entry of the Filters panel under the pointer, and where the focus is only once keys are used', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), ...backtestsAnswer(PAGE) });
    await renderApp('/backtests');
    await screen.findByText('#12');
    await userEvent.click(screen.getByRole('button', { name: 'Filters: All backtests' }));
    const panel = screen.getByRole('dialog', { name: 'Filters' });
    const first = within(panel).getAllByRole('option')[0]!;
    expect(document.activeElement).toBe(first);
    expect(panel.hasAttribute('data-keys')).toBe(false);
    expect(first.className.split(' ')).toEqual(
      expect.arrayContaining(['hover:bg-accent', 'in-data-keys:focus:bg-accent']),
    );
    expect(first.className).not.toMatch(/(^| )focus:bg-accent/);
    expect(first.querySelector('svg')?.getAttribute('class')).toMatch(/(^| )size-4( |$)/);
    await userEvent.keyboard('{ArrowDown}');
    expect(panel.getAttribute('data-keys')).toBe('true');
    expect(document.activeElement).toBe(within(panel).getAllByRole('option')[1]);
    await userEvent.keyboard('{Escape}');
    // Opened from the keyboard, the panel says so at once.
    await userEvent.keyboard('{Enter}');
    expect(screen.getByRole('dialog', { name: 'Filters' }).getAttribute('data-keys')).toBe('true');
  });

  it('writes the headers of the number columns in mono at the right', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), ...backtestsAnswer(PAGE) });
    await renderApp('/backtests');
    await screen.findByText('#12');
    const mono = screen
      .getAllByRole('columnheader')
      .filter((head) => head.className.split(' ').includes('font-mono'))
      .map((head) => head.textContent);
    expect(mono).toEqual(['Return', 'Sharpe', 'Max drawdown']);
  });

  it('opens a row from the keyboard, ringing the row while its link has the focus', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), ...backtestsAnswer(PAGE) });
    const { router } = await renderApp('/backtests');
    const star = await screen.findByRole('button', { name: 'Star backtest 12' });
    star.focus();
    await userEvent.tab();
    const opener = screen.getByRole('link', { name: 'Backtest #12' });
    expect(document.activeElement).toBe(opener);
    expect(opener.className.split(' ')).toEqual(
      expect.arrayContaining(['after:absolute', 'after:inset-0', 'focus-visible:after:ring-2']),
    );
    await userEvent.keyboard('{Enter}');
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/backtests/bt_7f3k2m9qx4'));
  });
  it('stars a backtest at once and takes the star back when the API refuses', async () => {
    stubApi({
      ...sessionWithPreferences(ARSEN),
      ...backtestsAnswer(PAGE),
      'PATCH /api/v1/backtests/bt_7f3k2m9qx4': () =>
        new Promise((resolve) => setTimeout(() => resolve(problem(404, 'not-found', 'Gone.')), 50)),
    });
    await renderApp('/backtests');
    const starOf = () => screen.getByRole('button', { name: 'Star backtest 12' });
    await userEvent.click(await screen.findByRole('button', { name: 'Star backtest 12' }));
    expect(starOf().getAttribute('aria-pressed')).toBe('true');
    expect(starOf().textContent).toBe('★');
    await vi.waitFor(() => expect(starOf().getAttribute('aria-pressed')).toBe('false'));
  });

  it('drops a backtest that loses its star from the Starred list at once', async () => {
    stubApi({
      ...sessionWithPreferences(ARSEN),
      ...backtestsAnswer({ ...PAGE, backtests: [TEN], total: 1 }, '?show=starred'),
      'PATCH /api/v1/backtests/bt_q7r8s9t2u3': () => new Promise(() => {}),
    });
    await renderApp('/backtests?show=starred');
    await userEvent.click(await screen.findByRole('button', { name: 'Star backtest 10' }));
    expect(screen.getByText('No backtests match')).toBeDefined();
    expect(subtitle()).toBe('0 of 3');
  });

  it("opens a backtest's page, but not from its star", async () => {
    stubApi({
      ...sessionWithPreferences(ARSEN),
      ...backtestsAnswer(PAGE),
      'PATCH /api/v1/backtests/bt_q7r8s9t2u3': { status: 200, body: { ...TEN, starred: false } },
    });
    const { router } = await renderApp('/backtests');
    await userEvent.click(await screen.findByRole('button', { name: 'Star backtest 10' }));
    expect(router.state.location.pathname).toBe('/backtests');
    const opener = screen.getByRole('link', { name: 'Backtest #10' });
    expect(opener.getAttribute('href')).toBe('/backtests/bt_q7r8s9t2u3');
    await userEvent.click(opener);
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/backtests/bt_q7r8s9t2u3'));
    expect(router.state.location.state.from).toBeUndefined();
  });

  it('pages through the backtests', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), ...backtestsAnswer({ ...PAGE, pages: 2, total: 13 }) });
    await renderApp('/backtests');
    const pager = await screen.findByRole('navigation', { name: 'Pages' });
    expect(pager.textContent).toBe('PreviousPage 1 of 2Next');
    expect(within(pager).getByRole('link', { name: 'Next' }).getAttribute('href')).toBe('/backtests?page=2');
  });

  it('goes to the last page from a link past the end', async () => {
    stubApi({
      ...sessionWithPreferences(ARSEN),
      ...backtestsAnswer({ ...PAGE, backtests: [], page: 9, pages: 2, total: 13 }, '?page=9'),
      ...backtestsAnswer({ ...PAGE, page: 2, pages: 2, total: 13 }, '?page=2'),
    });
    const { router } = await renderApp('/backtests?page=9');
    await vi.waitFor(() => expect(router.state.location.search).toEqual({ page: 2 }));
    expect(await screen.findByText('Page 2 of 2')).toBeDefined();
  });
});
