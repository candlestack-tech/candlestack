import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider, createMemoryHistory, createRootRoute, createRouter } from '@tanstack/react-router';
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { meQueryOptions, type Instrument } from '@/api';
import type { ListSort } from '@/routes/instrumentsSearch';
import { ARSEN } from '@/test/api';
import { classes } from '@/test/markup';

import { InstrumentTable } from './InstrumentTable';

const DAY = 86_400;
const OCT_4 = 1_759_536_000; // 2025-10-04 00:00 UTC
// Ten daily candles, every one up.
const TEN_DAYS: Instrument['spark'] = Array.from({ length: 10 }, (_, i) => [
  OCT_4 + i * DAY,
  80_000 + i * 100,
  80_150 + i * 100,
  79_950 + i * 100,
  80_100 + i * 100,
]);

const BTC: Instrument = {
  id: 'crypto:BTCUSDT',
  symbol: 'BTCUSDT',
  name: 'Bitcoin / TetherUS',
  market: 'crypto',
  exchange: null,
  source: 'binance',
  feed: 'spot',
  base: 'BTC',
  quote: 'USDT',
  price: 83249.35,
  change_24h: 1.51,
  volume_24h: 1258811866,
  spark: TEN_DAYS,
  available_from: 1_502_942_400, // 2017-08-17 04:00 UTC
};
const AAPL: Instrument = {
  id: 'stock:AAPL',
  symbol: 'AAPL',
  name: 'Apple Inc.',
  market: 'stock',
  exchange: 'NASDAQ',
  source: 'alpaca',
  feed: 'sip',
  base: null,
  quote: null,
  price: 247.85,
  change_24h: -0.71,
  volume_24h: 341000000,
  // a down day, a day without a candle, an up day
  spark: [
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    [OCT_4, 250, 251, 247, 248],
    null,
    [OCT_4 + 2 * DAY, 248, 249, 247.5, 248.5],
  ],
  available_from: 1_451_917_800, // 2016-01-04 14:30 UTC
};
const NEW: Instrument = {
  ...AAPL,
  id: 'stock:AAC.U',
  symbol: 'AAC.U',
  name: 'Ares Acquisition Corporation II Units',
  exchange: 'NYSE',
  price: null,
  change_24h: null,
  volume_24h: null,
  spark: null,
  available_from: null,
};
const FLAT: Instrument = { ...AAPL, id: 'stock:SPY', symbol: 'SPY', change_24h: 0.001 };

// The rows' links need a router; a one-route router on "/", loaded first, is enough. The hover
// texts ask whether someone is signed in: a visitor, unless the client says otherwise.
async function render(
  sort?: ListSort,
  items: Instrument[] = [BTC, AAPL, NEW, FLAT],
  queryClient = new QueryClient(),
): Promise<string> {
  const rootRoute = createRootRoute({ component: () => <InstrumentTable items={items} sort={sort} /> });
  const router = createRouter({ routeTree: rootRoute, history: createMemoryHistory({ initialEntries: ['/'] }) });
  await router.load();
  return renderToString(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

const rows = (html: string) =>
  [...html.matchAll(/<tr class="relative[^>]*>(.*?)<\/tr>/g)].map((match) => match[1] ?? '');
const cells = (row: string) => [...row.matchAll(/<td[^>]*>(.*?)<\/td>/g)].map((match) => match[0]);
// The text between the tags of server-rendered markup (a test helper, not a sanitizer).
const text = (html: string) =>
  [...html.matchAll(/>([^<]*)</g)]
    .map((match) => match[1] ?? '')
    .join('')
    .replaceAll('&amp;', '&');
const head = (html: string) => html.match(/<thead.*<\/thead>/)?.[0] ?? '';
const headers = (html: string) => [...head(html).matchAll(/<th [^>]*>.*?<\/th>/g)].map((match) => match[0]);

describe('InstrumentTable', () => {
  it('shows a 32 px icon before the symbol of each row', async () => {
    const [btc, aapl] = rows(await render());
    expect(btc).toMatch(/<img [^>]*src="\/api\/v1\/data\/instruments\/crypto%3ABTCUSDT\/icon"[^>]*>.*>BTCUSDT<\/a>/);
    expect(aapl).toMatch(/<img [^>]*src="\/api\/v1\/data\/instruments\/stock%3AAAPL\/icon"[^>]*>.*>AAPL<\/a>/);
    expect(btc).toMatch(/<img [^>]*width="32"/);
  });

  it('shows the price, the 24 h change and volume, the name, the type, the exchange and since when', async () => {
    const [btc = '', aapl = ''] = rows(await render());
    expect(cells(btc).map(text)).toEqual([
      'BTCUSDTBinance · $1.26B',
      '7 daily candles, +0.87%',
      '83,249.35+1.51%',
      '1.51%',
      // for screen readers, then the hover text of each day
      expect.stringMatching(/^10 daily candles, \+1\.25%Oct 4 · close 80,100.00.*Oct 13 · close 81,000.00$/),
      '$1.26B',
      'Bitcoin / TetherUS',
      'Crypto',
      'Binance',
      'Aug 2017',
    ]);
    expect(cells(aapl).map(text)).toEqual([
      'AAPLNASDAQ · $341M',
      '2 daily candles, −0.60%',
      '247.85−0.71%',
      '0.71%',
      '2 daily candles, −0.60%Oct 4 · close 248.00Oct 6 · close 248.50',
      '$341M',
      'Apple Inc.',
      'Stock',
      'NASDAQ',
      'Jan 2016',
    ]);
  });

  it('colours the change and points its triangle up or down; 0.00 % is muted without one', async () => {
    const [btc = '', aapl = '', , flat = ''] = rows(await render());
    const change = (row: string) => cells(row)[3] ?? '';
    expect(change(btc)).toContain('text-up');
    expect(change(btc)).toContain('d="M4 1 7.5 7h-7Z"');
    expect(change(aapl)).toContain('text-down');
    expect(change(aapl)).toContain('d="M4 7 .5 1h7Z"');
    expect(change(flat)).toContain('text-muted-foreground');
    expect(change(flat)).not.toContain('<svg');
    expect(text(change(flat))).toBe('0.00%');
    // on a phone, under the price, with its sign
    expect(cells(btc)[2]).toMatch(/<span class="[^"]*text-up[^"]*">\+1\.51%<\/span>/);
  });

  it('shows dashes without stats, "no stats yet" on a phone', async () => {
    const [, , fresh = ''] = rows(await render());
    expect(cells(fresh).map(text)).toEqual([
      'AAC.UNYSE · no stats yet',
      'No chart yet',
      '—No stats yet',
      '—',
      'No chart yet',
      '—',
      'Ares Acquisition Corporation II Units',
      'Stock',
      'NYSE',
      '—',
    ]);
    expect(classes(cells(fresh)[2] ?? '', /<span [^>]*>—/)).toContain('text-muted-foreground');
  });

  it('writes since when there is data as month and year in the instrument page’s zone, a dash while unknown', async () => {
    // 2024-02-01 02:00 UTC: still January in New York, where a visitor sees stocks.
    const lateNight = { ...AAPL, available_from: Date.UTC(2024, 1, 1, 2) / 1000 };
    const [stock = '', fresh = ''] = rows(await render(undefined, [lateNight, NEW]));
    const since = (row: string) => cells(row).at(-1) ?? '';
    expect(text(since(stock))).toBe('Jan 2024');
    expect(classes(since(stock), /<td [^>]*>/)).toEqual(expect.arrayContaining(['font-mono', 'text-muted-foreground']));
    expect(since(fresh)).toMatch(/<span title="Not known yet">—<\/span>/);
  });

  it('writes the numbers in Geist Mono', async () => {
    const [btc = ''] = rows(await render());
    expect(classes(btc, /<span [^>]*>83,249.35/)).toContain('font-mono');
    expect(classes(btc, /<td [^>]*>\$1.26B/)).toContain('font-mono');
    expect(classes(btc, /<a [^>]*>BTCUSDT/)).toContain('font-mono');
  });

  it('fits the width of its box: fixed columns, the name takes the rest, a narrow box gets three columns', async () => {
    const html = await render();
    expect(classes(html, /<table [^>]*>/)).toContain('table-fixed');
    const th = (index: number) => classes(headers(html)[index] ?? '', /<th [^>]*>/);
    const [phoneSymbol, phoneCandles, phonePrice, symbol, price, change, candles, volume, name, type, exchange, since] =
      [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11].map(th);
    // a narrow box (a phone): the symbol takes the rest, the candles 60 px, the price 100 px
    expect(phoneSymbol).toContain('@min-[740px]:hidden');
    expect(phoneSymbol?.some((each) => each.startsWith('w-'))).toBe(false);
    expect(phoneCandles).toEqual(expect.arrayContaining(['@min-[740px]:hidden', 'w-[60px]']));
    expect(phonePrice).toEqual(expect.arrayContaining(['@min-[740px]:hidden', 'w-[100px]']));
    // from a 740 px wide table
    expect(symbol).toEqual(expect.arrayContaining(['hidden', '@min-[740px]:table-cell', 'w-[168px]']));
    expect(price).toEqual(expect.arrayContaining(['hidden', '@min-[740px]:table-cell', 'w-[128px]']));
    expect(change).toEqual(expect.arrayContaining(['hidden', '@min-[740px]:table-cell', 'w-[104px]']));
    expect(volume).toEqual(expect.arrayContaining(['hidden', '@min-[740px]:table-cell', 'w-[112px]']));
    // from 880 px, as below it would leave the name no room
    expect(candles).toEqual(expect.arrayContaining(['hidden', '@min-[880px]:table-cell', 'w-[136px]']));
    expect(name?.some((each) => each.startsWith('w-'))).toBe(false);
    expect(type).toEqual(expect.arrayContaining(['hidden', '@min-[1180px]:table-cell', 'w-[88px]']));
    expect(exchange).toEqual(expect.arrayContaining(['hidden', '@min-[740px]:table-cell', 'w-[104px]']));
    // from 1000 px, as below it would leave the name no room
    expect(since).toEqual(expect.arrayContaining(['hidden', '@min-[1000px]:table-cell', 'w-[96px]']));
    const [btc = ''] = rows(html);
    expect(classes(btc, /<span [^>]*>Bitcoin/)).toContain('truncate');
    expect(classes(btc, /<a [^>]*>BTCUSDT</)).toContain('truncate');
  });

  it('shows cut text in full on hover', async () => {
    const [btc = ''] = rows(await render());
    expect(btc).toMatch(/<a [^>]*title="BTCUSDT"[^>]*>BTCUSDT<\/a>/);
    expect(btc).toMatch(/<span title="Bitcoin \/ TetherUS"[^>]*>Bitcoin/);
    expect(btc).toMatch(/<span title="Binance · \$1.26B"/);
    expect(btc).toMatch(/<td title="Binance"[^>]*>Binance</);
  });

  it('keeps the symbol the only link a keyboard or screen reader reaches, so the row stays one tab stop', async () => {
    const all = rows(await render());
    expect(all).toHaveLength(4);
    for (const row of all) {
      const links = [...row.matchAll(/<a [^>]*>/g)].map((match) => match[0]);
      expect(links.filter((link) => !link.includes('tabindex="-1"'))).toHaveLength(1);
    }
  });
});

describe('InstrumentTable candles', () => {
  const svgs = (cell: string) => [...cell.matchAll(/<svg [^>]*>.*?<\/svg>/g)].map((match) => match[0]);

  it('draws 10 days in 112 × 32 px from lg and the last 7 in 44 × 24 on a phone', async () => {
    const [btc = ''] = rows(await render());
    const [phone = ''] = svgs(cells(btc)[1] ?? '');
    const [wide = ''] = svgs(cells(btc)[4] ?? '');
    expect(phone).toContain('viewBox="0 0 44 24"');
    expect(wide).toContain('viewBox="0 0 112 32"');
    expect(wide).toContain('aria-hidden="true"');
    expect(cells(btc)[1]).toContain('style="width:44px;height:24px"');
    expect(cells(btc)[4]).toContain('style="width:112px;height:32px"');
  });

  it('colours up days with --candle-up and down days with --candle-down', async () => {
    const [btc = '', aapl = ''] = rows(await render());
    const [wide = ''] = svgs(cells(btc)[4] ?? '');
    expect(wide).toMatch(/<path class="fill-candle-up" d="M5 [^"]+"/);
    expect(wide).toContain('<path class="fill-candle-down" d=""');
    const [both = ''] = svgs(cells(aapl)[4] ?? '');
    expect(both).toMatch(/<path class="fill-candle-up" d="M106 [^"]+"/); // the last slot
    expect(both).toMatch(/<path class="fill-candle-down" d="M83 [^"]+"/); // the 8th slot
  });

  it('shows the date and close of a day on hover, wide screens only', async () => {
    const [btc = ''] = rows(await render());
    expect(cells(btc)[4]).toMatch(
      /<a [^>]*tabindex="-1"[^>]*aria-hidden="true"[^>]*href="\/instrument\/crypto%3ABTCUSDT"/,
    );
    expect(cells(btc)[4]).toContain('group-hover/day:block">Oct 9 · close 80,600.00</span>');
    expect(cells(btc)[1]).not.toContain('<a ');
  });

  it("writes the day as the chart's axis does for a signed-in user", async () => {
    const signedIn = new QueryClient();
    signedIn.setQueryData(meQueryOptions().queryKey, ARSEN);
    const [btc = ''] = rows(await render(undefined, [BTC], signedIn));
    expect(cells(btc)[4]).toContain('group-hover/day:block">9 Oct · close 80,600.00</span>');
  });

  it('draws a dashed line without candles', async () => {
    const [, , fresh = ''] = rows(await render());
    for (const cell of [cells(fresh)[1] ?? '', cells(fresh)[4] ?? '']) {
      expect(text(cell)).toBe('No chart yet');
      expect(cell).toContain('stroke-dasharray="3 3"');
      expect(cell).not.toContain('<a ');
    }
  });
});

describe('InstrumentTable header', () => {
  // Each header's text, its aria-sort and whether it shows an arrow (↓ or ↑) and is ink.
  const sorted = async (sort?: ListSort) =>
    headers(await render(sort, [BTC])).map((th) => {
      const arrow = th.includes('lucide-arrow-down') ? ' ↓' : th.includes('lucide-arrow-up') ? ' ↑' : '';
      return `${text(th)}${arrow}${th.match(/aria-sort="(\w+)"/)?.[1] === 'none' ? '' : ' (sorted)'}`;
    });

  // The phone's two headers come first (hidden from md up), then the columns from md up.
  it('marks Volume 24h by default; on a phone the symbol header, which says "by volume"', async () => {
    expect(await sorted()).toEqual([
      'Symbol · by volume ↓ (sorted)',
      '7d',
      'Price',
      'Symbol',
      'Price',
      '24h',
      '10d',
      'Volume 24h ↓ (sorted)',
      'Name',
      'Type',
      'Exchange',
      'Data since',
    ]);
  });

  it('marks 24h when sorted by change, down for gainers and up for losers; on a phone the price header', async () => {
    expect((await sorted('gainers')).slice(0, 8)).toEqual([
      'Symbol',
      '7d',
      'Price · 24h ↓ (sorted)',
      'Symbol',
      'Price',
      '24h ↓ (sorted)',
      '10d',
      'Volume 24h',
    ]);
    expect((await sorted('losers')).slice(0, 6)).toEqual([
      'Symbol',
      '7d',
      'Price · 24h ↑ (sorted)',
      'Symbol',
      'Price',
      '24h ↑ (sorted)',
    ]);
  });

  it('marks Symbol going up when sorted by symbol', async () => {
    expect((await sorted('symbol')).slice(0, 8)).toEqual([
      'Symbol ↑ (sorted)',
      '7d',
      'Price',
      'Symbol ↑ (sorted)',
      'Price',
      '24h',
      '10d',
      'Volume 24h',
    ]);
  });

  it('turns the sorted header ink', async () => {
    const [, , , , , change = ''] = headers(await render('gainers', [BTC]));
    expect(classes(change, /<th [^>]*>/)).toContain('text-foreground');
  });
});
