import { useMutation, useQueryClient, useSuspenseQuery } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';

import { DashboardCard } from '@/components/DashboardCard';
import { errorMessage, preferencesQueryOptions, saveBacktestDefaults, TIMEFRAMES, type DefaultTimeframe } from '@/api';
import { ACCENT_BUTTON, BUTTON_SIZES } from '@/components/buttonStyles';
import { Hint } from '@/components/Hint';
import { SlidersIcon } from '@/components/icons';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

import { BenchmarkSelect } from './BenchmarkSelect';
import { Choice } from './controls';
import { SettingsFooter, SettingsRow } from './rows';
import { DirectionCards, PeriodInput, UnitInput, ValueSlider } from './defaultsControls';
import { checkDefaults, formOf, sameDefaults, type DefaultsForm, type TypedField } from './defaultsForm';

type Setter = <K extends keyof DefaultsForm>(key: K) => (value: DefaultsForm[K]) => void;
type FieldsProps = { form: DefaultsForm; set: Setter; invalid: ReadonlySet<TypedField> };

// The card being edited, starting from the saved defaults. Save sends it once something changed
// and every typed field is right (a wrong one is marked); the API's answer becomes the account's
// defaults.
function useDefaultsForm() {
  const queryClient = useQueryClient();
  const saved = useSuspenseQuery(preferencesQueryOptions()).data.backtest_defaults;
  const [form, setForm] = useState<DefaultsForm>(() => formOf(saved));
  const save = useMutation({
    mutationFn: saveBacktestDefaults,
    onSuccess: (defaults) =>
      queryClient.setQueryData(
        preferencesQueryOptions().queryKey,
        (preferences) => preferences && { ...preferences, backtest_defaults: defaults },
      ),
  });
  const { defaults, invalid } = checkDefaults(form);
  const set: Setter = (key) => (value) => setForm((current) => ({ ...current, [key]: value }));
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (defaults !== undefined && !sameDefaults(defaults, saved) && !save.isPending) save.mutate(defaults);
  };
  const error = save.error
    ? errorMessage(save.error, 'Could not save the defaults. Check the values and try again.')
    : undefined;
  return { form, set, invalid, saving: save.isPending, submit, error };
}

// What to test: the timeframe and the test period.
function DataRows({ form, set, invalid }: FieldsProps) {
  return (
    <>
      <SettingsRow label="Timeframe">
        <Choice<DefaultTimeframe>
          label="Timeframe"
          value={form.timeframe}
          options={TIMEFRAMES.map((timeframe) => [timeframe, timeframe])}
          onChange={set('timeframe')}
        />
      </SettingsRow>
      <SettingsRow label="Test period">
        <div className="flex items-center gap-2">
          <PeriodInput
            label="From"
            value={form.period_start}
            invalid={invalid.has('period_start')}
            onChange={set('period_start')}
          />
          <span className="text-muted-foreground">to</span>
          <PeriodInput
            label="To"
            value={form.period_end}
            invalid={invalid.has('period_end')}
            onChange={set('period_end')}
          />
        </div>
      </SettingsRow>
    </>
  );
}

// How to trade: the direction, the threshold and the stop loss.
function TradeRows({ form, set }: FieldsProps) {
  return (
    <>
      <SettingsRow label="Direction" top>
        <DirectionCards value={form.direction} onChange={set('direction')} />
      </SettingsRow>
      <SettingsRow label="Threshold" hint="Signal strength to trade">
        <ValueSlider
          label="Threshold"
          value={form.threshold}
          min={0}
          max={0.5}
          step={0.01}
          format={(value) => value.toFixed(2)}
          onChange={set('threshold')}
        />
      </SettingsRow>
      <SettingsRow label="Stop loss" hint="Exit when a trade loses this much">
        <ValueSlider
          label="Stop loss"
          value={form.stop_loss_pct}
          min={1}
          max={20}
          step={1}
          format={(value) => `${value} %`}
          onChange={set('stop_loss_pct')}
        />
      </SettingsRow>
    </>
  );
}

// What it costs and what it is measured against.
function CostRows({ form, set, invalid }: FieldsProps) {
  return (
    <>
      <SettingsRow label="Fees">
        <UnitInput
          label="Fees"
          unit="% per side"
          value={form.fees_pct}
          invalid={invalid.has('fees_pct')}
          onChange={set('fees_pct')}
        />
      </SettingsRow>
      <SettingsRow label="Slippage">
        <UnitInput
          label="Slippage"
          unit="%"
          value={form.slippage_pct}
          invalid={invalid.has('slippage_pct')}
          onChange={set('slippage_pct')}
        />
      </SettingsRow>
      <SettingsRow label="Benchmark" hint="What each backtest is measured against">
        <BenchmarkSelect value={form.benchmark} onChange={set('benchmark')} />
      </SettingsRow>
    </>
  );
}

// How a new backtest starts: every control of the design, then Save defaults.
export function BacktestDefaultsSection() {
  const { form, set, invalid, saving, submit, error } = useDefaultsForm();
  const fields = { form, set, invalid };
  return (
    <form onSubmit={submit}>
      <DashboardCard icon={SlidersIcon} title="Backtest defaults" aside={<Hint>New backtests start here</Hint>}>
        <DataRows {...fields} />
        <TradeRows {...fields} />
        <CostRows {...fields} />
        <SettingsFooter error={error}>
          <Button type="submit" className={cn(BUTTON_SIZES.sm, ACCENT_BUTTON)} disabled={saving}>
            Save defaults
          </Button>
        </SettingsFooter>
      </DashboardCard>
    </form>
  );
}
