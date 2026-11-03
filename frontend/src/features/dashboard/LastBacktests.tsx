import type { Backtest } from '@/api';
import { ClockIcon } from '@/components/icons';
import {
  BacktestCell,
  BacktestInstruments,
  BacktestReturn,
  BacktestWhen,
  CELL,
  COMPARE_CELL,
  CompareToggle,
  HEAD,
  NAME_CELL,
  NAME_HEAD,
  compareTarget,
  hasResults,
  Missing,
  NUMBER,
  ROW,
  TABLE,
  WIDTH,
  useComparedRow,
} from '@/features/backtests';
import { formatSharpe } from '@/lib/results';
import { cn } from '@/lib/utils';

import { Widget, WidgetLink } from './widgets';

// When hides in a card under 520 px (the dashboard at 1024 px), so the name keeps its room.
const WHEN = '@max-[520px]:hidden';

// The backtest takes what the others leave, its name up to 150 px; the others take what their
// values need, When on two lines (backtest-names, .dd-card .tbl).
const COLUMNS: readonly { label: string; number: boolean; width?: string; hide?: string }[] = [
  { label: 'Backtest', number: false, width: NAME_HEAD },
  { label: 'Instruments', number: false },
  { label: 'Return', number: true },
  { label: 'Sharpe', number: true },
  { label: 'When', number: false, hide: WHEN },
  { label: 'Compare', number: false, width: WIDTH.compare },
];

type RowProps = { backtest: Backtest; onStar: (backtest: Backtest) => void };

function Row({ backtest, onStar }: RowProps) {
  const compared = useComparedRow(backtest.id);
  return (
    <tr className={cn(ROW, compared)}>
      <td className={cn(CELL, NAME_CELL)}>
        <BacktestCell backtest={backtest} onStar={onStar} />
      </td>
      <td className={CELL}>
        <BacktestInstruments backtest={backtest} max={1} />
      </td>
      <td className={cn(CELL, NUMBER)}>
        <BacktestReturn backtest={backtest} />
      </td>
      <td className={cn(CELL, NUMBER)}>{hasResults(backtest) ? formatSharpe(backtest.sharpe) : <Missing />}</td>
      <td className={cn(CELL, WHEN, 'whitespace-normal text-muted-foreground')}>
        <BacktestWhen backtest={backtest} />
      </td>
      <td className={cn(CELL, COMPARE_CELL)}>
        <CompareToggle target={compareTarget(backtest)} />
      </td>
    </tr>
  );
}

type Props = { backtests: Backtest[] } & Omit<RowProps, 'backtest'>;

// The six newest backtests, failed ones too (mockups/app, ddLast; backtest-names): the star, the
// name and the number, the instrument with its icon, the return, the Sharpe ratio and when it ran.
// All backtests leads to the whole list.
export function LastBacktests({ backtests, onStar }: Props) {
  return (
    <Widget icon={ClockIcon} title="Last backtests" aside={<WidgetLink to="/backtests">All backtests</WidgetLink>}>
      <div className="@container overflow-x-auto">
        <table className={TABLE}>
          <thead>
            <tr>
              {COLUMNS.map(({ label, number, width, hide }) => (
                <th key={label} scope="col" className={cn(HEAD, width, hide, number && NUMBER)}>
                  {label === 'Compare' ? <span className="sr-only">{label}</span> : label}
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
