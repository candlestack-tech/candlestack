import { describe, expect, it } from 'vitest';

import { ARSEN, backtestDocument, spyDocument } from '@/test/api';
import { experiment, TYMUR } from '@/test/experiments';

import { benchmarkLabel, cardOf, cardOfDocument, experimentTarget, firstName, readsText } from './format';

describe('what a published model reads', () => {
  it('names the representation as the manifests do, with the timeframe and the input', () => {
    expect(readsText(experiment())).toBe('Renko · 30m · 25 × 23');
    expect(readsText(experiment({ representation: 'windows' }))).toBe('Candles · 30m · 25 × 23');
    const unknown = experiment({ representation: 'kagi', model: { ...experiment().model, input_shape: null } });
    expect(readsText(unknown)).toBe('Kagi · 30m');
  });
});

describe('the words of Experiments', () => {
  it('names the benchmark as the backtest page does', () => {
    expect(benchmarkLabel('bh')).toBe('Buy and hold');
    expect(benchmarkLabel('QQQ')).toBe('QQQ');
  });

  it('takes the first name of an author', () => {
    expect(firstName('Tymur Tkach')).toBe('Tymur');
    expect(firstName('  Cher ')).toBe('Cher');
  });
});

describe('experimentTarget', () => {
  it("adds another account's backtest to compare with its author, the user's own without", () => {
    expect(experimentTarget(experiment())).toEqual({
      id: 'bt_t9n8v7d6a5',
      number: 9,
      name: 'Velvet Deneb',
      description: 'NVDA · Renko · 30m',
      status: 'finished',
      symbol: 'NVDA',
      author: 'Tymur Tkach',
    });
    expect(experimentTarget(experiment({ author: { ...TYMUR, mine: true } })).author).toBeUndefined();
  });
});

describe('the facts of a card', () => {
  it('come from a published backtest', () => {
    expect(cardOf(experiment())).toMatchObject({
      name: 'Velvet Deneb',
      description: 'NVDA · Renko · 30m',
      model: { name: 'blesk-novol-8.keras', framework: 'keras' },
      representation: 'Renko',
      timeframe: '30m',
      input: '25 × 23',
      strategy: [0, 0.1, 0.25, 0.412],
      benchmark: { label: 'SPY', series: [0, 0.05, 0.1, 0.143], value: 0.143 },
      totalReturn: 0.412,
      trades: 188,
      author: TYMUR,
      publishedAt: '2026-10-17T10:14:00Z',
    });
  });

  it('come from the document of the backtest about to be published, as returns from 0', () => {
    const doc = backtestDocument();
    const card = cardOfDocument(doc, doc.results!);
    expect(card).toMatchObject({
      name: 'Bright Vega',
      description: 'AAPL · Candles · 30m',
      instruments: [{ symbol: 'AAPL', market: 'stock' }],
      representation: 'Candles',
      timeframe: '30m',
      input: '25 × 5',
      benchmark: { label: 'Buy and hold', value: -0.02 },
      totalReturn: -0.05195,
      author: { name: ARSEN.display_name, mine: true },
      publishedAt: null,
    });
    expect(card.strategy?.[0]).toBe(0);
    expect(card.strategy?.at(-1)).toBeCloseTo(-0.05195, 5);
    expect(card.benchmark.series?.at(-1)).toBeCloseTo(-0.02, 5);
  });

  it('name an ETF benchmark by its symbol', () => {
    const doc = spyDocument();
    expect(cardOfDocument(doc, doc.results!).benchmark).toMatchObject({ label: 'SPY', value: 0.013 });
  });

  it('keep at most about 120 points of a long series, its last one too', () => {
    const doc = backtestDocument();
    const results = doc.results!;
    const long = Array.from({ length: 1000 }, (_, index) => 1 + index / 1000);
    const card = cardOfDocument(doc, {
      ...results,
      series: { ...results.series, columns: { ...results.series.columns, equity: long } },
    });
    expect(card.strategy!.length).toBeLessThanOrEqual(121);
    expect(card.strategy!.at(-1)).toBeCloseTo(0.999, 5);
  });
});
