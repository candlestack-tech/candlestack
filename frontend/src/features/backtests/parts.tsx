import type { Backtest, BacktestStatus } from '@/api';
import { FrameworkMini, frameworkOf } from '@/features/models';
import { useViewerDateStyle } from '@/features/preferences';
import { formatRecentTime } from '@/lib/dates';
import { formatPercent, MISSING } from '@/lib/results';
import { cn } from '@/lib/utils';

import { chipsOf, hasResults, returnClass, symbolsOf } from './format';

// The small parts every list of backtests draws the same way (mockups/app).

// The instruments as mono chips without icons (.ichips): up to `max`, the rest as "+N". `tight`
// is the slimmer chip of the dashboard's starred rows.
export function InstrumentChips({
  backtest,
  max,
  tight = false,
}: {
  backtest: Backtest;
  max: number;
  tight?: boolean;
}) {
  return (
    <span className="inline-flex flex-nowrap gap-1">
      {chipsOf(symbolsOf(backtest), max).map((chip) => (
        <span
          key={chip}
          className={cn(
            'rounded-full border bg-background font-mono font-medium text-foreground',
            tight ? 'px-1.5 text-[11px]/[normal]' : 'px-[7px] py-px text-[11.5px]/[normal]',
          )}
        >
          {chip}
        </span>
      ))}
    </span>
  );
}

// "#12", the user's own number of the backtest (.rid).
export function BacktestNumber({ backtest, className }: { backtest: Backtest; className?: string }) {
  return <span className={cn('font-mono text-[13px]/[normal] font-medium', className)}>#{backtest.number}</span>;
}

const BADGES: Record<BacktestStatus, { text: string; tone: string }> = {
  finished: { text: 'Finished', tone: 'text-up border-[color-mix(in_srgb,var(--up)_45%,var(--rule))]' },
  running: { text: 'Running', tone: 'text-primary border-[color-mix(in_srgb,var(--accent)_45%,var(--rule))]' },
  failed: { text: 'Failed', tone: 'text-down border-[color-mix(in_srgb,var(--down)_45%,var(--rule))]' },
};

// How far a backtest got (.badge.ok, .badge.run, .badge.bad): Finished in green, Running in the
// accent, Failed in red.
export function StatusBadge({ status }: { status: BacktestStatus }) {
  const { text, tone } = BADGES[status];
  return (
    <span
      className={cn(
        'inline-block rounded-[4px] border px-1.5 py-px font-mono text-[11px]/4 font-medium whitespace-nowrap',
        tone,
      )}
    >
      {text}
    </span>
  );
}

// The red Failed badge (.badge.bad) where a failed backtest has no return.
export function FailedBadge() {
  return <StatusBadge status="failed" />;
}

// A missing result: a muted en dash.
export function Missing() {
  return <span className="text-muted-foreground">{MISSING}</span>;
}

// The return in bold, green or red; the only result with a colour.
export function ReturnValue({ value }: { value: number }) {
  return <b className={cn('font-semibold', returnClass(value))}>{formatPercent(value)}</b>;
}

// The model by its framework's 14 px mark (.bt-m), the name beside it. Where it has less room than
// it needs (an entry of the Filters panel on a phone), the name ends in an ellipsis.
export function ModelName({ name, framework }: { name: string; framework: string }) {
  const mark = frameworkOf(framework);
  return (
    <span className="inline-flex max-w-full items-center gap-[7px]">
      {mark && <FrameworkMini framework={mark} />}
      <span className="min-w-0 truncate">{name}</span>
    </span>
  );
}

// A backtest's return, or the Failed badge when it has none.
export function BacktestReturn({ backtest }: { backtest: Backtest }) {
  const value = hasResults(backtest) ? backtest.total_return : null;
  return value === null ? <FailedBadge /> : <ReturnValue value={value} />;
}

// When it ran, on the viewer's clock in the user's formats: "Today 16:05".
export function BacktestWhen({ backtest }: { backtest: Backtest }) {
  const dateStyle = useViewerDateStyle();
  return formatRecentTime(new Date(backtest.created_at), dateStyle);
}
