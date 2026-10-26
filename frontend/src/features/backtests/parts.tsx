import type { Backtest, BacktestStatus } from '@/api';
import { InstrumentSymbol } from '@/components/InstrumentSymbol';
import { FrameworkMini, frameworkOf } from '@/features/models';
import { useViewerDateStyle } from '@/features/preferences';
import { formatRecentTime } from '@/lib/dates';
import { formatPercent, MISSING } from '@/lib/results';
import { cn } from '@/lib/utils';

import { hasResults, instrumentId, instrumentList, instrumentsShown, returnClass, symbolsOf } from './format';

// The small parts every list of backtests draws the same way (mockups/app).

// The instruments with their icons (InstrumentSymbol, #318): up to `max`, or `max - 1` and "+N"
// for the rest, all of them in the title then. On one line: where the column is narrower than
// them, the symbols give way with an ellipsis. `size` 16 is the slimmer row of the dashboard's
// starred backtests.
export function BacktestInstruments({ backtest, max, size = 20 }: { backtest: Backtest; max: number; size?: 16 | 20 }) {
  const { shown, more } = instrumentsShown(backtest.instruments, max);
  return (
    <span
      title={more > 0 ? instrumentList(symbolsOf(backtest)) : undefined}
      className="flex max-w-full min-w-0 items-center gap-2"
    >
      {shown.map((instrument) => (
        <InstrumentSymbol
          key={instrumentId(instrument)}
          id={instrumentId(instrument)}
          symbol={instrument.symbol}
          size={size}
        />
      ))}
      {more > 0 && <span className="shrink-0 font-mono text-muted-foreground">+{more}</span>}
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

// A missing result: a muted en dash.
export function Missing() {
  return <span className="text-muted-foreground">{MISSING}</span>;
}

// The return in bold, green or red; the only result with a colour.
export function ReturnValue({ value }: { value: number }) {
  return <b className={cn('font-semibold', returnClass(value))}>{formatPercent(value)}</b>;
}

// The model by its framework's 14 px mark (.bt-m), the name beside it. Where it has less room than
// it needs (an entry of the Filters panel on a phone), the name ends in an ellipsis; with `wrap`
// (the Backtests table, where long file names are common) it takes up to two lines, broken
// anywhere, and only then ends in one.
export function ModelName({ name, framework, wrap = false }: { name: string; framework: string; wrap?: boolean }) {
  const mark = frameworkOf(framework);
  return (
    <span className={cn('inline-flex max-w-full gap-[7px]', wrap ? 'items-start' : 'items-center')}>
      {mark && <FrameworkMini framework={mark} />}
      <span className={cn('min-w-0', wrap ? 'line-clamp-2 whitespace-normal [overflow-wrap:anywhere]' : 'truncate')}>
        {name}
      </span>
    </span>
  );
}

// A backtest's return, or its badge (Failed, Running) when it has none.
export function BacktestReturn({ backtest }: { backtest: Backtest }) {
  const value = hasResults(backtest) ? backtest.total_return : null;
  return value === null ? <StatusBadge status={backtest.status} /> : <ReturnValue value={value} />;
}

// When it ran, on the viewer's clock in the user's formats: "Today 16:05".
export function BacktestWhen({ backtest }: { backtest: Backtest }) {
  const dateStyle = useViewerDateStyle();
  return formatRecentTime(new Date(backtest.created_at), dateStyle);
}
