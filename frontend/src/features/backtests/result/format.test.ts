import { describe, expect, it } from 'vitest';

import { backtestDocument } from '@/test/api';
import { step } from '@/test/steps';

import {
  allMetrics,
  basicMetrics,
  candlesText,
  durationText,
  loadedText,
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
  const group = (doc: typeof DOC, name: string) => setupOf(doc, DMY).find((item) => item.name === name)?.rows;

  it('writes each step of the builder as a group with one value per row', () => {
    expect(setupOf(DOC, DMY)).toEqual([
      {
        name: 'Model',
        rows: [
          ['Reads', 'Candles'],
          ['Timeframe', '30m'],
          ['Input', '25 × 5'],
        ],
      },
      {
        name: 'Data',
        rows: [
          ['From', '3 Jun 2024'],
          ['To', '3 Jun 2024'],
          ['Candles', '5'],
        ],
      },
      {
        name: 'Decision',
        rows: [
          ['Threshold', '0.05'],
          ['Positions', 'Long and short'],
        ],
      },
      {
        name: 'Execution',
        rows: [
          ['Fills at', "Next candle's open"],
          ['Stop loss', '5 %'],
          ['Fees', '0.1 %'],
          ['Slippage', '0 %'],
          ['Benchmark', 'Buy and hold'],
        ],
      },
    ]);
  });

  it("takes the run's timeframe without a manifest and leaves out what it does not know", () => {
    const doc = backtestDocument({ model: { ...DOC.model, manifest: {} } });
    expect(group(doc, 'Model')).toEqual([['Timeframe', '30m']]);
    expect(group(backtestDocument({ data: { ...DOC.data, rows: null } }), 'Data')).toHaveLength(2);
  });

  it('writes every rule and the steps that are on, before and after it', () => {
    const decision = (fields: Partial<typeof DOC.decision>) =>
      group(backtestDocument({ decision: { ...DOC.decision, ...fields } }), 'Decision');
    expect(decision({ rule: { name: 'hysteresis', enter: 0.3, exit: 0.1 }, long_only: true })).toEqual([
      ['Enter at', '0.3'],
      ['Leave at', '0.1'],
      ['Positions', 'Long only'],
    ]);
    expect(
      decision({
        rule: { name: 'kmeans', k: 5, window: 500, refit_every: 50 },
        calibrate: { name: 'rank', window: 500 },
        ema: { span: 5 },
        confirm: { candles: 2 },
        min_hold: { candles: 1 },
      }),
    ).toEqual([
      ['Calibration', 'Rank 500'],
      ['Smoothing', 'EMA 5'],
      ['K-means groups', '5'],
      ['K-means window', '500'],
      ['Refit every', '50 candles'],
      ['Confirm', '2 candles'],
      ['Minimum hold', '1 candle'],
      ['Positions', 'Long and short'],
    ]);
    expect(decision({ calibrate: { name: 'zscore', window: 200 } })?.[0]).toEqual(['Calibration', 'Z-score 200']);
  });

  it('writes the fill, the stops and the costs, and no benchmark without one', () => {
    const doc = backtestDocument({
      execution: {
        fill: 'signal_close',
        stop_loss: null,
        drawdown_stop: 0.2,
        position_size: 1,
        fee_bps: 2.5,
        slippage_bps: 1,
      },
      benchmarks: [],
    });
    expect(group(doc, 'Execution')).toEqual([
      ['Fills at', "Signal candle's close"],
      ['Drawdown stop', '20 %'],
      ['Fees', '0.025 %'],
      ['Slippage', '0.01 %'],
    ]);
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
