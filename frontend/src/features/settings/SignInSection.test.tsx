// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { meQueryOptions, type Me } from '@/api';
import { ADA, session, stubApi } from '@/test/api';
import { renderApp, warmUpSettings } from '@/test/app';

beforeAll(warmUpSettings);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// Google signs in with an account of another email: each row says its own.
const BOTH: Me = {
  ...ADA,
  providers: ['github', 'google'],
  sign_in_methods: [...ADA.sign_in_methods, { provider: 'google', email: 'ada.lovelace@example.org', login: null }],
};

const card = () => within(screen.getByRole('region', { name: 'Sign-in' }));
// The row of a provider: the box around its name.
const row = (name: string) => within(card().getByText(name).closest('div') as HTMLElement);

describe('Sign-in', () => {
  it('shows GitHub connected and Google to connect, which leaves for Google and comes back here', async () => {
    stubApi(session(ADA));
    await renderApp('/settings/sign-in');
    expect(card().getByText('Sign in with either. Keep at least one connected.')).toBeDefined();
    expect(row('GitHub').getByText('Connected · ada@example.com')).toBeDefined();
    expect(row('Google').getByText('Not connected')).toBeDefined();
    expect(row('Google').getByRole('link', { name: 'Connect' }).getAttribute('href')).toBe(
      '/api/v1/auth/google/start?intent=connect&next=%2Fsettings%2Fsign-in',
    );
  });

  it('cannot disconnect the only way to sign in', async () => {
    stubApi(session(ADA));
    await renderApp('/settings/sign-in');
    const disconnect = row('GitHub').getByRole('button', { name: 'Disconnect' });
    expect(disconnect).toHaveProperty('disabled', true);
    expect(disconnect.getAttribute('title')).toBe('Your only way to sign in');
  });

  it('shows each connected method with the email of its own account', async () => {
    stubApi(session(BOTH));
    await renderApp('/settings/sign-in');
    expect(row('GitHub').getByText('Connected · ada@example.com')).toBeDefined();
    expect(row('Google').getByText('Connected · ada.lovelace@example.org')).toBeDefined();
    expect(row('Google').getByRole('button', { name: 'Disconnect' })).toHaveProperty('disabled', false);
  });

  it('disconnects one of two; the other then is the only one', async () => {
    const fetch = stubApi({ ...session(BOTH), 'DELETE /api/v1/me/sign-in-methods/google': { status: 204 } });
    const { queryClient } = await renderApp('/settings/sign-in');
    await userEvent.setup().click(row('Google').getByRole('button', { name: 'Disconnect' }));

    await vi.waitFor(() => expect(row('Google').getByText('Not connected')).toBeDefined());
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/me/sign-in-methods/google',
      expect.objectContaining({ method: 'DELETE' }),
    );
    const me = queryClient.getQueryData(meQueryOptions().queryKey);
    expect(me?.providers).toEqual(['github']);
    expect(me?.sign_in_methods).toEqual(ADA.sign_in_methods);
    expect(row('GitHub').getByRole('button', { name: 'Disconnect' })).toHaveProperty('disabled', true);
  });

  it('says so when the API keeps the last method, and why any other failure happened', async () => {
    stubApi({
      ...session(BOTH),
      'DELETE /api/v1/me/sign-in-methods/github': {
        status: 409,
        body: { type: 'https://candlestack.tech/problems/last-sign-in-method', detail: 'Last one.' },
      },
      'DELETE /api/v1/me/sign-in-methods/google': { status: 503, body: { detail: 'Down for a minute.' } },
    });
    await renderApp('/settings/sign-in');
    const user = userEvent.setup();
    await user.click(row('GitHub').getByRole('button', { name: 'Disconnect' }));
    expect((await card().findByRole('alert')).textContent).toBe('Keep at least one way to sign in.');
    await user.click(row('Google').getByRole('button', { name: 'Disconnect' }));
    await vi.waitFor(() => expect(card().getByRole('alert').textContent).toBe('Down for a minute.'));
    expect(row('GitHub').getByText('Connected · ada@example.com')).toBeDefined();
  });

  it('says how connecting ended when the API comes back with an error', async () => {
    stubApi(session(ADA));
    await renderApp('/settings/sign-in?error=taken');
    expect(card().getByRole('alert').textContent).toBe('That account already signs in to another CandleStack user.');
    cleanup();

    stubApi(session(ADA));
    await renderApp('/settings/sign-in?error=failed');
    expect(card().getByRole('alert').textContent).toBe('Could not connect. Try again.');
    cleanup();

    stubApi(session(ADA));
    await renderApp('/settings/sign-in?error=nope');
    expect(card().queryByRole('alert')).toBeNull();
  });

  it('drops the error from the address but keeps saying it until the next action goes through', async () => {
    stubApi({ ...session(BOTH), 'DELETE /api/v1/me/sign-in-methods/google': { status: 204 } });
    const { router } = await renderApp('/settings/sign-in?error=taken');
    await vi.waitFor(() => expect(router.state.location.searchStr).toBe(''));
    expect(router.state.location.pathname).toBe('/settings/sign-in');
    expect(card().getByRole('alert').textContent).toBe('That account already signs in to another CandleStack user.');

    await userEvent.setup().click(row('Google').getByRole('button', { name: 'Disconnect' }));
    await vi.waitFor(() => expect(row('Google').getByText('Not connected')).toBeDefined());
    expect(card().queryByRole('alert')).toBeNull();
  });
});
