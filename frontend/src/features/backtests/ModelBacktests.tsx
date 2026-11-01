import { Link } from '@tanstack/react-router';

import { errorMessage, type Backtest } from '@/api';
import { HINT } from '@/components/Hint';
import { formatPercent, formatSharpe } from '@/lib/results';
import { cn } from '@/lib/utils';

import { CompareToggle } from './compare/CompareToggle';
import { useComparedRow } from './compare/selection';
import { compareTarget, hasResults } from './format';
import { BacktestInstruments, BacktestReturn, BacktestWhen, Missing } from './parts';
import { BacktestCell, CELL, COMPARE_CELL, HEAD, NUMBER, ROW, TABLE } from './table';
import { useBacktestsPage, useStarBacktest } from './useBacktests';
import { WIDTH } from './widths';

// The columns with the widths every table of backtests gives them (the instruments take the
// rest), and from which width down each one hides so the table never scrolls sideways.
const COLUMNS: readonly { label: string; number?: boolean; width?: string; hide?: string }[] = [
  { label: 'Backtest', width: WIDTH.backtest },
  { label: 'Instruments', hide: 'max-[420px]:hidden' },
  { label: 'Return', number: true, width: WIDTH.return },
  { label: 'Sharpe', number: true, width: WIDTH.sharpe },
  { label: 'Max drawdown', number: true, width: WIDTH.maxDrawdown, hide: 'max-[760px]:hidden' },
  { label: 'When', width: WIDTH.when, hide: 'max-[560px]:hidden' },
  { label: 'Compare', width: WIDTH.compare },
];

const hideOf = (label: string) => COLUMNS.find((column) => column.label === label)?.hide;

type RowProps = { backtest: Backtest; onStar: (backtest: Backtest) => void };

function Row({ backtest, onStar }: RowProps) {
  const done = hasResults(backtest);
  const compared = useComparedRow(backtest.id);
  return (
    <tr className={cn(ROW, compared)}>
      <td className={CELL}>
        <BacktestCell backtest={backtest} onStar={onStar} />
      </td>
      <td className={cn(CELL, hideOf('Instruments'))}>
        <BacktestInstruments backtest={backtest} max={2} />
      </td>
      <td className={cn(CELL, NUMBER)}>
        <BacktestReturn backtest={backtest} />
      </td>
      <td className={cn(CELL, NUMBER)}>{done ? formatSharpe(backtest.sharpe) : <Missing />}</td>
      <td className={cn(CELL, NUMBER, hideOf('Max drawdown'))}>
        {done ? formatPercent(backtest.max_drawdown) : <Missing />}
      </td>
      <td className={cn(CELL, 'text-muted-foreground', hideOf('When'))}>
        <BacktestWhen backtest={backtest} />
      </td>
      <td className={cn(CELL, COMPARE_CELL)}>
        <CompareToggle target={compareTarget(backtest)} />
      </td>
    </tr>
  );
}

type TableProps = Omit<RowProps, 'backtest'> & { backtests: Backtest[] };

function Table({ backtests, onStar }: TableProps) {
  return (
    <div className="border-t">
      <table className={TABLE}>
        <thead>
          <tr>
            {COLUMNS.map((column) => (
              <th
                key={column.label}
                scope="col"
                className={cn(HEAD, column.width, column.number && NUMBER, column.hide)}
              >
                {column.label === 'Compare' ? <span className="sr-only">{column.label}</span> : column.label}
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
  );
}

// The backtests of one model (mockups/app, pageModel's runs): the Backtests page's list filtered
// by the model's name, its first page, newest first, and a link to all of them when there are
// more. A row opens the backtest; the star works as everywhere.
export function ModelBacktests({ name }: { name: string }) {
  const { data, error } = useBacktestsPage({ model: name });
  const star = useStarBacktest();
  const backtests = data?.backtests ?? [];
  const more = data !== undefined && data.total > backtests.length;
  return (
    <section className="flex min-w-0 flex-col overflow-hidden rounded-lg border bg-card">
      <div className="flex flex-wrap items-baseline justify-between gap-2 p-4">
        <h2 className="text-[15px] font-semibold">
          Backtests {data && <span className="font-normal text-muted-foreground">{data.total}</span>}
        </h2>
        {more && (
          <Link
            to="/backtests"
            search={{ model: name }}
            className="text-[13px] font-medium text-primary underline-offset-2 hover:underline"
          >
            All {data.total}
          </Link>
        )}
      </div>
      {error && <p className={cn(HINT, 'px-4 pb-4')}>{errorMessage(error, 'Could not load the backtests.')}</p>}
      {data && backtests.length === 0 && <p className="px-4 pb-4 text-muted-foreground">None yet</p>}
      {backtests.length > 0 && <Table backtests={backtests} onStar={(item) => star.mutate(item)} />}
    </section>
  );
}
