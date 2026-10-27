import { Clock } from 'lucide-react';
import type { ReactNode } from 'react';

import type { BacktestDocument, BacktestStep } from '@/api';
import { HINT } from '@/components/Hint';
import { cn } from '@/lib/utils';
import { secondsSince, useNow } from '@/lib/useNow';

import { candlesText, durationText, loadedText, modelText, STEP_NAMES, stepTime } from './format';

// The steps of a running or failed backtest (#316, design "Backtest steps"): each with its mark,
// its name, what it did and how long it took. A running step's time and the wait for the Alpaca
// limit count on in the browser between the page's requests.

type StepId = BacktestStep['id'];

const ORDER: readonly StepId[] = ['candles', 'queue', 'model', 'trades'];
// After this long the candles' step says why a long period takes a while.
const SLOW_S = 10;

const DETAIL = 'text-[12.5px] text-muted-foreground';

type Mark = BacktestStep['state'] | 'not-run';

const MARKS: Record<Mark, ReactNode> = {
  done: (
    <>
      <circle cx="10" cy="10" r="9" className="fill-accent-soft" />
      <path
        d="M6 10.5l2.6 2.6L14 7.6"
        className="fill-none stroke-primary"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </>
  ),
  running: (
    <>
      <circle cx="10" cy="10" r="8" className="fill-none stroke-surface-2" strokeWidth="2.5" />
      <path d="M10 2a8 8 0 0 1 8 8" className="fill-none stroke-primary" strokeWidth="2.5" strokeLinecap="round" />
    </>
  ),
  failed: (
    <>
      <circle cx="10" cy="10" r="9" className="fill-down-soft" />
      <path d="M7 7l6 6M13 7l-6 6" className="stroke-down" strokeWidth="2" strokeLinecap="round" />
    </>
  ),
  pending: <circle cx="10" cy="10" r="8" className="fill-none stroke-muted-foreground/40" strokeWidth="2" />,
  'not-run': (
    <circle
      cx="10"
      cy="10"
      r="8"
      className="fill-none stroke-muted-foreground/40"
      strokeWidth="2"
      strokeDasharray="2 3"
    />
  ),
};

// The mark of a step: a check when done, a turning ring while it runs, a cross when it failed, an
// empty ring before it began, dashed once it never will.
function StepMark({ mark }: { mark: Mark }) {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 20 20"
      aria-hidden="true"
      className={cn(mark === 'running' && 'animate-spin motion-reduce:animate-none')}
    >
      {MARKS[mark]}
    </svg>
  );
}

// The candles as they load: the share of the chunks loaded, the months so far, then either the
// wait for the Alpaca limit counting down or, after a while, why it takes long.
function CandlesProgress({ step, periodStart, now }: { step: BacktestStep; periodStart: number; now: number }) {
  const share = step.total ? Math.min(step.loaded ?? 0, step.total) / step.total : 0;
  const span = loadedText(step.loaded_until, periodStart);
  // The wait shows only while it lasts: the step is saved up to a few seconds after it ended.
  const wait = step.waiting_until === null ? 0 : Math.ceil((Date.parse(step.waiting_until) - now) / 1000);
  const slow = (secondsSince(step.started_at, now) ?? 0) >= SLOW_S;
  return (
    <>
      <progress
        aria-label="Candles loaded"
        max={100}
        value={Math.round(share * 100)}
        className="block h-1.5 w-full appearance-none overflow-hidden rounded-[3px] border-0 bg-surface-2 [&::-moz-progress-bar]:rounded-[3px] [&::-moz-progress-bar]:bg-primary [&::-webkit-progress-bar]:bg-surface-2 [&::-webkit-progress-value]:rounded-[3px] [&::-webkit-progress-value]:bg-primary"
      />
      {span && <span className={DETAIL}>{span}</span>}
      {wait > 0 ? (
        <span className="flex items-center gap-1.5 text-[12.5px] text-warn">
          <Clock aria-hidden="true" className="size-3.5 flex-none" />
          Waiting for the Alpaca request limit. Continues in {wait} s.
        </span>
      ) : (
        slow && (
          <span className={DETAIL}>
            The first load of a long period takes a few minutes. Later backtests take it from the cache.
          </span>
        )
      )}
    </>
  );
}

// Why the step failed: the backtest's error and, when candles were loaded, that they are kept.
function StepError({ error, kept }: { error: string; kept: boolean }) {
  return (
    <div
      role="alert"
      className="flex flex-col gap-1 rounded-md border border-[color-mix(in_srgb,var(--down)_40%,var(--rule))] px-3 py-2.5 text-[13px]"
    >
      <span className="min-w-0 break-words">{error}</span>
      {kept && <span className="text-muted-foreground">The loaded part is kept, so the next run is faster.</span>}
    </div>
  );
}

// What a done step did: the candles it loaded, the worker's times.
function doneDetail(id: StepId, step: BacktestStep): string {
  if (id === 'candles') return candlesText(step);
  return id === 'model' ? modelText(step) : '';
}

type DetailProps = { id: StepId; step: BacktestStep; doc: BacktestDocument; now: number };

// Where the failed step got to, then the backtest's error.
function FailedDetail({ id, step, doc }: DetailProps) {
  const candles = id === 'candles';
  const span = candles && loadedText(step.loaded_until, doc.data.period.start);
  return (
    <>
      {span && <span className={DETAIL}>{span}</span>}
      <StepError error={doc.backtest.error ?? 'The backtest failed.'} kept={candles && (step.loaded ?? 0) > 0} />
    </>
  );
}

// What a step did or does, under its name; a failed one with the backtest's error.
function StepDetail(props: DetailProps) {
  const { id, step, doc, now } = props;
  switch (step.state) {
    case 'done': {
      const text = doneDetail(id, step);
      return text && <span className={DETAIL}>{text}</span>;
    }
    case 'running':
      if (id === 'candles') return <CandlesProgress step={step} periodStart={doc.data.period.start} now={now} />;
      return id === 'model' && <span className={DETAIL}>Loads the model, builds its input, predicts</span>;
    case 'failed':
      return <FailedDetail {...props} />;
    case 'pending':
      return null;
  }
}

// How long a step took, or runs so far.
function timeText(step: BacktestStep, now: number): string {
  if (step.state === 'running') {
    const seconds = secondsSince(step.started_at, now);
    return seconds === null ? '' : durationText(seconds);
  }
  return step.duration_s === null ? '' : stepTime(step.duration_s);
}

const TIME_TONES: Record<BacktestStep['state'], string> = {
  done: 'text-muted-foreground',
  running: '',
  failed: 'text-down',
  pending: '',
};

// At the right of a step: its time, or that it never ran.
function StepTime({ step, notRun, now }: { step: BacktestStep | undefined; notRun: boolean; now: number }) {
  if (notRun) return <span className="text-right text-xs text-muted-foreground">not run</span>;
  return (
    <span className={cn('text-right font-mono text-[13px] whitespace-nowrap', TIME_TONES[step?.state ?? 'pending'])}>
      {step ? timeText(step, now) : ''}
    </span>
  );
}

// A step's state in words for screen readers, which skip the mark. A step that never ran says so
// in its time.
const STATE_WORDS: Record<BacktestStep['state'], string> = {
  done: 'done',
  running: 'running',
  failed: 'failed',
  pending: 'waiting to start',
};

const NAMES: Record<BacktestStep['state'], string> = {
  done: 'font-semibold',
  running: 'font-semibold',
  failed: 'font-semibold text-down',
  pending: 'text-muted-foreground',
};

// The four steps of a running backtest, or of a failed one up to where it failed; the steps after
// a failure did not run. While it runs, it may be left.
export function RunSteps({ doc }: { doc: BacktestDocument }) {
  const { status, steps } = doc.backtest;
  const running = status === 'running';
  const now = useNow(running);
  const byId = new Map(steps.map((step) => [step.id, step]));
  return (
    <section aria-label="Steps" className="flex flex-col rounded-lg border bg-card px-4 pt-1.5 pb-3.5">
      <ol>
        {ORDER.map((id) => {
          const step = byId.get(id);
          const state = step?.state ?? 'pending';
          const notRun = status === 'failed' && state === 'pending';
          // A bar or an error box stands further from the name than a line of text.
          const roomy = state === 'failed' || (state === 'running' && id === 'candles');
          return (
            <li
              key={id}
              className="grid grid-cols-[22px_minmax(0,1fr)_92px] items-start gap-3 border-b py-3 last:border-b-0"
            >
              <StepMark mark={notRun ? 'not-run' : state} />
              <div className={cn('flex min-w-0 flex-col', roomy ? 'gap-2' : 'gap-0.5')}>
                <span className={cn('text-sm', NAMES[state])}>
                  {STEP_NAMES[id]}
                  {!notRun && <span className="sr-only">, {STATE_WORDS[state]}</span>}
                </span>
                {step && <StepDetail id={id} step={step} doc={doc} now={now} />}
              </div>
              <StepTime step={step} notRun={notRun} now={now} />
            </li>
          );
        })}
      </ol>
      {running && <p className={cn(HINT, 'mt-1')}>You can close the page. The backtest keeps going.</p>}
    </section>
  );
}
