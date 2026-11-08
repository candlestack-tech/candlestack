import { useEffect, useRef } from 'react';

import type { Backtest } from '@/api';
import { Tick } from '@/components/Tick';
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

// The cell of a checkbox: the 18 px box after 8 px, none after it.
const SELECT_CELL = 'pr-0';

// A checkbox of the table, drawn as the app's 18 px box (Tick) over a visually hidden input that
// keeps the focus and the keyboard. Above the row's own link, so a click selects and does not open
// the row. `mixed` is the select-all box while only some rows are chosen.
function SelectBox({
  label,
  on,
  mixed = false,
  onChange,
}: {
  label: string;
  on: boolean;
  mixed?: boolean;
  onChange: (on: boolean) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (input.current) input.current.indeterminate = mixed;
  }, [mixed]);
  return (
    <label className="relative z-10 flex cursor-pointer">
      <input
        ref={input}
        type="checkbox"
        aria-label={label}
        checked={on}
        onChange={(event) => onChange(event.target.checked)}
        className="peer sr-only"
      />
      <Tick on={on} mixed={mixed} className="mt-0" />
    </label>
  );
}

type Selection = {
  // The IDs chosen; those of other pages do not count here.
  selected: ReadonlySet<string>;
  onSelect: (ids: readonly string[], on: boolean) => void;
};

type HeaderProps = { search: BacktestsSearch; onSort: (column: BacktestsColumn) => void };

// The box that chooses every row of the page, or none once all are chosen.
function SelectAll({ backtests, selected, onSelect }: Selection & { backtests: readonly Backtest[] }) {
  const chosen = backtests.filter((backtest) => selected.has(backtest.id)).length;
  const all = backtests.length > 0 && chosen === backtests.length;
  return (
    <SelectBox
      label="Select all on this page"
      on={all}
      mixed={chosen > 0 && !all}
      onChange={() =>
        onSelect(
          backtests.map((backtest) => backtest.id),
          !all,
        )
      }
    />
  );
}

// The headers; a click sorts by the column (withSortBy), the sorted one ink with ↓ or ↑. The first
// chooses every row of the page.
function Header({ search, onSort, ...selection }: HeaderProps & Selection & { backtests: readonly Backtest[] }) {
  return (
    <thead>
      <tr>
        <th scope="col" className={cn(HEAD, WIDTH.select, SELECT_CELL)}>
          <SelectAll {...selection} />
        </th>
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

type RowProps = Pick<Selection, 'onSelect'> & {
  backtest: Backtest;
  selected: boolean;
  onStar: (backtest: Backtest) => void;
};

// A finished backtest shows its results; a failed or running one its badge and dashes.
function Row({ backtest, selected, onSelect, onStar }: RowProps) {
  const done = hasResults(backtest);
  const result = (text: string) => (done ? text : <Missing />);
  const compared = useComparedRow(backtest.id);
  return (
    <tr className={cn(ROW, compared)}>
      <td className={cn(CELL, SELECT_CELL)}>
        <SelectBox label={`Select ${backtest.name}`} on={selected} onChange={(on) => onSelect([backtest.id], on)} />
      </td>
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

type Props = HeaderProps &
  Selection & {
    backtests: readonly Backtest[];
    // What the one row says when there are none: "No backtests match", or "No backtests yet".
    empty: string;
    onStar: (backtest: Backtest) => void;
  };

// The Backtests page's table (mockups/app, btTable; backtest-names): the star, the name and the
// number, the instruments with their icons, the model by its mark, the results in mono at the
// right, and when it ran. Its layout is fixed: the columns keep their widths and the backtest
// takes the rest. A checkbox before each row, and one for the whole page, choose what to delete.
export function BacktestsTable({ backtests, empty, search, onSort, onStar, selected, onSelect }: Props) {
  return (
    <table className={cn(TABLE, 'table-fixed')}>
      <Header search={search} onSort={onSort} backtests={backtests} selected={selected} onSelect={onSelect} />
      <tbody>
        {backtests.map((backtest) => (
          <Row
            key={backtest.id}
            backtest={backtest}
            selected={selected.has(backtest.id)}
            onSelect={onSelect}
            onStar={onStar}
          />
        ))}
        {backtests.length === 0 && (
          <tr>
            <td colSpan={COLUMNS.length + 1} className="p-6 text-center text-muted-foreground">
              {empty}
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
