import { describe, expect, it } from 'vitest';

import { ApiError } from '@/api';

import { problemOf } from './problems';

const context = { symbol: 'BTCUSDT', write: (day: string) => `<${day}>` };

// A problem of the API as src/api/client.ts makes it.
const refused = (status: number, slug: string, extensions: object = {}) =>
  new ApiError(status, `The API's detail of ${slug}`, {
    problem: slug,
    body: { type: `https://candlestack.tech/problems/${slug}`, status, ...extensions },
  });

const problem = (status: number, slug: string, extensions: object = {}) =>
  problemOf(refused(status, slug, extensions), context);

describe("the API's problems in the builder", () => {
  it('go to the Model step when the model is gone or has no manifest', () => {
    expect(problem(404, 'not-found')).toEqual({ step: 0, message: 'This model is gone. Choose another one.' });
    expect(problem(409, 'model-needs-setup')).toEqual({ step: 0, message: 'This model needs a manifest first.' });
  });

  it('go to the Data step about the instrument and the period, with the days written out', () => {
    expect(problem(404, 'instrument-not-found').message).toBe(
      'BTCUSDT is not in the list any more. Choose another one.',
    );
    expect(problem(422, 'instrument-not-for-model', { session: '24/7' })).toEqual({
      step: 1,
      message: 'This model runs on crypto pairs.',
    });
    expect(problem(422, 'instrument-not-for-model', { session: 'regular' }).message).toBe('This model runs on stocks.');
    expect(problem(422, 'period-out-of-range', { available_from: 1_502_942_400, available_to: 1_760_918_400 })).toEqual(
      { step: 1, message: 'BTCUSDT has candles from <2017-08-17> to <2025-10-20>.' },
    );
    expect(problem(422, 'period-out-of-range', { available_from: null, available_to: 1_760_918_400 }).message).toBe(
      'BTCUSDT has no candles after <2025-10-20>.',
    );
    expect(problem(422, 'warm-up-out-of-range', { candles: 1200, earliest_start: 1_503_100_800 })).toEqual({
      step: 1,
      message: 'Start on <2017-08-19> or later. The model needs 1,200 candles before the start.',
    });
    expect(problem(422, 'warm-up-out-of-range', { candles: 24, earliest_start: null }).message).toBe(
      'The model needs 24 candles before the start.',
    );
    expect(problem(422, 'too-many-candles', { requested: 80_539, maximum: 50_000 })).toEqual({
      step: 1,
      message: '80,539 candles with the warm-up, at most 50,000 can run. Choose a shorter period.',
    });
  });

  it('go to the step of the field the API refused', () => {
    const field = (group: string) => problem(422, 'validation', { errors: [{ loc: ['body', group, 'x'] }] }).step;
    expect(['model', 'data', 'benchmarks', 'decision', 'execution', 'other'].map(field)).toEqual([0, 1, 1, 2, 3, 4]);
    expect(problem(422, 'validation').message).toBe('Check this step and try again.');
  });

  it('stay on the Run step when the backtest cannot start now', () => {
    expect(problem(409, 'backtest-running')).toEqual({
      step: 4,
      message: 'Another backtest is running. Run this one when it has finished.',
    });
    expect(problem(503, 'backtests-unavailable').message).toBe('Backtests cannot run right now. Try again later.');
    expect(problem(503, 'source-unavailable').message).toBe(
      'The market data cannot be loaded right now. Try again later.',
    );
    expect(problem(500, 'internal')).toEqual({ step: 4, message: "The API's detail of internal" });
    expect(problemOf(new ApiError(0, 'Cannot reach the server. Check your connection.'), context).message).toBe(
      'Cannot reach the server. Check your connection.',
    );
    expect(problemOf(new Error('boom'), context)).toEqual({
      step: 4,
      message: 'Could not start the backtest. Try again.',
    });
  });
});
