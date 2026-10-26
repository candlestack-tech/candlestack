// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ARSEN,
  backtest,
  dashboardAnswer,
  failedBacktest,
  model,
  modelsAnswer,
  sessionWithPreferences,
  stubApi,
} from '@/test/api';
import { renderApp, viewerIn } from '@/test/app';

const TWELVE = backtest({ starred: true, sharpe: 1.344 });
const ELEVEN = failedBacktest({ id: 'bt_a2b3c4d5e6', number: 11, starred: true, created_at: '2026-10-16T22:10:00Z' });
const TEN = backtest({
  id: 'bt_q7r8s9t2u3',
  number: 10,
  representation: 'renko',
  instruments: ['AAPL', 'MSFT', 'NVDA', 'SPY'].map((symbol) => ({ symbol, market: 'stock' as const })),
  total_return: 0.965,
  sharpe: 0.5,
  max_drawdown: -0.08,
  win_rate: 0.556,
  equity: null,
  created_at: '2026-10-12T23:44:00Z',
});
const FULL = {
  counts: { all: 3, starred: 2, failed: 1 },
  last: [TWELVE, ELEVEN, TEN],
  best: { sharpe: [TWELVE, TEN], total_return: [TEN, TWELVE], max_drawdown: [TEN, TWELVE], win_rate: [TEN, TWELVE] },
  starred: { backtests: [TWELVE, ELEVEN], total: 2 },
};
const LSTM = model({
  id: 'mdl_a2b3c4d5e6',
  name: 'lstm-signals.keras',
  starred: true,
  backtest_count: 1,
  best_sharpe: 0.79,
});
const DRAFT = model({ id: 'mdl_q7r8s9t2u3', name: 'draft.keras', starred: true, input_shape: null, backtest_count: 3 });
const TSMIXER = model({ starred: true, backtest_count: 10, best_sharpe: 1.34 });

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

const heading = () => screen.getByRole('heading', { level: 1 }).textContent;
const widget = (name: string) => within(screen.getByRole('region', { name }));
const widgets = () => screen.queryAllByRole('region').map((region) => region.querySelector('h2')?.textContent);
const rowsOf = (name: string) =>
  widget(name)
    .getAllByRole('row')
    .slice(1)
    .map((row) =>
      within(row)
        .getAllByRole('cell')
        .map((cell) => cell.textContent),
    );

async function open(answers: object) {
  stubApi({ ...sessionWithPreferences(ARSEN), ...answers });
  return renderApp('/dashboard');
}

describe('the dashboard of a new account', () => {
  it('welcomes the user with two steps to start and nothing else', async () => {
    await open({ ...dashboardAnswer(), ...modelsAnswer([]) });
    await vi.waitFor(() => expect(heading()).toBe('Welcome, Arsen Labovich'));
    expect(screen.getByText('Your account is ready.')).toBeDefined();
    expect(widgets()).toEqual(['Get started']);
    expect(widget('Get started').getByText('2 steps')).toBeDefined();
    expect(
      widget('Get started')
        .getAllByRole('button')
        .map((step) => step.textContent),
    ).toEqual([
      '1Build your first backtestPick the data, a representation, a model and a rule→',
      '2Upload your own modelWe check that it is a real model with trained weights→',
    ]);
    expect(screen.queryByRole('button', { name: /Compare backtests/ })).toBeNull();
  });

  it('opens New backtest and Upload model from the steps', async () => {
    await open({ ...dashboardAnswer(), ...modelsAnswer([]) });
    await userEvent.click(await screen.findByRole('button', { name: /^2Upload your own model/ }));
    expect(await screen.findByRole('dialog', { name: 'Add a model' })).toBeDefined();
    await userEvent.keyboard('{Escape}');
    await userEvent.click(screen.getByRole('button', { name: /^1Build your first backtest/ }));
    expect(await screen.findByRole('heading', { level: 1, name: 'New backtest' })).toBeDefined();
    expect(screen.getByRole('link', { name: '← Dashboard' })).toBeDefined();
  });

  it('shows Starred models once a model is starred', async () => {
    await open({ ...dashboardAnswer(), ...modelsAnswer([LSTM]) });
    await vi.waitFor(() => expect(widgets()).toEqual(['Get started', 'Starred models']));
    expect(widget('Starred models').getByText('lstm-signals.keras')).toBeDefined();
  });

  it('waits with the greeting until the dashboard has loaded, and says why it could not', async () => {
    await open({ 'GET /api/v1/dashboard': () => new Promise(() => {}), ...modelsAnswer([]) });
    expect(heading()).toBe('\u00a0');
    cleanup();

    await open({ 'GET /api/v1/dashboard': { status: 503, body: { detail: 'Down.' } }, ...modelsAnswer([]) });
    await vi.waitFor(() => expect(heading()).toBe('Welcome back, Arsen Labovich'));
    expect(screen.getByText('Down.')).toBeDefined();
  });
});

describe('the dashboard of an account with backtests', () => {
  it('welcomes the user back with the quick actions and every widget', async () => {
    await open({ ...dashboardAnswer(FULL), ...modelsAnswer([LSTM]) });
    await vi.waitFor(() => expect(heading()).toBe('Welcome back, Arsen Labovich'));
    expect(screen.getByRole('heading', { level: 1 }).parentElement?.querySelector('p')).toBeNull();
    expect(
      ['New backtest', 'Upload model', 'Compare backtests'].map(
        (name) => screen.getByRole('button', { name: new RegExp(`^${name}\\s*\\S`) }).textContent,
      ),
    ).toEqual([
      'New backtestPick the data, a model and a rule',
      'Upload modelKerasONNX',
      'Compare backtestsTwo side by side on one chart',
    ]);
    await vi.waitFor(() =>
      expect(widgets()).toEqual(['Last backtests', 'Best backtests', 'Starred backtests', 'Starred models']),
    );
  });

  it('opens the dialogs of the quick actions', async () => {
    await open({ ...dashboardAnswer(FULL), ...modelsAnswer([]) });
    await userEvent.click(await screen.findByRole('button', { name: /^Compare backtests/ }));
    const compare = await screen.findByRole('dialog', { name: 'Compare backtests' });
    expect(
      within(compare).getByText(
        'Change one layer, run again and see which combination holds up across market conditions.',
      ),
    ).toBeDefined();
    await userEvent.keyboard('{Escape}');
    await userEvent.click(screen.getByRole('button', { name: /^New backtest\s*Pick/ }));
    expect(await screen.findByRole('heading', { level: 1, name: 'New backtest' })).toBeDefined();
  });

  it('lists the last backtests, failed ones too, with a link to all of them', async () => {
    await open({ ...dashboardAnswer(FULL), ...modelsAnswer([]) });
    await screen.findByRole('region', { name: 'Last backtests' });
    expect(rowsOf('Last backtests')).toEqual([
      ['★#12', 'AAPLMSFT', '+24.6 %', '1.34', 'Today 10:05'],
      ['★#11', 'AAPLMSFT', 'Failed', '–', 'Yesterday 18:10'],
      ['☆#10', 'AAPL+3', '+96.5 %', '0.50', '12 Oct 19:44'],
    ]);
    // #318: each instrument with its icon, in every widget.
    const icons = (name: string) =>
      [...screen.getByRole('region', { name }).querySelectorAll('img')].map((img) => img.getAttribute('src'));
    expect(icons('Last backtests')).toHaveLength(5);
    expect(icons('Best backtests')).toHaveLength(3);
    expect(icons('Starred backtests')).toEqual([
      '/api/v1/data/instruments/stock%3AAAPL/icon',
      '/api/v1/data/instruments/stock%3AMSFT/icon',
      '/api/v1/data/instruments/stock%3AAAPL/icon',
      '/api/v1/data/instruments/stock%3AMSFT/icon',
    ]);
    expect(widget('Last backtests').getByRole('link', { name: 'All backtests' }).getAttribute('href')).toBe(
      '/backtests',
    );
  });

  it('ranks the best backtests by the result picked, Sharpe first', async () => {
    await open({ ...dashboardAnswer(FULL), ...modelsAnswer([]) });
    const best = within(await screen.findByRole('region', { name: 'Best backtests' }));
    expect(best.getByText('by Sharpe')).toBeDefined();
    expect(rowsOf('Best backtests')).toEqual([
      ['1', '#12AAPLMSFT', '1.34', '+24.6 %', '−19.7 %', '51.2 %'],
      ['2', '#10AAPL+3', '0.50', '+96.5 %', '−8.0 %', '55.6 %'],
    ]);
    expect(best.getByRole('button', { name: 'Sharpe ↓' }).getAttribute('aria-pressed')).toBe('true');
    await userEvent.click(best.getByRole('button', { name: 'Return' }));
    expect(best.getByText('by return')).toBeDefined();
    expect(best.getByRole('button', { name: 'Return ↓' }).getAttribute('aria-pressed')).toBe('true');
    expect(rowsOf('Best backtests').map((row) => row[1])).toEqual(['#10AAPL+3', '#12AAPLMSFT']);
    await userEvent.click(best.getByRole('button', { name: 'Drawdown' }));
    expect(best.getByText('by drawdown')).toBeDefined();
    await userEvent.click(best.getByRole('button', { name: 'Win rate' }));
    expect(best.getByText('by win rate')).toBeDefined();
  });

  it('lists the starred backtests with their line, and a failed one with its badge', async () => {
    await open({ ...dashboardAnswer(FULL), ...modelsAnswer([]) });
    const starred = within(await screen.findByRole('region', { name: 'Starred backtests' }));
    expect(starred.getByText('by Sharpe')).toBeDefined();
    const rows = starred.getAllByRole('button', { name: /^Unstar backtest/ }).map((star) => star.closest('div'));
    expect(rows.map((row) => row?.textContent)).toEqual([
      '★#12AAPLMSFT+24.6 %Time windows · blesk-novol-8.kerasSharpe 1.34',
      '★#11AAPLMSFTFailedTime windows · blesk-novol-8.keras',
    ]);
    expect(rows[0]?.querySelectorAll('svg path')).toHaveLength(2);
    expect(rows[1]?.querySelector('svg')).toBeNull();
    expect(starred.queryByRole('link', { name: /More/ })).toBeNull();
  });

  it('leads to all starred backtests from More when there are more than four', async () => {
    await open({
      ...dashboardAnswer({ ...FULL, starred: { backtests: FULL.starred.backtests, total: 5 } }),
      ...modelsAnswer([]),
    });
    const starred = within(await screen.findByRole('region', { name: 'Starred backtests' }));
    const more = starred.getByRole('link', { name: 'More5 starred →' });
    expect(more.getAttribute('href')).toBe('/backtests');
  });

  it('asks to star a backtest or a model when none is', async () => {
    await open({ ...dashboardAnswer({ ...FULL, starred: { backtests: [], total: 0 } }), ...modelsAnswer([]) });
    expect(await screen.findByText('Star a backtest to keep it here.')).toBeDefined();
    expect(await screen.findByText('Star a model to keep it here.')).toBeDefined();
  });

  it('takes a backtest off Starred backtests at once when its star goes', async () => {
    await open({
      ...dashboardAnswer(FULL),
      ...modelsAnswer([]),
      'PATCH /api/v1/backtests/bt_7f3k2m9qx4': () => new Promise(() => {}),
    });
    const starred = within(await screen.findByRole('region', { name: 'Starred backtests' }));
    await userEvent.click(starred.getByRole('button', { name: 'Unstar backtest 12' }));
    expect(starred.queryByRole('button', { name: 'Unstar backtest 12' })).toBeNull();
    expect(widget('Last backtests').getByRole('button', { name: 'Star backtest 12' }).textContent).toBe('☆');
  });

  it("opens a backtest's page from a widget, with its way back to the dashboard", async () => {
    await open({ ...dashboardAnswer(FULL), ...modelsAnswer([]) });
    const best = within(await screen.findByRole('region', { name: 'Best backtests' }));
    const opener = best.getByRole('link', { name: 'Backtest #10' });
    await userEvent.click(opener);
    expect(await screen.findByRole('link', { name: '← Dashboard' })).toBeDefined();
    expect((await screen.findByRole('heading', { level: 1 })).textContent).toBe('Not found');
  });

  it('gives the focus back to what opened a dialog when it closes', async () => {
    await open({ ...dashboardAnswer(FULL), ...modelsAnswer([]) });
    const user = userEvent.setup();
    await screen.findByRole('region', { name: 'Best backtests' });
    const openers = [
      [() => screen.getByRole('button', { name: /^Upload model/ }), 'Add a model'],
      [() => screen.getByRole('button', { name: /^Compare backtests/ }), 'Compare backtests'],
    ] as const;
    for (const [opener, dialog] of openers) {
      await user.click(opener());
      await screen.findByRole('dialog', { name: dialog });
      await user.keyboard('{Escape}');
      await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      expect(document.activeElement).toBe(opener());
    }
  });

  it('lists the starred models by best Sharpe, then by use, with More past four', async () => {
    const OTHER = model({ id: 'mdl_o', name: 'other.keras', starred: true, backtest_count: 0 });
    const UNSTARRED = model({ id: 'mdl_u', name: 'plain.keras', best_sharpe: 9 });
    await open({ ...dashboardAnswer(FULL), ...modelsAnswer([DRAFT, OTHER, LSTM, UNSTARRED, TSMIXER]) });
    const starred = within(await screen.findByRole('region', { name: 'Starred models' }));
    await starred.findByText('my-tsmixer.keras');
    const rows = starred.getAllByRole('button', { name: /^Unstar model/ }).map((star) => star.closest('div'));
    expect(rows.map((row) => row?.textContent)).toEqual([
      '★my-tsmixer.kerasin 25×23 → 1 · Keras1.3410 backtests',
      '★lstm-signals.kerasin 25×23 → 1 · Keras0.791 backtest',
      '★draft.kerasKeras–3 backtests',
      '★other.kerasin 25×23 → 1 · Keras–0 backtests',
    ]);
    expect(starred.getByRole('link', { name: 'my-tsmixer.keras' }).getAttribute('href')).toBe('/models');
    expect(starred.queryByRole('link', { name: /More/ })).toBeNull();
  });

  it('keeps Starred models hidden while the models load', async () => {
    await open({ ...dashboardAnswer(FULL), 'GET /api/v1/models': () => new Promise(() => {}) });
    await vi.waitFor(() => expect(widgets()).toEqual(['Last backtests', 'Best backtests', 'Starred backtests']));
  });

  it('writes the headers of the number columns in mono', async () => {
    await open({ ...dashboardAnswer(FULL), ...modelsAnswer([]) });
    await screen.findByRole('region', { name: 'Best backtests' });
    const mono = (name: string) =>
      widget(name)
        .getAllByRole('columnheader')
        .filter((head) => head.className.split(' ').includes('font-mono'))
        .map((head) => head.textContent);
    expect(mono('Last backtests')).toEqual(['Return', 'Sharpe']);
    expect(mono('Best backtests')).toEqual(['Sharpe ↓', 'Return', 'Drawdown', 'Win rate']);
  });

  it('writes the widgets’ hints in the design’s hint style', async () => {
    await open({ ...dashboardAnswer(FULL), ...modelsAnswer([]) });
    const hint = within(await screen.findByRole('region', { name: 'Best backtests' })).getByText('by Sharpe');
    expect(hint.className.split(' ')).toEqual(['text-[12px]/[1.5]', 'text-muted-foreground']);
  });

  it('says in Starred models why the models could not load', async () => {
    await open({ ...dashboardAnswer(FULL), 'GET /api/v1/models': { status: 503, body: { detail: 'Down.' } } });
    const starred = within(await screen.findByRole('region', { name: 'Starred models' }));
    expect((await starred.findByRole('alert')).textContent).toBe('Down.');
    cleanup();

    await open({ ...dashboardAnswer(), 'GET /api/v1/models': { status: 503, body: { detail: 'Down.' } } });
    expect(await screen.findByRole('alert')).toBeDefined();
    expect(widgets()).toEqual(['Get started', 'Starred models']);
  });

  it('lets the parts of every widget touch', async () => {
    await open({ ...dashboardAnswer(FULL), ...modelsAnswer([]) });
    await screen.findByRole('region', { name: 'Starred models' });
    for (const region of screen.getAllByRole('region')) expect(region.className.split(' ')).not.toContain('gap-1');
  });

  it('leads to the Models page from More past four starred models', async () => {
    const five = [1, 2, 3, 4, 5].map((n) => model({ id: `mdl_${n}`, name: `m${n}.keras`, starred: true }));
    await open({ ...dashboardAnswer(FULL), ...modelsAnswer(five) });
    const starred = within(await screen.findByRole('region', { name: 'Starred models' }));
    const more = await starred.findByRole('link', { name: 'More5 starred →' });
    expect(more.getAttribute('href')).toBe('/models');
  });
});
