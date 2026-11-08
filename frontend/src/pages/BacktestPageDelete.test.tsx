// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { BacktestDocument } from '@/api';
import { addToCompare, clearCompare } from '@/features/backtests/compare/selection';
import {
  ARSEN,
  backtestDocument,
  backtestsAnswer,
  dashboardAnswer,
  problem,
  sessionWithPreferences,
  stubApi,
} from '@/test/api';
import { renderApp, viewerIn } from '@/test/app';
import { candles } from '@/test/candles';
import { othersDocument } from '@/test/experiments';
import { charts } from '@/test/lightweightCharts';
import { queuedDocument, runningDocument } from '@/test/runs';

vi.mock('lightweight-charts', () => import('@/test/lightweightCharts'));

// Delete on a backtest's page: the user's own, whatever its status, after a confirm.

const ID = 'bt_7f3k2m9qx4';
const OTHERS = 'bt_t9n8v7d6a5';
const PAGE = `/backtests/${ID}`;
const DOCUMENT = `GET /api/v1/backtests/${ID}`;
const DELETE = `DELETE /api/v1/backtests/${ID}`;
const CANDLES = 'GET /api/v1/data/instruments/stock%3AAAPL/candles?timeframe=30m&start=1717372800&end=1717459200';
const TIMES = backtestDocument().results!.series.columns.ts;

const published = (): BacktestDocument => {
  const doc = backtestDocument();
  return { ...doc, backtest: { ...doc.backtest, public: true, published_at: '2026-10-17T14:41:00Z' } };
};

beforeEach(() => {
  clearCompare();
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-17T16:00:00Z'));
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

async function openPage(answers: Answers, url = PAGE) {
  const fetch = stubApi({
    ...sessionWithPreferences(ARSEN),
    [CANDLES]: { status: 200, body: candles(TIMES) },
    ...backtestsAnswer({ backtests: [] }),
    ...dashboardAnswer(),
    ...answers,
  });
  const app = await renderApp(url);
  await screen.findByRole('heading', { level: 1 });
  return { ...app, fetch, user: userEvent.setup() };
}

async function openDelete(doc: BacktestDocument, answers: Answers = {}) {
  const opened = await openPage({ [DOCUMENT]: { status: 200, body: doc }, ...answers });
  await opened.user.click(screen.getByRole('button', { name: 'Delete' }));
  const dialog = within(await screen.findByRole('dialog', { name: `Delete ${doc.backtest.name}?` }));
  return { ...opened, dialog };
}

const deletes = (fetch: ReturnType<typeof stubApi>) =>
  fetch.mock.calls.filter(([path, init]) => path === `/api/v1/backtests/${ID}` && init?.method === 'DELETE').length;

describe('deleting a backtest', () => {
  it('says what goes and that it cannot be undone, and Cancel changes nothing', async () => {
    const { user, dialog, fetch } = await openDelete(backtestDocument());
    expect(
      dialog.getByText('It goes from your Backtests with its results and trades. This cannot be undone.'),
    ).toBeDefined();
    await user.click(dialog.getByRole('button', { name: 'Cancel' }));
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(deletes(fetch)).toBe(0);
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Bright Vega');
  });

  it('deletes it, leaves Compare, refreshes the lists and goes to Backtests', async () => {
    addToCompare({ id: ID, number: 12, name: 'Bright Vega', symbol: 'AAPL' });
    const { user, dialog, fetch, router, queryClient } = await openDelete(backtestDocument(), {
      [DELETE]: { status: 204 },
    });
    const compared = () => localStorage.getItem('compare-backtests') ?? '';
    expect(compared()).toContain(ID);
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    await user.click(dialog.getByRole('button', { name: 'Delete' }));

    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/backtests'));
    expect(deletes(fetch)).toBe(1);
    for (const queryKey of [
      ['account', 'backtests'],
      ['account', 'dashboard'],
      ['account', 'experiments'],
      ['account', 'models'],
    ]) {
      expect(invalidate).toHaveBeenCalledWith({ queryKey });
    }
    expect(compared()).not.toContain(ID);
    // Its document is forgotten, so going back asks the API again.
    await vi.waitFor(() => expect(queryClient.getQueryData(['account', 'backtest', ID])).toBeUndefined());
  });

  it('goes to Backtests too when it was deleted meanwhile', async () => {
    const { user, dialog, router } = await openDelete(backtestDocument(), {
      [DELETE]: problem(404, 'not-found', 'You have no backtest with this ID.'),
    });
    await user.click(dialog.getByRole('button', { name: 'Delete' }));
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/backtests'));
  });

  it('stays open with the reason when the API cannot delete', async () => {
    const { user, dialog, router } = await openDelete(backtestDocument(), {
      [DELETE]: { status: 503, body: { detail: 'Down for a minute.' } },
    });
    await user.click(dialog.getByRole('button', { name: 'Delete' }));
    expect((await dialog.findByRole('alert')).textContent).toBe('Down for a minute.');
    expect(router.state.location.pathname).toBe(PAGE);
  });

  it('cannot be closed while the request runs', async () => {
    let answer: ((value: { status: number; body?: unknown }) => void) | undefined;
    const { user, dialog, router } = await openDelete(backtestDocument(), {
      [DELETE]: () => new Promise((resolve) => (answer = resolve)),
    });
    await user.click(dialog.getByRole('button', { name: 'Delete' }));
    await vi.waitFor(() => expect(dialog.getByRole('button', { name: 'Cancel' }).hasAttribute('disabled')).toBe(true));
    await user.keyboard('{Escape}');
    expect(screen.getByRole('dialog')).toBeDefined();
    answer?.({ status: 204 });
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/backtests'));
  });

  it.each([
    [
      'a published one leaves Experiments',
      published(),
      'It goes from your Backtests with its results and trades. It also leaves Experiments and the Compare of anyone who added it. This cannot be undone.',
    ],
    [
      'a running one is stopped',
      runningDocument(),
      'It stops running and goes from your Backtests. This cannot be undone.',
    ],
    [
      'a queued one leaves the queue',
      queuedDocument(2),
      'It leaves your queue and goes from your Backtests. This cannot be undone.',
    ],
  ])('says that %s', async (_, doc, text) => {
    const { dialog } = await openDelete(doc);
    expect(dialog.getByText(text)).toBeDefined();
  });

  it("is not offered on another account's published backtest", async () => {
    await openPage(
      { [`GET /api/v1/backtests/${OTHERS}`]: { status: 200, body: othersDocument() } },
      `/backtests/${OTHERS}`,
    );
    expect(screen.getByRole('button', { name: 'Download JSON' })).toBeDefined();
    expect(screen.queryByRole('button', { name: 'Delete' })).toBeNull();
  });
});
