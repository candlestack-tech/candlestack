// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ARSEN,
  backtest,
  backtestDocument,
  backtestsAnswer,
  failedBacktest,
  problem,
  sessionWithPreferences,
  stubApi,
} from '@/test/api';
import { renderApp } from '@/test/app';
import { TYMUR, experiment, experimentsAnswer, othersDocument } from '@/test/experiments';

import { addToCompare, claimCompare, clearCompare, comparedFromOf, leaveCompare, nameInCompare } from './selection';

vi.mock('lightweight-charts', () => import('@/test/lightweightCharts'));

const NAMES: Record<number, string> = {
  8: 'Quiet Lyra',
  9: 'Hidden Quasar',
  10: 'Radiant Altair',
  11: 'Frozen Pulsar',
  12: 'Bright Vega',
  13: 'Silent Orion',
  14: 'Crimson Nova',
  15: 'Golden Rigel',
  16: 'Swift Comet',
};
const named = (number: number) => ({ number, name: NAMES[number], generated_name: NAMES[number] });
const FINISHED = [12, 13, 14, 15, 16].map((number) => backtest({ id: `bt_${String(number)}`, ...named(number) }));
const FAILED = failedBacktest({ id: 'bt_failed', ...named(11) });
const SEEDED = backtest({ id: 'bt_seeded', ...named(9), seeded: true });
const RUNNING = backtest({ id: 'bt_running', ...named(10), status: 'running', total_return: null, sharpe: null });
const QUEUED = backtest({
  id: 'bt_queued',
  ...named(8),
  status: 'queued',
  queue_position: 1,
  total_return: null,
  sharpe: null,
});
const PAGE = {
  backtests: [...FINISHED, FAILED, RUNNING, QUEUED, SEEDED],
  counts: { all: 8, starred: 0, failed: 1, public: 0 },
  models: [],
};
// What a slot says of a backtest of the list: its name over its two instruments, what its model
// reads and the timeframe.
const LINE = 'AAPL and MSFT · Candles · 30m';
const STORAGE_KEY = 'compare-backtests';

const toggle = (label: RegExp) => screen.getByRole('button', { name: label });
const panel = () => screen.getByRole('complementary', { name: 'Compare' });
const saved = () => JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}') as { slots: unknown[] };

async function openList() {
  stubApi({ ...sessionWithPreferences(ARSEN), ...backtestsAnswer(PAGE) });
  await renderApp('/backtests');
  await screen.findByRole('link', { name: 'Bright Vega' });
  return userEvent.setup();
}

// A choice this browser kept before backtests had names, as another tab would write it.
function keepOldChoice(slots: unknown[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ slots, hidden: false, owner: ARSEN.id }));
  window.dispatchEvent(new StorageEvent('storage', { key: STORAGE_KEY }));
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
    await user.click(toggle(/^Add to compare: Bright Vega$/));
    expect(within(panel()).getByText('1 of 4')).toBeDefined();
    expect(within(panel()).getByRole('link', { name: 'Bright Vega' })).toBeDefined();
    expect(within(panel()).getByRole('button', { name: 'Compare' })).toHaveProperty('disabled', true);
    await user.click(toggle(/^Add to compare: Silent Orion$/));
    expect(within(panel()).getByRole('link', { name: 'Compare' }).getAttribute('href')).toBe('/backtests/compare');
    expect(toggle(/^Remove from compare: Bright Vega$/).getAttribute('aria-pressed')).toBe('true');
  });

  it('names a slot by the name over what it ran on, never by its number (#376)', async () => {
    const user = await openList();
    await user.click(toggle(/^Add to compare: Bright Vega$/));
    const slot = within(panel()).getByRole('link', { name: 'Bright Vega' }).closest<HTMLElement>('div.h-11')!;
    expect(slot.textContent).toBe(`Bright Vega${LINE}`);
    expect(within(slot).getByRole('button', { name: 'Remove Bright Vega from compare' })).toBeDefined();
    expect(saved().slots[0]).toMatchObject({ id: 'bt_12', number: 12, name: 'Bright Vega', description: LINE });
  });

  it('refuses a fifth, and a backtest that failed, still runs or waits, and says why', async () => {
    const user = await openList();
    expect(toggle(/: Frozen Pulsar$/)).toHaveProperty('disabled', true);
    expect(toggle(/: Frozen Pulsar$/).title).toBe('Failed: no results to compare');
    expect(toggle(/: Radiant Altair$/).title).toBe('Still running');
    expect(toggle(/: Quiet Lyra$/).title).toBe('Queued: no results yet');
    // Its badge says it waits.
    expect(screen.getByText('Queued')).toBeDefined();
    expect(toggle(/: Hidden Quasar$/)).toHaveProperty('disabled', true);
    expect(toggle(/: Hidden Quasar$/).title).toBe('Test data: no results to compare');
    for (const number of [12, 13, 14, 15]) {
      await user.click(toggle(new RegExp(`^Add to compare: ${NAMES[number] ?? ''}$`)));
    }
    expect(within(panel()).getByText('4 of 4')).toBeDefined();
    expect(toggle(/: Swift Comet$/)).toHaveProperty('disabled', true);
    expect(toggle(/: Swift Comet$/).title).toBe('Up to 4 backtests. Remove one first');
  });

  it('keeps the slot of the others when one leaves, and takes the first free one', async () => {
    const user = await openList();
    for (const number of [12, 13, 14]) {
      await user.click(toggle(new RegExp(`^Add to compare: ${NAMES[number] ?? ''}$`)));
    }
    await user.click(within(panel()).getByRole('button', { name: 'Remove Silent Orion from compare' }));
    await user.click(toggle(/^Add to compare: Golden Rigel$/));
    const order = within(panel())
      .getAllByRole('link')
      .map((link) => link.textContent)
      .filter((text) => text !== 'Compare');
    expect(order).toEqual(['Bright Vega', 'Golden Rigel', 'Crimson Nova']);
  });

  it('folds the panel to a button, and Clear empties it', async () => {
    const user = await openList();
    await user.click(toggle(/^Add to compare: Bright Vega$/));
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
    expect(localStorage.getItem(STORAGE_KEY)).toContain('bt_12');
    claimCompare('usr_b');
    expect(localStorage.getItem(STORAGE_KEY)).not.toContain('bt_12');
  });

  it('remembers the choice in this browser', () => {
    addToCompare({ id: 'bt_12', number: 12, name: 'Bright Vega', description: LINE, symbol: 'AAPL' });
    expect(saved().slots[0]).toEqual({
      id: 'bt_12',
      number: 12,
      name: 'Bright Vega',
      description: LINE,
      symbol: 'AAPL',
    });
  });

  it('takes a new name into its slot, and keeps the line', () => {
    addToCompare({ id: 'bt_12', number: 12, name: 'Bright Vega', description: LINE, symbol: 'AAPL' });
    nameInCompare('bt_12', 'AAPL baseline');
    expect(saved().slots[0]).toMatchObject({ name: 'AAPL baseline', description: LINE });
  });

  it('calls a backtest’s page by the backtest’s name on the way back from the comparison', () => {
    expect(comparedFromOf('/backtests/bt_12', 'Bright Vega')).toBe('Bright Vega');
    expect(comparedFromOf('/backtests/bt_12')).toBe('Backtest');
    expect(comparedFromOf('/backtests/new', 'Bright Vega')).toBe('Back');
  });
});

describe('a choice kept before backtests had names (#376)', () => {
  it('keeps the picks, names them by number and instrument until their documents come, then by name', async () => {
    keepOldChoice([{ id: 'bt_7f3k2m9qx4', number: 12, symbol: 'AAPL' }, null, null, null]);
    stubApi({
      ...sessionWithPreferences(ARSEN),
      ...backtestsAnswer(PAGE),
      'GET /api/v1/backtests/bt_7f3k2m9qx4': { status: 200, body: backtestDocument() },
    });
    await renderApp('/backtests');
    const link = await within(panel()).findByRole('link', { name: 'Bright Vega' });
    expect(link.closest('div.h-11')?.textContent).toBe('Bright VegaAAPL · Candles · 30m');
    expect(saved().slots[0]).toEqual({
      id: 'bt_7f3k2m9qx4',
      number: 12,
      symbol: 'AAPL',
      name: 'Bright Vega',
      description: 'AAPL · Candles · 30m',
    });
  });

  it('says the number and the instrument while the document has not come', async () => {
    keepOldChoice([{ id: 'bt_slow', number: 12, symbol: 'AAPL' }, null, null, null]);
    stubApi({
      ...sessionWithPreferences(ARSEN),
      ...backtestsAnswer(PAGE),
      'GET /api/v1/backtests/bt_slow': problem(500, 'internal', 'Down.'),
    });
    await renderApp('/backtests');
    const link = await within(panel()).findByRole('link', { name: 'Backtest #12' });
    expect(link.closest('div.h-11')?.textContent).toBe('Backtest #12AAPL');
  });
});

describe('another account’s backtest in the comparison', () => {
  it('leaves the choice once unpublished but keeps its slot, saying so, until dismissed', async () => {
    claimCompare(ARSEN.id);
    addToCompare({ id: 'bt_12', number: 12, name: 'Bright Vega', description: LINE, symbol: 'AAPL' });
    addToCompare({
      id: 'bt_57',
      number: 57,
      name: 'Distant Lyra',
      description: 'BTCUSDT · Kagi · 30m',
      symbol: 'BTCUSDT',
      author: 'Vladyslav Shudiehov',
    });
    stubApi({
      ...sessionWithPreferences(ARSEN),
      ...backtestsAnswer(PAGE),
      'GET /api/v1/backtests/bt_57': problem(404, 'not-found', 'Not found.'),
    });
    await renderApp('/backtests');
    const slot = (await within(panel()).findByText('Distant Lyra was unpublished')).closest<HTMLElement>('div.h-11')!;
    expect(slot.getAttribute('title')).toBe('Vladyslav Shudiehov unpublished it');
    expect(slot.textContent).toBe('VSDistant Lyra was unpublishedBTCUSDT · Kagi · 30m');
    expect(within(slot).getByTitle('Vladyslav Shudiehov').textContent).toBe('VS');
    expect(within(panel()).getByText('1 of 4')).toBeDefined();
    // It still takes its slot: the next one goes to the third.
    addToCompare({ id: 'bt_13', number: 13, symbol: 'AAPL' });
    expect(saved().slots.map((each) => (each as { id: string } | null)?.id ?? null)).toEqual([
      'bt_12',
      'bt_57',
      'bt_13',
      null,
    ]);
    await userEvent.click(within(slot).getByRole('button', { name: 'Dismiss' }));
    expect(within(panel()).queryByText('Distant Lyra was unpublished')).toBeNull();
    expect(saved().slots[1]).toBeNull();
  });

  it('shows the name its author gave it since it was added', async () => {
    claimCompare(ARSEN.id);
    addToCompare({
      id: 'bt_t9n8v7d6a5',
      number: 9,
      name: 'Velvet Deneb',
      description: 'AAPL · Candles · 30m',
      symbol: 'AAPL',
      author: 'Tymur Tkach',
    });
    const renamed = othersDocument();
    renamed.backtest.name = 'NVDA momentum';
    stubApi({
      ...sessionWithPreferences(ARSEN),
      ...backtestsAnswer(PAGE),
      'GET /api/v1/backtests/bt_t9n8v7d6a5': { status: 200, body: renamed },
    });
    await renderApp('/backtests');
    expect(await within(panel()).findByRole('link', { name: 'NVDA momentum' })).toBeDefined();
    expect(saved().slots[0]).toMatchObject({ name: 'NVDA momentum' });
  });

  it('drops a deleted backtest of the user’s own at once', () => {
    addToCompare({ id: 'bt_12', number: 12, symbol: 'AAPL' });
    leaveCompare('bt_12');
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}')).toMatchObject({
      slots: [null, null, null, null],
    });
  });

  it('is added from the Experiments tab of the picker, with its author', async () => {
    stubApi({
      ...sessionWithPreferences(ARSEN),
      ...backtestsAnswer(PAGE),
      ...experimentsAnswer({
        experiments: [
          experiment(),
          experiment({ id: 'bt_mine', number: 12, name: 'Bright Vega', author: { ...TYMUR, mine: true } }),
        ],
        counts: { all: 5, mine: 1, people: 3 },
      }),
      'GET /api/v1/backtests/bt_t9n8v7d6a5': { status: 200, body: othersDocument() },
    });
    await renderApp('/backtests/compare');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Add backtests' }));
    const dialog = within(await screen.findByRole('dialog', { name: /^Add backtests/ }));
    const tabs = () => dialog.getAllByRole('tab').map((tab) => tab.textContent);
    await vi.waitFor(() => expect(tabs()).toEqual(['Yours8', 'Experiments4']));
    expect(dialog.getByRole('searchbox', { name: 'Search backtests' }).getAttribute('placeholder')).toBe(
      'Name, instrument or model',
    );
    await user.click(dialog.getByRole('tab', { name: /^Experiments/ }));
    expect(dialog.getByRole('tab', { name: /^Experiments/ }).getAttribute('aria-selected')).toBe('true');
    expect(dialog.getByRole('searchbox', { name: 'Search experiments' }).getAttribute('placeholder')).toBe(
      'Name, instrument, model or author',
    );
    // Only the other accounts’: the user's own are under Yours. One line: the name, then its number.
    const row = (await dialog.findByText('Tymur Tkach · blesk-novol-8.keras')).closest<HTMLElement>('div.grid')!;
    expect(row.textContent).toBe('Velvet Deneb#9NVDATTTymur Tkach · blesk-novol-8.keras+41.2 %');
    expect(dialog.queryByText('Bright Vega')).toBeNull();
    await user.click(within(row).getByRole('button', { name: 'Add to compare: Velvet Deneb' }));
    expect(saved()).toMatchObject({
      slots: [{ id: 'bt_t9n8v7d6a5', name: 'Velvet Deneb', author: 'Tymur Tkach' }, null, null, null],
    });
  });
});
