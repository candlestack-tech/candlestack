import { Check } from 'lucide-react';
import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

import { STEPS } from './form';

// The builder's own parts (mockups/app, .b-*): the steps along the top, the rows of a step, the
// number with − and +, and a problem in a red box.

// A label in 132 px, then its controls (.b-r); on a phone the label goes above them.
export function BuilderRow({ label, children, top = false }: { label: string; children: ReactNode; top?: boolean }) {
  return (
    <div
      className={cn(
        'grid grid-cols-[132px_minmax(0,1fr)] gap-3.5 max-[640px]:grid-cols-[minmax(0,1fr)] max-[640px]:gap-1.5',
        top ? 'items-start' : 'items-center',
      )}
    >
      <span className="text-[13.5px] text-muted-foreground">{label}</span>
      <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-2">{children}</div>
    </div>
  );
}

// What stops the step or what the API refused (.b-err).
export function StepError({ children }: { children: ReactNode }) {
  return (
    <p
      role="alert"
      className="rounded-lg bg-[color-mix(in_srgb,var(--down)_9%,var(--background))] px-3 py-2.5 text-[13px] break-words text-down"
    >
      {children}
    </p>
  );
}

// The small mono heading of a part of a step (.b-h3): Advanced.
export function StepHeading({ children }: { children: ReactNode }) {
  return (
    <h3 className="font-mono text-[11px]/4 font-medium tracking-[0.06em] text-muted-foreground uppercase">
      {children}
    </h3>
  );
}

const STEPPER_BUTTON =
  'flex h-8 w-[30px] cursor-pointer items-center justify-center text-muted-foreground hover:text-primary disabled:cursor-default disabled:opacity-45';

// A count of candles with − and + (.b-num), 1 to 50; 1 is off.
export function CandlesInput({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <fieldset
      aria-label={label}
      className="m-0 inline-flex h-8 min-w-0 items-center rounded-md border bg-background p-0"
    >
      <button
        type="button"
        className={STEPPER_BUTTON}
        aria-label={`Fewer ${label.toLowerCase()} candles`}
        disabled={value <= 1}
        onClick={() => onChange(Math.max(1, value - 1))}
      >
        −
      </button>
      <span aria-live="polite" className="min-w-[34px] text-center font-mono text-[13px] font-semibold tabular-nums">
        {value > 1 ? value : 'Off'}
      </span>
      <button
        type="button"
        className={STEPPER_BUTTON}
        aria-label={`More ${label.toLowerCase()} candles`}
        disabled={value >= 50}
        onClick={() => onChange(Math.min(50, value + 1))}
      >
        +
      </button>
    </fieldset>
  );
}

type StepperProps = { step: number; canOpen: (step: number) => boolean; onOpen: (step: number) => void };

// A step's number in its circle, or a tick once it is done, and its name.
function StepMark({ index, name, done, open }: { index: number; name: string; done: boolean; open: boolean }) {
  return (
    <>
      <i
        aria-hidden
        className={cn(
          'grid size-[26px] flex-none place-items-center rounded-full border-[1.5px] bg-background font-mono text-xs font-semibold not-italic',
          done && 'border-primary bg-primary text-primary-foreground',
          open && 'border-primary text-primary shadow-[0_0_0_3px_var(--accent-soft)]',
        )}
      >
        {done ? <Check aria-hidden className="size-3.5" strokeWidth={3} /> : index + 1}
      </i>
      <b
        className={cn('text-[13.5px] font-medium', (done || open) && 'text-foreground', !open && 'max-[640px]:hidden')}
      >
        {name}
      </b>
    </>
  );
}

const ITEM = 'flex items-center gap-2 rounded-md px-0.5 py-1 whitespace-nowrap text-muted-foreground';

// The five steps in a row (.b-stp): a done one ticked in the accent and the line after it too,
// the open one ringed, the others it can go to a button. On a phone only the open step keeps its
// name.
export function Stepper({ step, canOpen, onOpen }: StepperProps) {
  return (
    <ol className="flex items-center">
      {STEPS.map((name, index) => {
        const mark = <StepMark index={index} name={name} done={index < step} open={index === step} />;
        return (
          <li
            key={name}
            aria-current={index === step ? 'step' : undefined}
            className={cn(
              'flex min-w-0 flex-1 items-center last:flex-none',
              // On a phone the open step keeps its name whole and the lines around it give way.
              index === step && 'max-[640px]:flex-[1_0_auto]',
              'not-last:after:mx-2.5 not-last:after:h-0.5 not-last:after:flex-1 not-last:after:rounded-full not-last:after:bg-border',
              index < step && 'not-last:after:bg-primary',
            )}
          >
            {index !== step && canOpen(index) ? (
              <button
                type="button"
                className={cn(ITEM, 'cursor-pointer hover:[&_b]:text-foreground')}
                onClick={() => onOpen(index)}
              >
                {mark}
              </button>
            ) : (
              <span className={ITEM}>{mark}</span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
