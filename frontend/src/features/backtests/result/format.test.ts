import { describe, expect, it } from 'vitest';

import { backtestDocument, manifest } from '@/test/api';
import { step } from '@/test/steps';

import {
  allMetrics,
  basicMetrics,
  candlesText,
  durationText,
  loadedText,
  manifestText,
  modelText,
  periodText,
  setupOf,
  stepTime,
} from './format';

const DOC = backtestDocument();
const STATS = DOC.results!.stats;
const DMY = { dateFormat: 'dmy' } as const;

describe('the metrics', () => {
  it('writes the first six for the strip, the return and the fall in colour', () => {
    expect(basicMetrics(STATS)).toEqual([
      { name: 'Return', value: '−5.2 %', tone: 'down' },
      { name: 'Buy and hold', value: '−2.0 %', tone: undefined },
      { name: 'Sharpe', value: '–', tone: undefined },
      { name: 'Max drawdown', value: '−6.0 %', tone: 'down' },
      { name: 'Trades', value: '1', tone: undefined },
      { name: 'Win rate', value: '0.0 %', tone: undefined },
    ]);
  });

  it("lists all twelve of the engine's in its order", () => {
    expect(allMetrics(STATS).map(({ name, value }) => `${name} ${value}`)).toEqual([
      'Total return −5.2 %',
      'Max drawdown −6.0 %',
      'Sharpe –',
      'Sortino –',
      'Trades 1',
      'Win rate 0.0 %',
      'Avg. trade −5.2 %',
      'Best trade −5.2 %',
      'Worst trade −5.2 %',
      'Time in market 20.0 %',
      'Fees paid 0.2 %',
      'Buy and hold −2.0 %',
    ]);
  });

  it('colours a best trade that made money green and leaves a drawdown of nothing plain', () => {
    const metrics = allMetrics({ ...STATS, best_trade_return: 0.12, max_drawdown: 0 });
    expect(metrics.find((metric) => metric.name === 'Best trade')?.tone).toBe('up');
    expect(metrics.find((metric) => metric.name === 'Max drawdown')?.tone).toBeUndefined();
  });
});

describe('the header', () => {
  it('writes the period as its first and last UTC day, in the date format', () => {
    // [2024-01-01, 2024-10-01): the last day is 30 Sep.
    const period = { start: 1704067200, end: 1727740800 };
    expect(periodText(period, DMY)).toBe('1 Jan 2024 – 30 Sep 2024');
    expect(periodText(period, { dateFormat: 'iso' })).toBe('2024-01-01 – 2024-09-30');
  });

  it('writes how long it ran in seconds and minutes', () => {
    expect(durationText(42.4)).toBe('42 s');
    expect(durationText(162)).toBe('2 min 42 s');
  });
});

describe('the setup', () => {
  it('reads the manifest it ran with in short', () => {
    expect(manifestText(manifest() as unknown as Record<string, unknown>)).toBe('Candles · 30m · 25 × 5');
    expect(manifestText({})).toBe('');
  });

  it('writes each step of the builder with its details', () => {
    expect(setupOf(DOC, DMY)).toEqual([
      { step: 'Model', main: 'raw-candles.keras', detail: 'Candles · 30m · 25 × 5' },
      { step: 'Data', main: 'AAPL', detail: '30m · 3 Jun 2024 – 3 Jun 2024' },
      { step: 'Decision', main: 'threshold 0.05 · long and short', detail: '' },
      {
        step: 'Execution',
        main: "next candle's open",
        detail: 'stop loss 5 % · fees 0.1 % · slippage 0 % · vs buy and hold',
      },
    ]);
  });

  it('writes every rule and the steps that are on', () => {
    const decision = (fields: Partial<typeof DOC.decision>) =>
      setupOf(backtestDocument({ decision: { ...DOC.decision, ...fields } }), DMY)[2];
    expect(decision({ rule: { name: 'hysteresis', enter: 0.3, exit: 0.1 }, long_only: true })).toEqual({
      step: 'Decision',
      main: 'hysteresis 0.3 / 0.1 · long only',
      detail: '',
    });
    expect(
      decision({
        rule: { name: 'kmeans', k: 5, window: 500, refit_every: 50 },
        calibrate: { name: 'rank', window: 500 },
        ema: { span: 5 },
        confirm: { candles: 2 },
        min_hold: { candles: 3 },
      }),
    ).toEqual({
      step: 'Decision',
      main: 'k-means 5 · 500 · refit 50 · long and short',
      detail: 'rank 500 · EMA 5 · confirm 2 · hold 3',
    });
    expect(decision({ calibrate: { name: 'zscore', window: 200 } })?.detail).toBe('z-score 200');
  });

  it('writes the fill, the stops and the costs, and says when there is no benchmark', () => {
    const doc = backtestDocument({
      execution: { fill: 'signal_close', stop_loss: null, drawdown_stop: 0.2, fee_bps: 2.5, slippage_bps: 1 },
      benchmarks: [],
    });
    expect(setupOf(doc, DMY)[3]).toEqual({
      step: 'Execution',
      main: "signal candle's close",
      detail: 'drawdown stop 20 % · fees 0.025 % · slippage 0.01 % · no benchmark',
    });
  });
});

describe('the steps of a run', () => {
  it('writes how long a step took in tenths under a minute, then in minutes', () => {
    expect(stepTime(0.04)).toBe('0 s');
    expect(stepTime(0.31)).toBe('0.3 s');
    expect(stepTime(10.34)).toBe('10.3 s');
    expect(stepTime(59.96)).toBe('1 min 0 s');
    expect(stepTime(192.4)).toBe('3 min 12 s');
    expect(stepTime(null)).toBe('–');
  });

  it("writes the months loaded from the period's first, none while it loads the warm-up", () => {
    const start = Date.UTC(2016, 0, 4) / 1000;
    expect(loadedText('2019-01-31', start)).toBe('Loaded Jan 2016 – Jan 2019');
    expect(loadedText('2016-01-04', start)).toBe('Loaded Jan 2016 – Jan 2016');
    expect(loadedText('2016-01-03', start)).toBe('');
    expect(loadedText(null, start)).toBe('');
  });

  it('writes what the candles and the model did', () => {
    expect(candlesText(step('candles', { candles: 35912, from_cache: false }))).toBe('35,912 candles');
    expect(candlesText(step('candles', { candles: 412, from_cache: true }))).toBe('412 candles · from the cache');
    expect(candlesText(step('candles'))).toBe('');
    expect(modelText(step('model', { load_s: 1.4, input_s: 2.1, predict_s: 6.8 }))).toBe(
      'load 1.4 s · input 2.1 s · predict 6.8 s',
    );
    expect(modelText(step('model', { load_s: 1.4 }))).toBe('load 1.4 s');
    expect(modelText(step('model'))).toBe('');
  });
});
