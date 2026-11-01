import { describe, expect, it } from 'vitest';

import { DEFAULT_PREFERENCES, model, rawCandlesSummary } from '@/test/api';

import {
  backtestOf,
  decisionProblems,
  earliestStart,
  executionProblems,
  formOf,
  marketOf,
  periodProblem,
  quickPeriods,
  withCalibration,
  type ChosenInstrument,
} from './form';

const DEFAULTS = DEFAULT_PREFERENCES.backtest_defaults;
const STOCK_MODEL = model({ state: 'ready', manifest_summary: rawCandlesSummary() });
const CRYPTO_MODEL = model({
  state: 'ready',
  manifest_summary: { ...rawCandlesSummary(), timeframe: '1h', session: '24/7' },
});
// 2017-08-17 04:00 UTC, Binance's first BTCUSDT candle.
const BTC: ChosenInstrument = {
  id: 'crypto:BTCUSDT',
  symbol: 'BTCUSDT',
  name: 'BTC/USDT',
  market: 'crypto',
  available_from: 1_502_942_400,
};
// 2016-01-04 14:30 UTC.
const AAPL: ChosenInstrument = {
  id: 'stock:AAPL',
  symbol: 'AAPL',
  name: 'Apple Inc.',
  market: 'stock',
  available_from: 1_451_917_800,
};
const write = (day: string) => `<${day}>`;
const NOW = Date.UTC(2026, 9, 17, 12) / 1000;

describe('the builder form', () => {
  it('starts from the Backtest defaults, with the rest off', () => {
    const form = formOf(DEFAULTS, 'mdl_7f3k2m9qx4');
    expect(form).toMatchObject({
      modelId: 'mdl_7f3k2m9qx4',
      from: '2021-01-04',
      to: '2024-12-31',
      threshold: 0.05,
      direction: 'long',
      stopLoss: 7,
      fees: '0.1',
      slippage: '0.02',
      benchmark: 'bh',
      calibration: 'none',
      ema: 1,
      rule: 'threshold',
      confirm: 1,
      minHold: 1,
      drawdownStop: 0,
      positionSize: 100,
      honestFill: true,
    });
  });

  it('starts with buy and hold when the default benchmark cannot run yet', () => {
    expect(formOf({ ...DEFAULTS, benchmark: 'SPY' }, undefined).benchmark).toBe('bh');
  });

  it('moves the threshold to the scale of a new calibration', () => {
    const form = formOf(DEFAULTS, undefined);
    expect(withCalibration(form, 'zscore')).toMatchObject({ threshold: 1, exit: 0.5 });
    expect(withCalibration(form, 'rank')).toMatchObject({ threshold: 0.5, exit: 0.25 });
    expect(withCalibration(form, 'none')).toMatchObject({ threshold: 0.05, exit: 0.02 });
  });

  it('reads the market from the session of the manifest', () => {
    expect(marketOf(STOCK_MODEL)).toBe('stock');
    expect(marketOf(CRYPTO_MODEL)).toBe('crypto');
  });
});

describe('the earliest start', () => {
  it('is the first candle and the warm-up after it, on the next whole day, for crypto', () => {
    // 24 one-hour candles after 2017-08-17 04:00 end on 2017-08-18 04:00.
    expect(earliestStart(BTC, CRYPTO_MODEL)).toBe('2017-08-19');
  });

  it('leaves room for weekends and holidays for stocks', () => {
    // 24 half-hour candles are two sessions: 3 days with the weekends, and 3 more.
    expect(earliestStart(AAPL, STOCK_MODEL)).toBe('2016-01-11');
  });

  it('is the first candle without a warm-up, and unknown without a first candle', () => {
    const none = model({
      state: 'ready',
      manifest_summary: { ...rawCandlesSummary(), warm_up: { candles: 0, kind: 'exact' } },
    });
    expect(earliestStart(AAPL, none)).toBe('2016-01-05');
    expect(earliestStart({ ...AAPL, available_from: null }, STOCK_MODEL)).toBeUndefined();
  });

  it('starts the quick periods no earlier', () => {
    expect(quickPeriods('2026-10-17', '2026-01-10')).toEqual([
      { label: 'Last 12 months', from: '2026-01-10', to: '2026-10-17' },
      { label: 'All data', from: '2026-01-10', to: '2026-10-17' },
    ]);
    expect(quickPeriods('2026-10-17', undefined).map((period) => period.label)).toEqual(['Last 12 months', '2025']);
  });
});

describe('what stops a step', () => {
  const form = formOf(DEFAULTS, undefined);
  const today = '2026-10-17';

  it('says what is wrong with the period, the first thing only', () => {
    const problem = (change: object, instrument = BTC) =>
      periodProblem({ ...form, ...change }, instrument, CRYPTO_MODEL, today, write);
    expect(problem({})).toBeUndefined();
    expect(problem({ from: '2021-02-30' })).toBe('Write the days as YYYY-MM-DD.');
    expect(problem({ from: '2024-12-31' })).toBe('The period ends before it starts.');
    expect(problem({ to: '2026-10-18' })).toBe('There are no candles after <2026-10-17>.');
    expect(problem({ from: '2017-08-18' })).toBe(
      'Start on <2017-08-19> or later. BTCUSDT has candles from <2017-08-17> and the model needs some before the start.',
    );
  });

  it('marks a window of the Advanced part that the API would refuse', () => {
    const decision = (change: object) => decisionProblems({ ...form, ...change });
    expect(decision({}).fields.size).toBe(0);
    expect(decision({ calibration: 'zscore', calibrationWindow: '1' })).toEqual({
      fields: new Set(['calibrationWindow']),
      message: 'The calibration window takes 2 to 1,000 predictions.',
    });
    expect(decision({ rule: 'kmeans', k: 4, kmeansWindow: '7' }).message).toBe(
      'The k-means window takes 8 to 5,000 predictions.',
    );
    expect(decision({ rule: 'kmeans', kmeansWindow: '1000', refitEvery: '49' })).toEqual({
      fields: new Set(['refitEvery']),
      message: 'Refit every 50 rows or more.',
    });
    // Off, a window is not checked.
    expect(decision({ calibrationWindow: 'x', kmeansWindow: 'x' }).fields.size).toBe(0);
  });

  it('marks fees and slippage outside 0 to 5 %', () => {
    expect(executionProblems(form).fields.size).toBe(0);
    expect(executionProblems({ ...form, fees: '6', slippage: 'x' })).toEqual({
      fields: new Set(['fees', 'slippage']),
      message: 'Fees and slippage take 0 to 5 %.',
    });
  });
});

describe('the request', () => {
  const form = formOf(DEFAULTS, undefined);

  it('sends the setup groups: the model, the instrument and the days in UTC, the decision and the costs', () => {
    expect(backtestOf(form, CRYPTO_MODEL, BTC, NOW)).toEqual({
      model: { id: CRYPTO_MODEL.id },
      data: {
        instrument: { id: 'crypto:BTCUSDT' },
        period: { start: Date.UTC(2021, 0, 4) / 1000, end: Date.UTC(2025, 0, 1) / 1000 },
      },
      decision: {
        calibrate: null,
        ema: null,
        rule: { name: 'threshold', limit: 0.05 },
        long_only: true,
        confirm: null,
        min_hold: null,
      },
      execution: {
        fill: 'next_open',
        stop_loss: 0.07,
        drawdown_stop: null,
        position_size: 1,
        fee_bps: 10,
        slippage_bps: 2,
      },
      benchmarks: [{ id: 'bh', kind: 'buy_and_hold', instrument: 'crypto:BTCUSDT' }],
    });
  });

  it('ends a period that ends today now', () => {
    const today = { ...form, from: '2026-01-01', to: '2026-10-17' };
    expect(backtestOf(today, CRYPTO_MODEL, BTC, NOW + 0.7).data.period.end).toBe(NOW);
  });

  it('sends every step of Advanced that is on', () => {
    const advanced = {
      ...form,
      direction: 'both' as const,
      calibration: 'rank' as const,
      calibrationWindow: '300',
      ema: 10,
      rule: 'hysteresis' as const,
      threshold: 0.6,
      exit: 0.3,
      confirm: 2,
      minHold: 5,
      drawdownStop: 25,
      positionSize: 50,
      honestFill: false,
      fees: '0,07',
      slippage: '0',
    };
    const body = backtestOf(advanced, CRYPTO_MODEL, BTC, NOW);
    expect(body.decision).toEqual({
      calibrate: { name: 'rank', window: 300 },
      ema: { span: 10 },
      rule: { name: 'hysteresis', enter: 0.6, exit: 0.3 },
      long_only: false,
      confirm: { candles: 2 },
      min_hold: { candles: 5 },
    });
    expect(body.execution).toEqual({
      fill: 'signal_close',
      stop_loss: 0.07,
      drawdown_stop: 0.25,
      position_size: 0.5,
      fee_bps: 7,
      slippage_bps: 0,
    });
    expect(backtestOf({ ...form, rule: 'kmeans', k: 2 }, CRYPTO_MODEL, BTC, NOW).decision.rule).toEqual({
      name: 'kmeans',
      k: 2,
      window: 500,
      refit_every: 25,
    });
  });
});
