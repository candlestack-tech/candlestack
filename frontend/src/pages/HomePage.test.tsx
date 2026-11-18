import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider, createMemoryHistory, createRootRoute, createRouter } from '@tanstack/react-router';
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { meQueryOptions, type Me } from '@/api';
import { ADA } from '@/test/api';
import { classes } from '@/test/markup';

import { HomePage } from './HomePage';

// The page needs a router for its links and a query client that knows whether the visitor is
// signed in (null: signed out).
async function render(me: Me | null = null): Promise<string> {
  const rootRoute = createRootRoute({ component: HomePage });
  const router = createRouter({ routeTree: rootRoute, history: createMemoryHistory({ initialEntries: ['/'] }) });
  await router.load();
  const queryClient = new QueryClient();
  queryClient.setQueryData(meQueryOptions().queryKey, me);
  return renderToString(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

// The markup of the section with the given id.
function section(html: string, id: string): string {
  const match = new RegExp(`<section [^>]*id="${id}"[^>]*>.*?</section>`, 's').exec(html);
  if (match === null) throw new Error(`no section "${id}"`);
  return match[0];
}

describe('HomePage', () => {
  it('leads with the headline and puts the sections below it in the order of the design', async () => {
    const html = await render();
    expect(html).toMatch(/<h1 [^>]*>Backtest your models and compare every experiment<\/h1>/);
    const headings = [...html.matchAll(/<h2 [^>]*>([^<]*)<\/h2>/g)].map((match) => match[1]);
    expect(headings).toEqual([
      'Three layers you can swap',
      'US stocks and crypto pairs, candles from 1m to 1d',
      'See which setup holds up',
      'Let your AI agent run the backtests',
      'See what others published',
    ]);
  });

  it('asks a visitor to sign in to browse the instruments, and back to the list after it', async () => {
    const html = await render();
    const markets = section(html, 'markets');
    expect(markets).toContain('Sign in to browse them and open their candles.');
    expect(markets).toMatch(/<a [^>]*href="\/signin\?next=%2Finstruments"[^>]*>Sign in to browse<\/a>/);
    expect(html).not.toContain('Browse instruments');
    expect(html).not.toContain('href="/instruments"');
  });

  it('leads a signed-in visitor to the instrument list from the markets only', async () => {
    const html = await render(ADA);
    const links = html.match(/<a [^>]*href="\/instruments"[^>]*>Browse instruments<\/a>/g);
    expect(links).toHaveLength(1);
    expect(section(html, 'markets')).toContain('>Browse instruments</a>');
    expect(html).not.toContain('Sign in to browse');
  });

  it('shows the example of the instruments, with no link into them', async () => {
    const markets = section(await render(), 'markets');
    expect(markets).toContain('>EXAMPLE</span>');
    expect(markets).not.toContain('/instrument/');
  });

  it('leads Try Backtest to the sign-in page', async () => {
    const html = await render();
    expect(html.match(/>Try Backtest</g)).toHaveLength(2);
    expect(html).toMatch(/<a [^>]*href="\/signin"[^>]*>Try Backtest<\/a>/);
  });

  it('marks the examples of compare, agents and experiments, and nothing as coming soon', async () => {
    const html = await render();
    for (const id of ['compare', 'agents', 'experiments']) expect(section(html, id)).toContain('>EXAMPLE</span>');
    expect(html).not.toContain('Coming soon');
  });

  it('leads Connect an agent from the hero down to the agents', async () => {
    const html = await render();
    expect(html).toMatch(/<a [^>]*href="#agents"[^>]*>Connect an agent<\/a>/);
    expect(html).toMatch(/<section [^>]*id="agents"/);
  });

  it('counts the instruments as the API has them', async () => {
    const html = await render();
    expect(html).toContain('14,000+ stocks and pairs');
    expect(html).not.toContain('15,000');
  });

  it('lists the model frameworks a user can bring', async () => {
    const html = await render();
    const badges = [...section(html, 'layers').matchAll(/<li [^>]*>([^<]*)<\/li>/g)].map((match) => match[1]);
    expect(badges).toEqual(expect.arrayContaining(['Keras', 'ONNX']));
    expect(badges).not.toContain('PyTorch');
    expect(badges).toHaveLength(8);
  });

  it('stops the dashes on the pipeline when the user asks for less motion', async () => {
    const html = await render();
    const dash = classes(html, /<span [^>]*animate-pipeline-flow[^>]*>/);
    expect(dash).toContain('motion-reduce:animate-none');
    expect(dash).toContain('motion-reduce:left-1/2');
    expect(html.match(/animate-pipeline-flow/g)).toHaveLength(4);
  });
});
