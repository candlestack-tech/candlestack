import type { BacktestDocument, Experiment, ExperimentsPage } from '@/api';

import { backtest, backtestDocument, type stubApi } from './api';

// Experiments in tests: another account of the beta, its published backtest as the list and its
// page show it, and the list's answer.

type Answers = Parameters<typeof stubApi>[0];

// Another account of the beta, as an author of Experiments.
export const TYMUR = { id: 'usr_t9m8r7k6j5', name: 'Tymur Tkach', mine: false };

// A published backtest as Experiments shows it: Tymur's #9, Velvet Deneb, on NVDA with a Renko model, against
// SPY, published at 10:14 on 17 Oct 2026.
export const experiment = (fields: Partial<Experiment> = {}): Experiment => ({
  id: 'bt_t9n8v7d6a5',
  name: 'Velvet Deneb',
  number: 9,
  instruments: [{ symbol: 'NVDA', market: 'stock' }],
  representation: 'renko',
  model: { name: 'blesk-novol-8.keras', framework: 'keras', input_shape: [25, 23] },
  setup: { ...backtest().setup, benchmark: 'SPY' },
  total_return: 0.412,
  benchmark_return: 0.143,
  buy_and_hold_return: 0.389,
  sharpe: 1.38,
  max_drawdown: -0.124,
  win_rate: 0.548,
  num_trades: 188,
  equity: { strategy: [0, 0.1, 0.25, 0.412], benchmark: [0, 0.05, 0.1, 0.143] },
  author: TYMUR,
  published_at: '2026-10-17T10:14:00Z',
  ...fields,
});

// GET /api/v1/experiments (with `query`, e.g. '?mine=true') with this page; the counts say it
// holds them all and the facets are empty unless given.
export const experimentsAnswer = (page: Partial<ExperimentsPage> = {}, query = ''): Answers => {
  const experiments = page.experiments ?? [];
  return {
    [`GET /api/v1/experiments${query}`]: {
      status: 200,
      body: {
        experiments,
        total: experiments.length,
        page: 1,
        pages: 1,
        counts: { all: experiments.length, mine: 0, people: experiments.length === 0 ? 0 : 1 },
        facets: { markets: [], instruments: [], representations: [], timeframes: [] },
        ...page,
      },
    },
  };
};

// The backtest of api.ts's backtestDocument as another account published it: Tymur's #9, Velvet Deneb, read
// only, its model without an ID (the file stays with him).
export const othersDocument = (): BacktestDocument => {
  const doc = backtestDocument();
  return {
    ...doc,
    backtest: {
      ...doc.backtest,
      id: 'bt_t9n8v7d6a5',
      name: 'Velvet Deneb',
      generated_name: 'Velvet Deneb',
      number: 9,
      author: TYMUR,
      public: true,
      published_at: '2026-10-17T10:14:00Z',
    },
    model: { ...doc.model, id: null },
  };
};
