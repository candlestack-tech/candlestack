// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, renderHook, screen } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { meQueryOptions } from '@/api';
import { viewerTimeZone } from '@/lib/dates';
import { ARSEN, DEFAULT_PREFERENCES, session, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp, viewerIn } from '@/test/app';

import { useDateStyle, useViewerDateStyle } from './useAppearance';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.documentElement.dataset.theme = 'dark';
  delete document.documentElement.dataset.motion;
  localStorage.clear();
});

const LIGHT_CALM = {
  ...DEFAULT_PREFERENCES,
  appearance: { ...DEFAULT_PREFERENCES.appearance, theme: 'light' as const, reduce_motion: true },
};

describe('the account’s appearance on the page', () => {
  it('wins over this browser’s theme on any page once signed in, and becomes its cache', async () => {
    stubApi(sessionWithPreferences(ARSEN, LIGHT_CALM));
    await renderApp('/');
    await vi.waitFor(() => expect(document.documentElement.dataset.theme).toBe('light'));
    expect(document.documentElement.dataset.motion).toBe('reduce');
    expect(localStorage.getItem('candlestack-theme')).toBe('light');
    expect(localStorage.getItem('candlestack-reduce-motion')).toBe('true');
  });

  it('drops a cached Reduce motion for a visitor, who keeps the theme', async () => {
    document.documentElement.dataset.motion = 'reduce';
    localStorage.setItem('candlestack-reduce-motion', 'true');
    document.documentElement.dataset.theme = 'light';
    stubApi(session(null));
    await renderApp('/');
    await vi.waitFor(() => expect(document.documentElement.dataset.motion).toBeUndefined());
    expect(localStorage.getItem('candlestack-reduce-motion')).toBeNull();
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('lets a visitor switch the theme on this browser only', async () => {
    const fetch = stubApi(session(null));
    await renderApp('/');
    await userEvent.setup().click(screen.getByRole('button', { name: 'Switch to light theme' }));
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(fetch.mock.calls.map(([path]) => path)).not.toContain('/api/v1/me/preferences/appearance');
  });

  it('still gets the account’s settings when the header’s toggle is used before they arrive', async () => {
    const fetch = stubApi(session(ARSEN));
    const answer = fetch.getMockImplementation();
    let theme = 'dark';
    let arrive = () => undefined as void;
    const arrived = new Promise<void>((resolve) => (arrive = resolve));
    fetch.mockImplementation(async (path, init) => {
      if (path === '/api/v1/me/preferences/appearance') {
        theme = 'light';
        return new Response(JSON.stringify({ ...LIGHT_CALM.appearance, theme }), { status: 200 });
      }
      if (path === '/api/v1/me/preferences') {
        const body = JSON.stringify({ ...LIGHT_CALM, appearance: { ...LIGHT_CALM.appearance, theme } });
        await arrived;
        return new Response(body, { status: 200 });
      }
      return answer!(path, init);
    });
    await renderApp('/');
    await vi.waitFor(() => expect(fetch.mock.calls.map(([path]) => path)).toContain('/api/v1/me/preferences'));

    await userEvent.setup().click(screen.getByRole('button', { name: 'Switch to light theme' }));
    arrive();
    await vi.waitFor(() => expect(document.documentElement.dataset.motion).toBe('reduce'));
    await vi.waitFor(() => expect(document.documentElement.dataset.theme).toBe('light'));
  });

  it('drops Reduce motion when the user signs out', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN, LIGHT_CALM), 'POST /api/v1/auth/sign-out': { status: 204 } });
    const { router } = await renderApp('/dashboard');
    await vi.waitFor(() => expect(document.documentElement.dataset.motion).toBe('reduce'));
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Account' }));
    await user.click(screen.getByRole('menuitem', { name: 'Sign out' }));
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(document.documentElement.dataset.motion).toBeUndefined();
    expect(document.documentElement.dataset.theme).toBe('light');
  });
});

describe('useDateStyle', () => {
  function wrapper(queryClient: QueryClient) {
    return ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  }

  it('is the account’s choice when signed in', async () => {
    stubApi(
      sessionWithPreferences(ARSEN, {
        ...DEFAULT_PREFERENCES,
        appearance: { ...DEFAULT_PREFERENCES.appearance, date_format: 'iso', time_format: '12' },
      }),
    );
    const queryClient = new QueryClient();
    queryClient.setQueryData(meQueryOptions().queryKey, ARSEN);
    const { result } = renderHook(useDateStyle, { wrapper: wrapper(queryClient) });
    await vi.waitFor(() =>
      expect(result.current).toEqual({ timeZone: 'America/New_York', dateFormat: 'iso', timeFormat: '12' }),
    );
  });

  it('takes the account’s time zone over the one a page offers a visitor', async () => {
    stubApi(
      sessionWithPreferences(ARSEN, {
        ...DEFAULT_PREFERENCES,
        appearance: { ...DEFAULT_PREFERENCES.appearance, time_zone: 'local' },
      }),
    );
    const queryClient = new QueryClient();
    queryClient.setQueryData(meQueryOptions().queryKey, ARSEN);
    const { result } = renderHook(() => useDateStyle('America/New_York'), { wrapper: wrapper(queryClient) });
    await vi.waitFor(() => expect(result.current.timeZone).toBe(viewerTimeZone()));
  });

  it('is the defaults for a visitor, without asking the API', () => {
    const fetch = stubApi({});
    const queryClient = new QueryClient();
    queryClient.setQueryData(meQueryOptions().queryKey, null);
    const { result } = renderHook(useDateStyle, { wrapper: wrapper(queryClient) });
    expect(result.current).toEqual({ timeZone: 'America/New_York', dateFormat: 'dmy', timeFormat: '24' });
    expect(fetch).not.toHaveBeenCalled();
  });

  it('puts a visitor in the zone the page offers (the chart: its market’s)', () => {
    stubApi({});
    const queryClient = new QueryClient();
    queryClient.setQueryData(meQueryOptions().queryKey, null);
    const { result } = renderHook(() => useDateStyle('Europe/Bratislava'), { wrapper: wrapper(queryClient) });
    expect(result.current).toEqual({ timeZone: 'Europe/Bratislava', dateFormat: 'dmy', timeFormat: '24' });
  });
});

describe('useViewerDateStyle', () => {
  function wrapper(queryClient: QueryClient) {
    return ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  }

  it('is the viewer’s own zone in the account’s formats, though the setting is New York', async () => {
    viewerIn('Europe/Bratislava');
    stubApi(
      sessionWithPreferences(ARSEN, {
        ...DEFAULT_PREFERENCES,
        appearance: { ...DEFAULT_PREFERENCES.appearance, time_zone: 'ny', date_format: 'iso', time_format: '12' },
      }),
    );
    const queryClient = new QueryClient();
    queryClient.setQueryData(meQueryOptions().queryKey, ARSEN);
    const { result } = renderHook(useViewerDateStyle, { wrapper: wrapper(queryClient) });
    await vi.waitFor(() =>
      expect(result.current).toEqual({ timeZone: 'Europe/Bratislava', dateFormat: 'iso', timeFormat: '12' }),
    );
  });

  it('is the viewer’s own zone for a visitor too', () => {
    viewerIn('Europe/Bratislava');
    stubApi({});
    const queryClient = new QueryClient();
    queryClient.setQueryData(meQueryOptions().queryKey, null);
    const { result } = renderHook(useViewerDateStyle, { wrapper: wrapper(queryClient) });
    expect(result.current).toEqual({ timeZone: 'Europe/Bratislava', dateFormat: 'dmy', timeFormat: '24' });
  });
});
