import type { BacktestDocument } from '@/api';
import type { DateStyle } from '@/lib/dates';
import { MISSING, formatCount, formatPercent, formatSharpe, formatWinRate } from '@/lib/results';

import { setupOf } from '../result/format';

// What the table of the comparison shows, one value per backtest: the results with the best and
// the worst of each row marked, then the setup.

type Stats = NonNullable<BacktestDocument['results']>['stats'];

export type Mark = 'best' | 'worst' | undefined;
export type Cell = { text: string; mark: Mark };
export type CompareRow = { label: string; cells: Cell[] };

type Metric = {
  label: string;
  key: keyof Stats;
  // Which way is better: up, down, or neither way (more trades are not better in themselves).
  better: 1 | -1 | 0;
  write: (value: number | null) => string;
};

// A fall is the same however it is signed: the smaller, the better.
const sized = (value: number) => Math.abs(value);

// No fall is no sign.
const fall = (value: number | null) => (value === 0 ? '0.0 %' : formatPercent(value));

// The average trade is small: two decimals, so close ones do not read as equal.
function smallPercent(value: number | null) {
  if (value === null) return MISSING;
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(value * 100).toFixed(2)} %`;
}

const METRICS: readonly Metric[] = [
  { label: 'Total return', key: 'total_return', better: 1, write: formatPercent },
  { label: 'Max drawdown', key: 'max_drawdown', better: -1, write: fall },
  { label: 'Sharpe', key: 'sharpe', better: 1, write: formatSharpe },
  { label: 'Sortino', key: 'sortino', better: 1, write: formatSharpe },
  { label: 'Win rate', key: 'win_rate', better: 1, write: formatWinRate },
  { label: 'Avg. trade', key: 'avg_trade_return', better: 1, write: smallPercent },
  { label: 'Best trade', key: 'best_trade_return', better: 1, write: formatPercent },
  { label: 'Worst trade', key: 'worst_trade_return', better: 1, write: formatPercent },
  { label: 'Fees paid', key: 'fees_paid', better: -1, write: formatWinRate },
  { label: 'Trades', key: 'num_trades', better: 0, write: formatCount },
  { label: 'Time in market', key: 'exposure', better: 0, write: formatWinRate },
  { label: 'Buy and hold', key: 'buy_and_hold_return', better: 0, write: formatPercent },
];

// The texts of the best and the worst value: values that read the same count as equal, so a row
// never marks two figures that look alike. None with fewer than two values, or when all are equal.
function extremes(values: readonly { text: string; size: number }[], better: 1 | -1) {
  if (values.length < 2) return {};
  const sorted = [...values].sort((a, b) => (b.size - a.size) * better);
  const best = sorted[0]?.text;
  const worst = sorted.at(-1)?.text;
  return best === worst ? {} : { best, worst };
}

function metricRow({ label, key, better, write }: Metric, docs: readonly BacktestDocument[]): CompareRow {
  const raw = docs.map((doc) => doc.results?.stats[key] ?? null);
  const texts = raw.map((value) => (value === null ? MISSING : write(value)));
  const known = raw.flatMap((value, index) =>
    value === null ? [] : [{ text: texts[index] ?? MISSING, size: key === 'max_drawdown' ? sized(value) : value }],
  );
  const { best, worst } = better === 0 ? { best: undefined, worst: undefined } : extremes(known, better);
  return {
    label,
    cells: raw.map((value, index) => {
      const text = texts[index] ?? MISSING;
      if (value === null) return { text, mark: undefined };
      if (text === best) return { text, mark: 'best' };
      return { text, mark: text === worst ? 'worst' : undefined };
    }),
  };
}

export const resultRows = (docs: readonly BacktestDocument[]) => METRICS.map((metric) => metricRow(metric, docs));

const plain = (label: string, texts: readonly string[]): CompareRow => ({
  label,
  cells: texts.map((text) => ({ text, mark: undefined })),
});

// The setup as a backtest's page writes it. A row only some of them have (a rule's own settings,
// the steps that are on) has a dash for the others, and stays where it first came up.
export function setupRows(docs: readonly BacktestDocument[], style: Pick<DateStyle, 'dateFormat'>): CompareRow[] {
  const each = docs.map((doc) => new Map(setupOf(doc, style).flatMap(({ rows }) => rows)));
  const labels = [...new Set(each.flatMap((rows) => [...rows.keys()]))];
  return [
    plain(
      'Instrument',
      docs.map((doc) => doc.data.instrument.symbol),
    ),
    plain(
      'Model',
      docs.map((doc) => doc.model.name),
    ),
    ...labels.map((label) =>
      plain(
        label,
        each.map((rows) => rows.get(label) ?? MISSING),
      ),
    ),
  ];
}
