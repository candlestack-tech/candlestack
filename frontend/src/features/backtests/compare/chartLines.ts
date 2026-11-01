import type { BacktestDocument } from '@/api';

// What the comparison's charts draw of a backtest: its return and its fall from the top, in
// percent, on its own times, so a line starts where its period starts. Times are UTC epoch seconds.

export type Point = { time: number; value: number };
export type Line = { id: string; number: number; color: string; equity: Point[]; drawdown: Point[] };

const percentOf = (ts: readonly number[], values: readonly (number | null)[], from: (value: number) => number) =>
  ts.flatMap((time, index) => {
    const value = values[index];
    return value === null || value === undefined ? [] : [{ time, value: from(value) * 100 }];
  });

export function lineOf(doc: BacktestDocument, color: string): Line | null {
  const series = doc.results?.series;
  if (!series) return null;
  const { ts, equity, drawdown } = series.columns;
  return {
    id: doc.backtest.id,
    number: doc.backtest.number,
    color,
    equity: percentOf(ts, equity, (value) => value - 1),
    drawdown: percentOf(ts, drawdown, (value) => -Math.abs(value)),
  };
}

// The value of a line at a time on the chart: its last point up to then, none outside its period.
export function valueAt(points: readonly Point[], time: number): number | null {
  const after = points.findIndex((point) => point.time > time);
  if (after === 0 || points.length === 0) return null;
  if (after > 0) return points[after - 1]?.value ?? null;
  const last = points.at(-1);
  return last?.time === time ? last.value : null;
}
