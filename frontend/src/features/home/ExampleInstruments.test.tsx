import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { classes } from '@/test/markup';

import { ExampleInstruments } from './ExampleInstruments';

const render = () => renderToString(<ExampleInstruments />);

const rows = (html: string) =>
  [...html.matchAll(/<tr class="[^"]*border-b[^>]*>(.*?)<\/tr>/g)].map((match) => match[1] ?? '');
const cells = (row: string) => [...row.matchAll(/<td[^>]*>(.*?)<\/td>/g)].map((match) => match[0]);
// The text between the tags of server-rendered markup (a test helper, not a sanitizer).
const text = (html: string) =>
  [...html.matchAll(/>([^<]*)</g)]
    .map((match) => match[1] ?? '')
    .join('')
    .replaceAll('&amp;', '&');
const headers = (html: string) => [...html.matchAll(/<th [^>]*>.*?<\/th>/g)].map((match) => match[0]);

describe('ExampleInstruments', () => {
  it('shows the five sample rows: icon, symbol, exchange, 10 days of candles, price and 24 h change', () => {
    const all = rows(render());
    expect(all.map((row) => cells(row).map(text))).toEqual([
      ['BTCUSDT', 'Binance', '10 daily candles, +7.00%7 daily candles, +4.90%', '68,240.00+2.10%', '2.10%'],
      ['AAPL', 'NASDAQ', '10 daily candles, +2.00%7 daily candles, +1.19%', '214.30+0.80%', '0.80%'],
      ['ETHUSDT', 'Binance', '10 daily candles, −2.50%7 daily candles, −3.66%', '3,520.00−1.40%', '1.40%'],
      ['TSLA', 'NASDAQ', '10 daily candles, +8.00%7 daily candles, +5.37%', '251.70+3.20%', '3.20%'],
      ['SPY', 'ARCA', '10 daily candles, +1.90%7 daily candles, +1.29%', '548.90+0.50%', '0.50%'],
    ]);
    expect(all.map((row) => /<img [^>]*src="([^"]*)"/.exec(row)?.[1])).toEqual([
      '/api/v1/data/instruments/crypto%3ABTCUSDT/icon',
      '/api/v1/data/instruments/stock%3AAAPL/icon',
      '/api/v1/data/instruments/crypto%3AETHUSDT/icon',
      '/api/v1/data/instruments/stock%3ATSLA/icon',
      '/api/v1/data/instruments/stock%3ASPY/icon',
    ]);
  });

  it('has the columns of the list: Symbol, Exchange, 10d, Price and 24h', () => {
    expect(headers(render()).map(text)).toEqual(['Symbol', 'Exchange', '10d7d', 'PricePrice · 24h', '24h']);
  });

  it('colours the change and points its triangle up or down', () => {
    const [btc = '', , eth = ''] = rows(render());
    const change = (row: string) => cells(row)[4] ?? '';
    expect(change(btc)).toContain('text-up');
    expect(change(btc)).toContain('d="M4 1 7.5 7h-7Z"');
    expect(change(eth)).toContain('text-down');
    expect(change(eth)).toContain('d="M4 7 .5 1h7Z"');
  });

  it('is no link and does not react to the pointer', () => {
    const html = render();
    expect(html).not.toContain('<a ');
    expect(html).not.toContain('<button');
    expect(html).not.toMatch(/hover:|cursor-pointer/);
    expect(html).not.toContain('tabindex');
  });

  it('says the figures are an example under the rows', () => {
    const html = render();
    const foot = /<\/table><div [^>]*>(.*)<\/div><\/div>$/.exec(html)?.[1] ?? '';
    expect([...foot.matchAll(/<span[^>]*>([^<]*)<\/span>/g)].map((match) => match[1])).toEqual([
      'EXAMPLE',
      'Sample figures, not market data',
    ]);
    expect(classes(foot, /<span [^>]*>/)).toEqual(expect.arrayContaining(['rounded-full', 'border', 'font-mono']));
  });

  it('sits on a solid background, so the page grid does not show through', () => {
    expect(classes(render(), /<div [^>]*>/)).toEqual(expect.arrayContaining(['rounded-lg', 'border', 'bg-background']));
  });
});
