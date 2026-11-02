// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ARSEN, backtest, backtestsAnswer, failedBacktest, problem, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp } from '@/test/app';
import { TYMUR, experiment, experimentsAnswer, othersDocument } from '@/test/experiments';

import { addToCompare, claimCompare, clearCompare, leaveCompare } from './selection';

vi.mock('lightweight-charts', () => import('@/test/lightweightCharts'));

const FINISHED = [12, 13, 14, 15, 16].map((number) => backtest({ id: `bt_${String(number)}`, number }));
const FAILED = failedBacktest({ id: 'bt_failed', number: 11 });
const SEEDED = backtest({ id: 'bt_seeded', number: 9, seeded: true });
const RUNNING = backtest({ id: 'bt_running', number: 10, status: 'running', total_return: null, sharpe: null });
const PAGE = {
  backtests: [...FINISHED, FAILED, RUNNING, SEEDED],
  counts: { all: 7, starred: 0, failed: 1, public: 0 },
  models: [],
};

const toggle = (label: RegExp) => screen.getByRole('button', { name: label });
const panel = () => screen.getByRole('complementary', { name: 'Compare' });

async function openList() {
  stubApi({ ...sessionWithPreferences(ARSEN), ...backtestsAnswer(PAGE) });
  await renderApp('/backtests');
  await screen.findByRole('link', { name: 'Backtest #12' });
  return userEvent.setup();
}

beforeEach(clearCompare);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('choosing backtests to compare', () => {
  it('shows no panel until one is added, then lists it and keeps Compare off until two', async () => {
    const user = await openList();
    expect(screen.queryByRole('complementary', { name: 'Compare' })).toBeNull();
    await user.click(toggle(/^Add to compare: backtest 12$/));
    expect(within(panel()).getByText('1 of 4')).toBeDefined();
    expect(within(panel()).getByRole('link', { name: '#12' })).toBeDefined();
    expect(within(panel()).getByRole('button', { name: 'Compare' })).toHaveProperty('disabled', true);
    await user.click(toggle(/^Add to compare: backtest 13$/));
    expect(within(panel()).getByRole('link', { name: 'Compare' }).getAttribute('href')).toBe('/backtests/compare');
    expect(toggle(/^Remove from compare: backtest 12$/).getAttribute('aria-pressed')).toBe('true');
  });

  it('refuses a fifth, and a backtest that failed or still runs, and says why', async () => {
    const user = await openList();
    expect(toggle(/: backtest 11$/)).toHaveProperty('disabled', true);
    expect(toggle(/: backtest 11$/).title).toBe('Failed: no results to compare');
    expect(toggle(/: backtest 10$/).title).toBe('Still running');
    expect(toggle(/: backtest 9$/)).toHaveProperty('disabled', true);
    expect(toggle(/: backtest 9$/).title).toBe('Test data: no results to compare');
    for (const number of [12, 13, 14, 15]) {
      await user.click(toggle(new RegExp(`^Add to compare: backtest ${String(number)}$`)));
    }
    expect(within(panel()).getByText('4 of 4')).toBeDefined();
    expect(toggle(/: backtest 16$/)).toHaveProperty('disabled', true);
    expect(toggle(/: backtest 16$/).title).toBe('Up to 4 backtests. Remove one first');
  });

  it('keeps the slot of the others when one leaves, and takes the first free one', async () => {
    const user = await openList();
    for (const number of [12, 13, 14]) {
      await user.click(toggle(new RegExp(`^Add to compare: backtest ${String(number)}$`)));
    }
    await user.click(within(panel()).getByRole('button', { name: /backtest 13 from compare/ }));
    await user.click(toggle(/^Add to compare: backtest 15$/));
    const order = within(panel())
      .getAllByRole('link')
      .map((link) => link.textContent)
      .filter((text) => text.startsWith('#'));
    expect(order).toEqual(['#12', '#15', '#14']);
  });

  it('folds the panel to a button, and Clear empties it', async () => {
    const user = await openList();
    await user.click(toggle(/^Add to compare: backtest 12$/));
    await user.click(within(panel()).getByRole('button', { name: 'Hide' }));
    expect(screen.queryByRole('complementary', { name: 'Compare' })).toBeNull();
    await user.click(screen.getByRole('button', { name: /^Compare 1 of 4$/ }));
    await user.click(within(panel()).getByRole('button', { name: 'Clear' }));
    expect(screen.queryByRole('complementary', { name: 'Compare' })).toBeNull();
  });

  it('forgets the choice when another account signs in, not when the same one comes back', () => {
    claimCompare('usr_a');
    addToCompare({ id: 'bt_12', number: 12, symbol: 'AAPL' });
    claimCompare('usr_a');
    expect(localStorage.getItem('compare-backtests')).toContain('bt_12');
    claimCompare('usr_b');
    expect(localStorage.getItem('compare-backtests')).not.toContain('bt_12');
  });

  it('remembers the choice in this browser', () => {
    addToCompare({ id: 'bt_12', number: 12, symbol: 'AAPL' });
    const saved = JSON.parse(localStorage.getItem('compare-backtests') ?? '{}') as { slots: unknown[] };
    expect(saved.slots[0]).toEqual({ id: 'bt_12', number: 12, symbol: 'AAPL' });
  });
});

describe('another account’s backtest in the comparison', () => {
  it('leaves the choice once unpublished but keeps its slot, saying so, until dismissed', async () => {
    claimCompare(ARSEN.id);
    addToCompare({ id: 'bt_12', number: 12, symbol: 'AAPL' });
    addToCompare({ id: 'bt_57', number: 57, symbol: 'BTCUSDT', author: 'Vladyslav Shudiehov' });
    stubApi({
      ...sessionWithPreferences(ARSEN),
      ...backtestsAnswer(PAGE),
      'GET /api/v1/backtests/bt_57': problem(404, 'not-found', 'Not found.'),
    });
    await renderApp('/backtests');
    const slot = (await within(panel()).findByText('#57 was unpublished')).parentElement!;
    expect(slot.getAttribute('title')).toBe('Vladyslav Shudiehov unpublished it');
    expect(within(slot).getByTitle('Vladyslav Shudiehov').textContent).toBe('VS');
    expect(within(panel()).getByText('1 of 4')).toBeDefined();
    // It still takes its slot: the next one goes to the third.
    addToCompare({ id: 'bt_13', number: 13, symbol: 'AAPL' });
    const saved = () => JSON.parse(localStorage.getItem('compare-backtests') ?? '{}') as { slots: unknown[] };
    expect(saved().slots.map((each) => (each as { id: string } | null)?.id ?? null)).toEqual([
      'bt_12',
      'bt_57',
      'bt_13',
      null,
    ]);
    await userEvent.click(within(slot).getByRole('button', { name: 'Dismiss' }));
    expect(within(panel()).queryByText('#57 was unpublished')).toBeNull();
    expect(saved().slots[1]).toBeNull();
  });

  it('drops a deleted backtest of the user’s own at once', () => {
    addToCompare({ id: 'bt_12', number: 12, symbol: 'AAPL' });
    leaveCompare('bt_12');
    expect(JSON.parse(localStorage.getItem('compare-backtests') ?? '{}')).toMatchObject({
      slots: [null, null, null, null],
    });
  });

  it('is added from the Experiments tab of the picker, with its author', async () => {
    stubApi({
      ...sessionWithPreferences(ARSEN),
      ...backtestsAnswer(PAGE),
      ...experimentsAnswer({
        experiments: [experiment(), experiment({ id: 'bt_mine', number: 12, author: { ...TYMUR, mine: true } })],
        counts: { all: 5, mine: 1, people: 3 },
      }),
      'GET /api/v1/backtests/bt_t9n8v7d6a5': { status: 200, body: othersDocument() },
    });
    await renderApp('/backtests/compare');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Add backtests' }));
    const dialog = within(await screen.findByRole('dialog', { name: /^Add backtests/ }));
    const tabs = () => dialog.getAllByRole('tab').map((tab) => tab.textContent);
    await vi.waitFor(() => expect(tabs()).toEqual(['Yours7', 'Experiments4']));
    await user.click(dialog.getByRole('tab', { name: /^Experiments/ }));
    expect(dialog.getByRole('tab', { name: /^Experiments/ }).getAttribute('aria-selected')).toBe('true');
    expect(dialog.getByRole('searchbox', { name: 'Search experiments' }).getAttribute('placeholder')).toBe(
      'Instrument, model or author',
    );
    // Only the other accounts’: the user's own are under Yours.
    const row = (await dialog.findByText('Tymur Tkach · blesk-novol-8.keras')).closest('div')!;
    expect(row.textContent).toBe('#9NVDATTTymur Tkach · blesk-novol-8.keras+41.2 %');
    expect(dialog.queryByText('#12')).toBeNull();
    await user.click(within(row).getByRole('button', { name: 'Add to compare: backtest 9' }));
    expect(JSON.parse(localStorage.getItem('compare-backtests') ?? '{}')).toMatchObject({
      slots: [{ id: 'bt_t9n8v7d6a5', author: 'Tymur Tkach' }, null, null, null],
    });
  });
});
