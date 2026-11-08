// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { backtest, failedBacktest } from '@/test/api';

import { BacktestReturn } from './parts';

afterEach(() => {
  cleanup();
});

// The Return cell of the Backtests table, the dashboard's widgets and a model's backtests (#400).
describe('the return of a backtest in a list', () => {
  it('is the badge with its place for a queued backtest, what it means in the title', () => {
    render(<BacktestReturn backtest={backtest({ status: 'queued', queue_position: 2, total_return: null })} />);
    const badge = screen.getByText('Queued · 2');
    expect(badge.getAttribute('title')).toBe('2 backtests ahead of it');
    // A screen reader hears what the title says.
    expect(badge.textContent).toBe('Queued · 2 ahead');
  });

  it('says 1 backtest for the first in the queue', () => {
    render(<BacktestReturn backtest={backtest({ status: 'queued', queue_position: 1, total_return: null })} />);
    expect(screen.getByText('Queued · 1').getAttribute('title')).toBe('1 backtest ahead of it');
  });

  it('is the plain badge without a place, running or failed', () => {
    render(
      <>
        <BacktestReturn backtest={backtest({ status: 'queued', queue_position: null, total_return: null })} />
        <BacktestReturn backtest={backtest({ status: 'running', total_return: null })} />
        <BacktestReturn backtest={failedBacktest()} />
      </>,
    );
    expect(screen.getByText('Queued').getAttribute('title')).toBeNull();
    expect(screen.getByText('Running')).toBeDefined();
    expect(screen.getByText('Failed')).toBeDefined();
  });

  it('is the return once it finished', () => {
    render(<BacktestReturn backtest={backtest()} />);
    expect(screen.getByText('+24.6 %')).toBeDefined();
  });
});
