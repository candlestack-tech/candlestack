import type { BacktestDefaults, BacktestIn, Benchmark, Direction, Instrument, Market, Model } from '@/api';
import { isDay, percent } from '@/features/settings';

// The builder's choices (mockups/app, pageBuilder) and how they become the body of
// POST /api/v1/backtests (BacktestIn, docs/engine.md "The result document"). The model fixes the
// timeframe and the input; everything else starts from the account's Backtest defaults.

export const STEPS = ['Model', 'Data', 'Decision', 'Execution', 'Run'] as const;
export const LAST_STEP = STEPS.length - 1;

export type Calibration = 'none' | 'zscore' | 'rank';
export type RuleName = 'threshold' | 'hysteresis' | 'kmeans';

// An instrument as the search list gives it.
export type ChosenInstrument = Pick<Instrument, 'id' | 'symbol' | 'name' | 'market' | 'available_from'>;

export type BuilderForm = {
  // The model chosen; the first one that can run while none is.
  modelId: string | undefined;
  // The instrument chosen; the first of the list while none that fits the model is.
  instrument: ChosenInstrument | undefined;
  // The first and the last day of the test, YYYY-MM-DD, as typed.
  from: string;
  to: string;
  // The rule's limit, and with hysteresis the one a position closes below.
  threshold: number;
  exit: number;
  direction: Direction;
  // In whole percent.
  stopLoss: number;
  calibration: Calibration;
  calibrationWindow: string;
  // The EMA's span; 1 is off.
  ema: number;
  rule: RuleName;
  k: number;
  kmeansWindow: string;
  refitEvery: string;
  // Candles; 1 is off.
  confirm: number;
  minHold: number;
  // In whole percent; 0 is off.
  drawdownStop: number;
  // Buy at the next candle's open (on) or at the close of the candle that decided.
  honestFill: boolean;
  // In percent, as typed.
  fees: string;
  slippage: string;
  benchmark: Benchmark;
};

// The benchmarks a backtest can have so far: buy and hold of its own instrument.
export const BENCHMARKS_AVAILABLE: readonly Benchmark[] = ['bh'];

export function formOf(defaults: BacktestDefaults, modelId: string | undefined): BuilderForm {
  return {
    modelId,
    instrument: undefined,
    from: defaults.period_start,
    to: defaults.period_end,
    threshold: defaults.threshold,
    exit: Math.min(0.02, defaults.threshold),
    direction: defaults.direction,
    stopLoss: defaults.stop_loss_pct,
    calibration: 'none',
    calibrationWindow: '500',
    ema: 1,
    rule: 'threshold',
    k: 3,
    kmeansWindow: '500',
    refitEvery: '25',
    confirm: 1,
    minHold: 1,
    drawdownStop: 0,
    honestFill: true,
    fees: String(defaults.fees_pct),
    slippage: String(defaults.slippage_pct),
    benchmark: BENCHMARKS_AVAILABLE.includes(defaults.benchmark) ? defaults.benchmark : 'bh',
  };
}

// The threshold's slider for each calibration (min, max, step), and where it starts when the
// calibration changes: a raw score, standard deviations or a rank in [-1, 1].
export const THRESHOLD_SCALE: Record<Calibration, { min: number; max: number; step: number; start: number }> = {
  none: { min: 0, max: 0.5, step: 0.01, start: 0.05 },
  zscore: { min: 0, max: 3, step: 0.1, start: 1 },
  rank: { min: 0, max: 1, step: 0.05, start: 0.5 },
};

export function withCalibration(form: BuilderForm, calibration: Calibration): BuilderForm {
  const start = THRESHOLD_SCALE[calibration].start;
  return { ...form, calibration, threshold: start, exit: calibration === 'none' ? 0.02 : start / 2 };
}

// The model can run: it has a manifest.
export const canRun = (model: Model) => model.state !== 'needs_setup';

// The market a model's candles come from: US trading hours are stocks, around the clock crypto.
export const marketOf = (model: Model): Market => (model.manifest_summary?.session === '24/7' ? 'crypto' : 'stock');

// --- Days -------------------------------------------------------------------------------------

const DAY = 86_400;

// The UTC day of epoch seconds, YYYY-MM-DD.
export const dayOf = (seconds: number) => new Date(seconds * 1000).toISOString().slice(0, 10);

// Epoch seconds of a day's UTC midnight.
export const secondsOf = (day: string) => Date.parse(`${day}T00:00:00Z`) / 1000;

const SECONDS: Record<string, number> = {
  '1m': 60,
  '5m': 300,
  '15m': 900,
  '30m': 1800,
  '1h': 3600,
  '4h': 14_400,
  '1d': DAY,
};
// Candles of a regular US session (6.5 hours) by timeframe.
const SESSION_CANDLES: Record<string, number> = {
  '1m': 390,
  '5m': 78,
  '15m': 26,
  '30m': 13,
  '1h': 7,
  '4h': 2,
  '1d': 1,
};

// The first day a test can start on: the instrument's first candle and the model's warm-up after
// it. For stocks an estimate with room for weekends and holidays; the API checks it exactly.
export function earliestStart(instrument: ChosenInstrument, model: Model): string | undefined {
  const summary = model.manifest_summary;
  if (instrument.available_from === null || !summary) return undefined;
  const candles = summary.warm_up.candles;
  const seconds =
    candles === 0
      ? 0
      : instrument.market === 'crypto'
        ? candles * (SECONDS[summary.timeframe] ?? DAY)
        : (Math.ceil((Math.ceil(candles / (SESSION_CANDLES[summary.timeframe] ?? 1)) * 7) / 5) + 3) * DAY;
  return dayOf(Math.ceil((instrument.available_from + seconds) / DAY) * DAY);
}

// The quick periods under the days: the last 12 months, the last whole year and all the data,
// none starting before the earliest start.
export function quickPeriods(today: string, earliest: string | undefined) {
  const year = Number(today.slice(0, 4)) - 1;
  const yearAgo = dayOf(secondsOf(today) - 365 * DAY);
  const later = (day: string) => (earliest !== undefined && earliest > day ? earliest : day);
  const periods = [
    { label: 'Last 12 months', from: later(yearAgo), to: today },
    { label: String(year), from: later(`${year}-01-01`), to: `${year}-12-31` },
    ...(earliest === undefined ? [] : [{ label: 'All data', from: earliest, to: today }]),
  ];
  // One the data cannot fill is left out.
  return periods.filter((period) => period.from < period.to);
}

// --- What stops a step ------------------------------------------------------------------------

export type DateWriter = (day: string) => string;

// Why the period cannot run, the first thing that is wrong; undefined when it can.
export function periodProblem(
  form: BuilderForm,
  instrument: ChosenInstrument | undefined,
  model: Model | undefined,
  today: string,
  write: DateWriter,
): string | undefined {
  if (!isDay(form.from) || !isDay(form.to)) return 'Write the days as YYYY-MM-DD.';
  if (form.from >= form.to) return 'The period ends before it starts.';
  if (form.to > today) return `There are no candles after ${write(today)}.`;
  if (!instrument || !model) return undefined;
  const earliest = earliestStart(instrument, model);
  if (earliest !== undefined && form.from < earliest) {
    const first = write(dayOf(instrument.available_from ?? 0));
    return `Start on ${write(earliest)} or later. ${instrument.symbol} has candles from ${first} and the model needs some before the start.`;
  }
  return undefined;
}

// A whole number from min to max, or undefined.
function wholeNumber(text: string, min: number, max: number): number | undefined {
  const value = Number(text.trim());
  return text.trim() !== '' && Number.isInteger(value) && value >= min && value <= max ? value : undefined;
}

// The API's limits of the windows (document.py): a calibration window of 2 to 1,000, a k-means
// window of 2 × k to 5,000 refitted at least every window / 20 rows.
const CALIBRATION_MAX = 1000;
const KMEANS_MAX = 5000;
const ROWS_PER_REFIT = 20;

export type DecisionField = 'calibrationWindow' | 'kmeansWindow' | 'refitEvery';

// The windows of the Advanced part that are not right, with the first problem in words.
export function decisionProblems(form: BuilderForm): { fields: Set<DecisionField>; message?: string } {
  const problems: [DecisionField, string][] = [];
  if (form.calibration !== 'none' && wholeNumber(form.calibrationWindow, 2, CALIBRATION_MAX) === undefined) {
    problems.push(['calibrationWindow', 'The calibration window takes 2 to 1,000 predictions.']);
  }
  if (form.rule === 'kmeans') {
    const window = wholeNumber(form.kmeansWindow, 2 * form.k, KMEANS_MAX);
    if (window === undefined) {
      problems.push(['kmeansWindow', `The k-means window takes ${2 * form.k} to 5,000 predictions.`]);
    } else {
      const least = Math.ceil(window / ROWS_PER_REFIT);
      if (wholeNumber(form.refitEvery, least, Number.MAX_SAFE_INTEGER) === undefined) {
        problems.push(['refitEvery', `Refit every ${least} rows or more.`]);
      }
    }
  }
  return { fields: new Set(problems.map(([field]) => field)), message: problems[0]?.[1] };
}

export type ExecutionField = 'fees' | 'slippage';

export function executionProblems(form: BuilderForm): { fields: Set<ExecutionField>; message?: string } {
  const fields = new Set<ExecutionField>();
  if (percent(form.fees) === undefined) fields.add('fees');
  if (percent(form.slippage) === undefined) fields.add('slippage');
  return { fields, message: fields.size > 0 ? 'Fees and slippage take 0 to 5 %.' : undefined };
}

// --- The request ------------------------------------------------------------------------------

// Percent as basis points, without the float's tail (0.07 % is 7 bps, not 7.000000000000001).
const bps = (pct: number) => Math.round(pct * 1_000_000) / 10_000;

const steps = (candles: number) => (candles > 1 ? { candles } : null);

function decisionOf(form: BuilderForm): BacktestIn['decision'] {
  const rule: BacktestIn['decision']['rule'] =
    form.rule === 'kmeans'
      ? { name: 'kmeans', k: form.k, window: Number(form.kmeansWindow), refit_every: Number(form.refitEvery) }
      : form.rule === 'hysteresis'
        ? { name: 'hysteresis', enter: form.threshold, exit: Math.min(form.exit, form.threshold) }
        : { name: 'threshold', limit: form.threshold };
  return {
    calibrate: form.calibration === 'none' ? null : { name: form.calibration, window: Number(form.calibrationWindow) },
    ema: form.ema > 1 ? { span: form.ema } : null,
    rule,
    long_only: form.direction === 'long',
    confirm: steps(form.confirm),
    min_hold: steps(form.minHold),
  };
}

// The body of POST /api/v1/backtests. The period is [first day, the day after the last) in UTC,
// ending no later than `now` (epoch seconds): the API has no candles after it.
export function backtestOf(form: BuilderForm, model: Model, instrument: ChosenInstrument, now: number): BacktestIn {
  return {
    model: { id: model.id },
    data: {
      instrument: { id: instrument.id },
      period: { start: secondsOf(form.from), end: Math.min(secondsOf(form.to) + DAY, Math.floor(now)) },
    },
    decision: decisionOf(form),
    execution: {
      fill: form.honestFill ? 'next_open' : 'signal_close',
      stop_loss: form.stopLoss / 100,
      drawdown_stop: form.drawdownStop > 0 ? form.drawdownStop / 100 : null,
      fee_bps: bps(percent(form.fees) ?? 0),
      slippage_bps: bps(percent(form.slippage) ?? 0),
    },
    benchmarks: form.benchmark === 'bh' ? [{ id: 'bh', kind: 'buy_and_hold', instrument: instrument.id }] : [],
  };
}
