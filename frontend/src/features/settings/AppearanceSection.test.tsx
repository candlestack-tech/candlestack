// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { preferencesQueryOptions, type Appearance } from '@/api';
import { cityOf, viewerTimeZone } from '@/lib/dates';
import { ARSEN, DEFAULT_PREFERENCES, sentBody, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp, warmUpSettings } from '@/test/app';

beforeAll(warmUpSettings);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.documentElement.dataset.theme = 'dark';
  delete document.documentElement.dataset.motion;
  localStorage.clear();
});

const UPDATE = 'PATCH /api/v1/me/preferences/appearance';

const answered = (change: Partial<Appearance>) => ({
  status: 200,
  body: { ...DEFAULT_PREFERENCES.appearance, ...change },
});

const card = () => within(screen.getByRole('region', { name: 'Appearance' }));
const indicator = () => card().getByRole('status');
const choices = (name: string) =>
  within(card().getByRole('radiogroup', { name }))
    .getAllByRole('radio')
    .map((radio) => `${radio.textContent}${radio.getAttribute('aria-checked') === 'true' ? ' *' : ''}`);

describe('Appearance', () => {
  it('shows the account’s settings, each format on the same moment', async () => {
    stubApi(sessionWithPreferences(ARSEN));
    await renderApp('/settings/appearance');
    expect(choices('Theme')).toEqual(['Light', 'Dark *']);
    expect(choices('Time zone')).toEqual(['New York *', cityOf(viewerTimeZone())]);
    expect(card().getByText('Exchange time')).toBeDefined();
    expect(choices('Date format')).toEqual(['12 Oct 2024 *', '2024-10-12', '12.10.2024']);
    expect(choices('Time format')).toEqual(['16:05 *', '4:05 PM']);
    expect(card().getByRole('switch', { name: 'Reduce motion' }).getAttribute('aria-checked')).toBe('false');
    expect(card().getByText('Turns off the small animations')).toBeDefined();
  });

  it('switches the theme of the page at once and saves it, like the header’s toggle', async () => {
    const fetch = stubApi({ ...sessionWithPreferences(ARSEN), [UPDATE]: answered({ theme: 'light' }) });
    await renderApp('/settings/appearance');
    await userEvent.setup().click(card().getByRole('radio', { name: 'Light' }));

    expect(document.documentElement.dataset.theme).toBe('light');
    expect(localStorage.getItem('candlestack-theme')).toBe('light');
    expect(screen.getByRole('button', { name: 'Switch to dark theme' })).toBeDefined();
    await vi.waitFor(() => expect(sentBody(fetch, UPDATE)).toEqual({ theme: 'light' }));
  });

  it('follows the header’s toggle, which saves to the account too', async () => {
    const fetch = stubApi({ ...sessionWithPreferences(ARSEN), [UPDATE]: answered({ theme: 'light' }) });
    await renderApp('/settings/appearance');
    await userEvent.setup().click(screen.getByRole('button', { name: 'Switch to light theme' }));
    expect(choices('Theme')).toEqual(['Light *', 'Dark']);
    await vi.waitFor(() => expect(sentBody(fetch, UPDATE)).toEqual({ theme: 'light' }));
  });

  it('saves the time zone and the formats as each changes', async () => {
    const fetch = stubApi({ ...sessionWithPreferences(ARSEN), [UPDATE]: answered({ time_zone: 'local' }) });
    const { queryClient } = await renderApp('/settings/appearance');
    const user = userEvent.setup();

    await user.click(card().getByRole('radio', { name: cityOf(viewerTimeZone()) }));
    expect(card().getByText('Your time zone')).toBeDefined();
    await vi.waitFor(() => expect(sentBody(fetch, UPDATE)).toEqual({ time_zone: 'local' }));

    await user.click(card().getByRole('radio', { name: '2024-10-12' }));
    await vi.waitFor(() => expect(sentBody(fetch, UPDATE)).toEqual({ date_format: 'iso' }));
    await user.click(card().getByRole('radio', { name: '4:05 PM' }));
    await vi.waitFor(() => expect(sentBody(fetch, UPDATE)).toEqual({ time_format: '12' }));
    await vi.waitFor(() => expect(queryClient.isMutating()).toBe(0));
    expect(indicator().textContent).toBe('Saved');
  });

  it('turns the animations off for the whole page with Reduce motion', async () => {
    const fetch = stubApi({ ...sessionWithPreferences(ARSEN), [UPDATE]: answered({ reduce_motion: true }) });
    await renderApp('/settings/appearance');
    const motion = card().getByRole('switch', { name: 'Reduce motion' });
    await userEvent.setup().click(motion);

    expect(motion.getAttribute('aria-checked')).toBe('true');
    await vi.waitFor(() => expect(document.documentElement.dataset.motion).toBe('reduce'));
    expect(localStorage.getItem('candlestack-reduce-motion')).toBe('true');
    await vi.waitFor(() => expect(sentBody(fetch, UPDATE)).toEqual({ reduce_motion: true }));
  });

  it('goes back and says why when a change cannot be saved, and Retry makes it again', async () => {
    const answers = [{ status: 503, body: { detail: 'Down for a minute.' } }, answered({ theme: 'light' })];
    const fetch = stubApi({ ...sessionWithPreferences(ARSEN), [UPDATE]: async () => answers.shift()! });
    const { queryClient } = await renderApp('/settings/appearance');
    const user = userEvent.setup();
    await user.click(card().getByRole('radio', { name: 'Light' }));

    expect((await card().findByRole('alert')).textContent).toBe('Down for a minute.');
    expect(indicator().textContent).toBe("Couldn't save");
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(choices('Theme')).toEqual(['Light', 'Dark *']);
    expect(queryClient.getQueryData(preferencesQueryOptions().queryKey)?.appearance.theme).toBe('dark');

    await user.click(card().getByRole('button', { name: 'Retry' }));
    await vi.waitFor(() => expect(indicator().textContent).toBe('Saved'));
    expect(sentBody(fetch, UPDATE)).toEqual({ theme: 'light' });
    expect(choices('Theme')).toEqual(['Light *', 'Dark']);
    await vi.waitFor(() => expect(document.documentElement.dataset.theme).toBe('light'));
    expect(card().queryByRole('alert')).toBeNull();
  });

  it('does not bring back an old failure on coming back to the section', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), [UPDATE]: { status: 503, body: { detail: 'Down for a minute.' } } });
    const { router } = await renderApp('/settings/appearance');
    await userEvent.setup().click(card().getByRole('radio', { name: 'Light' }));
    await card().findByRole('alert');

    await router.navigate({ to: '/settings/$section', params: { section: 'profile' } });
    await router.navigate({ to: '/settings/$section', params: { section: 'appearance' } });
    expect(await screen.findByRole('region', { name: 'Appearance' })).toBeDefined();
    expect(card().queryByRole('alert')).toBeNull();
    expect(indicator().textContent).toBe('');
  });
});
