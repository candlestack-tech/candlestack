// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router';
import { cleanup, render, screen } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { meQueryOptions } from '@/api';
import { ARSEN, NOT_SIGNED_IN, stubApi } from '@/test/api';

import { useDeleteAccount, useSignOut, useSignOutEverywhere } from './useLeaveAccount';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME = meQueryOptions().queryKey;
const PREFERENCES = ['account', 'preferences'];
const MODELS = ['account', 'models'];

type Hook = typeof useSignOut;

// A page at /settings with one button that leaves through `useLeave`, in a router that also has
// the home page; the query client knows the user, their preferences and their models.
async function open(useLeave: Hook) {
  function Leave() {
    const leave = useLeave();
    return (
      <>
        <button type="button" onClick={() => leave.mutate()}>
          Leave
        </button>
        {leave.isError && <p>Still here</p>}
      </>
    );
  }
  const rootRoute = createRootRoute();
  const routeTree = rootRoute.addChildren([
    createRoute({ getParentRoute: () => rootRoute, path: '/', component: () => <h1>Home</h1> }),
    createRoute({ getParentRoute: () => rootRoute, path: '/settings', component: Leave }),
  ]);
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: ['/settings'] }) });
  await router.load();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  queryClient.setQueryData(ME, ARSEN);
  queryClient.setQueryData(PREFERENCES, { theme: 'dark' });
  queryClient.setQueryData(MODELS, []);
  queryClient.setQueryData(['auth', 'denied'], null);
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  await userEvent.setup().click(screen.getByRole('button', { name: 'Leave' }));
  return { router, queryClient };
}

describe('useLeaveAccount', () => {
  it.each([
    ['useSignOut', useSignOut, 'POST', '/api/v1/auth/sign-out'],
    ['useSignOutEverywhere', useSignOutEverywhere, 'POST', '/api/v1/auth/sign-out-everywhere'],
    ['useDeleteAccount', useDeleteAccount, 'DELETE', '/api/v1/me'],
  ])('%s asks the API, forgets the user and their data and goes home', async (_, hook, method, path) => {
    const fetch = stubApi({ [`${method} ${path}`]: { status: 204 } });
    const { router, queryClient } = await open(hook);
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(fetch).toHaveBeenCalledWith(path, expect.objectContaining({ method }));
    expect(screen.getByRole('heading', { name: 'Home' })).toBeDefined();
    // The user is known to be signed out, not unknown: no page asks the API again.
    expect(queryClient.getQueryState(ME)?.data).toBeNull();
    expect(queryClient.getQueryState(PREFERENCES)).toBeUndefined();
    expect(queryClient.getQueryState(MODELS)).toBeUndefined();
    // Only the account's data goes.
    expect(queryClient.getQueryState(['auth', 'denied'])).toBeDefined();
  });

  it('goes home also when the session had already ended', async () => {
    stubApi({ 'POST /api/v1/auth/sign-out': NOT_SIGNED_IN });
    const { router, queryClient } = await open(useSignOut);
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(queryClient.getQueryData(ME)).toBeNull();
  });

  it('keeps the user signed in on the page when the API cannot do it', async () => {
    stubApi({ 'POST /api/v1/auth/sign-out': { status: 503, body: { detail: 'Down for a minute.' } } });
    const { router, queryClient } = await open(useSignOut);
    expect(await screen.findByText('Still here')).toBeDefined();
    expect(router.state.location.pathname).toBe('/settings');
    expect(queryClient.getQueryData(ME)).toEqual(ARSEN);
    expect(queryClient.getQueryData(PREFERENCES)).toEqual({ theme: 'dark' });
  });
});
