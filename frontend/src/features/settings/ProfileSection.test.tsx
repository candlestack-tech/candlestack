// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { ARSEN, problem, sentBody, session, stubApi } from '@/test/api';
import { renderApp, warmUpSettings } from '@/test/app';

beforeAll(warmUpSettings);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const RENAME = 'PATCH /api/v1/me';
const RENAMED = { ...ARSEN, display_name: 'Filip Hodoň' };

const card = () => within(screen.getByRole('region', { name: 'Profile' }));
const nameField = () => card().getByRole<HTMLInputElement>('textbox', { name: 'Display name' });
const indicator = () => card().getByRole('status');

describe('Profile', () => {
  it('shows the drawn initials, the name and email, the display name to change, the email and the user ID', async () => {
    stubApi(session(ARSEN));
    await renderApp('/settings/profile');
    expect(card().getByText('AL')).toBeDefined();
    expect(nameField().value).toBe('Arsen Labovich');
    expect(card().getAllByText('arsen@example.com')).toHaveLength(2);
    expect(card().getByText('usr_7f3k2m9qx4').tagName).toBe('CODE');
    expect(card().queryByRole('button', { name: /copy/i })).toBeNull();
    expect(card().queryByRole('button', { name: /save/i })).toBeNull();
    expect(indicator().textContent).toBe('');
  });

  it('saves a changed name, trimmed, on leaving the field, and shows it everywhere', async () => {
    const fetch = stubApi({ ...session(ARSEN), [RENAME]: { status: 200, body: RENAMED } });
    await renderApp('/settings/profile');
    const user = userEvent.setup();

    await user.clear(nameField());
    await user.type(nameField(), '  Filip Hodoň ');
    expect(sentBody(fetch, RENAME)).toBeUndefined();
    await user.tab();

    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Account' }).textContent).toBe('FH'));
    expect(sentBody(fetch, RENAME)).toEqual({ display_name: 'Filip Hodoň' });
    expect(card().getByText('Filip Hodoň', { selector: 'b' })).toBeDefined();
    expect(indicator().textContent).toBe('Saved');
  });

  it('saves the name on Enter, once', async () => {
    const fetch = stubApi({ ...session(ARSEN), [RENAME]: { status: 200, body: RENAMED } });
    await renderApp('/settings/profile');
    const user = userEvent.setup();
    await user.clear(nameField());
    await user.type(nameField(), 'Filip Hodoň{Enter}');
    await vi.waitFor(() => expect(indicator().textContent).toBe('Saved'));
    await user.tab();
    expect(fetch.mock.calls.filter(([path, init]) => path === '/api/v1/me' && init?.method === 'PATCH')).toHaveLength(
      1,
    );
  });

  it('puts the saved name back on Esc and sends nothing', async () => {
    const fetch = stubApi(session(ARSEN));
    await renderApp('/settings/profile');
    const user = userEvent.setup();
    await user.type(nameField(), ' Jr');
    await user.keyboard('{Escape}');
    expect(nameField().value).toBe('Arsen Labovich');
    await user.tab();
    expect(sentBody(fetch, RENAME)).toBeUndefined();
  });

  it('does not send an empty name: the field says so and the card shows Not saved', async () => {
    const fetch = stubApi(session(ARSEN));
    await renderApp('/settings/profile');
    const user = userEvent.setup();
    await user.clear(nameField());
    expect(nameField().getAttribute('aria-invalid')).toBeNull();
    await user.tab();

    expect(nameField().getAttribute('aria-invalid')).toBe('true');
    expect(card().getByText('Enter a name. Not saved until fixed.')).toBeDefined();
    expect(indicator().textContent).toBe('Not saved');
    expect(sentBody(fetch, RENAME)).toBeUndefined();
  });

  it("says Couldn't save, keeps the name and sends it again with Retry", async () => {
    const answers = [
      { status: 503, body: { detail: 'Down for a minute.' } },
      { status: 200, body: RENAMED },
    ];
    const fetch = stubApi({ ...session(ARSEN), [RENAME]: async () => answers.shift()! });
    await renderApp('/settings/profile');
    const user = userEvent.setup();
    await user.clear(nameField());
    await user.type(nameField(), 'Filip Hodoň{Enter}');

    expect((await card().findByRole('alert')).textContent).toBe('Down for a minute.');
    expect(indicator().textContent).toBe("Couldn't save");
    expect(nameField().value).toBe('Filip Hodoň');
    await user.click(card().getByRole('button', { name: 'Retry' }));
    await vi.waitFor(() => expect(indicator().textContent).toBe('Saved'));
    expect(card().queryByRole('button', { name: 'Retry' })).toBeNull();
    expect(card().queryByRole('alert')).toBeNull();
    expect(sentBody(fetch, RENAME)).toEqual({ display_name: 'Filip Hodoň' });
  });

  it("says in plain words when the API refuses the name, not in the API's", async () => {
    stubApi({
      ...session(ARSEN),
      [RENAME]: problem(422, 'validation', "body 'display_name': String should have at most 80 characters"),
    });
    await renderApp('/settings/profile');
    const user = userEvent.setup();
    await user.type(nameField(), ' Jr');
    await user.keyboard('{Enter}');
    expect((await card().findByRole('alert')).textContent).toBe('This name cannot be used. Try another one.');
  });

  it('asks before leaving with an empty name: Keep editing stays, Discard leaves', async () => {
    stubApi(session(ARSEN));
    const { router } = await renderApp('/settings/profile');
    const user = userEvent.setup();
    await user.clear(nameField());
    await user.click(screen.getByRole('link', { name: 'Appearance' }));

    const question = await screen.findByRole('dialog', { name: 'Discard the change?' });
    await user.click(within(question).getByRole('button', { name: 'Keep editing' }));
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(router.state.location.pathname).toBe('/settings/profile');
    expect(nameField().value).toBe('');

    await user.click(screen.getByRole('link', { name: 'Appearance' }));
    await user.click(await screen.findByRole('button', { name: 'Discard' }));
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/settings/appearance'));
  });
});
