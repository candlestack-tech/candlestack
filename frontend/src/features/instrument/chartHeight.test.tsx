import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider, createMemoryHistory, createRootRoute, createRouter } from '@tanstack/react-router';
import { renderToStaticMarkup, renderToString } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

import type { InstrumentDetail } from '@/api';
import { Layout } from '@/components/Layout';
import { classes } from '@/test/markup';

import { CandleChart } from './CandleChart';
import { InstrumentCandles } from './InstrumentCandles';

// The theme reads the page's <html>, which a server render has not.
vi.mock('@/theme', () => ({ useTheme: () => 'dark' }));

const BTC: InstrumentDetail = {
  id: 'crypto:BTCUSDT',
  symbol: 'BTCUSDT',
  name: 'Bitcoin / TetherUS',
  market: 'crypto',
  exchange: null,
  source: 'binance',
  feed: 'spot',
  base: 'BTC',
  quote: 'USDT',
  available_from: 0,
  available_to: 0,
  max_candles: 1000,
  timeframes: ['1h'],
};

const STYLE = { timeZone: 'UTC', dateFormat: 'dmy', timeFormat: '24' } as const;

const withQueries = (node: React.ReactNode) => (
  <QueryClientProvider client={new QueryClient()}>{node}</QueryClientProvider>
);

async function renderLayout(): Promise<string> {
  const rootRoute = createRootRoute({ component: Layout });
  const router = createRouter({ routeTree: rootRoute, history: createMemoryHistory({ initialEntries: ['/'] }) });
  await router.load();
  return renderToString(withQueries(<RouterProvider router={router} />));
}

// The chart takes the height the page leaves through a chain of flex columns: the screen-high
// layout, its main column, the candles section and the chart in it. A dropped link brings back
// a fixed height or a page that scrolls.
describe('the chart height chain', () => {
  it('makes the layout at least screen-high, with the page in a growing flex column', async () => {
    const html = await renderLayout();
    const root = classes(html, /<div [^>]*>/);
    expect(root).toEqual(
      expect.arrayContaining(['flex', 'flex-col', 'min-h-screen', 'supports-[height:100dvh]:min-h-dvh']),
    );
    expect(classes(html, /<main [^>]*>/)).toEqual(expect.arrayContaining(['flex', 'flex-1', 'flex-col']));
  });

  it('lets the candles section grow, also while it loads, up to 64rem', () => {
    const html = renderToStaticMarkup(
      withQueries(<InstrumentCandles instrument={BTC} timeframe="1h" dateStyle={STYLE} />),
    );
    expect(html).toContain('Loading candles…');
    expect(classes(html, /<section [^>]*>/)).toEqual(
      expect.arrayContaining(['flex', 'flex-1', 'flex-col', 'max-h-[64rem]']),
    );
  });

  it('lets the chart fill its section, never under 16rem, 20rem from sm up', () => {
    const bars = [{ time: 3600, open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 }];
    const html = renderToStaticMarkup(
      <CandleChart bars={bars} market="crypto" dateStyle={STYLE} daily={false} onNearLeftEdge={() => undefined} />,
    );
    expect(classes(html, /<div [^>]*>/)).toEqual(
      expect.arrayContaining(['relative', 'flex-1', 'min-h-64', 'sm:min-h-80']),
    );
    // The chart's own box fills the wrapper, so it follows the wrapper's height.
    const inner = html.slice(html.indexOf('>') + 1);
    expect(classes(inner, /<div [^>]*>/)).toEqual(expect.arrayContaining(['absolute', 'inset-0']));
  });
});
