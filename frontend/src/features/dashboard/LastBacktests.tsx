import type { Backtest } from '@/api';
import { ClockIcon } from '@/components/icons';
import {
  BacktestCell,
  BacktestReturn,
  BacktestWhen,
  CELL,
  HEAD,
  hasResults,
  InstrumentChips,
  Missing,
  NUMBER,
  ROW,
  TABLE,
} from '@/features/backtests';
import { formatSharpe } from '@/lib/results';
import { cn } from '@/lib/utils';

import { Widget, WidgetLink } from './widgets';

const COLUMNS = [
  { label: 'Backtest', number: false },
  { label: 'Instruments', number: false },
  { label: 'Return', number: true },
  { label: 'Sharpe', number: true },
  { label: 'When', number: false },
];

type RowProps = { backtest: Backtest; onStar: (backtest: Backtest) => void };

function Row({ backtest, onStar }: RowProps) {
  return (
    <tr className={ROW}>
      <td className={CELL}>
        <BacktestCell backtest={backtest} onStar={onStar} />
      </td>
      <td className={CELL}>
        <InstrumentChips backtest={backtest} max={3} />
      </td>
      <td className={cn(CELL, NUMBER)}>
        <BacktestReturn backtest={backtest} />
      </td>
      <td className={cn(CELL, NUMBER)}>{hasResults(backtest) ? formatSharpe(backtest.sharpe) : <Missing />}</td>
      <td className={cn(CELL, 'text-muted-foreground')}>
        <BacktestWhen backtest={backtest} />
      </td>
    </tr>
  );
}

type Props = { backtests: Backtest[] } & Omit<RowProps, 'backtest'>;

// The six newest backtests, failed ones too (mockups/app, ddLast): the star and the number, up to
// three instruments, the return, the Sharpe ratio and when it ran. All backtests leads to the
// whole list.
export function LastBacktests({ backtests, onStar }: Props) {
  return (
    <Widget icon={ClockIcon} title="Last backtests" aside={<WidgetLink to="/backtests">All backtests</WidgetLink>}>
      <div className="overflow-x-auto">
        <table className={TABLE}>
          <thead>
            <tr>
              {COLUMNS.map(({ label, number }) => (
                <th key={label} scope="col" className={cn(HEAD, number && NUMBER)}>
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {backtests.map((backtest) => (
              <Row key={backtest.id} backtest={backtest} onStar={onStar} />
            ))}
          </tbody>
        </table>
      </div>
    </Widget>
  );
}
