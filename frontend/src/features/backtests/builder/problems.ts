import { ApiError } from '@/api';

import { dayOf, type DateWriter } from './form';

// What the API said when Run failed, in plain words, and the step it is about: the builder goes
// back there and shows it (POST /api/v1/backtests, docs/architecture.md "Backtests").

export type BuilderProblem = { step: number; message: string };

const MODEL = 0;
const DATA = 1;
const DECISION = 2;
const EXECUTION = 3;
const RUN = 4;

type Context = { symbol: string; write: DateWriter };

type Extensions = {
  session?: unknown;
  available_from?: unknown;
  available_to?: unknown;
  earliest_start?: unknown;
  candles?: unknown;
  requested?: unknown;
  maximum?: unknown;
  errors?: unknown;
};

const extensionsOf = (error: ApiError): Extensions =>
  typeof error.body === 'object' && error.body !== null ? error.body : {};

const isNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const count = (value: number) => value.toLocaleString('en-US');

function periodOutOfRange(extensions: Extensions, { symbol, write }: Context): string {
  const { available_from: from, available_to: to } = extensions;
  if (isNumber(from) && isNumber(to)) return `${symbol} has candles from ${write(dayOf(from))} to ${write(dayOf(to))}.`;
  if (isNumber(to)) return `${symbol} has no candles after ${write(dayOf(to))}.`;
  return `${symbol} has no candles for this period.`;
}

function warmUpOutOfRange(extensions: Extensions, { write }: Context): string {
  const needs = isNumber(extensions.candles)
    ? `The model needs ${count(extensions.candles)} candles before the start.`
    : 'The model needs candles before the start.';
  return isNumber(extensions.earliest_start)
    ? `Start on ${write(dayOf(extensions.earliest_start))} or later. ${needs}`
    : needs;
}

function tooManyCandles(extensions: Extensions): string {
  const { requested, maximum } = extensions;
  return isNumber(requested) && isNumber(maximum)
    ? `${count(requested)} candles with the warm-up, at most ${count(maximum)} can run. Choose a shorter period.`
    : 'Too many candles. Choose a shorter period.';
}

// The step a refused field belongs to, by the group its location starts with (body, decision, ...).
function stepOfField(extensions: Extensions): number {
  const first = Array.isArray(extensions.errors) ? (extensions.errors[0] as { loc?: unknown } | undefined) : undefined;
  const group = Array.isArray(first?.loc) ? first.loc[1] : undefined;
  if (group === 'model') return MODEL;
  if (group === 'data' || group === 'benchmarks') return DATA;
  if (group === 'decision') return DECISION;
  if (group === 'execution') return EXECUTION;
  return RUN;
}

type Handler = (extensions: Extensions, context: Context) => BuilderProblem;

const at =
  (step: number, message: string): Handler =>
  () => ({ step, message });

// Each problem of POST /api/v1/backtests by its slug.
const HANDLERS: Record<string, Handler> = {
  'not-found': at(MODEL, 'This model is gone. Choose another one.'),
  'model-needs-setup': at(MODEL, 'This model needs a manifest first.'),
  'instrument-not-found': (_, { symbol }) => ({
    step: DATA,
    message: `${symbol} is not in the list any more. Choose another one.`,
  }),
  'instrument-not-for-model': ({ session }) => ({
    step: DATA,
    message: session === '24/7' ? 'This model runs on crypto pairs.' : 'This model runs on stocks.',
  }),
  'period-out-of-range': (extensions, context) => ({ step: DATA, message: periodOutOfRange(extensions, context) }),
  'warm-up-out-of-range': (extensions, context) => ({ step: DATA, message: warmUpOutOfRange(extensions, context) }),
  'too-many-candles': (extensions) => ({ step: DATA, message: tooManyCandles(extensions) }),
  validation: (extensions) => ({ step: stepOfField(extensions), message: 'Check this step and try again.' }),
  'backtest-running': at(RUN, 'Another backtest is running. Run this one when it has finished.'),
  'backtests-unavailable': at(RUN, 'Backtests cannot run right now. Try again later.'),
  'source-unavailable': at(RUN, 'The market data cannot be loaded right now. Try again later.'),
  'source-data-invalid': at(RUN, 'The market data cannot be loaded right now. Try again later.'),
};

export function problemOf(error: Error, context: Context): BuilderProblem {
  if (!(error instanceof ApiError)) return { step: RUN, message: 'Could not start the backtest. Try again.' };
  const handler = error.problem === undefined ? undefined : HANDLERS[error.problem];
  return handler ? handler(extensionsOf(error), context) : { step: RUN, message: error.message };
}
