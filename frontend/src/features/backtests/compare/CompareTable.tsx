import { Link } from '@tanstack/react-router';
import { X } from 'lucide-react';

import type { CompareMetric, ComparedBacktest } from '@/api';
import { Button } from '@/components/ui/button';
import { BUTTON_SIZES } from '@/components/buttonStyles';
import { useViewerDateStyle } from '@/features/preferences';
import { cn } from '@/lib/utils';

import { AuthorAvatar } from '../experiments/AuthorAvatar';
import { comparedDescription } from '../names';
import { BacktestName } from '../parts';
import { resultRows, setupRows, type CompareRow, type Mark } from './rows';
import { removeFromCompare } from './selection';

// A backtest of the comparison, its place in the answer (`index`) and its slot's colour.
export type CompareColumn = { backtest: ComparedBacktest; index: number; color: string };

const LABEL = 'px-3.5 py-2 text-left align-middle text-[13px] font-normal text-muted-foreground';
const CELL = 'min-w-0 truncate px-3.5 py-2 align-middle text-[13.5px]';
const LINE = 'border-b [tr:last-child>&]:border-b-0';
const MARKS: Record<NonNullable<Mark>, string> = {
  best: 'bg-[color-mix(in_srgb,var(--up)_14%,transparent)] font-semibold text-up',
  worst: 'bg-[color-mix(in_srgb,var(--down)_14%,transparent)] font-semibold text-down',
};

// Whose a backtest is: the initials and the name (or "You"), in a column's head and the Author row.
function Author({ name, text = name, className }: { name: string; text?: string; className: string }) {
  return (
    <span className={cn('flex min-w-0 items-center whitespace-nowrap', className)}>
      <AuthorAvatar name={name} size="xs" />
      <span className="truncate">{text}</span>
    </span>
  );
}

// A column's head: its colour and its name opening it, what it ran on under it, and another
// account's author by initials and name (backtest-names, .cmp-h).
function Heading({ backtest, color }: Omit<CompareColumn, 'index'>) {
  const { id, name, author } = backtest;
  return (
    <th scope="col" className={cn(LINE, 'px-3.5 py-2 text-left align-top font-normal')}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <span aria-hidden="true" className="size-2.5 flex-none rounded-full" style={{ background: color }} />
            <Link
              to="/backtests/$backtestId"
              params={{ backtestId: id }}
              state={{ from: 'compare' }}
              className="flex min-w-0 text-sm/5 font-semibold outline-none hover:text-primary focus-visible:text-primary"
            >
              <BacktestName name={name} />
            </Link>
          </div>
          <span className="mt-px ml-[18px] block truncate font-mono text-xs text-muted-foreground">
            {comparedDescription(backtest)}
          </span>
          {!author.mine && <Author name={author.name} className="mt-[5px] ml-[18px] gap-1.5 text-xs font-medium" />}
        </div>
        <button
          type="button"
          aria-label={`Remove ${name} from compare`}
          title="Remove from compare"
          onClick={() => {
            removeFromCompare(id);
          }}
          className="grid size-7 flex-none cursor-pointer place-items-center rounded-md text-muted-foreground outline-none hover:bg-surface-2 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <X aria-hidden="true" className="size-3.5" />
        </button>
      </div>
    </th>
  );
}

type RowsProps = { title: string; rows: readonly CompareRow[]; mono: boolean; spare: boolean; span: number };

// A group: its title across the table, then a row per value.
function Rows({ title, rows, mono, spare, span }: RowsProps) {
  return (
    <>
      <tr>
        <th
          scope="colgroup"
          colSpan={span}
          className={cn(
            LINE,
            'px-3.5 pt-[18px] pb-2 text-left font-mono text-[11px]/4 font-medium tracking-[0.06em] text-muted-foreground uppercase',
          )}
        >
          {title}
        </th>
      </tr>
      {rows.map(({ label, cells }) => (
        <tr key={label}>
          <th scope="row" className={cn(LINE, LABEL)}>
            {label}
          </th>
          {cells.map(({ text, mark, avatar }, index) => (
            <td
              // eslint-disable-next-line react/no-array-index-key -- the columns have no other key here
              key={index}
              title={text}
              className={cn(LINE, CELL, mono && 'font-mono', mark && MARKS[mark])}
            >
              {avatar ? <Author name={avatar} text={text} className="gap-[7px]" /> : text}
            </td>
          ))}
          {spare && (
            <td className={LINE}>
              <span className="sr-only">No backtest</span>
            </td>
          )}
        </tr>
      ))}
    </>
  );
}

type Props = {
  columns: readonly CompareColumn[];
  metrics: readonly CompareMetric[];
  canAdd: boolean;
  onAdd: () => void;
};

// A column per backtest under its colour, the results with the best of each row in green and the
// worst in red, then the setup. Beside them the way to add one more while there is room.
export function CompareTable({ columns, metrics, canAdd, onAdd }: Props) {
  const style = useViewerDateStyle();
  const backtests = columns.map(({ backtest }) => backtest);
  const results = resultRows(
    metrics,
    columns.map(({ index }) => index),
  );
  const span = 1 + columns.length + (canAdd ? 1 : 0);
  return (
    <section className="mt-4 overflow-x-auto rounded-lg border bg-background">
      <table className="w-full min-w-[760px] table-fixed border-separate border-spacing-0">
        <thead>
          <tr>
            <th scope="col" className={cn(LINE, LABEL, 'w-[180px]')}>
              Backtest
            </th>
            {columns.map((column) => (
              <Heading key={column.backtest.id} backtest={column.backtest} color={column.color} />
            ))}
            {canAdd && (
              <th scope="col" className={cn(LINE, 'px-3.5 py-2 text-left align-middle font-normal')}>
                <Button type="button" variant="outline" className={BUTTON_SIZES.sm} onClick={onAdd}>
                  + Add backtest
                </Button>
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          <Rows title="Results" rows={results} mono spare={canAdd} span={span} />
          <Rows title="Setup" rows={setupRows(backtests, style)} mono={false} spare={canAdd} span={span} />
        </tbody>
      </table>
    </section>
  );
}
