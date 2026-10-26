import { describe, expect, it } from 'vitest';

import { backtest, failedBacktest } from '@/test/api';

import { hasResults, instrumentId, instrumentList, instrumentsShown, representationName } from './format';

describe('hasResults', () => {
  it('is true for a finished backtest only', () => {
    expect(hasResults(backtest())).toBe(true);
    expect(hasResults(failedBacktest())).toBe(false);
    expect(hasResults(backtest({ status: 'running' }))).toBe(false);
  });
});

describe('instrumentList', () => {
  it('writes one, a few, or the first three and how many more', () => {
    expect(instrumentList(['AAPL'])).toBe('AAPL');
    expect(instrumentList(['MSFT', 'GOOGL'])).toBe('MSFT and GOOGL');
    expect(instrumentList(['AAPL', 'MSFT', 'NVDA', 'SPY', 'TSLA'])).toBe('AAPL, MSFT, NVDA, SPY and TSLA');
    expect(instrumentList(['AAPL', 'MSFT', 'NVDA', 'SPY', 'TSLA', 'AMD', 'META', 'QQQ'])).toBe(
      'AAPL, MSFT, NVDA and 5 more',
    );
  });
});

describe('instrumentsShown', () => {
  it('shows every instrument that fits, else one less (at least one) and how many more', () => {
    expect(instrumentsShown(['AAPL', 'MSFT'], 2)).toEqual({ shown: ['AAPL', 'MSFT'], more: 0 });
    expect(instrumentsShown(['AAPL', 'MSFT', 'NVDA'], 2)).toEqual({ shown: ['AAPL'], more: 2 });
    expect(instrumentsShown(['AAPL', 'MSFT', 'NVDA'], 3)).toEqual({ shown: ['AAPL', 'MSFT', 'NVDA'], more: 0 });
    expect(instrumentsShown(['AAPL', 'MSFT', 'NVDA', 'SPY', 'TSLA'], 3)).toEqual({ shown: ['AAPL', 'MSFT'], more: 3 });
    expect(instrumentsShown(['AAPL', 'MSFT'], 1)).toEqual({ shown: ['AAPL'], more: 1 });
  });
});

describe('instrumentId', () => {
  it('is the market and the symbol, as the icon is asked for', () => {
    expect(instrumentId({ market: 'stock', symbol: 'BRK.B' })).toBe('stock:BRK.B');
    expect(instrumentId({ market: 'crypto', symbol: 'BTCUSDT' })).toBe('crypto:BTCUSDT');
  });
});

describe('representationName', () => {
  it('names the three representations', () => {
    expect(['renko', 'kagi', 'windows'].map((each) => representationName(each as 'renko'))).toEqual([
      'Renko',
      'Kagi',
      'Time windows',
    ]);
  });
});
