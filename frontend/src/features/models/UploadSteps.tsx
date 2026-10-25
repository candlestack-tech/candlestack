import { Check, ChevronDown, LoaderCircle, XIcon } from 'lucide-react';
import type { ReactNode } from 'react';

import { BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

// The parts of the upload in three steps (mockups/app, .d4-*): a numbered step, the facts a done
// step folds into, the error a failed one shows in its place, the bar where model and manifest
// meet.

// `on`: the step in work; `busy`: its file is being checked; `done`; `bad`: it failed; `wait`: a
// later step, its title only.
export type StepState = 'on' | 'busy' | 'done' | 'bad' | 'wait';

const NUMBER: Record<StepState, string> = {
  on: 'bg-primary text-primary-foreground',
  busy: 'bg-primary text-primary-foreground',
  done: 'bg-[color-mix(in_srgb,var(--up)_16%,var(--bg))] text-up',
  bad: 'bg-[color-mix(in_srgb,var(--down)_15%,var(--bg))] text-down',
  wait: 'bg-surface-2 text-muted-foreground',
};

// What screen readers hear before the title of a step that is done or failed.
const SPOKEN: Partial<Record<StepState, string>> = { done: 'Done:', bad: 'Failed:', busy: 'Checking:' };

const ICONS: Partial<Record<StepState, ReactNode>> = {
  done: <Check />,
  bad: <XIcon />,
  busy: <LoaderCircle className="animate-spin" />,
};

function StepNumber({ n, state }: { n: number; state: StepState }) {
  const icon = ICONS[state];
  return (
    <span
      aria-hidden="true"
      className={cn(
        'grid size-7 place-items-center rounded-full font-mono text-[13px] font-semibold [&_svg]:size-3.5',
        NUMBER[state],
      )}
    >
      {icon ?? n}
    </span>
  );
}

type StepProps = { n: number; state: StepState; title: string; children?: ReactNode };

// A step (.d4-s): its number in a circle, a line down to the next one (green once it is done),
// the title and what the step shows.
export function Step({ n, state, title, children }: StepProps) {
  return (
    <li
      className={cn(
        'relative grid grid-cols-[28px_minmax(0,1fr)] gap-3.5 pb-5 last:pb-0',
        'not-last:before:absolute not-last:before:top-[30px] not-last:before:bottom-1 not-last:before:left-[13.5px] not-last:before:w-px not-last:before:bg-border',
        state === 'done' && 'not-last:before:bg-[color-mix(in_srgb,var(--up)_45%,var(--rule))]',
      )}
    >
      <StepNumber n={n} state={state} />
      <div className="flex min-w-0 flex-col gap-2.5 pt-[3px]">
        <h3 className={cn('text-[15px]/[22px] font-semibold', state === 'wait' && 'font-medium text-muted-foreground')}>
          {SPOKEN[state] && <span className="sr-only">{SPOKEN[state]}</span>} {title}
        </h3>
        {children}
      </div>
    </li>
  );
}

export function Steps({ children }: { children: ReactNode }) {
  return <ol className="flex flex-col">{children}</ol>;
}

// The file a step took and what else goes with it on the line (.d4-ok).
export function StepLine({ name, children }: { name: string; children?: ReactNode }) {
  return (
    <p className="flex min-w-0 flex-wrap items-baseline gap-2.5">
      <b className="font-medium [overflow-wrap:anywhere]">{name}</b>
      {children}
    </p>
  );
}

// A button in the look of a link (.link): Change, Add it now.
export function LinkButton({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="cursor-pointer rounded-sm text-[13px] font-medium text-primary underline-offset-2 outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50"
    >
      {children}
    </button>
  );
}

// A link out (the manifest format, how to export to ONNX), in a new tab.
export function OutLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener"
      className="font-medium text-primary underline-offset-2 hover:underline"
    >
      {children}
    </a>
  );
}

// What a done step found, on one line that wraps (.d4-facts).
export function Facts({ facts }: { facts: [string, ReactNode][] }) {
  return (
    <dl className="flex flex-wrap gap-x-[22px] gap-y-1.5">
      {facts.map(([term, value]) => (
        <div key={term} className="min-w-0">
          <dt className="text-[11.5px] text-muted-foreground">{term}</dt>
          <dd className="mt-px text-[13.5px]">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

// Why a step failed, in its place (.d4-err): what is wrong, the details, and Choose another file.
export function StepError({ title, lines, onAgain }: { title: string; lines: string[]; onAgain: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-col gap-1 rounded-lg border border-[color-mix(in_srgb,var(--down)_40%,var(--rule))] bg-[color-mix(in_srgb,var(--down)_6%,var(--bg))] px-3.5 py-3 text-[13px] break-words"
    >
      <b className="font-semibold text-down">{title}</b>
      {lines.map((line, index) => (
        <span key={index}>{line}</span>
      ))}
      <div className="mt-1.5">
        <Button type="button" variant="outline" className={cn(BUTTON_SIZES.sm, OUTLINE_BUTTON)} onClick={onAgain}>
          Choose another file
        </Button>
      </div>
    </div>
  );
}

type MatchProps = { model: string; manifest: string; expanded: boolean; onToggle: () => void };

// The model's input and the manifest's side by side, equal (.d4-match); it opens the input below.
export function MatchBar({ model, manifest, expanded, onToggle }: MatchProps) {
  return (
    <button
      type="button"
      aria-expanded={expanded}
      onClick={onToggle}
      className="group flex w-full cursor-pointer items-center gap-2.5 rounded-lg border border-[color-mix(in_srgb,var(--up)_40%,var(--rule))] bg-[color-mix(in_srgb,var(--up)_8%,var(--bg))] px-3 py-[9px] text-left text-[13px] outline-none hover:border-up focus-visible:ring-[3px] focus-visible:ring-ring/50"
    >
      {/* The spaces between the parts make one sentence for screen readers; the flex gap draws them. */}
      <span className="font-mono text-[12.5px]">model {model}</span>{' '}
      <span className="font-mono text-[15px] font-semibold text-up">=</span>{' '}
      <span className="font-mono text-[12.5px]">manifest {manifest}</span>
      <span className="grid size-5 place-items-center rounded-full bg-[color-mix(in_srgb,var(--up)_18%,var(--bg))] text-up [&_svg]:size-3">
        <Check aria-hidden="true" />
      </span>
      <ChevronDown
        aria-hidden="true"
        className="ml-auto size-4 text-muted-foreground transition-transform duration-150 group-aria-expanded:rotate-180"
      />
    </button>
  );
}
