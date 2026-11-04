// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { meQueryOptions, preferencesQueryOptions } from '@/api';
import {
  ARSEN,
  NOT_SIGNED_IN,
  dashboardAnswer,
  model,
  modelsAnswer,
  sessionWithPreferences,
  stubApi,
} from '@/test/api';
import { renderApp, warmUpSettings } from '@/test/app';

beforeAll(warmUpSettings);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const card = () => within(screen.getByRole('region', { name: 'Account' }));

async function signedOutAtHome(router: Awaited<ReturnType<typeof renderApp>>['router']) {
  await vi.waitFor(() => expect(router.state.location.pathname).toBe('/'));
  expect(within(screen.getByRole('banner')).getByRole('link', { name: 'Sign in' })).toBeDefined();
}

describe('Account', () => {
  it('signs out everywhere: ends every session, forgets the user and goes home', async () => {
    const fetch = stubApi({
      ...sessionWithPreferences(ARSEN),
      'POST /api/v1/auth/sign-out-everywhere': { status: 204 },
    });
    const { router, queryClient } = await renderApp('/settings/account');
    await queryClient.ensureQueryData(preferencesQueryOptions());
    expect(card().getByText('Ends every session on every device.')).toBeDefined();
    await userEvent.setup().click(card().getByRole('button', { name: 'Sign out everywhere' }));

    await signedOutAtHome(router);
    expect(fetch).toHaveBeenCalledWith('/api/v1/auth/sign-out-everywhere', expect.objectContaining({ method: 'POST' }));
    expect(queryClient.getQueryData(meQueryOptions().queryKey)).toBeNull();
    expect(queryClient.getQueryData(preferencesQueryOptions().queryKey)).toBeUndefined();
  });

  it('counts a session that had already ended as signed out', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), 'POST /api/v1/auth/sign-out-everywhere': NOT_SIGNED_IN });
    const { router } = await renderApp('/settings/account');
    await userEvent.setup().click(card().getByRole('button', { name: 'Sign out everywhere' }));
    await signedOutAtHome(router);
  });

  it('stays signed in and says why when it cannot sign out everywhere', async () => {
    stubApi({
      ...sessionWithPreferences(ARSEN),
      'POST /api/v1/auth/sign-out-everywhere': { status: 503, body: { detail: 'Down for a minute.' } },
    });
    const { router } = await renderApp('/settings/account');
    await userEvent.setup().click(card().getByRole('button', { name: 'Sign out everywhere' }));
    expect((await card().findByRole('alert')).textContent).toBe('Down for a minute.');
    expect(router.state.location.pathname).toBe('/settings/account');
  });

  it('asks before deleting the account; Cancel keeps it', async () => {
    const fetch = stubApi(sessionWithPreferences(ARSEN));
    await renderApp('/settings/account');
    const user = userEvent.setup();
    await user.click(card().getByRole('button', { name: 'Delete account' }));

    const dialog = within(screen.getByRole('dialog', { name: 'Delete account?' }));
    expect(
      dialog.getByText('This deletes your backtests, uploaded models and your account. This cannot be undone.'),
    ).toBeDefined();
    await user.click(dialog.getByRole('button', { name: 'Cancel' }));
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(fetch).not.toHaveBeenCalledWith('/api/v1/me', expect.objectContaining({ method: 'DELETE' }));
  });

  it('says how many backtests and uploaded models go with the account', async () => {
    stubApi({
      ...sessionWithPreferences(ARSEN),
      ...dashboardAnswer({ counts: { all: 15, starred: 5, failed: 2, public: 0 } }),
      ...modelsAnswer([model(), model({ id: 'mdl_a2b3c4d5e6' })]),
    });
    await renderApp('/settings/account');
    await userEvent.setup().click(card().getByRole('button', { name: 'Delete account' }));
    const dialog = within(screen.getByRole('dialog', { name: 'Delete account?' }));
    expect(
      await dialog.findByText('This deletes 15 backtests, 2 uploaded models and your account. This cannot be undone.'),
    ).toBeDefined();
  });

  it('deletes the account from the dialog and goes home signed out', async () => {
    const fetch = stubApi({ ...sessionWithPreferences(ARSEN), 'DELETE /api/v1/me': { status: 204 } });
    const { router, queryClient } = await renderApp('/settings/account');
    const user = userEvent.setup();
    await user.click(card().getByRole('button', { name: 'Delete account' }));
    const dialog = within(screen.getByRole('dialog', { name: 'Delete account?' }));
    await user.click(dialog.getByRole('button', { name: 'Delete account' }));

    await signedOutAtHome(router);
    expect(fetch).toHaveBeenCalledWith('/api/v1/me', expect.objectContaining({ method: 'DELETE' }));
    expect(queryClient.getQueryData(meQueryOptions().queryKey)).toBeNull();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('says in the dialog why the account could not be deleted, and forgets it once closed', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), 'DELETE /api/v1/me': { status: 503, body: { detail: 'Down.' } } });
    await renderApp('/settings/account');
    const user = userEvent.setup();
    await user.click(card().getByRole('button', { name: 'Delete account' }));
    let dialog = within(screen.getByRole('dialog'));
    await user.click(dialog.getByRole('button', { name: 'Delete account' }));
    expect((await dialog.findByRole('alert')).textContent).toBe('Down.');

    await user.keyboard('{Escape}');
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await user.click(card().getByRole('button', { name: 'Delete account' }));
    dialog = within(screen.getByRole('dialog'));
    expect(dialog.queryByRole('alert')).toBeNull();
  });
});
