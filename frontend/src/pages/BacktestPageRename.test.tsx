// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ADA, backtest, backtestDocument, problem, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp, viewerIn } from '@/test/app';
import { candles } from '@/test/candles';
import { charts } from '@/test/lightweightCharts';

vi.mock('lightweight-charts', () => import('@/test/lightweightCharts'));

// Rename on a backtest's page (#376, backtest-names MODALS.rename).

const ID = 'bt_7f3k2m9qx4';
const PAGE = `/backtests/${ID}`;
const DOCUMENT = `GET /api/v1/backtests/${ID}`;
// The run's 30-minute candles of AAPL, the whole test period in one request.
const CANDLES = 'GET /api/v1/data/instruments/stock%3AAAPL/candles?timeframe=30m&start=1717372800&end=1717459200';
const TIMES = backtestDocument().results!.series.columns.ts;

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
  charts.reset();
});

type Answers = Parameters<typeof stubApi>[0];

async function openPage(answer: Answers[string], answers: Answers = {}) {
  const fetch = stubApi({
    ...sessionWithPreferences(ADA),
    [DOCUMENT]: answer,
    [CANDLES]: { status: 200, body: candles(TIMES) },
    ...answers,
  });
  const app = await renderApp(PAGE);
  return { ...app, fetch };
}

describe('renaming a backtest', () => {
  // #376: the user's own name replaces the generated one; a toast says what it is called now.
  it('renames the backtest from its header and shows the name with its number', async () => {
    const doc = backtestDocument();
    let current = doc;
    const { fetch } = await openPage(async () => Promise.resolve({ status: 200, body: current }), {
      [`PATCH /api/v1/backtests/${ID}`]: async () => {
        current = { ...doc, backtest: { ...doc.backtest, name: 'Half size' } };
        return Promise.resolve({ status: 200, body: backtest({ name: 'Half size' }) });
      },
    });
    await userEvent.click(await screen.findByRole('button', { name: 'Rename' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Rename Bright Vega' }));
    const field = dialog.getByRole('textbox', { name: 'Name' });
    expect(field).toHaveProperty('value', 'Bright Vega');
    expect(document.activeElement).toBe(field);
    expect(dialog.getByText('11 / 80')).toBeDefined();
    expect(
      dialog.getByText('This is the name CandleStack gave it. Type your own, you can come back to this one later.'),
    ).toBeDefined();
    expect(dialog.getByText('Only you see it until you publish the backtest.')).toBeDefined();
    // The name it has already cannot be saved.
    expect(dialog.getByRole('button', { name: 'Save' })).toHaveProperty('disabled', true);

    await userEvent.clear(field);
    expect(dialog.getByRole('alert').textContent).toBe('A name can’t be empty.');
    expect(dialog.getByRole('button', { name: 'Save' })).toHaveProperty('disabled', true);
    await userEvent.type(field, '  Half size ');
    expect(dialog.queryByRole('alert')).toBeNull();
    expect(dialog.getByText('9 / 80')).toBeDefined();
    expect(dialog.getByText('Bright Vega').parentElement?.textContent).toBe('Generated name Bright Vega');
    expect(dialog.getByRole('button', { name: 'Use the generated name' })).toBeDefined();
    await userEvent.click(dialog.getByRole('button', { name: 'Save' }));

    expect(await screen.findByRole('heading', { level: 1, name: 'Half size' })).toBeDefined();
    expect(fetch.mock.calls.find(([, init]) => init?.method === 'PATCH')?.[1]?.body).toBe('{"name":"Half size"}');
    expect(screen.getByText('#12')).toBeDefined();
    expect(screen.getByRole('status').textContent).toBe('Renamed to Half size');
    expect(screen.queryByRole('dialog')).toBeNull();
    await vi.waitFor(() => expect(document.title).toBe('Half size · CandleStack'));
  });

  it('goes back to the generated name, and saves it as no name of its own', async () => {
    const doc = backtestDocument();
    const named = { ...doc, backtest: { ...doc.backtest, name: 'Half size' } };
    let current = named;
    const { fetch } = await openPage(async () => Promise.resolve({ status: 200, body: current }), {
      [`PATCH /api/v1/backtests/${ID}`]: async () => {
        current = doc;
        return Promise.resolve({ status: 200, body: backtest() });
      },
    });
    await userEvent.click(await screen.findByRole('button', { name: 'Rename' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Rename Half size' }));
    const field = dialog.getByRole('textbox', { name: 'Name' });
    expect(dialog.getByText('Bright Vega').parentElement?.textContent).toBe('Generated name Bright Vega');
    await userEvent.click(dialog.getByRole('button', { name: 'Use the generated name' }));
    expect(field).toHaveProperty('value', 'Bright Vega');
    expect(document.activeElement).toBe(field);
    expect(dialog.queryByRole('button', { name: 'Use the generated name' })).toBeNull();
    expect(dialog.getByText(/^This is the name CandleStack gave it/)).toBeDefined();
    await userEvent.click(dialog.getByRole('button', { name: 'Save' }));

    expect(await screen.findByRole('heading', { level: 1, name: 'Bright Vega' })).toBeDefined();
    expect(fetch.mock.calls.find(([, init]) => init?.method === 'PATCH')?.[1]?.body).toBe('{"name":null}');
    expect(screen.getByRole('status').textContent).toBe('Back to the generated name, Bright Vega');
  });

  it('says which backtest has a name already, and saves again once the name changes', async () => {
    const taken = 'You already have a backtest called Bright Vega (#12). Pick another name.';
    const doc = backtestDocument();
    const thirteen = { ...doc, backtest: { ...doc.backtest, name: 'Silent Orion', generated_name: 'Silent Orion' } };
    const { fetch } = await openPage(
      { status: 200, body: thirteen },
      { [`PATCH /api/v1/backtests/${ID}`]: problem(409, 'name-taken', taken) },
    );
    await userEvent.click(await screen.findByRole('button', { name: 'Rename' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Rename Silent Orion' }));
    const field = dialog.getByRole('textbox', { name: 'Name' });
    await userEvent.clear(field);
    await userEvent.type(field, 'bright vega{Enter}');
    expect((await dialog.findByRole('alert')).textContent).toBe(taken);
    expect(field.getAttribute('aria-invalid')).toBe('true');
    expect(dialog.getByRole('button', { name: 'Save' })).toHaveProperty('disabled', true);
    expect(screen.getByRole('heading', { level: 1, hidden: true }).textContent).toBe('Silent Orion');
    await userEvent.type(field, ' 2');
    expect(dialog.queryByRole('alert')).toBeNull();
    expect(dialog.getByRole('button', { name: 'Save' })).toHaveProperty('disabled', false);
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(1);
  });

  it('warns that a public backtest changes its name in Experiments too', async () => {
    const doc = backtestDocument();
    await openPage({
      status: 200,
      body: { ...doc, backtest: { ...doc.backtest, public: true, published_at: '2026-10-17T09:12:00Z' } },
    });
    await userEvent.click(await screen.findByRole('button', { name: 'Rename' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Rename Bright Vega' }));
    expect(
      dialog.getByText('It’s public. The new name shows in Experiments and in other people’s Compare right away.'),
    ).toBeDefined();
  });
});
