import type { BacktestDefaults, BacktestDefaultsIn } from '@/api';

// The Backtest defaults card while it is edited: the typed fields as typed (the dates and the
// percentages), the rest as chosen. It is saved once every field holds what the API takes.

export type TypedField = 'period_start' | 'period_end' | 'fees_pct' | 'slippage_pct';

export type DefaultsForm = Omit<BacktestDefaults, TypedField> & Record<TypedField, string>;

// The API's bounds of the fees and the slippage, in percent (BacktestDefaults).
const PERCENT_MAX = 5;

export function formOf(defaults: BacktestDefaults): DefaultsForm {
  return {
    ...defaults,
    fees_pct: String(defaults.fees_pct),
    slippage_pct: String(defaults.slippage_pct),
  };
}

// A day as YYYY-MM-DD that exists in the calendar (not 2024-02-30).
export function isDay(value: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (match === null) return false;
  const [year, month, day] = match.slice(1).map(Number) as [number, number, number];
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

// A percentage from 0 to 5; a comma works as the decimal point too.
export function percent(value: string): number | undefined {
  const text = value.trim().replace(',', '.');
  if (!/^(\d+\.?\d*|\.\d+)$/.test(text)) return undefined;
  const number = Number(text);
  return number <= PERCENT_MAX ? number : undefined;
}

// The test period's fields that are not right: each must be a day, and it must end after it
// starts.
function periodErrors({ period_start: start, period_end: end }: DefaultsForm): TypedField[] {
  if (!isDay(start)) return isDay(end) ? ['period_start'] : ['period_start', 'period_end'];
  // ISO days sort as text.
  return isDay(end) && start < end ? [] : ['period_end'];
}

// What is saved, once every typed field holds what the API takes; until then, the fields that
// do not. It has the nine fields and nothing else: the API refuses any other key.
export function checkDefaults(form: DefaultsForm): { defaults?: BacktestDefaultsIn; invalid: Set<TypedField> } {
  const fees = percent(form.fees_pct);
  const slippage = percent(form.slippage_pct);
  const invalid = new Set(periodErrors(form));
  if (fees === undefined) invalid.add('fees_pct');
  if (slippage === undefined) invalid.add('slippage_pct');
  if (invalid.size > 0 || fees === undefined || slippage === undefined) return { invalid };
  const { timeframe, period_start, period_end, direction, threshold, stop_loss_pct, benchmark } = form;
  const defaults = { timeframe, period_start, period_end, direction, threshold, stop_loss_pct, benchmark };
  return { defaults: { ...defaults, fees_pct: fees, slippage_pct: slippage }, invalid };
}

const KEYS: readonly (keyof BacktestDefaults)[] = [
  'timeframe',
  'period_start',
  'period_end',
  'direction',
  'threshold',
  'stop_loss_pct',
  'fees_pct',
  'slippage_pct',
  'benchmark',
];

export function sameDefaults(a: BacktestDefaultsIn, b: BacktestDefaultsIn): boolean {
  return KEYS.every((key) => a[key] === b[key]);
}

// What a field that is not right says to fix (the test period's two say it once, under both).
const UNSAVED = 'Not saved until fixed.';
export const PROBLEMS = {
  fees_pct: `Fees are 0 to 5 %. ${UNSAVED}`,
  slippage_pct: `Slippage is 0 to 5 %. ${UNSAVED}`,
} as const;

export function periodProblem({ period_start: start, period_end: end }: DefaultsForm): string {
  return isDay(start) && isDay(end)
    ? `The test period must end after it starts. ${UNSAVED}`
    : `Write the days as YYYY-MM-DD. ${UNSAVED}`;
}
