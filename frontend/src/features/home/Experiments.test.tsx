import { RouterProvider, createMemoryHistory, createRootRoute, createRouter } from '@tanstack/react-router';
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { Experiments } from './Experiments';

// The section needs a router for its link.
async function render(): Promise<string> {
  const rootRoute = createRootRoute({ component: Experiments });
  const router = createRouter({ routeTree: rootRoute, history: createMemoryHistory({ initialEntries: ['/'] }) });
  await router.load();
  return renderToString(<RouterProvider router={router} />);
}

describe('Experiments', () => {
  it('leads to the public experiments', async () => {
    expect(await render()).toMatch(/<a [^>]*href="\/experiments"[^>]*>Open Experiments<\/a>/);
  });

  it('shows three example cards with their figures, marked as an example', async () => {
    const html = await render();
    const names = [...html.matchAll(/<h3 [^>]*>([^<]*)<\/h3>/g)].map((match) => match[1]);
    expect(names).toEqual(['Quiet Otter', 'Bright Falcon', 'Calm Lynx']);
    const terms = [...html.matchAll(/<dt [^>]*>([^<]*)<\/dt>/g)].map((match) => match[1]);
    expect(terms).toEqual(Array.from({ length: 3 }, () => ['Return', 'Sharpe', 'Max DD', 'Trades']).flat());
    expect(html).toContain('>EXAMPLE</span>');
    expect(html).toContain('Sample figures, not market data');
    expect(html).not.toContain('Coming soon');
  });

  it('draws both lines of a card inside its box', async () => {
    const html = await render();
    const charts = [...html.matchAll(/<svg viewBox="0 0 300 40".*?<\/svg>/g)].map((match) => match[0]).join('');
    const ys = [...charts.matchAll(/<path d="([^"]*)"/g)].flatMap((match) =>
      [...(match[1] ?? '').matchAll(/[ML][\d.]+ (-?[\d.]+)/g)].map((point) => Number(point[1])),
    );
    expect(ys).toHaveLength(3 * 2 * 21);
    expect(Math.min(...ys)).toBe(2);
    expect(Math.max(...ys)).toBe(38);
  });
});
