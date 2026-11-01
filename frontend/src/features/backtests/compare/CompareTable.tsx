import { Link } from '@tanstack/react-router';
import { X } from 'lucide-react';

import type { BacktestDocument } from '@/api';
import { Button } from '@/components/ui/button';
import { BUTTON_SIZES } from '@/components/buttonStyles';
import { useViewerDateStyle } from '@/features/preferences';
import { cn } from '@/lib/utils';

import { resultRows, setupRows, type CompareRow, type Mark } from './rows';
import { removeFromCompare } from './selection';

export type CompareColumn = { doc: BacktestDocument; color: string };

const LABEL = 'px-3.5 py-2 text-left align-middle text-[13px] font-normal text-muted-foreground';
const CELL = 'min-w-0 truncate px-3.5 py-2 align-middle text-[13.5px]';
const LINE = 'border-b [tr:last-child>&]:border-b-0';
const MARKS: Record<NonNullable<Mark>, string> = {
  best: 'bg-[color-mix(in_srgb,var(--up)_14%,transparent)] font-semibold text-up',
  worst: 'bg-[color-mix(in_srgb,var(--down)_14%,transparent)] font-semibold text-down',
};

function Heading({ doc, color }: CompareColumn) {
  const { id, number } = doc.backtest;
  return (
    <th scope="col" className={cn(LINE, 'px-3.5 py-2 text-left align-top font-normal')}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm">
            <span aria-hidden="true" className="size-2.5 flex-none rounded-full" style={{ background: color }} />
            <Link
              to="/backtests/$backtestId"
              params={{ backtestId: id }}
              state={{ from: 'compare' }}
              className="font-mono font-medium outline-none hover:text-primary focus-visible:text-primary"
            >
              #{number}
            </Link>
            <span className="truncate font-semibold">{doc.data.instrument.symbol}</span>
          </div>
          <div title={doc.backtest.name ?? doc.model.name} className="mt-0.5 truncate text-xs text-muted-foreground">
            {doc.backtest.name ?? doc.model.name}
          </div>
        </div>
        <button
          type="button"
          aria-label={`Remove backtest ${number} from compare`}
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
          {cells.map(({ text, mark }, index) => (
            <td
              // eslint-disable-next-line react/no-array-index-key -- the columns have no other key here
              key={index}
              title={text}
              className={cn(LINE, CELL, mono && 'font-mono', mark && MARKS[mark])}
            >
              {text}
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

type Props = { columns: readonly CompareColumn[]; canAdd: boolean; onAdd: () => void };

// A column per backtest under its colour, the results with the best of each row in green and the
// worst in red, then the setup. Beside them the way to add one more while there is room.
export function CompareTable({ columns, canAdd, onAdd }: Props) {
  const style = useViewerDateStyle();
  const docs = columns.map(({ doc }) => doc);
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
              <Heading key={column.doc.backtest.id} {...column} />
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
          <Rows title="Results" rows={resultRows(docs)} mono spare={canAdd} span={span} />
          <Rows title="Setup" rows={setupRows(docs, style)} mono={false} spare={canAdd} span={span} />
        </tbody>
      </table>
    </section>
  );
}
