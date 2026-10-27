import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router';
import { renderToString } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

import { meQueryOptions, type Me } from '@/api';
import { Header } from '@/components/Header';
import { ARSEN } from '@/test/api';
import { classes } from '@/test/markup';

// The theme reads the page's <html>, which a server render has not.
vi.mock('@/theme', () => ({ useTheme: () => 'dark' }));

// The header over the public pages, on the given URL, loaded first, for a visitor signed out
// (null) or in.
async function render(url: string, me: Me | null = null): Promise<string> {
  const rootRoute = createRootRoute({ component: Header });
  const page = (path: string) => createRoute({ getParentRoute: () => rootRoute, path });
  const routeTree = rootRoute.addChildren([
    page('/'),
    page('/instruments'),
    page('/instrument/$id'),
    page('/signin'),
    page('/dashboard'),
  ]);
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: [url] }) });
  await router.load();
  const queryClient = new QueryClient();
  queryClient.setQueryData(meQueryOptions().queryKey, me);
  return renderToString(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

const navItem = (html: string, name: string) => classes(html, new RegExp(`<a [^>]*>${name}</a>`));
const isOn = (html: string, name: string) => navItem(html, name).includes('font-medium');

describe('Header', () => {
  it('links only Product and the API docs for a visitor: the instruments are for signed-in accounts', async () => {
    const html = await render('/');
    const nav = /<nav [^>]*aria-label="Main"[^>]*>.*?<\/nav>/.exec(html)?.[0] ?? '';
    expect([...nav.matchAll(/<a [^>]*>([^<]*)<\/a>/g)].map((match) => match[1])).toEqual(['Product', 'API']);
    expect(html).not.toContain('href="/instruments"');
  });

  it('marks Product on the home page', async () => {
    const html = await render('/', ARSEN);
    expect(isOn(html, 'Product')).toBe(true);
    expect(isOn(html, 'Instruments')).toBe(false);
  });

  it('links Instruments into the app for a signed-in user, marked on the list and on an instrument', async () => {
    expect(await render('/', ARSEN)).toMatch(/<a [^>]*href="\/instruments"[^>]*>Instruments<\/a>/);
    for (const url of ['/instruments?q=btc', '/instrument/crypto:BTCUSDT']) {
      const html = await render(url, ARSEN);
      expect(isOn(html, 'Instruments')).toBe(true);
      expect(isOn(html, 'Product')).toBe(false);
    }
  });

  it('opens the API docs of its own host in a new tab', async () => {
    const tag = /<a [^>]*>API<\/a>/.exec(await render('/'))?.[0];
    expect(tag).toContain('href="/api/v1/docs"');
    expect(tag).toContain('target="_blank"');
    expect(tag).toContain('rel="noopener"');
  });

  it('leads Sign in and Try Backtest to the sign-in page, Sign in hidden below md', async () => {
    const html = await render('/');
    expect(html).toMatch(/<a [^>]*href="\/signin"[^>]*>Sign in<\/a>/);
    expect(html).toMatch(/<a [^>]*href="\/signin"[^>]*>Try Backtest<\/a>/);
    expect(classes(html, /<a [^>]*>Sign in/)).toContain('max-md:hidden');
  });

  it('hovers Sign in with the full surface in the dark theme too', async () => {
    const signIn = classes(await render('/'), /<a [^>]*>Sign in/);
    expect(signIn).toContain('dark:hover:bg-accent');
    expect(signIn).not.toContain('dark:hover:bg-accent/50');
  });

  it('has the theme toggle as a 34 px ghost square, as high as the buttons beside it', async () => {
    const toggle = classes(await render('/'), /<button [^>]*aria-label="Switch to light theme"[^>]*>/);
    expect(toggle).toEqual(expect.arrayContaining(['size-[34px]', 'border', 'border-transparent']));
    expect(toggle).not.toContain('size-9');
  });

  it('leaves both out on the sign-in page itself', async () => {
    const html = await render('/signin');
    expect(html).not.toContain('>Sign in</a>');
    expect(html).not.toContain('>Try Backtest</a>');
  });

  it('shows Dashboard and the avatar instead to a signed-in user', async () => {
    const html = await render('/instruments', ARSEN);
    expect(html).toMatch(/<a [^>]*href="\/dashboard"[^>]*>Dashboard<\/a>/);
    expect(html).toMatch(/<button [^>]*aria-label="Account"[^>]*><span [^>]*>AL<\/span><\/button>/);
    expect(html).not.toContain('>Sign in</a>');
    expect(html).not.toContain('>Try Backtest</a>');
  });

  it('has no search box', async () => {
    expect(await render('/instrument/crypto:BTCUSDT')).not.toContain('<input');
  });
});
