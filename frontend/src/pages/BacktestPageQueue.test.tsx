// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { BacktestDocument } from '@/api';
import { ARSEN, problem, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp, viewerIn } from '@/test/app';
import { queuedDocument, runningDocument } from '@/test/runs';

// A queued backtest's page (#400): its place in the queue, and Cancel with a confirm.

const ID = 'bt_7f3k2m9qx4';
const PAGE = `/backtests/${ID}`;
const DOCUMENT = `GET /api/v1/backtests/${ID}`;
const CANCEL = `POST /api/v1/backtests/${ID}/cancel`;

// The API's answer to Cancel: failed, its steps never begun.
const cancelled = (): BacktestDocument => {
  const doc = queuedDocument(2);
  return {
    ...doc,
    backtest: {
      ...doc.backtest,
      status: 'failed',
      queue_position: null,
      error: 'You cancelled the backtest before it ran.',
      finished_at: '2026-10-17T16:00:00Z',
    },
  };
};

beforeEach(() => {
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

type Answers = Parameters<typeof stubApi>[0];

async function openPage(answers: Answers) {
  const all: Answers = { ...sessionWithPreferences(ARSEN), ...answers };
  const fetch = stubApi(all);
  const app = await renderApp(PAGE);
  return { ...app, fetch, all };
}

const cancels = (fetch: ReturnType<typeof stubApi>) =>
  fetch.mock.calls.filter(([path, init]) => path === `/api/v1/backtests/${ID}/cancel` && init?.method === 'POST')
    .length;

describe("a queued backtest's page", () => {
  it('says how many are ahead in its badge and when it starts', async () => {
    await openPage({ [DOCUMENT]: { status: 200, body: queuedDocument(2) } });
    expect(await screen.findByText('Queued · 2 ahead')).toBeDefined();
    expect(screen.getByText('It starts when the 2 backtests ahead of it end. You can close the page.')).toBeDefined();
  });

  it('asks before it cancels, then shows it failed as cancelled and refreshes the lists', async () => {
    const { fetch, queryClient } = await openPage({
      [DOCUMENT]: { status: 200, body: queuedDocument(2) },
      [CANCEL]: { status: 200, body: cancelled() },
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Cancel backtest' }));
    let dialog = within(await screen.findByRole('dialog', { name: 'Cancel Bright Vega?' }));
    expect(
      dialog.getByText('It leaves your queue and never runs. It stays in your Backtests as failed.'),
    ).toBeDefined();
    // Keep it changes nothing.
    await user.click(dialog.getByRole('button', { name: 'Keep it' }));
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(cancels(fetch)).toBe(0);

    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    await user.click(screen.getByRole('button', { name: 'Cancel backtest' }));
    dialog = within(await screen.findByRole('dialog', { name: 'Cancel Bright Vega?' }));
    await user.click(dialog.getByRole('button', { name: 'Cancel backtest' }));
    expect(await screen.findByText('Bright Vega was cancelled')).toBeDefined();
    expect(cancels(fetch)).toBe(1);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByText('Failed')).toBeDefined();
    expect(screen.getByRole('alert').textContent).toBe('You cancelled the backtest before it ran.');
    expect(screen.queryByRole('button', { name: 'Cancel backtest' })).toBeNull();
    // The lists, the dashboard and the models show it failed.
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['account', 'backtests'] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['account', 'dashboard'] });
  });

  it('says so when it began to run meanwhile, and shows it running', async () => {
    const { all, queryClient } = await openPage({
      [DOCUMENT]: { status: 200, body: queuedDocument() },
      [CANCEL]: problem(
        409,
        'backtest-not-queued',
        'The backtest is running already, so it can no longer be cancelled.',
      ),
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Cancel backtest' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Cancel Bright Vega?' }));
    all[DOCUMENT] = { status: 200, body: runningDocument() };
    await user.click(dialog.getByRole('button', { name: 'Cancel backtest' }));
    expect((await dialog.findByRole('alert')).textContent).toBe(
      'The backtest is running already, so it can no longer be cancelled.',
    );
    // Its page is asked for again: it runs now, without Cancel.
    await vi.waitFor(() =>
      expect(queryClient.getQueryData<BacktestDocument>(['account', 'backtest', ID])?.backtest.status).toBe('running'),
    );
    await user.click(dialog.getByRole('button', { name: 'Keep it' }));
    expect(await screen.findByText(/^Running · /)).toBeDefined();
    expect(screen.queryByRole('button', { name: 'Cancel backtest' })).toBeNull();
  });

  it('cannot be closed while the cancel runs, and closes with the toast once it answers', async () => {
    let answer: ((value: { status: number; body: unknown }) => void) | undefined;
    await openPage({
      [DOCUMENT]: { status: 200, body: queuedDocument(2) },
      [CANCEL]: () => new Promise((resolve) => (answer = resolve)),
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Cancel backtest' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Cancel Bright Vega?' }));
    await user.click(dialog.getByRole('button', { name: 'Cancel backtest' }));
    await vi.waitFor(() => expect(dialog.getByRole('button', { name: 'Keep it' }).hasAttribute('disabled')).toBe(true));
    await user.keyboard('{Escape}');
    expect(screen.getByRole('dialog')).toBeDefined();
    answer?.({ status: 200, body: cancelled() });
    expect(await screen.findByText('Bright Vega was cancelled')).toBeDefined();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('turns Cancel off in the open dialog once the page finds it running', async () => {
    const { all, fetch, queryClient } = await openPage({ [DOCUMENT]: { status: 200, body: queuedDocument() } });
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Cancel backtest' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Cancel Bright Vega?' }));
    all[DOCUMENT] = { status: 200, body: runningDocument() };
    await queryClient.refetchQueries({ queryKey: ['account', 'backtest', ID] });
    expect((await dialog.findByRole('alert')).textContent).toBe(
      'It has left the queue, so it can no longer be cancelled.',
    );
    expect(dialog.getByRole('button', { name: 'Cancel backtest' }).hasAttribute('disabled')).toBe(true);
    expect(cancels(fetch)).toBe(0);
  });

  it('has no Cancel once it runs', async () => {
    await openPage({ [DOCUMENT]: { status: 200, body: runningDocument() } });
    expect(await screen.findByText(/^Running · /)).toBeDefined();
    expect(screen.queryByRole('button', { name: 'Cancel backtest' })).toBeNull();
  });
});
