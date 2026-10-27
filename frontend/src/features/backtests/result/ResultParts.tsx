import type { ReactNode, Ref } from 'react';

import type { BacktestDocument } from '@/api';
import { InstrumentIcon } from '@/components/InstrumentIcon';
import { FrameworkTile, frameworkOf } from '@/features/models';
import { useDateStyle } from '@/features/preferences';
import { cn } from '@/lib/utils';

import { durationText, setupOf, STEP_NAMES, stepTime, TONE, type Metric } from './format';

// The boxes of a backtest's page (mockups/app, .r7-box and the parts in them).

// A card with its 14 px heading and, at the right of it, a legend or a button.
export function ResultBox({
  title,
  aside,
  className,
  children,
  ref,
}: {
  title: ReactNode;
  aside?: ReactNode;
  className?: string;
  children: ReactNode;
  ref?: Ref<HTMLElement>;
}) {
  return (
    <section ref={ref} className={cn('flex min-w-0 flex-col gap-2.5 rounded-lg border bg-card px-4 py-3.5', className)}>
      <div className="flex flex-wrap items-center justify-between gap-2.5">
        <h2 className="text-sm font-semibold">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
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

const LABEL = 'font-mono text-[10.5px]/4 font-medium tracking-[0.06em] text-muted-foreground uppercase';

// How long a finished run took (#316): in total, then each of its steps.
function RunTimes({ backtest }: { backtest: BacktestDocument['backtest'] }) {
  return (
    <div className="flex flex-col gap-1.5 border-t pt-3">
      <span className={LABEL}>Run</span>
      {backtest.duration_s !== null && <b className="font-medium">{durationText(backtest.duration_s)} in total</b>}
      <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-[3px] text-[12.5px] text-muted-foreground">
        {backtest.steps.map((step) => (
          <div key={step.id} className="contents">
            <dt>{STEP_NAMES[step.id]}</dt>
            <dd className="text-right font-mono">{stepTime(step.duration_s)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

// The model's name beside its framework's mark, in two lines at most (in full in its title).
function ModelName({ model }: { model: BacktestDocument['model'] }) {
  const framework = frameworkOf(model.framework);
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      {framework && <FrameworkTile framework={framework} />}
      <b title={model.name} className="line-clamp-2 leading-[1.35] font-medium [overflow-wrap:anywhere]">
        {model.name}
      </b>
    </div>
  );
}

// The instrument's icon beside its symbol over its name.
function InstrumentName({ instrument }: { instrument: BacktestDocument['data']['instrument'] }) {
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      <InstrumentIcon id={instrument.id} size={28} />
      <span className="flex min-w-0 flex-col">
        <b className="font-mono font-medium">{instrument.symbol}</b>
        <span title={instrument.name} className="truncate text-[12.5px] text-muted-foreground">
          {instrument.name}
        </span>
      </span>
    </div>
  );
}

// The setup it ran with (#309): the builder's steps as groups between rules, one value per row.
// The model starts with its name and mark, the data with the instrument. A finished run adds how
// long it took.
export function SetupBox({ doc }: { doc: BacktestDocument }) {
  const dateStyle = useDateStyle();
  const { backtest } = doc;
  return (
    <ResultBox title="Setup">
      <div className="flex flex-col text-[13px]">
        {setupOf(doc, dateStyle).map(({ name, rows }) => (
          <div key={name} className="flex min-w-0 flex-col gap-2 border-t py-3 first:border-t-0 first:pt-0">
            <span className={LABEL}>{name}</span>
            {name === 'Model' && <ModelName model={doc.model} />}
            {name === 'Data' && <InstrumentName instrument={doc.data.instrument} />}
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-[5px]">
              {rows.map(([label, value]) => (
                <div key={label} className="contents">
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd className="text-right font-mono text-[12.5px] [overflow-wrap:anywhere]">{value}</dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
        {backtest.status === 'finished' && backtest.steps.length > 0 && <RunTimes backtest={backtest} />}
      </div>
    </ResultBox>
  );
}

// Why it failed when it has no steps (it failed before they were recorded, .err): a red dot and
// the error as the API wrote it.
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
