import { useQueryClient, useSuspenseQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { DashboardCard } from '@/components/DashboardCard';
import { errorMessage, preferencesQueryOptions, saveBacktestDefaults, TIMEFRAMES, type DefaultTimeframe } from '@/api';
import { ErrorNote } from '@/components/ErrorNote';
import { Hint } from '@/components/Hint';
import { SlidersIcon } from '@/components/icons';

import { textFieldKeys, useAutosave } from './autosave';
import { BenchmarkSelect } from './BenchmarkSelect';
import { Choice } from './controls';
import { DirectionCards, PeriodInput, UnitInput, ValueSlider } from './defaultsControls';
import {
  checkDefaults,
  formOf,
  periodProblem,
  PROBLEMS,
  sameDefaults,
  type DefaultsForm,
  type TypedField,
} from './defaultsForm';
import { LeaveGuard } from './LeaveGuard';
import { FieldError, SettingsRow } from './rows';
import { SaveIndicator } from './SaveIndicator';

// `set` changes a field as it is typed or dragged; `choose` changes it and saves the card (a click,
// a slider let go); `keys` saves a typed field on leaving it or on Enter, and Esc puts its saved
// value back.
type Setter = <K extends keyof DefaultsForm>(key: K) => (value: DefaultsForm[K]) => void;
type FieldsProps = {
  form: DefaultsForm;
  set: Setter;
  choose: Setter;
  keys: (key: TypedField) => ReturnType<typeof textFieldKeys>;
  invalid: ReadonlySet<TypedField>;
};

// The card being edited, starting from the saved defaults. Each change is saved as a whole, the
// nine fields as before, once every typed field is right; a wrong one is not sent and says what to
// fix once left. The API's answer becomes the account's defaults.
function useDefaultsForm() {
  const queryClient = useQueryClient();
  const saved = useSuspenseQuery(preferencesQueryOptions()).data.backtest_defaults;
  const [form, setForm] = useState<DefaultsForm>(() => formOf(saved));
  const [checked, setChecked] = useState(false);
  const autosave = useAutosave({
    key: ['settings', 'backtest-defaults'],
    initial: saved,
    save: saveBacktestDefaults,
    same: sameDefaults,
    onSaved: (defaults) =>
      queryClient.setQueryData(
        preferencesQueryOptions().queryKey,
        (preferences) => preferences && { ...preferences, backtest_defaults: defaults },
      ),
  });
  const { invalid } = checkDefaults(form);
  const saveForm = (next: DefaultsForm) => {
    const { defaults } = checkDefaults(next);
    setChecked(defaults === undefined);
    if (defaults !== undefined) autosave.commit(defaults);
  };
  const change = (next: DefaultsForm, save: boolean) => {
    setForm(next);
    if (save) saveForm(next);
  };
  const set: Setter = (key) => (value) => change({ ...form, [key]: value }, false);
  const choose: Setter = (key) => (value) => change({ ...form, [key]: value }, true);
  const keys = (key: TypedField) =>
    textFieldKeys(
      () => saveForm(form),
      () => change({ ...form, [key]: formOf(saved)[key] }, true),
    );
  const shown = checked ? invalid : new Set<TypedField>();
  const state = { ...autosave.state, invalid: shown.size > 0 };
  const error = autosave.error
    ? errorMessage(autosave.error, 'Could not save the defaults. Check the values and try again.')
    : undefined;
  return { fields: { form, set, choose, keys, invalid: shown }, unsaved: invalid.size > 0, state, error };
}

// What to test: the timeframe and the test period.
function DataRows({ form, choose, set, keys, invalid }: FieldsProps) {
  const wrongPeriod = invalid.has('period_start') || invalid.has('period_end');
  return (
    <>
      <SettingsRow label="Timeframe">
        <Choice<DefaultTimeframe>
          label="Timeframe"
          value={form.timeframe}
          options={TIMEFRAMES.map((timeframe) => [timeframe, timeframe])}
          onChange={choose('timeframe')}
        />
      </SettingsRow>
      <SettingsRow label="Test period">
        <div className="flex items-center gap-2">
          <PeriodInput
            label="From"
            value={form.period_start}
            invalid={invalid.has('period_start')}
            onChange={set('period_start')}
            {...keys('period_start')}
          />
          <span className="text-muted-foreground">to</span>
          <PeriodInput
            label="To"
            value={form.period_end}
            invalid={invalid.has('period_end')}
            onChange={set('period_end')}
            {...keys('period_end')}
          />
        </div>
        {wrongPeriod && <FieldError>{periodProblem(form)}</FieldError>}
      </SettingsRow>
    </>
  );
}

// How to trade: the direction, the threshold and the stop loss.
function TradeRows({ form, set, choose }: FieldsProps) {
  return (
    <>
      <SettingsRow label="Direction" top>
        <DirectionCards value={form.direction} onChange={choose('direction')} />
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
          onCommit={choose('threshold')}
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
          onCommit={choose('stop_loss_pct')}
        />
      </SettingsRow>
    </>
  );
}

// What it costs and what it is measured against.
function CostRows({ form, set, choose, keys, invalid }: FieldsProps) {
  return (
    <>
      <SettingsRow label="Fees">
        <UnitInput
          label="Fees"
          unit="% per side"
          value={form.fees_pct}
          invalid={invalid.has('fees_pct')}
          onChange={set('fees_pct')}
          {...keys('fees_pct')}
        />
        {invalid.has('fees_pct') && <FieldError>{PROBLEMS.fees_pct}</FieldError>}
      </SettingsRow>
      <SettingsRow label="Slippage">
        <UnitInput
          label="Slippage"
          unit="%"
          value={form.slippage_pct}
          invalid={invalid.has('slippage_pct')}
          onChange={set('slippage_pct')}
          {...keys('slippage_pct')}
        />
        {invalid.has('slippage_pct') && <FieldError>{PROBLEMS.slippage_pct}</FieldError>}
      </SettingsRow>
      <SettingsRow label="Benchmark" hint="What each backtest is measured against">
        <BenchmarkSelect value={form.benchmark} onChange={choose('benchmark')} />
      </SettingsRow>
    </>
  );
}

// How a new backtest starts: every control of the design, each saving itself; the indicator by
// the title says how that went.
export function BacktestDefaultsSection() {
  const { fields, unsaved, state, error } = useDefaultsForm();
  return (
    <DashboardCard
      icon={SlidersIcon}
      title="Backtest defaults"
      aside={
        <div className="flex items-center gap-3">
          <Hint>New backtests start here</Hint>
          <SaveIndicator state={state} />
        </div>
      }
    >
      <DataRows {...fields} />
      <TradeRows {...fields} />
      <CostRows {...fields} />
      {error && <ErrorNote className="mt-2">{error}</ErrorNote>}
      <LeaveGuard when={unsaved} />
    </DashboardCard>
  );
}
