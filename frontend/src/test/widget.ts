import type { ToolResult } from '@/widget/bridge';
import { CHART_KEY } from '@/widget/data';

// Results of the MCP tools as a chat client hands them to the widget: the structured content the
// server answers (backend/src/candlestack/mcp/run.py, models.py) and the chart data of its _meta.

const URL = 'https://app.candlestack.tech';
export const START = 1_717_372_800; // 2024-06-03
const DAY = 86_400;

const days = (count: number) => Array.from({ length: count }, (_, index) => START + index * DAY);

const STATS = {
  total_return: -0.0563,
  max_drawdown: -0.1463,
  sharpe: -0.44,
  sortino: -0.6,
  num_trades: 349,
  win_rate: 0.527,
  avg_trade_return: -0.0001,
  best_trade_return: 0.04,
  worst_trade_return: -0.03,
  exposure: 0.789,
  fees_paid: 0.01,
  buy_and_hold_return: 0.1339,
};

export function backtestAnswer(status = 'finished') {
  const finished = status === 'finished';
  return {
    backtest: {
      id: 'bt_7f3k2m9qx4',
      number: 12,
      name: 'Silent Orion',
      status,
      author: { id: 'usr_7f3k2m9qx4', name: 'Ada Lovelace', mine: true },
      public: false,
      duration_s: finished ? 7 : null,
      url: `${URL}/backtests/bt_7f3k2m9qx4`,
    },
    setup: {
      model: { id: 'mdl_k2m9qx47f3', name: 'raw-candles.keras', framework: 'keras' },
      data: {
        instrument: { id: 'stock:SPY', name: 'SPDR S&P 500 ETF', currency: 'USD' },
        period: { start: START, end: START + 40 * DAY },
        timeframe: '30m',
        session: 'regular',
      },
      decision: { rule: { name: 'threshold', limit: 0.05 }, long_only: false },
      execution: { fill: 'next_open' },
      benchmarks: [{ id: 'bh', kind: 'buy_and_hold', instrument: 'stock:SPY' }],
    },
    candles: finished ? 3250 : null,
    metrics: finished ? STATS : null,
    benchmark_returns: finished ? { bh: 0.1339 } : {},
  };
}

// The equity a model asked get_backtest for (`equity_points`): the capital, 1 at the start.
export const EQUITY = {
  points: 40,
  rows: 3250,
  resolution: 'candle',
  ts: days(40),
  equity: days(40).map((_, index) => 1 - 0.0014 * index),
  drawdown: days(40).map((_, index) => (index === 16 ? -0.1463 : -0.01)),
  benchmarks: { bh: days(40).map((_, index) => 1 + 0.0034 * index) },
};

// The stored curves of the backtest, as the server's _meta and compare_backtests have them.
export const BACKTEST_CURVES = {
  times: days(40),
  backtests: [
    {
      id: 'bt_7f3k2m9qx4',
      equity: days(40).map((_, index) => -0.0014 * index),
      drawdown: days(40).map((_, index) => (index === 16 ? -0.1463 : -0.01)),
      buy_and_hold: days(40).map((_, index) => 0.0034 * index),
    },
  ],
};

export const backtestResult = (chart = true): ToolResult => ({
  structuredContent: backtestAnswer(),
  _meta: chart ? { [CHART_KEY]: BACKTEST_CURVES } : undefined,
});

const compared = (id: string, name: string, symbol: string) => ({
  id,
  number: 12,
  name,
  author: { id: 'usr_7f3k2m9qx4', name: 'Ada Lovelace', mine: true },
  public: false,
  status: 'finished',
  error: null,
  setup: { instrument: `stock:${symbol}`, decision: { long_only: true } },
  candles: 3250,
  equity: null,
  drawdown: null,
  buy_and_hold: null,
  url: `${URL}/backtests/${id}`,
});

export const comparisonAnswer = () => ({
  backtests: [compared('bt_7f3k2m9qx4', 'Silent Orion', 'SPY'), compared('bt_t9n8v7d6a5', 'Quiet Vega', 'MU')],
  metrics: [
    {
      key: 'total_return',
      label: 'Total return',
      better: 'higher',
      values: [-0.0563, -0.9513],
      marks: ['best', 'worst'],
    },
    {
      key: 'max_drawdown',
      label: 'Max drawdown',
      better: 'lower',
      values: [-0.1463, -0.9546],
      marks: ['best', 'worst'],
    },
    { key: 'num_trades', label: 'Trades', better: 'none', values: [349, 1490], marks: [null, null] },
  ],
  differs: ['instrument', 'execution.fee_bps', 'period.start'],
  times: [],
  url: `${URL}/backtests/compare?ids=bt_7f3k2m9qx4,bt_t9n8v7d6a5`,
});

export const CURVES = {
  times: days(5),
  backtests: [
    { id: 'bt_7f3k2m9qx4', equity: [0, -0.02, -0.1, -0.02, -0.0563] },
    { id: 'bt_t9n8v7d6a5', equity: [null, 0, -0.4, -0.8, -0.9513] },
  ],
};

export const comparisonResult = (chart = true): ToolResult => ({
  structuredContent: comparisonAnswer(),
  _meta: chart ? { [CHART_KEY]: CURVES } : undefined,
});

export const modelAnswer = () => ({
  id: 'mdl_k2m9qx47f3',
  name: 'renko-lstm.keras',
  framework: 'keras',
  size_bytes: 4_978_658,
  parameters: 410_717,
  input_shape: [25, 23],
  output_shape: [1],
  starred: true,
  backtest_count: 2,
  best_sharpe: -0.44,
  created_at: '2026-10-27T20:12:12Z',
  state: 'ready',
  url: `${URL}/models/mdl_k2m9qx47f3`,
  manifest_summary: {
    representation: { id: 'renko-triples@1', params: { atr_at: 'brick_start', atr_window: 14, atr_multiple: 0.6 } },
    timeframe: '30m',
    session: 'regular',
    input_shape: [25, 23],
    warm_up: { candles: 403, kind: 'estimate' },
  },
  checked_at: null,
  check_failed: null,
  check_error: null,
});

export const MODEL_EXTRAS = {
  backtests: [
    {
      id: 'bt_7f3k2m9qx4',
      name: 'Silent Orion',
      symbols: ['SPY'],
      total_return: -0.0563,
      sharpe: -0.44,
      url: `${URL}/backtests/bt_7f3k2m9qx4`,
    },
    {
      id: 'bt_t9n8v7d6a5',
      name: 'Quiet Vega',
      symbols: ['MU'],
      total_return: -0.9513,
      sharpe: -2.24,
      url: `${URL}/backtests/bt_t9n8v7d6a5`,
    },
  ],
  decision: { threshold: 0.05, direction: 'both' },
};

export const modelResult = (chart = true): ToolResult => ({
  structuredContent: modelAnswer(),
  _meta: chart ? { [CHART_KEY]: MODEL_EXTRAS } : undefined,
});
