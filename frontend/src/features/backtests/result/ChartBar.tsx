import { cn } from '@/lib/utils';

import { RANGES, type RangeName } from './chartNavigation';

// The bar under a backtest's chart (#309): the ranges on the left; on the right the size of the
// candles, Candles or Line and the Trades switch.

const TEXT_BUTTON =
  'rounded px-2 py-1 font-mono text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50';

// Green and red candles, as the chart draws them.
function CandlesIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M4.5 2v2.5M4.5 11v3" className="stroke-candle-up" strokeWidth="1.3" strokeLinecap="round" />
      <rect x="2.5" y="4.5" width="4" height="6.5" rx="1" className="fill-candle-up" />
      <path d="M11.5 1.5v3M11.5 10v4.5" className="stroke-candle-down" strokeWidth="1.3" strokeLinecap="round" />
      <rect x="9.5" y="4.5" width="4" height="5.5" rx="1" className="fill-candle-down" />
    </svg>
  );
}

// The line in the price line's blue.
function LineIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <path
        d="M1.5 12l4-4.5 3 2.5 6-6.5"
        className="stroke-price-line"
        strokeWidth="1.6"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// A buy's green triangle up and a sell's red one down.
function TradesIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
      <path d="M4 1.5l3 4.5H1z" className="fill-primary" />
      <path d="M10 12.5L7 8h6z" className="fill-down" />
    </svg>
  );
}

export type Kind = 'candles' | 'line';

const KINDS = [
  ['candles', 'Candles', CandlesIcon],
  ['line', 'Line', LineIcon],
] as const;

function KindToggle({ kind, onKind }: { kind: Kind; onKind: (kind: Kind) => void }) {
  return (
    <fieldset aria-label="Chart type" className="m-0 flex min-w-0 gap-0.5 rounded-md border-0 bg-muted p-0.5">
      {KINDS.map(([value, label, Icon]) => (
        <button
          key={value}
          type="button"
          aria-pressed={kind === value}
          aria-label={label}
          title={label}
          onClick={() => onKind(value)}
          className={cn(
            'grid h-6 w-7 place-items-center rounded outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
            kind === value ? 'bg-surface-2' : 'opacity-55 hover:opacity-100',
          )}
        >
          <Icon />
        </button>
      ))}
    </fieldset>
  );
}

function TradesSwitch({ on, onChange }: { on: boolean; onChange: (on: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      title="Show or hide the trades on the chart"
      onClick={() => onChange(!on)}
      className="group inline-flex items-center gap-2 rounded px-2 py-1 outline-none hover:bg-muted focus-visible:ring-[3px] focus-visible:ring-ring/50"
    >
      <span className={cn(!on && 'opacity-40')}>
        <TradesIcon />
      </span>
      <span>Trades</span>
      <span
        aria-hidden="true"
        className={cn(
          'relative h-4 w-7 rounded-full transition-colors motion-reduce:transition-none',
          on ? 'bg-primary' : 'bg-surface-2',
        )}
      >
        <span
          className={cn(
            'absolute top-0.5 left-0.5 size-3 rounded-full transition-transform motion-reduce:transition-none',
            on ? 'translate-x-3 bg-background' : 'bg-foreground',
          )}
        />
      </span>
    </button>
  );
}

type Props = {
  range: RangeName | null;
  onRange: (range: RangeName) => void;
  label: string;
  loading: boolean;
  kind: Kind;
  onKind: (kind: Kind) => void;
  trades: boolean;
  onTrades: (on: boolean) => void;
};

export function ChartBar({ range, onRange, label, loading, kind, onKind, trades, onTrades }: Props) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-t px-2 py-1 text-xs">
      <fieldset aria-label="Range" className="m-0 flex min-w-0 items-center gap-0.5 border-0 p-0">
        {RANGES.map(([name]) => (
          <button
            key={name}
            type="button"
            aria-pressed={range === name}
            onClick={() => onRange(name)}
            className={cn(TEXT_BUTTON, range === name && 'text-primary hover:text-primary')}
          >
            {name}
          </button>
        ))}
      </fieldset>
      <div className="flex items-center gap-1.5">
        <span
          title="Candles grow as you zoom out"
          aria-busy={loading}
          className={cn('px-2 font-mono text-muted-foreground', loading && 'animate-pulse')}
        >
          {label}
        </span>
        <KindToggle kind={kind} onKind={onKind} />
        <span aria-hidden="true" className="mx-1.5 h-3.5 w-px bg-border" />
        <TradesSwitch on={trades} onChange={onTrades} />
      </div>
    </div>
  );
}
