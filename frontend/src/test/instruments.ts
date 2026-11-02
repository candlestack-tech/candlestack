import type { InstrumentDetail } from '@/api';

// Bitcoin on Binance as the API describes it, with daily and hourly candles.
export const BTC: InstrumentDetail = {
  id: 'crypto:BTCUSDT',
  symbol: 'BTCUSDT',
  name: 'Bitcoin / TetherUS',
  market: 'crypto',
  exchange: 'BINANCE',
  source: 'binance',
  feed: 'spot',
  base: 'BTC',
  quote: 'USDT',
  available_from: 1_502_942_400, // 2017-08-17 04:00 UTC
  available_to: 1_790_812_800,
  max_candles: 1000,
  timeframes: ['1h', '1d'],
};
