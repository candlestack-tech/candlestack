import { Check, Minus, XIcon } from 'lucide-react';
import type { ReactNode } from 'react';

import type { Manifest, Model } from '@/api';
import { FactsStrip } from '@/components/FactsStrip';
import { HINT } from '@/components/Hint';
import { useViewerDateStyle } from '@/features/preferences';
import { formatRecentDayInline } from '@/lib/dates';
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
  const dateStyle = useViewerDateStyle();
  return (
    <div className="mb-[18px] flex flex-wrap items-center gap-3">
      <FrameworkTile framework={framework} size="xl" />
      <div className="flex min-w-0 flex-col gap-0.5">
        <h1 className="text-2xl/[30px] font-semibold tracking-[-0.02em] [overflow-wrap:anywhere]">{model.name}</h1>
        <span className="text-[13px] text-muted-foreground">
          {framework} · {formatSize(model.size_bytes)} · added{' '}
          {formatRecentDayInline(new Date(model.created_at), dateStyle)}
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
  return <FactsStrip facts={facts.map(([term, value]) => ({ term, value }))} />;
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

// `passed`; `failed`, with why in its hint; `todo`, not done yet; `none`, nothing to check (no
// example).
type CheckState = 'passed' | 'failed' | 'todo' | 'none';
type CheckItem = { state: CheckState; title: string; hint?: string | undefined };

const SPOKEN: Record<CheckState, string> = {
  passed: 'Passed',
  failed: 'Failed',
  todo: 'Not yet',
  none: 'Not checked',
};

const MARK: Record<CheckState, string> = {
  passed: 'bg-[color-mix(in_srgb,var(--up)_15%,var(--bg))] text-up',
  failed: 'bg-[color-mix(in_srgb,var(--down)_15%,var(--bg))] text-down',
  todo: 'bg-surface-2 text-muted-foreground',
  none: 'bg-surface-2 text-muted-foreground',
};

const ICON: Record<CheckState, ReactNode> = {
  passed: <Check aria-hidden="true" />,
  failed: <XIcon aria-hidden="true" />,
  todo: <Minus aria-hidden="true" />,
  none: <Minus aria-hidden="true" />,
};

// A check (.ck-r): passed on a green circle, failed on a red one with why under it, not done yet
// (or nothing to check) on a muted one with the title muted.
function CheckRow({ state, title, hint }: CheckItem) {
  const muted = state === 'todo' || state === 'none';
  return (
    <li className="flex items-start gap-2.5">
      <span
        className={cn('grid size-[22px] flex-none place-items-center rounded-full [&_svg]:size-[13px]', MARK[state])}
      >
        {ICON[state]}
      </span>
      <span className="min-w-0">
        <span className="sr-only">{SPOKEN[state]}: </span>
        <b className={cn('font-medium', muted && 'text-muted-foreground', state === 'failed' && 'text-down')}>
          {title}
        </b>
        {hint && <span className={cn('block break-words', HINT)}>{hint}</span>}
      </span>
    </li>
  );
}

const NO_EXAMPLE: CheckItem = { state: 'none', title: 'No example' };

// The example while it waits for a trial run: compared then, or nothing to compare without one.
const exampleLater = (manifest: Manifest | undefined, hint: string): CheckItem =>
  manifest?.example ? { state: 'todo', title: 'Example', hint } : NO_EXAMPLE;

// The trial run in the worker and its example once the model has a manifest: not run yet
// (`checked_at` null), passed, or failed in the trial run or on the example (`check_failed`, with
// the worker's sentences in `check_error`). The example is compared only after a trial run that
// passed, and only when the manifest has one.
function workerChecks(model: Model, manifest: Manifest | undefined): CheckItem[] {
  const error = model.check_error ?? undefined;
  if (model.state === 'verified') {
    return [
      { state: 'passed', title: 'Trial run' },
      { state: 'passed', title: 'Example matches' },
    ];
  }
  if (model.check_failed === 'example') {
    return [
      { state: 'passed', title: 'Trial run' },
      { state: 'failed', title: 'Example does not match', hint: error },
    ];
  }
  if (model.check_failed === 'trial_run' || error !== undefined) {
    return [{ state: 'failed', title: 'Trial run failed', hint: error }, exampleLater(manifest, 'Not compared')];
  }
  if (model.checked_at !== null) return [{ state: 'passed', title: 'Trial run' }, NO_EXAMPLE];
  return [{ state: 'todo', title: 'Trial run', hint: 'Not run yet' }, exampleLater(manifest, 'Not compared yet')];
}

// What the model passed: the file's checks on upload, the manifest's, then the trial run in the
// worker and its example, which make it verified.
function checksOf(model: Model, manifest: Manifest | undefined): CheckItem[] {
  const shapes = model.input_shape !== null && model.output_shape !== null;
  const checks: CheckItem[] = [
    { state: 'passed', title: 'Opens, no custom code' },
    { state: 'passed', title: 'Trained weights' },
    shapes ? { state: 'passed', title: 'One input, one output' } : { state: 'todo', title: 'Shape not in the file' },
  ];
  const summary = model.manifest_summary;
  if (model.state === 'needs_setup' || !summary) {
    return [
      ...checks,
      { state: 'todo', title: 'Manifest' },
      { state: 'todo', title: 'Trial run' },
      { state: 'todo', title: 'Example' },
    ];
  }
  return [
    ...checks,
    { state: 'passed', title: `Manifest fits ${formatDims(summary.input_shape)}` },
    ...workerChecks(model, manifest),
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
