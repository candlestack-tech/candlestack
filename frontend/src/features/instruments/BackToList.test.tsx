import { RouterProvider, createMemoryHistory, createRootRoute, createRouter } from '@tanstack/react-router';
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { BackToList } from './BackToList';
import { rememberListSearch } from './lastSearch';

// The back link on a one-route router, loaded first.
async function render(): Promise<{ href: string | undefined; label: string | undefined }> {
  const history = createMemoryHistory({ initialEntries: ['/'] });
  const rootRoute = createRootRoute({ component: BackToList });
  const router = createRouter({ routeTree: rootRoute, history });
  await router.load();
  const html = renderToString(<RouterProvider router={router} />);
  const match = /<a [^>]*href="([^"]*)"[^>]*>.*?(\w+)<\/a>/.exec(html);
  return { href: match?.[1], label: match?.[2] };
}

describe('BackToList', () => {
  it('returns to the list with the search the user came from', async () => {
    rememberListSearch({ q: 'btc', page: 2 });
    expect(await render()).toEqual({ href: '/instruments?q=btc&amp;page=2', label: 'Instruments' });
  });
});
