// @vitest-environment jsdom
import { cleanup, screen } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { session, stubApi } from '@/test/api';
import { renderApp } from '@/test/app';

// The page on its own; how the route skips it with a session and asks who was denied is in
// routeTree.test.tsx.

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const heading = () => screen.getByRole('heading', { level: 1 }).textContent;
const DENIED = {
  'GET /api/v1/auth/denied': { status: 200, body: { email: 'someone@example.com', provider: 'google' } },
};

// Where a provider button sends the browser: jsdom cannot leave the page, so the stub records it.
async function startWith(name: string) {
  const assign = vi.fn();
  vi.stubGlobal('location', { assign });
  await userEvent.setup().click(screen.getByRole('button', { name }));
  return assign.mock.calls[0]?.[0];
}

describe('SignInPage', () => {
  it('after a failed sign-in offers the buttons again, still to come back to the page asked for', async () => {
    stubApi(session(null));
    await renderApp('/signin?next=%2Fbacktests%3Fpage%3D2&error=failed');
    expect(heading()).toBe('Sign in to CandleStack');
    expect(await startWith('Continue with Google')).toBe('/api/v1/auth/google/start?next=%2Fbacktests%3Fpage%3D2');
  });

  it('after a denied sign-in, another account still comes back to the page asked for', async () => {
    stubApi({ ...session(null), ...DENIED });
    await renderApp('/signin?next=%2Fmodels&error=denied');
    await vi.waitFor(() => expect(heading()).toBe('You are not on the beta list yet'));
    await userEvent.setup().click(screen.getByRole('button', { name: 'Use another account' }));
    expect(await startWith('Continue with GitHub')).toBe('/api/v1/auth/github/start?next=%2Fmodels');
  });

  it('without a page asked for, leaves for the provider without one', async () => {
    stubApi({ ...session(null), ...DENIED });
    const { router } = await renderApp('/signin?error=denied');
    await vi.waitFor(() => expect(heading()).toBe('You are not on the beta list yet'));
    await userEvent.setup().click(screen.getByRole('button', { name: 'Use another account' }));
    expect(router.state.location.href).toBe('/signin');
    expect(await startWith('Continue with GitHub')).toBe('/api/v1/auth/github/start');
  });

  it('drops a next that leads off the app', async () => {
    stubApi(session(null));
    await renderApp('/signin?next=https%3A%2F%2Fevil.example');
    expect(await startWith('Continue with GitHub')).toBe('/api/v1/auth/github/start');
  });
});
