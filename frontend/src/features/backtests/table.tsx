import { Link } from '@tanstack/react-router';
import type { ReactNode } from 'react';

import type { Backtest } from '@/api';
import { StarButton } from '@/components/StarButton';
import { cn } from '@/lib/utils';

import { BacktestNameNumber, ModelName, PublicMark } from './parts';
import { useOpenedFrom } from './useOpenedFrom';

// The tables of backtests (mockups/app, .tbl): 13 px, the headers medium and muted over a line,
// the rows 8 px high around their text with a line under each but the last, 8 px at the sides
// to leave names room (backtest-names, .bt-t, .dd-card .tbl). A row opens its backtest: its
// surface lights up on hover.

export const TABLE = 'w-full border-collapse text-[13px]';
export const HEAD = 'border-b px-2 py-2 text-left font-medium whitespace-nowrap text-muted-foreground';
export const CELL = 'border-b px-2 py-2 whitespace-nowrap tabular-nums [tr:last-child>&]:border-b-0';
// The Backtest column of a table whose other columns take what their values need: it takes the
// rest, and the name gives way inside it (the cells' max-w-0 lets the column be narrower than the
// name, which then ends in an ellipsis).
export const NAME_HEAD = 'w-full';
export const NAME_CELL = 'max-w-0';
// The numbers and their headers (.num): mono, at the right.
export const NUMBER = 'text-right font-mono';
// The cell of the scale icon: its 32 px button sets the height of no row.
export const COMPARE_CELL = 'px-1 py-0 text-center';
export const ROW =
  'relative cursor-pointer [&>td]:transition-colors [&>td]:duration-150 hover:[&>td]:bg-muted motion-reduce:[&>td]:transition-none';

// A header that sorts or ranks the list (.dd-sort): ink on hover and while on, with its arrow.
export function SortButton({
  on,
  pressed,
  className,
  onClick,
  children,
}: {
  on: boolean;
  pressed?: boolean;
  className?: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={cn(
        'cursor-pointer whitespace-nowrap transition-colors duration-150 outline-none hover:text-foreground focus-visible:text-foreground focus-visible:underline motion-reduce:transition-none',
        on && 'text-foreground',
        className,
      )}
    >
      {children}
    </button>
  );
}

// What opens a row, stretched over it (::after). With the keyboard's focus the row gets a ring
// inside its edge; a click shows none, as in the mockup.
export const ROW_OPENER =
  'cursor-pointer outline-none after:absolute after:inset-0 focus-visible:text-primary focus-visible:after:rounded-md focus-visible:after:ring-2 focus-visible:after:ring-ring/60 focus-visible:after:ring-inset';

// The link to a row's backtest page, stretched over the whole row so the row is one click target
// and the keyboard reaches it; what else the row holds (the star) sits above it. Screen readers
// hear the backtest's name, or `label` where it says more (a card of Experiments). It lays out
// what it holds on one line, the name giving way.
export function RowOpener({
  backtest,
  label,
  children,
}: {
  backtest: Pick<Backtest, 'id' | 'name'>;
  label?: string;
  children: ReactNode;
}) {
  const from = useOpenedFrom();
  return (
    <Link
      to="/backtests/$backtestId"
      params={{ backtestId: backtest.id }}
      state={from && { from }}
      aria-label={label ?? backtest.name}
      className={cn(ROW_OPENER, 'flex min-w-0 items-center')}
    >
      {children}
    </Link>
  );
}

// The star of a row in a list or a table: above the row's own click target, so it does not open
// the row.
export function RowStar({ starred, label, onToggle }: { starred: boolean; label: string; onToggle: () => void }) {
  return <StarButton starred={starred} label={label} onToggle={onToggle} className="relative z-10" />;
}

// The star, the name and the number of a backtest, the name opening it (the Backtest column), and
// the globe once it is in Experiments (backtest-names, .bcell).
export function BacktestCell({ backtest, onStar }: { backtest: Backtest; onStar: (backtest: Backtest) => void }) {
  return (
    <span className="flex max-w-full min-w-0 items-center gap-1.5">
      <RowStar starred={backtest.starred} label={`Star ${backtest.name}`} onToggle={() => onStar(backtest)} />
      <RowOpener backtest={backtest}>
        <BacktestNameNumber backtest={backtest} />
      </RowOpener>
      {backtest.public && <PublicMark />}
    </span>
  );
}

const MODEL = 'inline-flex max-w-full min-w-0 align-middle text-[12px]';

// The model of a row in the Backtests table (#315): one step smaller than the row, its name on up
// to two lines and then cut with an ellipsis, the full name in the title. While the model
// exists, a link to its page above the row's own link.
export function ModelCell({ model }: { model: Backtest['model'] }) {
  const name = <ModelName name={model.name} framework={model.framework} wrap />;
  if (!model.id) {
    return (
      <span title={model.name} className={MODEL}>
        {name}
      </span>
    );
  }
  return (
    <Link
      to="/models/$modelId"
      params={{ modelId: model.id }}
      title={model.name}
      className={cn(
        MODEL,
        'relative z-10 outline-none hover:text-primary focus-visible:text-primary focus-visible:underline',
      )}
    >
      {name}
    </Link>
  );
}
