import type { ReactNode } from 'react';

import type { BacktestDocument } from '@/api';
import { HINT } from '@/components/Hint';
import { InstrumentSymbol } from '@/components/InstrumentSymbol';
import { useDateStyle } from '@/features/preferences';
import { cn } from '@/lib/utils';

import { setupOf, TONE, type Metric } from './format';

// The boxes of a backtest's page (mockups/app, .r7-box and the parts in them).

// A card with its 14 px heading and, at the right of it, a legend or a button.
export function ResultBox({
  title,
  aside,
  className,
  children,
}: {
  title: ReactNode;
  aside?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={cn('flex min-w-0 flex-col gap-2.5 rounded-lg border bg-card px-4 py-3.5', className)}>
      <div className="flex flex-wrap items-center justify-between gap-2.5">
        <h2 className="text-sm font-semibold">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

type Swatch = 'line' | 'dashed' | 'up' | 'down';

const SWATCHES: Record<Swatch, string> = {
  line: 'h-[3px] rounded-[2px] bg-primary',
  dashed: 'h-0 border-t-2 border-dashed border-muted-foreground',
  up: 'h-[3px] rounded-[2px] bg-up',
  down: 'h-[3px] rounded-[2px] bg-down',
};

// What the lines and arrows of a chart are (.r3-legend).
export function Legend({ items }: { items: readonly (readonly [Swatch, string])[] }) {
  return (
    <div className="flex flex-wrap gap-3.5 text-xs text-muted-foreground">
      {items.map(([swatch, label]) => (
        <span key={label} className="inline-flex items-center gap-1.5">
          <i aria-hidden="true" className={cn('inline-block w-3.5', SWATCHES[swatch])} />
          {label}
        </span>
      ))}
    </div>
  );
}

// Every metric, its name on the left and its value on the right, a line between them (.r7-all).
export function MetricList({ metrics }: { metrics: readonly Metric[] }) {
  return (
    <dl className="grid grid-cols-[minmax(0,1fr)_auto] text-[13px]">
      {metrics.map(({ name, value, tone }) => (
        <div key={name} className="contents [&:last-child>*]:border-b-0">
          <dt className="border-b py-[5px] text-muted-foreground">{name}</dt>
          <dd className={cn('border-b py-[5px] text-right font-mono font-medium tabular-nums', tone && TONE[tone])}>
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

// The setup it ran with, step by step (.r3-setup): each step's name over what it was and its
// details. The data names its instrument with the icon, the symbol and the name.
export function SetupBox({ doc }: { doc: BacktestDocument }) {
  const dateStyle = useDateStyle();
  const { instrument } = doc.data;
  return (
    <ResultBox title="Setup">
      <div className="flex flex-col gap-3 text-[13px]">
        {setupOf(doc, dateStyle).map(({ step, main, detail }) => (
          <div key={step} className="flex min-w-0 flex-col gap-[3px]">
            <span className="font-mono text-[10.5px]/4 font-medium tracking-[0.06em] text-muted-foreground uppercase">
              {step}
            </span>
            {step === 'Data' ? (
              <InstrumentSymbol id={instrument.id} symbol={instrument.symbol} name={instrument.name} size={20} />
            ) : (
              <b className="font-medium [overflow-wrap:anywhere]">{main}</b>
            )}
            {detail && <span className="font-mono text-[12.5px] text-muted-foreground">{detail}</span>}
          </div>
        ))}
      </div>
    </ResultBox>
  );
}

// While it runs: a bar that moves, as the API gives no progress.
export function RunningBox() {
  return (
    <section className="flex flex-col gap-3 rounded-lg border bg-card p-4">
      <b className="font-medium">Running</b>
      <span className="block h-1.5 overflow-hidden rounded-[3px] bg-surface-2">
        <i className="block h-full w-1/3 animate-result-run rounded-[3px] bg-primary motion-reduce:w-full motion-reduce:animate-none" />
      </span>
      <p className={HINT}>You can close the page. The backtest keeps going.</p>
    </section>
  );
}

// Why it failed (.err): a red dot and the error as the API wrote it.
export function FailedNote({ error }: { error: string }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-md border border-[color-mix(in_srgb,var(--down)_40%,var(--rule))] px-3 py-2.5 text-[13px]"
    >
      <span aria-hidden="true" className="mt-1.5 size-2 flex-none rounded-full bg-down" />
      <span className="min-w-0 break-words">{error}</span>
    </div>
  );
}
