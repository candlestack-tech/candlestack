import type { ComparedBacktest } from '@/api';

import { comparedDescription } from '../names';

// What the comparison's charts draw of a backtest: its return and its fall from the top, in
// percent, at the comparison's times within its period, so a line starts where its period starts.
// Times are UTC epoch seconds.

export type Point = { time: number; value: number };
// A line is named as its backtest (backtest-names, lgLabel): the name and "AAPL · Kagi · 1h".
// Another account's line carries its author's name, for the initials in the legends.
export type Line = {
  id: string;
  name: string;
  description: string;
  author: string | null;
  color: string;
  equity: Point[];
  drawdown: Point[];
};

const percentOf = (times: readonly number[], values: readonly (number | null)[]) =>
  times.flatMap((time, index) => {
    const value = values[index];
    return value === null || value === undefined ? [] : [{ time, value: value * 100 }];
  });

export function lineOf(backtest: ComparedBacktest, times: readonly number[], color: string): Line | null {
  const { equity, drawdown } = backtest;
  if (!equity || !drawdown) return null;
  return {
    id: backtest.id,
    name: backtest.name,
    description: comparedDescription(backtest),
    author: backtest.author.mine ? null : backtest.author.name,
    color,
    equity: percentOf(times, equity),
    drawdown: percentOf(times, drawdown),
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
