import type { ToolResult } from './bridge';

// What the widget draws, read from a tool's result: its structured content (the answer the model
// reads too) and the chart data the server put in its _meta for the widget alone
// (backend/src/candlestack/mcp/apps.py, CHART_KEY). Which tool answered is told by the answer's
// shape: get_backtest, compare_backtests or get_model.

export const CHART_KEY = 'candlestack.tech/chart';

export type Stats = {
  total_return: number | null;
  max_drawdown: number | null;
  sharpe: number | null;
  sortino: number | null;
  num_trades: number | null;
  win_rate: number | null;
  exposure: number | null;
  buy_and_hold_return: number | null;
};

export type Equity = {
  ts: number[];
  equity: number[];
  drawdown: number[];
  benchmarks: Record<string, number[]>;
};

export type Backtest = {
  backtest: {
    id: string;
    name: string;
    status: string;
    error?: string | null;
    duration_s?: number | null;
    url: string;
  };
  setup: {
    model: { name: string };
    data: { instrument: { id: string }; period: { start: number; end: number }; timeframe: string };
    decision: { long_only?: boolean };
    benchmarks: { id: string; instrument: string }[];
  };
  candles?: number | null;
  metrics?: Stats | null;
  benchmark_returns?: Record<string, number | null>;
  equity?: Equity | null;
};

export type Curve = (number | null)[];

export type Compared = {
  id: string;
  name: string;
  status: string;
  setup: Record<string, unknown>;
  url?: string;
  equity?: Curve | null;
  drawdown?: Curve | null;
};

export type Comparison = {
  backtests: Compared[];
  metrics: { key: string; label: string; values: (number | null)[]; marks: ('best' | 'worst' | null)[] }[];
  differs: string[];
  times: number[];
  shared_setup?: Record<string, unknown> | null;
  // The Compare page of 2 to 4 backtests; null for one or more (older servers leave it out).
  url?: string | null;
};

export type CurveSet = { id: string; equity?: Curve | null; drawdown?: Curve | null; buy_and_hold?: Curve | null };

// Curves on one time axis as compare_backtests answers them: cumulative returns, 0 at the start.
export type Curves = { times: number[]; backtests: CurveSet[] };

// What the backtest card draws: the equity (1 at the start), its fall, and what it is measured
// against, from the stored curves (the server's _meta, or compare_backtests asked by the widget)
// or from the equity a model asked get_backtest for.
export type BacktestChart = {
  times: number[];
  equity: Curve;
  drawdown: number[];
  benchmark: { values: Curve; label: string; total: number | null } | null;
};

export type Model = {
  id: string;
  name: string;
  framework: string;
  size_bytes: number;
  parameters: number;
  input_shape?: number[] | null;
  output_shape?: number[] | null;
  backtest_count: number;
  best_sharpe?: number | null;
  created_at: string;
  state: 'needs_setup' | 'ready' | 'verified';
  url?: string | null;
  manifest_summary?: {
    representation: { id: string; params: Record<string, unknown> };
    timeframe: string;
    session: string;
    input_shape: number[];
    warm_up: { candles: number; kind: 'exact' | 'estimate' };
  } | null;
};

export type ModelBacktest = {
  id: string;
  name: string;
  symbols: string[];
  total_return: number | null;
  sharpe: number | null;
  url: string;
};

export type ModelExtras = {
  backtests: ModelBacktest[];
  decision: { threshold: number; direction: string } | null;
};

export type Card =
  | { kind: 'backtest'; answer: Backtest; chart: BacktestChart | null }
  | { kind: 'compare'; answer: Comparison; curves: Curves | null }
  | { kind: 'model'; answer: Model; extras: ModelExtras | null };

const has = (value: Record<string, unknown>, ...keys: string[]) => keys.every((key) => key in value);

type Chart = Record<string, unknown> | null;

const symbolOf = (instrument: string) => instrument.slice(instrument.indexOf(':') + 1);
const plusOne = (curve: Curve) => curve.map((value) => (value === null ? null : 1 + value));

// The stored curves of the backtest: its benchmark there is holding its own instrument.
function fromCurves(answer: Backtest, curves: Curves): BacktestChart | null {
  const [own] = curves.backtests;
  if (!own?.equity || curves.times.length < 2) return null;
  const held = own.buy_and_hold;
  return {
    times: curves.times,
    equity: plusOne(own.equity),
    drawdown: (own.drawdown ?? []).map((value) => value ?? 0),
    benchmark: held
      ? {
          values: plusOne(held),
          label: `Buy and hold ${symbolOf(answer.setup.data.instrument.id)}`,
          total: answer.metrics?.buy_and_hold_return ?? null,
        }
      : null,
  };
}

// What the backtest is measured against: buy and hold of its own instrument, or an ETF.
function benchmarkLabel(answer: Backtest, held: string): string {
  const own = answer.setup.data.instrument.id.toLowerCase() === held.toLowerCase();
  return own ? `Buy and hold ${symbolOf(held)}` : symbolOf(held);
}

// The equity a model asked get_backtest for (`equity_points`).
function fromEquity(answer: Backtest, equity: Equity): BacktestChart | null {
  if (equity.ts.length < 2) return null;
  const benchmark = answer.setup.benchmarks.find(({ id }) => equity.benchmarks[id]);
  const values = benchmark ? equity.benchmarks[benchmark.id] : undefined;
  return {
    times: equity.ts,
    equity: equity.equity,
    drawdown: equity.drawdown,
    benchmark:
      benchmark && values
        ? {
            values,
            label: benchmarkLabel(answer, benchmark.instrument),
            total: answer.benchmark_returns?.[benchmark.id] ?? null,
          }
        : null,
  };
}

function backtestCard(answer: Record<string, unknown>, chart: Chart): Card {
  const backtest = answer as unknown as Backtest;
  const drawn =
    (chart ? fromCurves(backtest, chart as Curves) : null) ??
    (backtest.equity ? fromEquity(backtest, backtest.equity) : null);
  return { kind: 'backtest', answer: backtest, chart: drawn };
}

function compareCard(answer: Record<string, unknown>, chart: Chart): Card {
  const comparison = answer as unknown as Comparison;
  // Curves the model asked for (`curve_points`) serve as well.
  const asked = comparison.times.length > 0 ? { times: comparison.times, backtests: comparison.backtests } : null;
  return { kind: 'compare', answer: comparison, curves: (chart as Curves | null) ?? asked };
}

// The card a result shows, or null for one the widget does not draw (an error, another tool's).
export function cardOf(result: ToolResult): Card | null {
  const answer = result.structuredContent;
  if (result.isError || !answer) return null;
  const chart = (result._meta?.[CHART_KEY] ?? null) as Chart;
  if (has(answer, 'backtest', 'setup')) return backtestCard(answer, chart);
  if (has(answer, 'backtests', 'metrics', 'differs')) return compareCard(answer, chart);
  if (has(answer, 'framework', 'parameters', 'state')) {
    return { kind: 'model', answer: answer as unknown as Model, extras: chart as ModelExtras | null };
  }
  return null;
}

// Whether a card lacks what it draws beyond the answer: the server adds it for a client that said
// it shows the widget, and the widget asks for it itself from one that did not.
export function missing(card: Card): boolean {
  switch (card.kind) {
    case 'backtest':
      return card.chart === null && card.answer.backtest.status === 'finished';
    case 'compare':
      return card.curves === null;
    case 'model':
      return card.extras === null && card.answer.backtest_count > 0;
  }
}
