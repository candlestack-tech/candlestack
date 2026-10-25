import type { ComponentProps } from 'react';

import { Hint } from '@/components/Hint';
import { UploadDialog } from '@/features/models';
import { cn } from '@/lib/utils';

import { Widget } from './widgets';

type StepProps = ComponentProps<'button'> & { step: number; title: string; hint: string };

// A step (.dd-gs): its number in a circle, the first one filled with the accent, the title over a
// hint, an arrow at the right. It rises 1 px with a shadow on hover. A button, so a dialog's
// trigger can pass its props on.
function Step({ step, title, hint, className, ...props }: StepProps) {
  return (
    <button
      type="button"
      className={cn(
        'grid w-full cursor-pointer grid-cols-[22px_minmax(0,1fr)_auto] items-start gap-2.5 rounded-lg border bg-background px-3 py-2.5 text-left transition-[border-color,box-shadow,translate] duration-150 outline-none hover:-translate-y-px hover:border-[color-mix(in_srgb,var(--accent)_40%,var(--rule))] hover:shadow-[0_4px_14px_var(--shadow)] focus-visible:ring-[3px] focus-visible:ring-ring/50 motion-reduce:transition-none',
        className,
      )}
      {...props}
    >
      <span
        className={cn(
          'grid size-[22px] place-items-center rounded-full border font-mono text-[11px] font-medium',
          step === 1 && 'border-primary bg-primary text-primary-foreground',
        )}
      >
        {step}
      </span>
      <span>
        <b className="font-medium">{title}</b>
        <span className="block text-[12.5px] text-muted-foreground">{hint}</span>
      </span>
      <span className="text-muted-foreground">→</span>
    </button>
  );
}

// What a new account starts with instead of the quick actions (mockups/app, ddStart): its first
// backtest and its own model. There is no example backtest, so two steps, side by side, one under
// the other up to 860 px.
export function GetStarted({ onNewBacktest }: { onNewBacktest: () => void }) {
  return (
    <Widget title="Get started" aside={<Hint>2 steps</Hint>}>
      <div className="grid grid-cols-2 gap-2 max-[860px]:grid-cols-1">
        <Step
          step={1}
          title="Build your first backtest"
          hint="Pick the data, a representation, a model and a rule"
          onClick={onNewBacktest}
        />
        <UploadDialog>
          <Step step={2} title="Upload your own model" hint="We check that it is a real model with trained weights" />
        </UploadDialog>
      </div>
    </Widget>
  );
}
