// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { addToCompare, clearCompare } from '@/features/backtests/compare/selection';
import { ADA, backtest, backtestsAnswer, failedBacktest, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp, viewerIn } from '@/test/app';

// Deleting several backtests at once from the Backtests page: a checkbox per row and one for the
// page, Delete with the count, the same confirm as a backtest's page.

const TWELVE = backtest();
const ELEVEN = failedBacktest({ id: 'bt_a2b3c4d5e6', number: 11, name: 'Frozen Pulsar' });
const TEN = backtest({ id: 'bt_q7r8s9t2u3', number: 10, name: 'Crimson Nova' });
const PUBLISHED = backtest({ id: 'bt_p1q2r3s4t5', number: 9, name: 'Silent Orion', public: true });
const RUNNING = backtest({ id: 'bt_r1u2n3n4g5', number: 8, name: 'Quiet Lyra', status: 'running', total_return: null });
const COUNTS = { all: 3, starred: 0, failed: 1, public: 0 };
const BULK = 'POST /api/v1/backtests/delete';

beforeEach(() => {
  clearCompare();
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

type Answers = Parameters<typeof stubApi>[0];

async function openList(backtests = [TWELVE, ELEVEN, TEN], answers: Answers = {}) {
  const fetch = stubApi({
    ...sessionWithPreferences(ADA),
    ...backtestsAnswer({ backtests, counts: COUNTS, models: [] }),
    ...answers,
  });
  const app = await renderApp('/backtests');
  await screen.findByText(`#${String(backtests[0]!.number)}`);
  return { ...app, fetch, user: userEvent.setup() };
}

const box = (name: string) => screen.getByRole<HTMLInputElement>('checkbox', { name });
const all = () => box('Select all on this page');
const bulkPosts = (fetch: ReturnType<typeof stubApi>) =>
  fetch.mock.calls.filter(([path, init]) => path === '/api/v1/backtests/delete' && init?.method === 'POST');

async function openDialog(user: ReturnType<typeof userEvent.setup>, label: string, title: string) {
  await user.click(screen.getByRole('button', { name: label }));
  return within(await screen.findByRole('dialog', { name: title }));
}

describe('deleting backtests from the list', () => {
  it('chooses rows one by one or the whole page, and offers Delete with the count', async () => {
    const { user } = await openList();
    expect(screen.queryByRole('button', { name: /^Delete/ })).toBeNull();

    await user.click(box('Select Bright Vega'));
    expect(box('Select Bright Vega')).toHaveProperty('checked', true);
    expect(all()).toHaveProperty('indeterminate', true);
    expect(screen.getByRole('button', { name: 'Delete 1' })).toBeDefined();

    await user.click(all());
    expect(['Bright Vega', 'Frozen Pulsar', 'Crimson Nova'].map((name) => box(`Select ${name}`).checked)).toEqual([
      true,
      true,
      true,
    ]);
    expect(all()).toHaveProperty('indeterminate', false);
    expect(screen.getByRole('button', { name: 'Delete 3' })).toBeDefined();

    // Once all are chosen, the box for the page chooses none.
    await user.click(all());
    expect(screen.queryByRole('button', { name: /^Delete/ })).toBeNull();
  });

  it('says what goes and that it cannot be undone, and Cancel sends nothing', async () => {
    const { user, fetch } = await openList();
    await user.click(box('Select Bright Vega'));
    await user.click(box('Select Frozen Pulsar'));
    const dialog = await openDialog(user, 'Delete 2', 'Delete 2 backtests?');
    expect(
      dialog.getByText('They go from your Backtests with their results and trades. This cannot be undone.'),
    ).toBeDefined();
    await user.click(dialog.getByRole('button', { name: 'Cancel' }));
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(bulkPosts(fetch)).toHaveLength(0);
    expect(box('Select Bright Vega')).toHaveProperty('checked', true);
  });

  it('says when published ones leave Experiments and running ones are stopped', async () => {
    const { user } = await openList([PUBLISHED, RUNNING, TWELVE]);
    await user.click(all());
    const dialog = await openDialog(user, 'Delete 3', 'Delete 3 backtests?');
    expect(
      dialog.getByText(
        'They go from your Backtests with their results and trades. Running ones are stopped. Published ones also leave Experiments and the Compare of anyone who added them. This cannot be undone.',
      ),
    ).toBeDefined();
  });

  it('posts the IDs, clears the choice, leaves Compare, refreshes the lists and says so', async () => {
    addToCompare({ id: TWELVE.id, number: 12, name: 'Bright Vega', symbol: 'AAPL' });
    const { user, fetch, queryClient } = await openList(undefined, {
      [BULK]: { status: 200, body: { deleted: [TWELVE.id, TEN.id], count: 2, not_found: [] } },
    });
    await user.click(box('Select Bright Vega'));
    await user.click(box('Select Crimson Nova'));
    const dialog = await openDialog(user, 'Delete 2', 'Delete 2 backtests?');
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    await user.click(dialog.getByRole('button', { name: 'Delete' }));

    expect(await screen.findByRole('status')).toHaveProperty('textContent', '2 backtests were deleted');
    expect(bulkPosts(fetch).map(([, init]) => init?.body)).toEqual([`{"ids":["${TWELVE.id}","${TEN.id}"]}`]);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByRole('button', { name: /^Delete/ })).toBeNull();
    expect(localStorage.getItem('compare-backtests') ?? '').not.toContain(TWELVE.id);
    for (const queryKey of [
      ['account', 'backtests'],
      ['account', 'dashboard'],
      ['account', 'experiments'],
      ['account', 'models'],
    ]) {
      expect(invalidate).toHaveBeenCalledWith({ queryKey });
    }
  });

  it('deletes one chosen row by its own address and names it in the toast', async () => {
    const { user, fetch } = await openList(undefined, {
      [`DELETE /api/v1/backtests/${ELEVEN.id}`]: { status: 204 },
    });
    await user.click(box('Select Frozen Pulsar'));
    const dialog = await openDialog(user, 'Delete 1', 'Delete Frozen Pulsar?');
    expect(dialog.getByText('It goes from your Backtests. This cannot be undone.')).toBeDefined();
    await user.click(dialog.getByRole('button', { name: 'Delete' }));
    expect(await screen.findByRole('status')).toHaveProperty('textContent', 'Frozen Pulsar was deleted');
    expect(fetch).toHaveBeenCalledWith(`/api/v1/backtests/${ELEVEN.id}`, expect.objectContaining({ method: 'DELETE' }));
  });

  it('stays open with the reason when the API cannot delete, keeping the choice', async () => {
    const { user } = await openList(undefined, {
      [BULK]: { status: 503, body: { detail: 'Down for a minute.' } },
    });
    await user.click(all());
    const dialog = await openDialog(user, 'Delete 3', 'Delete 3 backtests?');
    await user.click(dialog.getByRole('button', { name: 'Delete' }));
    expect((await dialog.findByRole('alert')).textContent).toBe('Down for a minute.');
    await user.click(dialog.getByRole('button', { name: 'Cancel' }));
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.getByRole('button', { name: 'Delete 3' })).toBeDefined();
  });
});
