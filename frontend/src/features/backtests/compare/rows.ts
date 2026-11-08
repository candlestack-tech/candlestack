import type { CompareMetric, ComparedBacktest } from '@/api';
import type { DateStyle } from '@/lib/dates';
import { MISSING, formatCount, formatPercent, formatSharpe, formatWinRate } from '@/lib/results';

import { setupGroups } from '../result/format';

// How the table of the comparison writes what GET /api/v1/compare answers, one value per backtest:
// the results with the best and the worst of each row as the server marked them, then the setup.

export type Mark = 'best' | 'worst' | undefined;
// `avatar`: whose initials go before the text (the Author row).
export type Cell = { text: string; mark: Mark; avatar?: string };
export type CompareRow = { label: string; cells: Cell[] };

// No fall is no sign.
const fall = (value: number | null) => (value === 0 ? '0.0 %' : formatPercent(value));

// The average trade is small: two decimals, so close ones do not read as equal.
function smallPercent(value: number | null) {
  if (value === null) return MISSING;
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(value * 100).toFixed(2)} %`;
}

// How each statistic is written, by its key; one the app does not know yet reads as a percent.
const WRITE: Record<string, (value: number | null) => string> = {
  total_return: formatPercent,
  max_drawdown: fall,
  sharpe: formatSharpe,
  sortino: formatSharpe,
  win_rate: formatWinRate,
  avg_trade_return: smallPercent,
  best_trade_return: formatPercent,
  worst_trade_return: formatPercent,
  fees_paid: formatWinRate,
  num_trades: formatCount,
  exposure: formatWinRate,
  buy_and_hold_return: formatPercent,
};

// Each statistic in the server's order with the values of the backtests at `columns`, their
// places in the answer.
export function resultRows(metrics: readonly CompareMetric[], columns: readonly number[]): CompareRow[] {
  return metrics.map(({ key, label, values, marks }) => {
    const write = WRITE[key] ?? formatPercent;
    return {
      label,
      cells: columns.map((column) => {
        const value = values[column] ?? null;
        return { text: value === null ? MISSING : write(value), mark: marks[column] ?? undefined };
      }),
    };
  });
}

const plain = (label: string, texts: readonly string[]): CompareRow => ({
  label,
  cells: texts.map((text) => ({ text, mark: undefined })),
});

// Whose each backtest is, by initials and name, "You" for the user's own; only with another
// account's in the comparison (public-experiments, C_SETUP).
function authorRows(backtests: readonly ComparedBacktest[]): CompareRow[] {
  if (backtests.every((backtest) => backtest.author.mine)) return [];
  const cells = backtests.map(({ author }) => ({
    text: author.mine ? 'You' : author.name,
    mark: undefined,
    avatar: author.name,
  }));
  return [{ label: 'Author', cells }];
}

// The setup as a backtest's page writes it. A row only some of them have (a rule's own settings,
// the steps that are on) has a dash for the others, and stays where it first came up.
export function setupRows(backtests: readonly ComparedBacktest[], style: Pick<DateStyle, 'dateFormat'>): CompareRow[] {
  const each = backtests.map(
    ({ setup, candles }) => new Map(setupGroups({ ...setup, candles }, style).flatMap(({ rows }) => rows)),
  );
  const labels = [...new Set(each.flatMap((rows) => [...rows.keys()]))];
  return [
    ...authorRows(backtests),
    plain(
      'Instrument',
      backtests.map(({ setup }) => setup.instrument.slice(setup.instrument.indexOf(':') + 1)),
    ),
    plain(
      'Model',
      backtests.map(({ setup }) => setup.model),
    ),
    ...labels.map((label) =>
      plain(
        label,
        each.map((rows) => rows.get(label) ?? MISSING),
      ),
    ),
  ];
}
