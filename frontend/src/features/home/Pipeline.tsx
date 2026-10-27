import { Fragment } from 'react';

import { Eyebrow } from '@/components/Eyebrow';
import { cn } from '@/lib/utils';

type Step = { eyebrow: string; name: string; layer?: boolean; items: string[] };

// The path of every backtest, from the candles to the results; the three layers are what a user
// swaps.
const STEPS: Step[] = [
  { eyebrow: 'Input', name: 'Market data', items: ['14,000+ stocks and pairs', '1m to 1d candles', 'since 2016'] },
  {
    eyebrow: 'Layer 1',
    name: 'Representation',
    layer: true,
    items: ['Renko · Kagi · P&F', 'Heikin-Ashi · Line break', 'Range and volume bars', 'Time windows'],
  },
  {
    eyebrow: 'Layer 2',
    name: 'Model',
    layer: true,
    items: ['Your trained model', 'Keras, PyTorch, ONNX', 'Input shape check'],
  },
  { eyebrow: 'Layer 3', name: 'Decision', layer: true, items: ['Threshold', 'Stop loss', 'Smoothing'] },
  { eyebrow: 'Output', name: 'Results', items: ['Return, Sharpe', 'Drawdown', 'Trades on the chart'] },
];

function PipelineStep({ step }: { step: Step }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-2">
      <Eyebrow>{step.eyebrow}</Eyebrow>
      <div
        className={cn(
          'rounded-lg border bg-card px-3 py-2.5 font-semibold',
          step.layer && 'border-primary bg-accent-soft text-primary',
        )}
      >
        {step.name}
      </div>
      <ul className="flex flex-col gap-[3px] pl-0.5 font-mono text-[12.5px]/[normal] text-muted-foreground">
        {step.items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

// The wire between two steps, level with their boxes. A short dash runs along it, each wire 0.6 s
// after the one before, so the dashes travel the pipeline in turn; still in the middle of the wire
// when the user asks for less motion. On a phone, a short still line down between the stacked
// steps.
function Wire({ delay }: { delay: number }) {
  return (
    <div
      aria-hidden
      className="relative mt-[45px] h-0.5 flex-[0_0_44px] overflow-hidden bg-border max-sm:my-2 max-sm:ml-4 max-sm:h-auto max-sm:w-0.5 max-sm:flex-[0_0_20px]"
    >
      <span
        className="absolute -top-0.5 -left-1.5 size-1.5 animate-pipeline-flow rounded-full bg-primary motion-reduce:left-1/2 motion-reduce:animate-none max-sm:hidden"
        style={{ animationDelay: `${delay}ms` }}
      />
    </div>
  );
}

// The five steps side by side; narrower screens scroll them inside the card, not the page, and a
// phone stacks them.
export function Pipeline() {
  return (
    <div className="overflow-x-auto rounded-lg border bg-card p-[22px]">
      <div className="flex min-w-[880px] items-start max-sm:min-w-0 max-sm:flex-col max-sm:items-stretch">
        {STEPS.map((step, index) => (
          <Fragment key={step.name}>
            {index > 0 && <Wire delay={(index - 1) * 600} />}
            <PipelineStep step={step} />
          </Fragment>
        ))}
      </div>
    </div>
  );
}
