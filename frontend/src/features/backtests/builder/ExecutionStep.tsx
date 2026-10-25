import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Switch } from '@/components/ui/switch';
import { BenchmarkSelect, UnitInput } from '@/features/settings';

import { BENCHMARKS_AVAILABLE, type BuilderForm, type ExecutionField } from './form';
import { fillLine } from './format';
import { BuilderRow, StepError } from './parts';

// The fees and the slippage are as wide as their numbers (.b-unit).
const NARROW = 'w-[180px]';

type Props = {
  form: BuilderForm;
  invalid: ReadonlySet<ExecutionField>;
  problem: string | undefined;
  onChange: (change: Partial<BuilderForm>) => void;
};

// The candle that decides and the next one, and where each fill lands.
function FillSketch() {
  return (
    <svg viewBox="0 0 400 112" aria-hidden="true" className="block h-auto w-full">
      <line x1="70" x2="70" y1="12" y2="92" className="stroke-muted-foreground" />
      <rect x="56" y="24" width="28" height="56" rx="2" className="fill-candle-up" />
      <line x1="210" x2="210" y1="26" y2="96" className="stroke-muted-foreground" />
      <rect x="196" y="36" width="28" height="48" rx="2" className="fill-candle-down" />
      <line x1="96" x2="96" y1="8" y2="96" strokeDasharray="3 3" className="stroke-foreground" />
      <text x="102" y="16" className="fill-foreground text-[10.5px]">
        decides at the close
      </text>
      <circle cx="196" cy="36" r="5" className="fill-primary" />
      <text x="232" y="40" className="fill-foreground text-[10.5px]">
        on: buys at the next open
      </text>
      <circle cx="84" cy="24" r="5" className="fill-warn" />
      <text x="102" y="30" className="fill-muted-foreground font-mono text-[10.5px]">
        off: at the same close
      </text>
      <text x="58" y="108" className="fill-muted-foreground font-mono text-[10.5px]">
        candle t
      </text>
      <text x="190" y="108" className="fill-muted-foreground font-mono text-[10.5px]">
        candle t+1
      </text>
    </svg>
  );
}

// What honest fill means (.b-ex), behind the i beside the switch.
function FillInfo() {
  return (
    <Popover>
      <PopoverTrigger
        aria-label="What honest fill means"
        className="inline-grid size-[22px] cursor-pointer place-items-center rounded-full border bg-background p-0 font-mono text-[11px] font-semibold text-muted-foreground outline-none hover:border-primary hover:text-primary focus-visible:ring-[3px] focus-visible:ring-ring/50 data-[state=open]:border-primary data-[state=open]:text-primary"
      >
        i
      </PopoverTrigger>
      <PopoverContent align="end" className="flex w-[min(380px,calc(100vw-40px))] flex-col gap-2.5 p-3.5">
        <FillSketch />
        <p className="text-[12.5px] text-muted-foreground">
          A live run trades only after the candle that decided has closed. Off, the backtest buys at that same close,
          which looks better than it could be.
        </p>
      </PopoverContent>
    </Popover>
  );
}

// The fees and the slippage, in percent as in Settings.
function CostRows({ form, invalid, onChange }: Omit<Props, 'problem'>) {
  return (
    <>
      <BuilderRow label="Fees">
        <UnitInput
          className={NARROW}
          label="Fees"
          unit="% per side"
          value={form.fees}
          invalid={invalid.has('fees')}
          onChange={(fees) => onChange({ fees })}
        />
      </BuilderRow>
      <BuilderRow label="Slippage">
        <UnitInput
          className={NARROW}
          label="Slippage"
          unit="%"
          value={form.slippage}
          invalid={invalid.has('slippage')}
          onChange={(slippage) => onChange({ slippage })}
        />
      </BuilderRow>
    </>
  );
}

// Step 4 (stepExecution): the fill, fees, slippage and the benchmark.
export function ExecutionStep({ form, invalid, problem, onChange }: Props) {
  return (
    <>
      <BuilderRow label="Honest fill">
        <Switch
          aria-label="Honest fill"
          checked={form.honestFill}
          onCheckedChange={(honestFill) => onChange({ honestFill })}
        />
        <b className="font-mono text-[13.5px] font-semibold">{fillLine(form.honestFill)}</b>
        <FillInfo />
      </BuilderRow>
      <CostRows form={form} invalid={invalid} onChange={onChange} />
      <BuilderRow label="Benchmark">
        <BenchmarkSelect
          value={form.benchmark}
          available={BENCHMARKS_AVAILABLE}
          onChange={(benchmark) => onChange({ benchmark })}
        />
      </BuilderRow>
      {problem && <StepError>{problem}</StepError>}
    </>
  );
}
