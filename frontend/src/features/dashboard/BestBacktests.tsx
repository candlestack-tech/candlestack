import { useState } from 'react';

import type { Backtest, BestMetric, Dashboard } from '@/api';
import { Hint } from '@/components/Hint';
import { TrophyIcon } from '@/components/icons';
import {
  BacktestNumber,
  CELL,
  HEAD,
  InstrumentChips,
  NUMBER,
  ROW,
  RowOpener,
  SortButton,
  TABLE,
  returnClass,
} from '@/features/backtests';
import { formatPercent, formatSharpe, formatWinRate } from '@/lib/results';
import { cn } from '@/lib/utils';

import { Widget } from './widgets';

// The results Best backtests ranks by, in the order of their columns (mockups/app, DD_BEST): the
// heading, the hint's word, and how a value is written.
const METRICS: readonly { metric: BestMetric; label: string; by: string; format: (backtest: Backtest) => string }[] = [
  { metric: 'sharpe', label: 'Sharpe', by: 'Sharpe', format: (backtest) => formatSharpe(backtest.sharpe) },
  { metric: 'total_return', label: 'Return', by: 'return', format: (backtest) => formatPercent(backtest.total_return) },
  {
    metric: 'max_drawdown',
    label: 'Drawdown',
    by: 'drawdown',
    format: (backtest) => formatPercent(backtest.max_drawdown),
  },
  { metric: 'win_rate', label: 'Win rate', by: 'win rate', format: (backtest) => formatWinRate(backtest.win_rate) },
];

type RowProps = { backtest: Backtest; rank: number; by: BestMetric };

// The rank, the number with up to two instruments, then the four results; the one ranked by in
// semibold, the return in its colour.
function Row({ backtest, rank, by }: RowProps) {
  return (
    <tr className={ROW}>
      <td className={cn(CELL, 'w-7 font-mono text-xs text-muted-foreground')}>{rank}</td>
      <td className={CELL}>
        <span className="flex items-center gap-1.5">
          <RowOpener backtest={backtest}>
            <BacktestNumber backtest={backtest} />
          </RowOpener>
          <InstrumentChips backtest={backtest} max={2} />
        </span>
      </td>
      {METRICS.map(({ metric, format }) => (
        <td
          key={metric}
          className={cn(
            CELL,
            NUMBER,
            metric === by && 'font-semibold',
            metric === 'total_return' && backtest.total_return !== null && returnClass(backtest.total_return),
          )}
        >
          {format(backtest)}
        </td>
      ))}
    </tr>
  );
}

type Props = { best: Dashboard['best'] };

// The top five finished backtests by a result (mockups/app, ddBest); its headings rank them, the
// one in use in semibold with ↓. Sharpe first.
export function BestBacktests({ best }: Props) {
  const [by, setBy] = useState<BestMetric>('sharpe');
  const word = METRICS.find((each) => each.metric === by)?.by;
  return (
    <Widget icon={TrophyIcon} title="Best backtests" aside={<Hint>by {word}</Hint>}>
      <div className="overflow-x-auto">
        <table className={TABLE}>
          <thead>
            <tr>
              <th scope="col" className={HEAD}>
                <span className="sr-only">Rank</span>
              </th>
              <th scope="col" className={HEAD}>
                Backtest
              </th>
              {METRICS.map(({ metric, label }) => (
                <th key={metric} scope="col" className={cn(HEAD, NUMBER)}>
                  <SortButton
                    on={metric === by}
                    pressed={metric === by}
                    className={cn(metric === by && 'font-semibold')}
                    onClick={() => setBy(metric)}
                  >
                    {label}
                    {metric === by ? ' ↓' : ''}
                  </SortButton>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {best[by].map((backtest, index) => (
              <Row key={backtest.id} backtest={backtest} rank={index + 1} by={by} />
            ))}
          </tbody>
        </table>
      </div>
    </Widget>
  );
}
