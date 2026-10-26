import type { Backtest } from '@/api';
import { ClockIcon } from '@/components/icons';
import {
  BacktestCell,
  BacktestInstruments,
  BacktestReturn,
  BacktestWhen,
  CELL,
  HEAD,
  hasResults,
  Missing,
  NUMBER,
  ROW,
  TABLE,
  WIDTH,
} from '@/features/backtests';
import { formatSharpe } from '@/lib/results';
import { cn } from '@/lib/utils';

import { Widget, WidgetLink } from './widgets';

// When hides in a card under 500 px (the dashboard at 1024 px), so the instruments keep 100 px
// or more and nothing is cut.
const WHEN = '@max-[500px]:hidden';

// With the widths every table of backtests gives them; the instruments take the rest.
const COLUMNS: readonly { label: string; number: boolean; width?: string; hide?: string }[] = [
  { label: 'Backtest', number: false, width: WIDTH.backtest },
  { label: 'Instruments', number: false },
  { label: 'Return', number: true, width: WIDTH.return },
  { label: 'Sharpe', number: true, width: WIDTH.sharpe },
  { label: 'When', number: false, width: WIDTH.when, hide: WHEN },
];

type RowProps = { backtest: Backtest; onStar: (backtest: Backtest) => void };

function Row({ backtest, onStar }: RowProps) {
  return (
    <tr className={ROW}>
      <td className={CELL}>
        <BacktestCell backtest={backtest} onStar={onStar} />
      </td>
      <td className={CELL}>
        <BacktestInstruments backtest={backtest} max={2} />
      </td>
      <td className={cn(CELL, NUMBER)}>
        <BacktestReturn backtest={backtest} />
      </td>
      <td className={cn(CELL, NUMBER)}>{hasResults(backtest) ? formatSharpe(backtest.sharpe) : <Missing />}</td>
      <td className={cn(CELL, WHEN, 'text-muted-foreground')}>
        <BacktestWhen backtest={backtest} />
      </td>
    </tr>
  );
}

type Props = { backtests: Backtest[] } & Omit<RowProps, 'backtest'>;

// The six newest backtests, failed ones too (mockups/app, ddLast): the star and the number, up to
// two instruments with their icons in the width the fixed layout leaves them, the return, the
// Sharpe ratio and when it ran. All backtests leads to the whole list.
export function LastBacktests({ backtests, onStar }: Props) {
  return (
    <Widget icon={ClockIcon} title="Last backtests" aside={<WidgetLink to="/backtests">All backtests</WidgetLink>}>
      <div className="@container overflow-x-auto">
        <table className={cn(TABLE, 'table-fixed')}>
          <thead>
            <tr>
              {COLUMNS.map(({ label, number, width, hide }) => (
                <th key={label} scope="col" className={cn(HEAD, width, hide, number && NUMBER)}>
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
