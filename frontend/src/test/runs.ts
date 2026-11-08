import type { BacktestDocument, BacktestStep } from '@/api';

import { backtestDocument } from './api';
import { step } from './steps';

// Backtests that have not ended yet, as their pages get them: the document of #12, Bright Vega,
// while it runs and while it waits in the queue.

// The same document while it runs: the setup only, the candles loading (or the steps given).
export const runningDocument = (
  steps: BacktestStep[] = [
    step('candles', { state: 'running', started_at: '2026-10-17T14:05:00Z' }),
    step('queue'),
    step('model'),
    step('trades'),
  ],
): BacktestDocument => {
  const doc = backtestDocument();
  return {
    ...doc,
    backtest: { ...doc.backtest, status: 'running', finished_at: null, duration_s: null, steps },
    software: { ...doc.software, runner: null },
    data: { ...doc.data, loaded: null, changed: null, rows: null, predictions: null, align: null },
    results: null,
  };
};

// The same document while it waits in the user's queue, first in line unless `position` says
// otherwise: no step has begun.
export const queuedDocument = (position = 1): BacktestDocument => {
  const doc = runningDocument([step('candles'), step('queue'), step('model'), step('trades')]);
  return { ...doc, backtest: { ...doc.backtest, status: 'queued', queue_position: position, started_at: null } };
};
