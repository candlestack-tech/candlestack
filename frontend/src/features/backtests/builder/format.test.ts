import { describe, expect, it } from 'vitest';

import { DEFAULT_PREFERENCES, rawCandlesSummary } from '@/test/api';

import { formOf } from './form';
import { costsLine, decisionLine, periodLine, preprocessingLine } from './format';

const form = formOf(DEFAULT_PREFERENCES.backtest_defaults, undefined);

describe('how the builder writes its choices', () => {
  it("writes a model's preprocessing in one line from its manifest", () => {
    expect(preprocessingLine(rawCandlesSummary())).toBe('Candles · 30m · 5 features · window 25');
    const renko = {
      ...rawCandlesSummary(),
      representation: { id: 'renko-triples@1', params: { atr_multiple: 0.6, atr_window: 14 } },
      input_shape: [25, 23],
    };
    expect(preprocessingLine(renko)).toBe('Renko 0.6 × ATR · 30m · 23 features · window 25');
    expect(preprocessingLine({ ...rawCandlesSummary(), input_shape: [1] })).toBe('Candles · 30m');
  });

  it('writes the decision with what Advanced turned on', () => {
    expect(decisionLine(form)).toBe('threshold 0.05 · long only · stop 7 %');
    expect(
      decisionLine({
        ...form,
        direction: 'both',
        rule: 'hysteresis',
        threshold: 0.3,
        exit: 0.1,
        calibration: 'zscore',
        ema: 5,
        confirm: 2,
        minHold: 3,
        drawdownStop: 20,
      }),
    ).toBe('in 0.30, out 0.10 · long and short · stop 7 % · z-score · EMA 5 · confirm 2 · hold 3 · drawdown stop 20 %');
    expect(decisionLine({ ...form, rule: 'kmeans', k: 4 })).toBe('k-means, 4 groups · long only · stop 7 %');
  });

  it('writes the period and the costs', () => {
    expect(periodLine(form, (day) => day.slice(0, 4))).toBe('2021 – 2024');
    expect(costsLine(form)).toBe('fees 0.1 % · slippage 0.02 % · vs Buy and hold');
  });
});
