// @vitest-environment jsdom
import { cleanup, screen } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { addToCompare, clearCompare } from '@/features/backtests/compare/selection';
import { ADA, backtestDocument, backtestsAnswer, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp } from '@/test/app';
import { compareAnswers, compareMissing } from '@/test/compare';
import { othersDocument } from '@/test/experiments';
import { charts } from '@/test/lightweightCharts';

vi.mock('lightweight-charts', () => import('@/test/lightweightCharts'));

const NAMES: Record<number, string> = { 12: 'Bright Vega', 11: 'Frozen Pulsar', 10: 'Crimson Nova' };

// A finished backtest of the user's, with its number in its ID.
function doc(number: number) {
  const base = backtestDocument();
  const name = NAMES[number] ?? `Backtest ${String(number)}`;
  return backtestDocument({
    backtest: { ...base.backtest, id: `bt_${String(number)}`, number, name, generated_name: name },
  });
}

const BRIGHT = doc(12);
const FROZEN = doc(11);
const CRIMSON = doc(10);

const LISTS = {
  ...sessionWithPreferences(ADA),
  ...backtestsAnswer({ backtests: [], counts: { all: 0, starred: 0, failed: 0, public: 0 }, models: [] }),
};

const choose = (...docs: ReturnType<typeof doc>[]) => {
  docs.forEach(({ backtest }) => {
    addToCompare({ id: backtest.id, number: backtest.number, name: backtest.name, symbol: 'AAPL' });
  });
};

const kept = () => (JSON.parse(localStorage.getItem('compare-backtests') ?? '{}') as { slots: unknown[] }).slots;

beforeEach(() => {
  clearCompare();
  charts.reset();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the compare page’s address', () => {
  it('opens the backtests a link names, in its order, in place of the choice', async () => {
    choose(CRIMSON);
    const fetch = stubApi({ ...LISTS, ...compareAnswers([FROZEN, BRIGHT]) });
    const { router } = await renderApp('/backtests/compare?ids=bt_11,bt_12');
    expect(await screen.findByRole('link', { name: 'Frozen Pulsar' })).toBeDefined();
    expect(screen.getByRole('link', { name: 'Bright Vega' })).toBeDefined();
    expect(screen.queryByRole('link', { name: 'Crimson Nova' })).toBeNull();
    expect(screen.getByText('2 of 4 backtests')).toBeDefined();
    expect(kept()).toMatchObject([{ id: 'bt_11', number: 11 }, { id: 'bt_12', number: 12 }, null, null]);
    // The choice before was never compared.
    expect(fetch.mock.calls.map(([path]) => path)).not.toContain('/api/v1/compare?ids=bt_10');
    expect(router.state.location.search).toEqual({ ids: 'bt_11,bt_12' });
  });

  it('writes the choice into the address, and every change of it', async () => {
    choose(BRIGHT, FROZEN);
    stubApi({ ...LISTS, ...compareAnswers([BRIGHT, FROZEN]) });
    const { router } = await renderApp('/backtests/compare');
    await vi.waitFor(() => {
      expect(router.state.location.search).toEqual({ ids: 'bt_12,bt_11' });
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Remove Frozen Pulsar from compare' }));
    await vi.waitFor(() => {
      expect(router.state.location.search).toEqual({ ids: 'bt_12' });
    });
    await user.click(screen.getByRole('button', { name: 'Clear all' }));
    await vi.waitFor(() => {
      expect(router.state.location.search).toEqual({});
    });
    // In place: the history holds one entry, the page.
    expect(router.history.length).toBe(1);
  });

  it('drops a backtest of a link that does not exist, from the choice and the address', async () => {
    const missing = doc(99);
    stubApi({ ...LISTS, ...compareMissing([BRIGHT, missing], 'bt_99'), ...compareAnswers([BRIGHT]) });
    const { router } = await renderApp('/backtests/compare?ids=bt_12,bt_99');
    expect(await screen.findByText('1 of 4 backtests. Add one more to compare')).toBeDefined();
    await vi.waitFor(() => {
      expect(router.state.location.search).toEqual({ ids: 'bt_12' });
    });
    expect(kept()).toMatchObject([{ id: 'bt_12' }, null, null, null]);
  });

  it('counts another account’s backtest of a link as from Experiments', async () => {
    const theirs = othersDocument();
    stubApi({ ...LISTS, ...compareAnswers([BRIGHT, theirs]) });
    await renderApp(`/backtests/compare?ids=bt_12,${theirs.backtest.id}`);
    expect(await screen.findByText('2 of 4 backtests · 1 yours, 1 from Experiments')).toBeDefined();
    expect(kept()).toMatchObject([{ id: 'bt_12' }, { id: theirs.backtest.id, author: 'Alan Turing' }, null, null]);
  });

  it('keeps the choice and writes it when the address names nothing valid', async () => {
    choose(BRIGHT);
    stubApi({ ...LISTS, ...compareAnswers([BRIGHT]) });
    const { router } = await renderApp('/backtests/compare?ids=nothing');
    expect(await screen.findByText('1 of 4 backtests. Add one more to compare')).toBeDefined();
    await vi.waitFor(() => {
      expect(router.state.location.search).toEqual({ ids: 'bt_12' });
    });
  });

  it('back on the address it wrote, keeps a choice changed since', async () => {
    choose(BRIGHT, FROZEN);
    stubApi({
      ...LISTS,
      ...compareAnswers([BRIGHT, FROZEN]),
      [`GET /api/v1/backtests/${BRIGHT.backtest.id}`]: { status: 200, body: BRIGHT },
    });
    const { router } = await renderApp('/backtests/compare');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('link', { name: 'Bright Vega' }));
    await user.click(await screen.findByRole('button', { name: 'Added to compare' }));
    await user.click(screen.getByRole('button', { name: '← Compare backtests' }));
    expect(await screen.findByText('1 of 4 backtests. Add one more to compare')).toBeDefined();
    expect(screen.queryByRole('link', { name: 'Bright Vega' })).toBeNull();
    await vi.waitFor(() => {
      expect(router.state.location.search).toEqual({ ids: 'bt_11' });
    });
  });
});
