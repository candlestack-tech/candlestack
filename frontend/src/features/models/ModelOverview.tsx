import { Check, Minus } from 'lucide-react';
import type { ReactNode } from 'react';

import type { Manifest, Model } from '@/api';
import { HINT } from '@/components/Hint';
import { useDateStyle } from '@/features/preferences';
import { formatRecentDay } from '@/lib/dates';
import { formatSharpe } from '@/lib/results';
import { cn } from '@/lib/utils';

import { formatSize } from './format';
import { FrameworkTile } from './FrameworkMark';
import { API_FRAMEWORKS } from './frameworks';
import { InputMatrix } from './InputMatrix';
import { formatDims, formatReads, formatWarmUp } from './manifest';
import { StatePill } from './StatePill';

// The parts of a model's page (mockups/app, pageModel, .p5-*): one screen, nothing to switch.

// A box of the page (.p5-box): a card with its heading.
export function PageBox({ title, children, className }: { title: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn('flex min-w-0 flex-col gap-3 rounded-lg border bg-card p-4', className)}>
      <h2 className="text-[15px] font-semibold">{title}</h2>
      {children}
    </section>
  );
}

// The framework's mark, the name over what it is, the state, and the page's buttons (.p5-hd).
export function ModelHeader({ model, actions }: { model: Model; actions: ReactNode }) {
  const framework = API_FRAMEWORKS[model.framework];
  const dateStyle = useDateStyle();
  return (
    <div className="mb-[18px] flex flex-wrap items-center gap-3">
      <FrameworkTile framework={framework} size="xl" />
      <div className="flex min-w-0 flex-col gap-0.5">
        <h1 className="text-2xl/[30px] font-semibold tracking-[-0.02em] [overflow-wrap:anywhere]">{model.name}</h1>
        <span className="text-[13px] text-muted-foreground">
          {framework} · {formatSize(model.size_bytes)} · added {formatRecentDay(new Date(model.created_at), dateStyle)}
        </span>
      </div>
      <StatePill state={model.state} />
      <div className="ml-auto flex flex-wrap gap-2 max-[860px]:ml-0 max-[860px]:w-full">{actions}</div>
    </div>
  );
}

// The numbers in a strip (.p5-facts): what it reads and its warm-up (the input without a
// manifest), then its backtests and their best Sharpe.
export function ModelFacts({ model }: { model: Model }) {
  const summary = model.manifest_summary;
  const facts: [string, string][] = summary
    ? [
        ['Reads', formatReads(summary)],
        ['Warm-up', formatWarmUp(summary)],
      ]
    : [['Input', model.input_shape ? formatDims(model.input_shape) : '–']];
  facts.push(['Backtests', String(model.backtest_count)], ['Best Sharpe', formatSharpe(model.best_sharpe)]);
  return (
    <dl className="mb-4 flex flex-wrap gap-px overflow-hidden rounded-[10px] border bg-border">
      {facts.map(([term, value]) => (
        <div key={term} className="min-w-0 flex-[1_1_110px] bg-background px-3.5 py-2.5">
          <dt className="text-xs text-muted-foreground">{term}</dt>
          <dd className="mt-0.5 font-mono text-[15px] font-medium whitespace-nowrap tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

// The input as a matrix with its shape in the heading.
export function ModelInput({ manifest }: { manifest: Manifest | undefined }) {
  const shape = manifest ? formatDims(manifest.signature.input.shape) : '';
  return (
    <PageBox
      title={
        <>
          Input <span className="font-mono font-normal text-muted-foreground">{shape}</span>
        </>
      }
    >
      {manifest ? (
        <InputMatrix manifest={manifest} hint="Click a column to see what it is." />
      ) : (
        <p className={HINT}>Loading the manifest…</p>
      )}
    </PageBox>
  );
}

type CheckItem = { passed: boolean; title: string; hint?: string };

// A check (.ck-r): passed on a green circle, not done yet on a muted one with the title muted.
function CheckRow({ passed, title, hint }: CheckItem) {
  return (
    <li className="flex items-start gap-2.5">
      <span
        className={cn(
          'grid size-[22px] flex-none place-items-center rounded-full [&_svg]:size-[13px]',
          passed ? 'bg-[color-mix(in_srgb,var(--up)_15%,var(--bg))] text-up' : 'bg-surface-2 text-muted-foreground',
        )}
      >
        {passed ? <Check aria-hidden="true" /> : <Minus aria-hidden="true" />}
      </span>
      <span className="min-w-0">
        <span className="sr-only">{passed ? 'Passed' : 'Not yet'}: </span>
        <b className={cn('font-medium', !passed && 'text-muted-foreground')}>{title}</b>
        {hint && <span className={cn('block', HINT)}>{hint}</span>}
      </span>
    </li>
  );
}

// What the model passed: the file's checks on upload, the manifest's, then the trial run in the
// worker and its example, which make it verified.
function checksOf(model: Model, manifest: Manifest | undefined): CheckItem[] {
  const shapes = model.input_shape !== null && model.output_shape !== null;
  const checks: CheckItem[] = [
    { passed: true, title: 'Opens, no custom code' },
    { passed: true, title: 'Trained weights' },
    { passed: shapes, title: shapes ? 'One input, one output' : 'Shape not in the file' },
  ];
  const summary = model.manifest_summary;
  if (model.state === 'needs_setup' || !summary) {
    return [
      ...checks,
      { passed: false, title: 'Manifest' },
      { passed: false, title: 'Trial run' },
      { passed: false, title: 'Example' },
    ];
  }
  checks.push({ passed: true, title: `Manifest fits ${formatDims(summary.input_shape)}` });
  if (model.state === 'verified') {
    return [...checks, { passed: true, title: 'Trial run' }, { passed: true, title: 'Example matches' }];
  }
  return [
    ...checks,
    { passed: false, title: 'Trial run', hint: 'Not run yet' },
    manifest?.example
      ? { passed: false, title: 'Example', hint: 'Not compared yet' }
      : { passed: false, title: 'No example' },
  ];
}

export function ModelChecks({ model, manifest }: { model: Model; manifest: Manifest | undefined }) {
  return (
    <PageBox title="Checks">
      <ul className="flex flex-col gap-2.5">
        {checksOf(model, manifest).map((check) => (
          <CheckRow key={check.title} {...check} />
        ))}
      </ul>
    </PageBox>
  );
}
