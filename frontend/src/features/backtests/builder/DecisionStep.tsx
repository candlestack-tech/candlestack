import { Hint } from '@/components/Hint';
import { Switch } from '@/components/ui/switch';
import { Choice, DirectionCards, UnitInput, ValueSlider } from '@/features/settings';

import {
  THRESHOLD_SCALE,
  withCalibration,
  type BuilderForm,
  type Calibration,
  type DecisionField,
  type RuleName,
} from './form';
import { BuilderRow, CandlesInput, StepError, StepHeading } from './parts';

type Props = {
  form: BuilderForm;
  invalid: ReadonlySet<DecisionField>;
  problem: string | undefined;
  onChange: (change: Partial<BuilderForm>) => void;
  onReplace: (form: BuilderForm) => void;
};

// The builder's sliders take the whole row (.b-sl).
const WIDE = 'w-full max-w-none';
// Its windows are as wide as their numbers (.b-unit).
const NARROW = 'w-[150px]';

const fixed = (value: number) => value.toFixed(2);
const percent = (value: number) => `${value} %`;

const CALIBRATIONS: readonly (readonly [Calibration, string])[] = [
  ['none', 'None'],
  ['zscore', 'Z-score'],
  ['rank', 'Rank'],
];
const SMOOTHING = [
  ['1', 'Off'],
  ['5', 'EMA 5'],
  ['10', 'EMA 10'],
  ['20', 'EMA 20'],
] as const;
const RULES: readonly (readonly [RuleName, string])[] = [
  ['threshold', 'Threshold'],
  ['hysteresis', 'Hysteresis'],
  ['kmeans', 'K-means'],
];
const GROUPS = [
  ['2', '2'],
  ['3', '3'],
  ['4', '4'],
  ['5', '5'],
] as const;

// The threshold, the direction and the stop loss, as in Settings, Backtest defaults.
function MainRows({ form, onChange }: Pick<Props, 'form' | 'onChange'>) {
  const scale = THRESHOLD_SCALE[form.calibration];
  return (
    <>
      {form.rule !== 'kmeans' && (
        <BuilderRow label={form.rule === 'hysteresis' ? 'Enter at' : 'Threshold'}>
          <ValueSlider
            className={WIDE}
            label={form.rule === 'hysteresis' ? 'Enter at' : 'Threshold'}
            value={form.threshold}
            min={scale.min}
            max={scale.max}
            step={scale.step}
            format={fixed}
            onChange={(threshold) => onChange({ threshold, exit: Math.min(form.exit, threshold) })}
          />
        </BuilderRow>
      )}
      <BuilderRow label="Direction" top>
        <DirectionCards value={form.direction} onChange={(direction) => onChange({ direction })} />
      </BuilderRow>
      <BuilderRow label="Stop loss">
        <ValueSlider
          className={WIDE}
          label="Stop loss"
          value={form.stopLoss}
          min={1}
          max={20}
          step={1}
          format={percent}
          onChange={(stopLoss) => onChange({ stopLoss })}
        />
      </BuilderRow>
    </>
  );
}

type RowsProps = Pick<Props, 'form' | 'invalid' | 'onChange'>;

// Where hysteresis leaves a position: at most where it enters.
function LeaveRow({ form, onChange }: Pick<Props, 'form' | 'onChange'>) {
  const scale = THRESHOLD_SCALE[form.calibration];
  return (
    <BuilderRow label="Leave at">
      <ValueSlider
        className={WIDE}
        label="Leave at"
        value={Math.min(form.exit, form.threshold)}
        min={scale.min}
        max={Math.max(form.threshold, scale.step)}
        step={scale.step}
        format={fixed}
        onChange={(exit) => onChange({ exit })}
      />
    </BuilderRow>
  );
}

// The groups of k-means, its window and how often it refits.
function KmeansRow({ form, invalid, onChange }: RowsProps) {
  return (
    <BuilderRow label="K-means">
      <Choice<string>
        label="Groups"
        value={String(form.k)}
        options={GROUPS}
        onChange={(k) => onChange({ k: Number(k) })}
      />
      <UnitInput
        className={NARROW}
        label="K-means window"
        unit="window"
        value={form.kmeansWindow}
        invalid={invalid.has('kmeansWindow')}
        onChange={(kmeansWindow) => onChange({ kmeansWindow })}
      />
      <UnitInput
        className={NARROW}
        label="Refit every"
        unit="refit"
        value={form.refitEvery}
        invalid={invalid.has('refitEvery')}
        onChange={(refitEvery) => onChange({ refitEvery })}
      />
    </BuilderRow>
  );
}

// Calibration with its window, smoothing and the rule with its own settings.
function SignalRows({ form, invalid, onChange, onReplace }: Omit<Props, 'problem'>) {
  return (
    <>
      <BuilderRow label="Calibration">
        <Choice
          label="Calibration"
          value={form.calibration}
          options={CALIBRATIONS}
          onChange={(calibration) => onReplace(withCalibration(form, calibration))}
        />
        {form.calibration !== 'none' && (
          <UnitInput
            className={NARROW}
            label="Calibration window"
            unit="window"
            value={form.calibrationWindow}
            invalid={invalid.has('calibrationWindow')}
            onChange={(calibrationWindow) => onChange({ calibrationWindow })}
          />
        )}
      </BuilderRow>
      <BuilderRow label="Smoothing">
        <Choice<string>
          label="Smoothing"
          value={String(form.ema)}
          options={SMOOTHING}
          onChange={(ema) => onChange({ ema: Number(ema) })}
        />
      </BuilderRow>
      <BuilderRow label="Rule">
        <Choice label="Rule" value={form.rule} options={RULES} onChange={(rule) => onChange({ rule })} />
      </BuilderRow>
      {form.rule === 'hysteresis' && <LeaveRow form={form} onChange={onChange} />}
      {form.rule === 'kmeans' && <KmeansRow form={form} invalid={invalid} onChange={onChange} />}
    </>
  );
}

// The drawdown stop: off, or the fall from the top that halts the run.
function DrawdownRow({ form, onChange }: Pick<Props, 'form' | 'onChange'>) {
  return (
    <BuilderRow label="Drawdown stop">
      <Switch
        aria-label="Drawdown stop"
        checked={form.drawdownStop > 0}
        onCheckedChange={(on) => onChange({ drawdownStop: on ? 20 : 0 })}
      />
      {form.drawdownStop > 0 && (
        <div className="min-w-[180px] flex-1">
          <ValueSlider
            className={WIDE}
            label="Drawdown stop"
            value={form.drawdownStop}
            min={5}
            max={50}
            step={1}
            format={(value) => `−${value} %`}
            onChange={(drawdownStop) => onChange({ drawdownStop })}
          />
        </div>
      )}
    </BuilderRow>
  );
}

// The share of the capital a position takes when it opens.
function PositionSizeRow({ form, onChange }: Pick<Props, 'form' | 'onChange'>) {
  return (
    <BuilderRow label="Position size">
      <div className="min-w-[180px] flex-1">
        <ValueSlider
          className={WIDE}
          label="Position size"
          value={form.positionSize}
          min={5}
          max={100}
          step={5}
          format={percent}
          onChange={(positionSize) => onChange({ positionSize })}
        />
      </div>
    </BuilderRow>
  );
}

// Advanced (.b-extra): calibration, smoothing, the rule, confirmation, the minimum hold, the
// position size and the drawdown stop, each off or whole until set.
function AdvancedRows(props: Omit<Props, 'problem'>) {
  const { form, onChange } = props;
  return (
    <div className="flex flex-col gap-3.5 border-t pt-[18px]">
      <StepHeading>Advanced</StepHeading>
      <SignalRows {...props} />
      <BuilderRow label="Confirm">
        <CandlesInput label="Confirm" value={form.confirm} onChange={(confirm) => onChange({ confirm })} />
        <Hint>candles</Hint>
      </BuilderRow>
      <BuilderRow label="Minimum hold">
        <CandlesInput label="Minimum hold" value={form.minHold} onChange={(minHold) => onChange({ minHold })} />
        <Hint>candles</Hint>
      </BuilderRow>
      <PositionSizeRow form={form} onChange={onChange} />
      <DrawdownRow form={form} onChange={onChange} />
    </div>
  );
}

// Step 3 (stepDecision): how the predictions become a signal (docs/engine.md, Signals).
export function DecisionStep({ problem, ...props }: Props) {
  return (
    <>
      <MainRows form={props.form} onChange={props.onChange} />
      <AdvancedRows {...props} />
      {problem && <StepError>{problem}</StepError>}
    </>
  );
}
