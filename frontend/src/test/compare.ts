import type { BacktestDocument, CompareMetric, ComparedBacktest, Comparison, Manifest } from '@/api';

// What GET /api/v1/compare answers for backtests of these documents, as the server builds it from
// what each stored: their statistics, setups and curves at the times of their series. The marks are
// the server's, so a test gives the ones it expects to see (none by default).

type Stats = NonNullable<BacktestDocument['results']>['stats'];
type Marks = Partial<Record<keyof Stats, CompareMetric['marks']>>;

const METRICS: readonly (readonly [keyof Stats, string, CompareMetric['better']])[] = [
  ['total_return', 'Total return', 'higher'],
  ['max_drawdown', 'Max drawdown', 'lower'],
  ['sharpe', 'Sharpe', 'higher'],
  ['sortino', 'Sortino', 'higher'],
  ['win_rate', 'Win rate', 'higher'],
  ['avg_trade_return', 'Avg. trade', 'higher'],
  ['best_trade_return', 'Best trade', 'higher'],
  ['worst_trade_return', 'Worst trade', 'higher'],
  ['fees_paid', 'Fees paid', 'lower'],
  ['num_trades', 'Trades', 'none'],
  ['exposure', 'Time in market', 'none'],
  ['buy_and_hold_return', 'Buy and hold', 'none'],
];

function setupOf(doc: BacktestDocument): ComparedBacktest['setup'] {
  const { input, signature } = doc.model.manifest as unknown as Partial<Manifest>;
  return {
    instrument: doc.data.instrument.id,
    model: doc.model.name,
    reads: input?.representation.id ?? null,
    input: signature?.input.shape ?? null,
    timeframe: doc.data.timeframe,
    period: doc.data.period,
    decision: doc.decision,
    execution: doc.execution,
    benchmarks: doc.benchmarks.map((benchmark) => benchmark.instrument),
  };
}

type Curves = Pick<ComparedBacktest, 'equity' | 'drawdown' | 'buy_and_hold'>;

// Its series at `times` (none outside it); none without results.
function curvesOf(doc: BacktestDocument, times: readonly number[]): Curves {
  const columns = doc.results?.series.columns;
  if (!columns) return { equity: null, drawdown: null, buy_and_hold: null };
  const curve = (values: readonly (number | null)[], from: (value: number) => number) =>
    times.map((time) => {
      const value = values[columns.ts.indexOf(time)];
      return value === null || value === undefined ? null : from(value);
    });
  const first = columns.close[0] ?? 1;
  return {
    equity: curve(columns.equity, (value) => value - 1),
    drawdown: curve(columns.drawdown, (value) => -Math.abs(value)),
    buy_and_hold: curve(columns.close, (value) => value / first - 1),
  };
}

function compared(doc: BacktestDocument, times: readonly number[]): ComparedBacktest {
  const { id, number, name, author, public: published, status, error } = doc.backtest;
  return {
    id,
    number,
    name,
    author,
    public: published,
    status,
    error,
    setup: setupOf(doc),
    candles: doc.data.rows?.count ?? null,
    ...curvesOf(doc, times),
  };
}

export function comparison(docs: readonly BacktestDocument[], marks: Marks = {}): Comparison {
  const times = [...new Set(docs.flatMap((doc) => doc.results?.series.columns.ts ?? []))].sort((a, b) => a - b);
  return {
    backtests: docs.map((doc) => compared(doc, times)),
    metrics: METRICS.map(([key, label, better]) => ({
      key,
      label,
      better,
      values: docs.map((doc) => doc.results?.stats[key] ?? null),
      marks: marks[key] ?? docs.map(() => null),
    })),
    differs: [],
    times,
  };
}

const path = (docs: readonly BacktestDocument[]) =>
  `GET /api/v1/compare?ids=${docs.map((doc) => doc.backtest.id).join(',')}`;

// The answers for these documents and for each of their sets in the same order, as the page asks
// again when one leaves; `marks` are those of all of them together.
export function compareAnswers(docs: readonly BacktestDocument[], marks: Marks = {}) {
  const sets = docs.reduce<BacktestDocument[][]>((all, doc) => [...all, ...all.map((set) => [...set, doc])], [[]]);
  return Object.fromEntries(
    sets
      .filter((set) => set.length > 0)
      .map((set) => [path(set), { status: 200, body: comparison(set, set.length === docs.length ? marks : {}) }]),
  );
}

// GET /api/v1/compare of these documents answering 404 for the one with `id`.
export const compareMissing = (docs: readonly BacktestDocument[], id: string) => ({
  [path(docs)]: { status: 404, body: { type: 'https://candlestack.tech/problems/not-found', title: 'Not Found', id } },
});
