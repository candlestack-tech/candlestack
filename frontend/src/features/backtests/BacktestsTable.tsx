import type { Backtest } from '@/api';
import { formatPercent, formatSharpe } from '@/lib/results';
import { cn } from '@/lib/utils';
import type { BacktestsColumn, BacktestsSearch } from '@/routes/backtestsSearch';

import { CompareToggle } from './compare/CompareToggle';
import { useComparedRow } from './compare/selection';
import { compareTarget, hasResults } from './format';
import { BacktestInstruments, BacktestReturn, BacktestWhen, Missing } from './parts';
import { BacktestCell, CELL, COMPARE_CELL, HEAD, ModelCell, NUMBER, ROW, SortButton, TABLE } from './table';
import { WIDTH } from './widths';

// The columns of the approved designs (#315, #376) with their fixed widths; the backtest takes the
// rest, its name up to 150 px, and a backtest without results shows its badge as its return
// (backtest-names, .bt-t). From which width down each column hides, so the table never scrolls
// sideways and the name keeps about 100 px or more: Max drawdown at 1200 px, When at 800 and
// between 980 and 1080 (the sidebar beside the page leaves it narrow there), Model at 720, Sharpe
// at 560, Instruments at 480. The trades are left out. The backtest sorts by name, A to Z first;
// Instruments do not sort.
const COLUMNS: readonly { label: string; sort?: BacktestsColumn; width?: string; hide?: string; number?: boolean }[] = [
  { label: 'Backtest', sort: 'name' },
  { label: 'Instruments', width: WIDTH.instruments, hide: 'max-[480px]:hidden' },
  { label: 'Model', sort: 'model', width: WIDTH.model, hide: 'max-[720px]:hidden' },
  { label: 'Return', sort: 'return', width: WIDTH.return, number: true },
  { label: 'Sharpe', sort: 'sharpe', width: WIDTH.sharpe, hide: 'max-[560px]:hidden', number: true },
  {
    label: 'Max drawdown',
    sort: 'max_drawdown',
    width: WIDTH.maxDrawdown,
    hide: 'max-[1200px]:hidden',
    number: true,
  },
  {
    label: 'When',
    sort: 'when',
    width: WIDTH.when,
    hide: 'max-[800px]:hidden min-[980px]:max-[1080px]:hidden',
  },
  { label: 'Compare', width: WIDTH.compare },
];
const hideOf = (label: string) => COLUMNS.find((column) => column.label === label)?.hide;

type HeaderProps = { search: BacktestsSearch; onSort: (column: BacktestsColumn) => void };

// The headers; a click sorts by the column (withSortBy), the sorted one ink with ↓ or ↑.
function Header({ search, onSort }: HeaderProps) {
  return (
    <thead>
      <tr>
        {COLUMNS.map(({ label, sort, width, hide, number }) => {
          const on = sort !== undefined && search.sort === sort;
          const arrow = on ? (search.order === 'asc' ? ' ↑' : ' ↓') : '';
          return (
            <th
              key={label}
              scope="col"
              aria-sort={on ? (search.order === 'asc' ? 'ascending' : 'descending') : undefined}
              className={cn(HEAD, width, hide, number && NUMBER)}
            >
              {sort ? (
                <SortButton on={on} onClick={() => onSort(sort)}>
                  {label}
                  {arrow}
                </SortButton>
              ) : label === 'Compare' ? (
                <span className="sr-only">{label}</span>
              ) : (
                label
              )}
            </th>
          );
        })}
      </tr>
    </thead>
  );
}

type RowProps = { backtest: Backtest; onStar: (backtest: Backtest) => void };

// A finished backtest shows its results; a failed or running one its badge and dashes.
function Row({ backtest, onStar }: RowProps) {
  const done = hasResults(backtest);
  const result = (text: string) => (done ? text : <Missing />);
  const compared = useComparedRow(backtest.id);
  return (
    <tr className={cn(ROW, compared)}>
      <td className={CELL}>
        <BacktestCell backtest={backtest} onStar={onStar} />
      </td>
      <td className={cn(CELL, hideOf('Instruments'))}>
        <BacktestInstruments backtest={backtest} max={1} />
      </td>
      <td className={cn(CELL, hideOf('Model'))}>
        <ModelCell model={backtest.model} />
      </td>
      <td className={cn(CELL, NUMBER)}>
        <BacktestReturn backtest={backtest} />
      </td>
      <td className={cn(CELL, NUMBER, hideOf('Sharpe'))}>{result(formatSharpe(backtest.sharpe))}</td>
      <td className={cn(CELL, NUMBER, hideOf('Max drawdown'))}>{result(formatPercent(backtest.max_drawdown))}</td>
      <td className={cn(CELL, hideOf('When'), 'whitespace-normal text-muted-foreground')}>
        <BacktestWhen backtest={backtest} />
      </td>
      <td className={cn(CELL, COMPARE_CELL)}>
        <CompareToggle target={compareTarget(backtest)} />
      </td>
    </tr>
  );
}

type Props = HeaderProps & {
  backtests: readonly Backtest[];
  // What the one row says when there are none: "No backtests match", or "No backtests yet".
  empty: string;
  onStar: (backtest: Backtest) => void;
};

// The Backtests page's table (mockups/app, btTable; backtest-names): the star, the name and the
// number, the instruments with their icons, the model by its mark, the results in mono at the
// right, and when it ran. Its layout is fixed: the columns keep their widths and the backtest
// takes the rest.
export function BacktestsTable({ backtests, empty, search, onSort, onStar }: Props) {
  return (
    <table className={cn(TABLE, 'table-fixed')}>
      <Header search={search} onSort={onSort} />
      <tbody>
        {backtests.map((backtest) => (
          <Row key={backtest.id} backtest={backtest} onStar={onStar} />
        ))}
        {backtests.length === 0 && (
          <tr>
            <td colSpan={COLUMNS.length} className="p-6 text-center text-muted-foreground">
              {empty}
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
