// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { InstrumentDetail } from '@/api';
import { ARSEN, dashboardAnswer, modelsAnswer, session, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp } from '@/test/app';

// The instrument page's chart draws on a canvas jsdom has not.
vi.mock('lightweight-charts', () => import('@/test/lightweightCharts'));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const heading = () => screen.getByRole('heading', { level: 1 }).textContent;

// What the stubbed fetch was asked for, in order.
const requests = (fetch: ReturnType<typeof stubApi>) => fetch.mock.calls.map(([path]) => path);

const BTC: InstrumentDetail = {
  id: 'crypto:BTCUSDT',
  symbol: 'BTCUSDT',
  name: 'Bitcoin / TetherUS',
  market: 'crypto',
  exchange: 'BINANCE',
  source: 'binance',
  feed: 'spot',
  base: 'BTC',
  quote: 'USDT',
  available_from: 1_502_942_400,
  available_to: 1_790_812_800,
  max_candles: 1000,
  timeframes: ['1h', '1d'],
};

describe('the signed-in pages', () => {
  it('send a visitor without a session to sign in, to come back to the page asked for', async () => {
    for (const url of ['/dashboard', '/backtests', '/models?x=1', '/settings']) {
      stubApi(session(null));
      const { router } = await renderApp(url);
      expect(router.state.location.pathname).toBe('/signin');
      expect(router.state.location.search).toEqual({ next: url });
      expect(heading()).toBe('Sign in to CandleStack');
      cleanup();
    }
  });

  it('keep the instruments from a visitor without a session, asking the API for none of their data', async () => {
    for (const url of ['/instruments?q=btc', '/instrument/crypto:BTCUSDT?tf=1h']) {
      const fetch = stubApi(session(null));
      const { router } = await renderApp(url);
      expect(router.state.location.pathname).toBe('/signin');
      expect(router.state.location.search).toEqual({ next: url });
      expect(heading()).toBe('Sign in to CandleStack');
      expect(requests(fetch)).toEqual(['/api/v1/me']);
      cleanup();
    }
  });

  it('show an error under the site header when the API cannot say who is signed in', async () => {
    stubApi({ 'GET /api/v1/me': { status: 503, body: { detail: 'Down for a minute.' } } });
    await renderApp('/dashboard');
    expect(heading()).toBe('Could not load this page');
    expect(screen.getByText('Down for a minute.')).toBeDefined();
    expect(screen.getByRole('navigation', { name: 'Main' })).toBeDefined();
    expect(screen.getByRole('link', { name: 'Back to home' }).getAttribute('href')).toBe('/');
    expect(screen.queryByRole('link', { name: /instruments/i })).toBeNull();
  });

  it('show the app shell to a signed-in user: header, sidebar, title, footer', async () => {
    stubApi({ ...session(ARSEN), ...dashboardAnswer({ counts: { all: 1, starred: 0, failed: 0 } }) });
    await renderApp('/dashboard');
    await vi.waitFor(() => expect(heading()).toBe('Welcome back, Arsen Labovich'));
    expect(screen.getByRole('link', { name: 'CandleStack dashboard' }).getAttribute('href')).toBe('/dashboard');
    expect(screen.getByRole('link', { name: 'New backtest' }).getAttribute('href')).toBe('/backtests/new');
    expect(screen.getByRole('button', { name: 'Account' }).textContent).toBe('AL');
    expect(screen.queryByRole('navigation', { name: 'Main' })).toBeNull();
    expect(screen.getByRole('contentinfo').textContent).toContain('API docs');
  });

  it('mark the open section in the sidebar', async () => {
    stubApi(session(ARSEN));
    await renderApp('/models');
    const sidebar = within(screen.getByRole('navigation', { name: 'Workspace' }));
    const links = sidebar.getAllByRole('link');
    expect(links.map((link) => [link.textContent, link.getAttribute('href')])).toEqual([
      ['Dashboard', '/dashboard'],
      ['Backtests', '/backtests'],
      ['Models', '/models'],
      ['Instruments', '/instruments'],
      ['Settings', '/settings'],
    ]);
    expect(links.map((link) => link.getAttribute('aria-current'))).toEqual([null, null, 'page', null, null]);
    expect(heading()).toBe('Models');
  });

  it('show the instrument list beside the sidebar, Instruments marked, its search kept', async () => {
    stubApi(session(ARSEN));
    const { router } = await renderApp('/instruments?q=btc&market=crypto');
    expect(heading()).toBe('Instruments');
    expect(router.state.location.search).toEqual({ q: 'btc', market: 'crypto' });
    const sidebar = within(screen.getByRole('navigation', { name: 'Workspace' }));
    expect(sidebar.getByRole('link', { name: 'Instruments' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('searchbox').getAttribute('value')).toBe('btc');
    expect(document.title).toBe('“btc” · Instruments · CandleStack');
  });

  it('show an instrument full width under the app header, its back link to the list', async () => {
    stubApi({
      ...sessionWithPreferences(ARSEN),
      'GET /api/v1/data/instruments/crypto%3ABTCUSDT': { status: 200, body: BTC },
    });
    const { router } = await renderApp('/instrument/crypto:BTCUSDT?tf=1d');
    await vi.waitFor(() => expect(heading()).toBe('Bitcoin / TetherUS'));
    expect(router.state.location.search).toEqual({ tf: '1d' });
    expect(screen.queryByRole('navigation', { name: 'Workspace' })).toBeNull();
    expect(screen.getByRole('link', { name: 'CandleStack dashboard' })).toBeDefined();
    // with the list's last search, if this run has opened the list before
    expect(screen.getByRole('link', { name: 'Instruments' }).getAttribute('href')).toMatch(/^\/instruments(\?|$)/);
    const timeframes = within(screen.getByRole('navigation', { name: 'Timeframe' }));
    expect(timeframes.getByRole('link', { name: '1d' }).getAttribute('aria-current')).toBe('page');
    // The chart takes the height the page leaves: the page's two wrappers are growing flex
    // columns, as the public layout's main column was (chartHeight.test.tsx).
    const page = screen.getByRole('heading', { level: 1 }).closest('main > div > div');
    for (const wrapper of [page, page?.parentElement]) {
      expect(wrapper?.className.split(' ')).toEqual(expect.arrayContaining(['flex', 'flex-1', 'flex-col']));
    }
    expect(document.title).toBe('BTCUSDT 1d · CandleStack');
  });

  it('lead from an instrument that cannot load back to the list, not the public home page', async () => {
    stubApi({
      ...sessionWithPreferences(ARSEN),
      'GET /api/v1/data/instruments/crypto%3ANOPE': { status: 404, body: { detail: 'No instrument crypto:NOPE.' } },
    });
    await renderApp('/instrument/crypto:NOPE');
    await vi.waitFor(() => expect(heading()).toBe('Not found'));
    expect(screen.getByText('No instrument crypto:NOPE.')).toBeDefined();
    expect(screen.getByRole('link', { name: 'Back to instruments' }).getAttribute('href')).toBe('/instruments');
    expect(screen.queryByRole('link', { name: 'Back to home' })).toBeNull();
  });

  it('note under the sidebar that market data is delayed', async () => {
    stubApi(session(ARSEN));
    await renderApp('/backtests');
    const sidebar = screen.getByRole('navigation', { name: 'Workspace' });
    expect(within(sidebar).getByText('Market data delayed 15 min').tagName).toBe('P');
  });

  it('give each page only its title row for now', async () => {
    stubApi(session(ARSEN));
    await renderApp('/settings');
    expect(heading()).toBe('Settings');
    expect(screen.getByText('Your account and how new backtests start')).toBeDefined();
    expect(document.title).toBe('Settings · CandleStack');
  });

  it('open the builder from New backtest, without the sidebar, its back link to where it was opened', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), ...modelsAnswer([]) });
    const { router } = await renderApp('/models');
    const user = userEvent.setup();
    await user.click(screen.getByRole('link', { name: 'New backtest' }));
    await vi.waitFor(() => expect(heading()).toBe('New backtest'));
    expect(router.state.location.pathname).toBe('/backtests/new');
    expect(screen.queryByRole('navigation', { name: 'Workspace' })).toBeNull();
    expect(screen.getByRole('link', { name: '← Models' }).getAttribute('href')).toBe('/models');
    expect(document.title).toBe('New backtest · CandleStack');
  });
});

describe('the sign-in page', () => {
  it('offers GitHub and Google, the beta note and what is shared', async () => {
    stubApi(session(null));
    await renderApp('/signin');
    expect(heading()).toBe('Sign in to CandleStack');
    expect(screen.getByText('New here? The same buttons create your account.')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Continue with GitHub' })).toBeDefined();
    expect(screen.getByRole('button', { name: 'Continue with Google' })).toBeDefined();
    expect(screen.getByText('In beta, open to people on the beta list')).toBeDefined();
    expect(screen.getByText('We only get your name and email. No password is stored.')).toBeDefined();
    expect(document.title).toBe('Sign in · CandleStack');
  });

  it('leaves for the provider through the API, to come back to the page asked for', async () => {
    for (const [name, provider, busy] of [
      ['Continue with GitHub', 'github', 'Opening GitHub'],
      ['Continue with Google', 'google', 'Opening Google'],
    ] as const) {
      stubApi(session(null));
      // jsdom cannot leave the page: the stub records where the page sends the browser.
      const assign = vi.fn();
      vi.stubGlobal('location', { assign });
      await renderApp('/signin?next=%2Fmodels');
      await userEvent.setup().click(screen.getByRole('button', { name }));
      expect(assign).toHaveBeenCalledExactlyOnceWith(`/api/v1/auth/${provider}/start?next=%2Fmodels`);
      expect(screen.getByRole('button', { name: busy })).toHaveProperty('disabled', true);
      cleanup();
      vi.unstubAllGlobals();
    }
  });

  it('leaves Sign in and Try Backtest out of the header', async () => {
    stubApi(session(null));
    await renderApp('/signin');
    const header = within(screen.getByRole('banner'));
    expect(header.queryByRole('link', { name: 'Sign in' })).toBeNull();
    expect(header.queryByRole('link', { name: 'Try Backtest' })).toBeNull();
  });

  it('is skipped with an open session, for the page it would have led to', async () => {
    const cases = [
      ['/signin', '/dashboard'],
      ['/signin?next=%2Fmodels', '/models'],
      ['/signin?next=https%3A%2F%2Fevil.example', '/dashboard'],
    ];
    for (const [url, landing] of cases) {
      stubApi(session(ARSEN));
      const { router } = await renderApp(url ?? '');
      expect([url, router.state.location.href]).toEqual([url, landing]);
      cleanup();
    }
  });

  it('names the account the beta list turned away, and offers another', async () => {
    stubApi({
      ...session(null),
      'GET /api/v1/auth/denied': { status: 200, body: { email: 'someone@example.com', provider: 'google' } },
    });
    const { router } = await renderApp('/signin?next=%2Fmodels&error=denied');
    expect(heading()).toBe('You are not on the beta list yet');
    expect(screen.getByText('someone@example.com').tagName).toBe('B');
    expect(screen.getByRole('link', { name: 'Contact the team' }).getAttribute('href')).toBe(
      'https://candlestack.tech',
    );

    await userEvent.setup().click(screen.getByRole('button', { name: 'Use another account' }));
    expect(heading()).toBe('Sign in to CandleStack');
    expect(router.state.location.search).toEqual({ next: '/models' });
  });

  it('asks who was denied once, answered or not, and only after a denied sign-in', async () => {
    const deniedRequests = (fetch: ReturnType<typeof stubApi>) =>
      fetch.mock.calls.filter(([path]) => path === '/api/v1/auth/denied').length;
    for (const [url, status, asked] of [
      ['/signin?error=denied', 200, 1],
      ['/signin?error=denied', 404, 1],
      ['/signin?error=failed', 200, 0],
      ['/signin', 200, 0],
    ] as const) {
      const fetch = stubApi({
        ...session(null),
        'GET /api/v1/auth/denied': { status, body: { email: 'someone@example.com', provider: 'github' } },
      });
      await renderApp(url);
      await screen.findByRole('heading', { level: 1 });
      expect([url, status, deniedRequests(fetch)]).toEqual([url, status, asked]);
      cleanup();
    }
  });

  it('offers the buttons again after a failed sign-in, or when it cannot say who was denied', async () => {
    for (const [url, denied] of [
      ['/signin?error=failed', 200],
      ['/signin?error=denied', 404],
    ] as const) {
      stubApi({
        ...session(null),
        'GET /api/v1/auth/denied': { status: denied, body: { email: 'someone@example.com', provider: 'github' } },
      });
      await renderApp(url);
      expect(heading()).toBe('Sign in to CandleStack');
      cleanup();
    }
  });
});

describe('the home page', () => {
  it('asks the API only who is signed in, no market data, and shows the example rows', async () => {
    const fetch = stubApi(session(null));
    await renderApp('/');
    expect(heading()).toBe('Backtest your models and compare every experiment');
    const markets = within(document.getElementById('markets') as HTMLElement);
    const rows = markets.getAllByRole('row').slice(1);
    expect(rows.map((row) => within(row).getAllByRole('cell')[0]?.textContent)).toEqual([
      'BTCUSDT',
      'AAPL',
      'ETHUSDT',
      'TSLA',
      'SPY',
    ]);
    expect(markets.getByText('Sample figures, not market data')).toBeDefined();
    expect(requests(fetch)).toEqual(['/api/v1/me']);
  });
});

describe('an unknown address', () => {
  it('shows the public Page not found, also under a signed-in path', async () => {
    for (const url of ['/nope', '/dashboard/nope']) {
      stubApi(session(ARSEN));
      await renderApp(url);
      expect(heading()).toBe('Page not found');
      expect(screen.getByRole('navigation', { name: 'Main' })).toBeDefined();
      cleanup();
    }
  });
});
