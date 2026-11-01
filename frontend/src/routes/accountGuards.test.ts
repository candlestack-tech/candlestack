import { QueryClient } from '@tanstack/react-query';
import { isRedirect, type ParsedLocation } from '@tanstack/react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { meQueryOptions } from '@/api';
import { ARSEN, session, stubApi } from '@/test/api';

import { requireSignedIn, skipSignInWhenSignedIn } from './accountGuards';

afterEach(() => vi.unstubAllGlobals());

const client = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });
const at = (href: string) => ({ href }) as ParsedLocation;

// Where a guard sends the browser: the options of the redirect it throws, or undefined when it lets the page open.
async function redirectOf(guard: Promise<unknown>) {
  try {
    await guard;
  } catch (thrown) {
    if (isRedirect(thrown)) return thrown.options;
    throw thrown;
  }
  return undefined;
}

describe('requireSignedIn', () => {
  it('sends a visitor without a session to sign in, to come back to the page asked for', async () => {
    stubApi(session(null));
    const sent = await redirectOf(requireSignedIn({ queryClient: client(), location: at('/backtests?page=2') }));
    expect(sent).toMatchObject({ to: '/signin', search: { next: '/backtests?page=2' } });
  });

  it('lets a signed-in user open the page', async () => {
    stubApi(session(ARSEN));
    expect(await redirectOf(requireSignedIn({ queryClient: client(), location: at('/models') }))).toBeUndefined();
  });

  it('asks the API nothing when the user is already known', async () => {
    const fetch = stubApi(session(null));
    const queryClient = client();
    queryClient.setQueryData(meQueryOptions().queryKey, ARSEN);
    expect(await redirectOf(requireSignedIn({ queryClient, location: at('/models') }))).toBeUndefined();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('passes on an error of the API instead of guessing who is signed in', async () => {
    stubApi({ 'GET /api/v1/me': { status: 503, body: { detail: 'Down for a minute.' } } });
    await expect(requireSignedIn({ queryClient: client(), location: at('/models') })).rejects.toThrow(
      'Down for a minute.',
    );
  });
});

describe('skipSignInWhenSignedIn', () => {
  it('lets a visitor without a session sign in', async () => {
    stubApi(session(null));
    const search = { next: '/models', error: undefined };
    expect(await redirectOf(skipSignInWhenSignedIn({ queryClient: client(), search }))).toBeUndefined();
  });

  it('sends a signed-in user where the sign-in would have led, the dashboard by default', async () => {
    for (const [next, landing] of [
      ['/models?page=2', '/models?page=2'],
      [undefined, '/dashboard'],
    ] as const) {
      stubApi(session(ARSEN));
      const sent = await redirectOf(
        skipSignInWhenSignedIn({ queryClient: client(), search: { next, error: undefined } }),
      );
      expect(sent).toMatchObject({ href: landing });
    }
  });
});
