import type { Market, Model } from '@/api';
import { Hint } from '@/components/Hint';
import { isDay, PeriodInput } from '@/features/settings';
import { cn } from '@/lib/utils';

import { earliestStart, quickPeriods, type BuilderForm, type ChosenInstrument } from './form';
import { InstrumentPicker } from './InstrumentPicker';
import { BuilderRow, StepError } from './parts';

type Change = (change: Partial<BuilderForm>) => void;

type Props = {
  form: BuilderForm;
  model: Model;
  market: Market;
  instrument: ChosenInstrument | undefined;
  today: string;
  // Why the period cannot run, or what the API refused.
  problem: string | undefined;
  onChange: Change;
};

const CHIP =
  'h-7 cursor-pointer rounded-full border bg-background px-2.5 font-mono text-xs font-medium text-foreground transition-colors hover:border-[color-mix(in_srgb,var(--accent)_45%,var(--rule))]';

type PeriodProps = { form: BuilderForm; earliest: string | undefined; today: string; onChange: Change };

// The first and the last day as in Settings, then the quick periods (.chips).
function PeriodRow({ form, earliest, today, onChange }: PeriodProps) {
  return (
    <BuilderRow label="Period" top>
      <div className="flex w-full flex-col gap-2">
        <div className="flex items-center gap-2">
          <PeriodInput
            label="From"
            value={form.from}
            invalid={!isDay(form.from)}
            onChange={(from) => onChange({ from })}
          />
          <span className="text-muted-foreground">to</span>
          <PeriodInput label="To" value={form.to} invalid={!isDay(form.to)} onChange={(to) => onChange({ to })} />
        </div>
        <div className="flex flex-wrap gap-1.5">
          {quickPeriods(today, earliest).map((period) => (
            <button
              key={period.label}
              type="button"
              className={cn(CHIP, form.from === period.from && form.to === period.to && 'border-primary text-primary')}
              onClick={() => onChange({ from: period.from, to: period.to })}
            >
              {period.label}
            </button>
          ))}
        </div>
      </div>
    </BuilderRow>
  );
}

// Step 2 (stepData): one instrument of the model's market, the timeframe of the model, the
// period, then what stops it.
export function DataStep({ form, model, market, instrument, today, problem, onChange }: Props) {
  return (
    <>
      <BuilderRow label="Instrument">
        <InstrumentPicker market={market} chosen={instrument} onChoose={(chosen) => onChange({ instrument: chosen })} />
      </BuilderRow>
      <BuilderRow label="Timeframe">
        <b className="font-mono text-[13.5px] font-semibold">{model.manifest_summary?.timeframe}</b>
        <Hint>from the model</Hint>
      </BuilderRow>
      <PeriodRow
        form={form}
        earliest={instrument && earliestStart(instrument, model)}
        today={today}
        onChange={onChange}
      />
      {problem && <StepError>{problem}</StepError>}
    </>
  );
}
