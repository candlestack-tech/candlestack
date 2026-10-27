import { Link } from '@tanstack/react-router';
import type { ComponentProps } from 'react';

import type { Backtest } from '@/api';
import { DashboardCard } from '@/components/DashboardCard';
import { returnTone } from '@/lib/results';
import { cn } from '@/lib/utils';

import { sparkPaths } from './sparkline';

// The parts the dashboard's widgets share (mockups/app, ddDash).

// A widget: the dashboard's card with its parts touching (.dd-card).
export function Widget(props: Omit<ComponentProps<typeof DashboardCard>, 'flush'>) {
  return <DashboardCard {...props} flush />;
}

// The accent link at a widget's right (.link): "All backtests".
export function WidgetLink({ to, children }: { to: '/backtests'; children: string }) {
  return (
    <Link
      to={to}
      className="text-[13px] font-medium text-primary underline-offset-2 transition-colors duration-150 outline-none hover:underline focus-visible:underline motion-reduce:transition-none"
    >
      {children}
    </Link>
  );
}

// Starred widgets show their best four; with more, the row under a line leads to all of them
// (.dd-more): "More … 5 starred →".
export const TOP = 4;

export function MoreRow({ total, to }: { total: number; to: '/backtests' | '/models' }) {
  if (total <= TOP) return null;
  return (
    <Link
      to={to}
      className="mt-1.5 flex w-full justify-between gap-2.5 border-t px-1.5 pt-2.5 text-[12.5px] font-medium text-muted-foreground transition-colors duration-150 outline-none hover:text-foreground focus-visible:text-foreground motion-reduce:transition-none"
    >
      <span>More</span>
      <span>{total} starred →</span>
    </Link>
  );
}

// A row of a starred list (.dd-sr): the whole row lights up on hover and opens what it stands for.
export const STARRED_ROW =
  'relative grid w-full items-center gap-2.5 rounded-md px-1.5 py-2 text-left transition-colors duration-150 hover:bg-muted motion-reduce:transition-none';

// A small equity line, 72 × 24 px: the strategy in the colour of its return (green from zero, red
// below) over a faint area, buy and hold a thin muted line behind it. A dashed line this small
// broke into dots, and the accent made a loss look like a gain.
export function Sparkline({ equity, total }: { equity: NonNullable<Backtest['equity']>; total: number }) {
  const paths = sparkPaths(equity.strategy, equity.benchmark, 72, 24);
  const up = returnTone(total) === 'up';
  return (
    <svg viewBox="0 0 72 24" aria-hidden="true" className="h-6 w-[72px] flex-none">
      <path d={paths.benchmark} className="fill-none stroke-muted-foreground/60 stroke-[1.2]" />
      <path d={paths.area} className={cn('stroke-none opacity-15', up ? 'fill-up' : 'fill-down')} />
      <path
        d={paths.strategy}
        className={cn('fill-none stroke-[1.8] [stroke-linejoin:round]', up ? 'stroke-up' : 'stroke-down')}
      />
    </svg>
  );
}
