import { Link, useRouterState } from '@tanstack/react-router';
import type { ReactNode } from 'react';

import type { Backtest } from '@/api';
import { StarButton } from '@/components/StarButton';
import { cn } from '@/lib/utils';

import { BacktestNumber } from './parts';

// The tables of backtests (mockups/app, .tbl): 13 px, the headers medium and muted over a line,
// the rows 8 px high around their text with a line under each but the last, 10 px at the sides
// (.bt-t, .dd-card .tbl). A row opens its backtest: its surface lights up on hover.

export const TABLE = 'w-full border-collapse text-[13px]';
export const HEAD = 'border-b px-2.5 py-2 text-left font-medium whitespace-nowrap text-muted-foreground';
export const CELL = 'border-b px-2.5 py-2 whitespace-nowrap tabular-nums [tr:last-child>&]:border-b-0';
// The numbers and their headers (.num): mono, at the right.
export const NUMBER = 'text-right font-mono';
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

// Where a backtest's page goes back to: the dashboard or the model's page it was opened from,
// else the Backtests page.
function useOpenedFrom(): 'dashboard' | 'model' | undefined {
  return useRouterState({
    select: ({ location }) => {
      if (location.pathname.startsWith('/dashboard')) return 'dashboard';
      return location.pathname.startsWith('/models/') ? 'model' : undefined;
    },
  });
}

// The link to a row's backtest page, stretched over the whole row so the row is one click target
// and the keyboard reaches it; what else the row holds (the star) sits above it.
export function RowOpener({ backtest, children }: { backtest: Backtest; children: ReactNode }) {
  const from = useOpenedFrom();
  return (
    <Link
      to="/backtests/$backtestId"
      params={{ backtestId: backtest.id }}
      state={from && { from }}
      aria-label={`Backtest #${backtest.number}`}
      className={ROW_OPENER}
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

// The star and the number of a backtest, the number opening it (the Backtest column).
export function BacktestCell({ backtest, onStar }: { backtest: Backtest; onStar: (backtest: Backtest) => void }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <RowStar
        starred={backtest.starred}
        label={`Star backtest ${backtest.number}`}
        onToggle={() => onStar(backtest)}
      />
      <RowOpener backtest={backtest}>
        <BacktestNumber backtest={backtest} />
      </RowOpener>
    </span>
  );
}
