import { describe, expect, it } from 'vitest';

import type { BacktestsPage, Dashboard } from '@/api';
import { backtest, failedBacktest } from '@/test/api';

import { starOnDashboard, starOnPage } from './useBacktests';

const A = backtest({ id: 'bt_a', number: 3, sharpe: 1.2 });
const B = backtest({ id: 'bt_b', number: 2, sharpe: 0.4, starred: true });
const C = failedBacktest({ id: 'bt_c', number: 1, starred: true });

const page = (backtests = [A, B, C]): BacktestsPage => ({
  backtests,
  total: backtests.length,
  page: 1,
  pages: 1,
  counts: { all: 3, starred: 2, failed: 1, public: 0 },
  models: [],
});

describe('starOnPage', () => {
  it('changes the star of the row and the count of starred backtests', () => {
    const next = starOnPage(page(), {}, { ...A, starred: true });
    expect(next.backtests.map((item) => item.starred)).toEqual([true, true, true]);
    expect(next.counts.starred).toBe(3);
    expect(next.total).toBe(3);
  });

  it('drops a backtest that loses its star from the Starred list', () => {
    const next = starOnPage(page([B, C]), { show: 'starred' }, { ...B, starred: false });
    expect(next.backtests).toEqual([C]);
    expect(next.total).toBe(1);
    expect(next.counts.starred).toBe(1);
  });

  it('keeps an unstarred backtest in the other lists', () => {
    const next = starOnPage(page(), { show: 'failed' }, { ...C, starred: false });
    expect(next.backtests.map((item) => item.starred)).toEqual([false, true, false]);
  });
});

describe('starOnDashboard', () => {
  const dashboard: Dashboard = {
    counts: { all: 3, starred: 2, failed: 1, public: 0 },
    last: [A, B, C],
    best: { sharpe: [A, B], total_return: [A, B], max_drawdown: [B, A], win_rate: [A, B] },
    starred: { backtests: [B, C], total: 2 },
    experiments: { backtests: [], total: 0 },
  };

  it('puts a new star in its place by Sharpe and counts it', () => {
    const next = starOnDashboard(dashboard, { ...A, starred: true });
    expect(next.starred).toEqual({ backtests: [{ ...A, starred: true }, B, C], total: 3 });
    expect(next.counts.starred).toBe(3);
    expect(next.last[0]?.starred).toBe(true);
    expect(next.best.max_drawdown[1]?.starred).toBe(true);
  });

  it('takes a star away from Starred backtests', () => {
    const next = starOnDashboard(dashboard, { ...B, starred: false });
    expect(next.starred).toEqual({ backtests: [C], total: 1 });
    expect(next.best.sharpe[1]?.starred).toBe(false);
  });

  it('keeps the four best', () => {
    const many = [1, 2, 3, 4].map((number) => backtest({ id: `bt_${number}`, number, sharpe: number, starred: true }));
    const next = starOnDashboard(
      { ...dashboard, starred: { backtests: many, total: 4 } },
      { ...A, sharpe: 2.5, starred: true },
    );
    expect(next.starred.backtests.map((item) => item.number)).toEqual([4, 3, 3, 2]);
    expect(next.starred.total).toBe(5);
  });
});
