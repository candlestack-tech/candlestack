// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { meQueryOptions } from '@/api';
import { ARSEN, NOT_SIGNED_IN, session, stubApi } from '@/test/api';
import { renderApp } from '@/test/app';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function openMenu() {
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Account' }));
  return { user, menu: screen.getByRole('menu') };
}

describe('AccountMenu', () => {
  it('shows who is signed in, then Settings, Home page, API docs and Sign out', async () => {
    stubApi(session(ARSEN));
    await renderApp('/dashboard');
    const { menu } = await openMenu();
    expect(menu.textContent).toContain('Arsen Labovich');
    expect(menu.textContent).toContain('arsen@example.com');
    const items = within(menu).getAllByRole('menuitem');
    expect(items.map((item) => item.textContent)).toEqual(['Settings', 'Home page', 'API docs', 'Sign out']);
    expect(items.map((item) => item.getAttribute('href'))).toEqual(['/settings', '/', '/api/v1/docs', null]);
    expect(items[2]?.getAttribute('target')).toBe('_blank');
    expect(items[3]?.dataset.variant).toBe('destructive');
    expect(items.every((item) => item.querySelector('svg') !== null)).toBe(true);
    expect(within(menu).getByRole('separator')).toBeDefined();
  });

  it('opens Settings', async () => {
    stubApi(session(ARSEN));
    const { router } = await renderApp('/dashboard');
    const { user, menu } = await openMenu();
    await user.click(within(menu).getByRole('menuitem', { name: 'Settings' }));
    expect(router.state.location.pathname).toBe('/settings/profile');
  });

  it('signs out: ends the session, forgets the user and goes to the home page', async () => {
    const fetch = stubApi({ ...session(ARSEN), 'POST /api/v1/auth/sign-out': { status: 204 } });
    const { router, queryClient } = await renderApp('/models');
    const { user, menu } = await openMenu();
    await user.click(within(menu).getByRole('menuitem', { name: 'Sign out' }));

    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(fetch).toHaveBeenCalledWith('/api/v1/auth/sign-out', expect.objectContaining({ method: 'POST' }));
    expect(queryClient.getQueryData(meQueryOptions().queryKey)).toBeNull();
    const header = within(screen.getByRole('banner'));
    expect(header.getByRole('link', { name: 'Sign in' })).toBeDefined();
    expect(header.queryByRole('button', { name: 'Account' })).toBeNull();
  });

  it('signs out also when the session had already ended', async () => {
    stubApi({ ...session(ARSEN), 'POST /api/v1/auth/sign-out': NOT_SIGNED_IN });
    const { router, queryClient } = await renderApp('/models');
    const { user, menu } = await openMenu();
    await user.click(within(menu).getByRole('menuitem', { name: 'Sign out' }));

    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(queryClient.getQueryData(meQueryOptions().queryKey)).toBeNull();
  });

  it('keeps the user signed in on the page when the API cannot sign out, to try again', async () => {
    const fetch = stubApi({
      ...session(ARSEN),
      'POST /api/v1/auth/sign-out': { status: 503, body: { detail: 'Down for a minute.' } },
    });
    const { router, queryClient } = await renderApp('/models');
    const first = await openMenu();
    await first.user.click(within(first.menu).getByRole('menuitem', { name: 'Sign out' }));

    await vi.waitFor(() =>
      expect(fetch).toHaveBeenCalledWith('/api/v1/auth/sign-out', expect.objectContaining({ method: 'POST' })),
    );
    await vi.waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
    expect(router.state.location.pathname).toBe('/models');
    expect(queryClient.getQueryData(meQueryOptions().queryKey)).toEqual(ARSEN);
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Models');

    stubApi({ ...session(ARSEN), 'POST /api/v1/auth/sign-out': { status: 204 } });
    const again = await openMenu();
    await again.user.click(within(again.menu).getByRole('menuitem', { name: 'Sign out' }));
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/'));
  });

  it('is in the public header too, beside Dashboard', async () => {
    stubApi(session(ARSEN));
    await renderApp('/');
    const header = within(screen.getByRole('banner'));
    expect(header.getByRole('link', { name: 'Dashboard' }).getAttribute('href')).toBe('/dashboard');
    expect(header.getByRole('button', { name: 'Account' }).textContent).toBe('AL');
    expect(header.queryByRole('link', { name: 'Sign in' })).toBeNull();
    expect(header.queryByRole('link', { name: 'Try Backtest' })).toBeNull();
  });
});
