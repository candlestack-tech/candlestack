import { Loader2 } from 'lucide-react';
import type { ReactNode } from 'react';

import type { Market, Model } from '@/api';
import { ACCENT_BUTTON, BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

import { DataStep } from './DataStep';
import { DecisionStep } from './DecisionStep';
import { ExecutionStep } from './ExecutionStep';
import { LAST_STEP, STEPS } from './form';
import { ModelStep } from './ModelStep';
import { Stepper } from './parts';
import { RunStep } from './RunStep';
import { useBuilder } from './useBuilder';

type Builder = ReturnType<typeof useBuilder>;

type Ready = Builder & { model: Model; market: Market };

// The steps after the first, which need a model; Run needs an instrument too.
const LATER_STEPS: Record<number, (builder: Ready) => ReactNode> = {
  1: (builder) => (
    <DataStep
      form={builder.form}
      model={builder.model}
      market={builder.market}
      instrument={builder.instrument}
      today={builder.today}
      problem={builder.stepProblem(1)}
      onChange={builder.change}
    />
  ),
  2: (builder) => (
    <DecisionStep
      form={builder.form}
      invalid={builder.invalid.decision}
      problem={builder.stepProblem(2)}
      onChange={builder.change}
      onReplace={builder.replace}
    />
  ),
  3: (builder) => (
    <ExecutionStep
      form={builder.form}
      invalid={builder.invalid.execution}
      problem={builder.stepProblem(3)}
      onChange={builder.change}
    />
  ),
  4: (builder) =>
    builder.instrument && (
      <RunStep
        form={builder.form}
        model={builder.model}
        instrument={builder.instrument}
        write={builder.write}
        problem={builder.stepProblem(4)}
        onEdit={builder.open}
      />
    ),
};

// The open step's controls. The later steps cannot open without a model.
function StepBody({ builder }: { builder: Builder }) {
  const { step, model, market } = builder;
  if (step === 0) return <ModelStep chosen={model} onChoose={builder.choose} />;
  if (!model || !market) return null;
  return LATER_STEPS[step]?.({ ...builder, model, market });
}

// Back, and Next, which stays off while the step has something to fix; Run backtest on the last
// step, busy while the API starts it.
function Footer({ builder }: { builder: Builder }) {
  const { step } = builder;
  return (
    <div className="flex items-center justify-between gap-2.5 border-t pt-[18px]">
      {step > 0 ? (
        <Button
          type="button"
          variant="outline"
          className={cn(BUTTON_SIZES.default, OUTLINE_BUTTON)}
          onClick={builder.back}
        >
          Back
        </Button>
      ) : (
        <span />
      )}
      {step < LAST_STEP ? (
        <Button
          type="button"
          className={cn(BUTTON_SIZES.default, ACCENT_BUTTON)}
          disabled={!builder.ready}
          onClick={builder.next}
        >
          Next
        </Button>
      ) : (
        <Button
          type="button"
          className={cn(BUTTON_SIZES.lg, ACCENT_BUTTON)}
          disabled={builder.running}
          onClick={builder.start}
        >
          {builder.running && <Loader2 aria-hidden className="animate-spin" />}
          Run backtest
        </Button>
      )}
    </div>
  );
}

// New backtest (#270, mockups/app pageBuilder): five steps, one at a time, in a 760 px column. Run
// exists only on the last step.
export function BacktestBuilder({ modelId }: { modelId: string | undefined }) {
  const builder = useBuilder(modelId);
  return (
    <div className="mx-auto flex max-w-[760px] flex-col gap-[18px]">
      <h1 className="text-3xl font-semibold tracking-tight">New backtest</h1>
      <Stepper step={builder.step} canOpen={builder.canOpen} onOpen={builder.open} />
      <section
        key={builder.step}
        aria-labelledby="builder-step"
        className="flex min-w-0 animate-in flex-col gap-[18px] rounded-xl border bg-card p-6 duration-200 fade-in motion-reduce:animate-none max-[640px]:p-4"
      >
        <h2 id="builder-step" className="text-lg font-semibold tracking-tight">
          {STEPS[builder.step]}
        </h2>
        <StepBody builder={builder} />
        <Footer builder={builder} />
      </section>
    </div>
  );
}
