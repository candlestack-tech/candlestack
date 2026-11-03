// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ARSEN, backtest, dashboardAnswer, modelsAnswer, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp, viewerIn } from '@/test/app';
import { experiment } from '@/test/experiments';
import { charts } from '@/test/lightweightCharts';

vi.mock('lightweight-charts', () => import('@/test/lightweightCharts'));

const VLAD = { id: 'usr_v1a2d3s4h5', name: 'Vladyslav Shudiehov', mine: false };
const NEWEST = [
  experiment(),
  experiment({
    id: 'bt_v21',
    number: 21,
    instruments: [{ symbol: 'BTCUSDT', market: 'crypto' }],
    author: VLAD,
    published_at: '2026-10-16T09:02:00Z',
    total_return: -0.084,
    sharpe: -0.31,
  }),
];
const PUBLISHED = backtest({ starred: true, public: true, published_at: '2026-10-16T09:12:00Z' });

async function open(dashboard: Parameters<typeof dashboardAnswer>[0]) {
  stubApi({ ...sessionWithPreferences(ARSEN), ...dashboardAnswer(dashboard), ...modelsAnswer([]) });
  await renderApp('/dashboard');
}

beforeEach(() => {
  charts.reset();
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

describe('New in Experiments on the dashboard', () => {
  it('lists the newest published backtests with their authors, and leads to Experiments', async () => {
    await open({ experiments: { backtests: NEWEST, total: 25 } });
    const widget = within(await screen.findByRole('region', { name: 'New in Experiments' }));
    expect(widget.getByText('from the beta')).toBeDefined();
    const rows = widget.getAllByRole('link', { name: /^Backtest #/ }).map((link) => link.closest('div')?.textContent);
    expect(rows).toEqual([
      'TT#9NVDA+41.2 %Tymur Tkach · todaySharpe 1.38',
      'VS#21BTCUSDT−8.4 %Vladyslav Shudiehov · yesterdaySharpe −0.31',
    ]);
    expect(widget.getByRole('link', { name: 'Backtest #9' }).getAttribute('href')).toBe('/backtests/bt_t9n8v7d6a5');
    expect(widget.getByRole('button', { name: 'Add to compare: backtest 21' })).toBeDefined();
    expect(widget.getByRole('link', { name: 'Open Experiments25 published →' }).getAttribute('href')).toBe(
      '/experiments',
    );
    // The tiny chart of each return, as Starred backtests draws it.
    expect(charts.of('Baseline')).toHaveLength(2);
  });

  it('sits on top of the right column, also for a new account', async () => {
    await open({ experiments: { backtests: NEWEST, total: 2 } });
    await screen.findByRole('region', { name: 'New in Experiments' });
    expect(screen.getAllByRole('region').map((region) => region.querySelector('h2')?.textContent)).toEqual([
      'Get started',
      'New in Experiments',
    ]);
  });

  it('is left out while nothing is published', async () => {
    await open({});
    await screen.findByRole('region', { name: 'Get started' });
    expect(screen.queryByRole('region', { name: 'New in Experiments' })).toBeNull();
  });

  it('marks the user’s public backtests with a globe in the other widgets', async () => {
    await open({
      counts: { all: 1, starred: 1, failed: 0, public: 1 },
      last: [PUBLISHED],
      best: { sharpe: [PUBLISHED], total_return: [PUBLISHED], max_drawdown: [PUBLISHED], win_rate: [PUBLISHED] },
      starred: { backtests: [PUBLISHED], total: 1 },
    });
    for (const name of ['Last backtests', 'Best backtests', 'Starred backtests']) {
      const widget = within(await screen.findByRole('region', { name }));
      expect(
        widget.getByRole('img', { name: 'Public, in Experiments' }).closest('[title]')?.getAttribute('title'),
      ).toBe('Public, in Experiments');
    }
  });
});
