// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { ADA, dashboardAnswer, MB, model, modelsAnswer, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp, warmUpSettings } from '@/test/app';

beforeAll(warmUpSettings);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const tabs = () => within(screen.getByRole('navigation', { name: 'Settings sections' }));

describe('Settings', () => {
  it('opens Profile from /settings, under the title', async () => {
    stubApi(sessionWithPreferences(ADA));
    const { router } = await renderApp('/settings');
    expect(router.state.location.pathname).toBe('/settings/profile');
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Settings');
    expect(screen.getByText('Your account and how new backtests start')).toBeDefined();
    expect(screen.getByRole('heading', { level: 2 }).textContent).toBe('Profile');
    expect(document.title).toBe('Settings · CandleStack');
  });

  it('opens Profile for a section it does not know', async () => {
    stubApi(sessionWithPreferences(ADA));
    const { router } = await renderApp('/settings/billing');
    expect(router.state.location.pathname).toBe('/settings/profile');
  });

  it('has the sections as tabs, each a link, the open one marked', async () => {
    stubApi(sessionWithPreferences(ADA));
    await renderApp('/settings/appearance');
    const links = tabs().getAllByRole('link');
    expect(links.map((link) => [link.textContent, link.getAttribute('href')])).toEqual([
      ['Profile', '/settings/profile'],
      ['Sign-in', '/settings/sign-in'],
      ['Backtest defaults', '/settings/backtest-defaults'],
      ['Appearance', '/settings/appearance'],
      ['Data and storage', '/settings/data-and-storage'],
      ['API and MCP', '/settings/api-and-mcp'],
      ['Account', '/settings/account'],
    ]);
    expect(links.map((link) => link.getAttribute('aria-current'))).toEqual([
      null,
      null,
      null,
      'page',
      null,
      null,
      null,
    ]);
  });

  it('shows one section at a time and switches with the tabs; the sidebar keeps Settings', async () => {
    stubApi(sessionWithPreferences(ADA));
    const { router } = await renderApp('/settings/profile');
    await userEvent.setup().click(tabs().getByRole('link', { name: 'Data and storage' }));
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/settings/data-and-storage'));
    expect(screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent)).toEqual([
      'Data and storage',
    ]);
    const sidebar = within(screen.getByRole('navigation', { name: 'Workspace' }));
    expect(sidebar.getByRole('link', { name: 'Settings' }).getAttribute('aria-current')).toBe('page');
  });

  it('loads the preferences before the sections that show them', async () => {
    const fetch = stubApi(sessionWithPreferences(ADA));
    await renderApp('/settings/backtest-defaults');
    expect(fetch).toHaveBeenCalledWith('/api/v1/me/preferences', expect.anything());
    expect(screen.getByRole('heading', { level: 2 }).textContent).toBe('Backtest defaults');
  });

  it('shows where the market data comes from', async () => {
    stubApi(sessionWithPreferences(ADA));
    await renderApp('/settings/data-and-storage');
    const card = within(screen.getByRole('region', { name: 'Data and storage' }));
    expect(card.getByText('Market data').textContent).toBe('Market dataDelayed 15 min');
    expect(card.getByText('Alpaca, SIP feed')).toBeDefined();
  });

  it('shows the storage the models take and how many were uploaded', async () => {
    stubApi({
      ...sessionWithPreferences(ADA),
      ...modelsAnswer([model({ size_bytes: 20 * MB }), model({ id: 'mdl_a2b3c4d5e6', size_bytes: 5 * MB })]),
    });
    await renderApp('/settings/data-and-storage');
    const card = within(screen.getByRole('region', { name: 'Data and storage' }));
    const usage = await card.findByText('25 MB of 100 MB');
    expect(card.getByText('Models').textContent).toBe('Models2 uploaded');
    const meter = usage.parentElement;
    expect((meter?.lastElementChild?.firstElementChild as HTMLElement | null)?.style.width).toBe('25%');
  });

  it('leaves the models row out until the API has said', async () => {
    stubApi({ ...sessionWithPreferences(ADA), 'GET /api/v1/models': { status: 503, body: { detail: 'Down.' } } });
    await renderApp('/settings/data-and-storage');
    const card = within(screen.getByRole('region', { name: 'Data and storage' }));
    expect(card.getByText('Alpaca, SIP feed')).toBeDefined();
    expect(card.queryByText('Models')).toBeNull();
  });

  it('shows how many backtests are kept, between the market data and the models', async () => {
    stubApi({
      ...sessionWithPreferences(ADA),
      ...dashboardAnswer({ counts: { all: 12, starred: 3, failed: 1, public: 0 } }),
      ...modelsAnswer([model()]),
    });
    await renderApp('/settings/data-and-storage');
    const card = within(screen.getByRole('region', { name: 'Data and storage' }));
    await card.findByText('Models');
    expect(card.getByText('Backtests').textContent).toBe('BacktestsKept until you delete them');
    expect(card.getByText('12')).toBeDefined();
    expect(card.getAllByText(/^(Market data|Backtests|Models)$/).map((label) => label.textContent)).toEqual([
      'Market dataDelayed 15 min',
      'BacktestsKept until you delete them',
      'Models1 uploaded',
    ]);
  });

  it('leaves the backtests row out until the API has said', async () => {
    stubApi({ ...sessionWithPreferences(ADA), 'GET /api/v1/dashboard': { status: 503, body: { detail: 'Down.' } } });
    await renderApp('/settings/data-and-storage');
    const card = within(screen.getByRole('region', { name: 'Data and storage' }));
    expect(card.getByText('Alpaca, SIP feed')).toBeDefined();
    expect(card.queryByText('Backtests')).toBeNull();
  });

  it('shows the API keys, and the MCP server with the address of this host', async () => {
    stubApi({
      ...sessionWithPreferences(ADA),
      'GET /api/v1/me/api-keys': { status: 200, body: { items: [], limit: 10 } },
      'GET /api/v1/me/connected-apps': { status: 200, body: { items: [] } },
    });
    await renderApp('/settings/api-and-mcp');
    const keys = within(screen.getByRole('region', { name: 'API keys' }));
    expect(await keys.findByText('No API keys yet')).toBeDefined();
    expect(keys.queryByText('Coming soon')).toBeNull();
    const mcp = within(screen.getByRole('region', { name: 'MCP server' }));
    expect(mcp.queryByText('Coming soon')).toBeNull();
    expect(mcp.getByText(`${window.location.origin}/mcp`)).toBeDefined();
    expect(await mcp.findByText('No apps connected')).toBeDefined();
  });
});
