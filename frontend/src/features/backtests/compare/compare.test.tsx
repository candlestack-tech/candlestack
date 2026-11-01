// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ARSEN, backtest, backtestsAnswer, failedBacktest, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp } from '@/test/app';

import { addToCompare, claimCompare, clearCompare } from './selection';

const FINISHED = [12, 13, 14, 15, 16].map((number) => backtest({ id: `bt_${String(number)}`, number }));
const FAILED = failedBacktest({ id: 'bt_failed', number: 11 });
const RUNNING = backtest({ id: 'bt_running', number: 10, status: 'running', total_return: null, sharpe: null });
const PAGE = {
  backtests: [...FINISHED, FAILED, RUNNING],
  counts: { all: 7, starred: 0, failed: 1 },
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
    expect(within(panel()).getByRole('button', { name: 'Compare' })).toHaveProperty('disabled', false);
    expect(toggle(/^Remove from compare: backtest 12$/).getAttribute('aria-pressed')).toBe('true');
  });

  it('refuses a fifth, and a backtest that failed or still runs, and says why', async () => {
    const user = await openList();
    expect(toggle(/: backtest 11$/)).toHaveProperty('disabled', true);
    expect(toggle(/: backtest 11$/).title).toBe('Failed: no results to compare');
    expect(toggle(/: backtest 10$/).title).toBe('Still running');
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
      .map((link) => link.textContent);
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
