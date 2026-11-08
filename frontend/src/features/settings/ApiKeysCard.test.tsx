// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { apiKeysQueryOptions, type ApiKey, type ApiKeys } from '@/api';
import { ARSEN, problem, sentBody, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp, warmUpSettings } from '@/test/app';

beforeAll(warmUpSettings);

// 8 Nov 2026, 12:00 UTC: 07:00 in New York, the zone of the API's default settings.
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-11-08T12:00:00Z'));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const key = (fields: Partial<ApiKey>): ApiKey => ({
  id: 'key_laptop0001',
  name: 'Claude Code on my laptop',
  prefix: 'cs_7Hq2Lm9x',
  scopes: ['read', 'backtests', 'models'],
  created_at: '2026-11-03T10:00:00Z',
  last_used_at: '2026-11-08T11:48:00Z',
  expires_at: '2027-02-01T10:00:00Z',
  ...fields,
});

// The design's keys: one used minutes ago, one expiring in 5 days, one never used without expiry,
// and one expired.
const KEYS: ApiKey[] = [
  key({}),
  key({
    id: 'key_nightly001',
    name: 'Nightly sweep script',
    prefix: 'cs_K4pW8nRt',
    scopes: ['read', 'backtests'],
    created_at: '2026-08-14T10:00:00Z',
    last_used_at: '2026-11-08T07:00:00Z',
    expires_at: '2026-11-13T12:00:00Z',
  }),
  key({
    id: 'key_cursor0001',
    name: 'Cursor',
    prefix: 'cs_Z2bE6yQa',
    scopes: ['read'],
    created_at: '2026-10-28T10:00:00Z',
    last_used_at: null,
    expires_at: null,
  }),
  key({
    id: 'key_notebook01',
    name: 'Old notebook',
    prefix: 'cs_M9tD3sVf',
    scopes: ['read', 'backtests'],
    created_at: '2026-08-02T10:00:00Z',
    last_used_at: '2026-10-30T15:00:00Z',
    expires_at: '2026-11-01T12:00:00Z',
  }),
];

const keysAnswer = (items: ApiKey[], limit = 10) => ({
  'GET /api/v1/me/api-keys': { status: 200, body: { items, limit } satisfies ApiKeys },
});

const card = () => within(screen.getByRole('region', { name: 'API keys' }));
// The row of a key: the box around its name.
const row = (name: string) => within(card().getByText(name).closest('div.grid') as HTMLElement);

async function open(answers: Record<string, unknown>) {
  const fetch = stubApi({ ...sessionWithPreferences(ARSEN), ...(answers as Parameters<typeof stubApi>[0]) });
  const app = await renderApp('/settings/api-and-mcp');
  return { fetch, ...app, user: userEvent.setup() };
}

describe('API keys card', () => {
  it('lists each key with its prefix, scopes, last use, creation and expiry', async () => {
    await open(keysAnswer(KEYS));
    expect(await card().findByText('3 of 10')).toBeDefined();
    expect(card().queryByText('Coming soon')).toBeNull();
    expect(
      card().getByText('Call the API from your scripts and agents. A key acts as you, so keep it secret.'),
    ).toBeDefined();

    const laptop = row('Claude Code on my laptop');
    expect(laptop.getByText('cs_7Hq2Lm9x…')).toBeDefined();
    expect(laptop.getByText('read')).toBeDefined();
    expect(laptop.getByText('models')).toBeDefined();
    expect(laptop.getByText('Last used 12 min ago')).toBeDefined();
    expect(laptop.getByText('Expires 1 Feb 2027')).toBeDefined();
    expect(laptop.getByText(/Created 3 Nov 2026/)).toBeDefined();
    expect(laptop.getByRole('button', { name: 'Revoke Claude Code on my laptop' })).toBeDefined();

    const nightly = row('Nightly sweep script');
    expect(nightly.getByText('Last used Today 02:00')).toBeDefined();
    expect(nightly.getByText('Expires in 5 days').classList).toContain('text-warn');

    const cursor = row('Cursor');
    expect(cursor.getByText('Never used')).toBeDefined();
    expect(cursor.getByText('No expiry')).toBeDefined();
  });

  it('greys an expired key with a tag and Delete, and leaves it out of the count', async () => {
    await open(keysAnswer(KEYS));
    await card().findByText('Old notebook');
    const notebook = row('Old notebook');
    expect(notebook.getByText('Expired')).toBeDefined();
    expect(notebook.getByText('Expired 1 Nov 2026').classList).toContain('text-down');
    expect(notebook.getByText('Last used 30 Oct 2026')).toBeDefined();
    expect(notebook.getByRole('button', { name: 'Delete Old notebook' })).toBeDefined();
    expect(card().getByText('3 of 10')).toBeDefined();
  });

  it('links the API guide', async () => {
    await open(keysAnswer(KEYS));
    const guide = card().getByRole('link', { name: 'API guide' });
    expect(guide.getAttribute('href')).toBe('https://candlestack.tech/docs/api');
    expect(guide.getAttribute('target')).toBe('_blank');
  });

  it('says there are no keys yet and offers to create one', async () => {
    const { user } = await open(keysAnswer([]));
    expect(await card().findByText('No API keys yet')).toBeDefined();
    expect(card().getByText('Create one for a script, Claude Code or Cursor.')).toBeDefined();
    expect(card().queryByText(/of 10/)).toBeNull();
    await user.click(card().getByRole('button', { name: 'Create key' }));
    expect(within(await screen.findByRole('dialog')).getByRole('heading', { name: 'Create API key' })).toBeDefined();
  });

  it('stops at the limit: Create key is off with why, and a note says to revoke one', async () => {
    const ten = Array.from({ length: 10 }, (_, index) => key({ id: `key_${index}`, name: `Key ${index}` }));
    await open(keysAnswer([...ten, KEYS[3] as ApiKey]));
    const count = await card().findByText('10 of 10');
    expect(count.classList).toContain('text-warn');
    const create = card().getByRole('button', { name: 'Create key' });
    expect(create).toHaveProperty('disabled', true);
    expect(create.getAttribute('title')).toBe('10 keys is the most. Revoke one first.');
    expect(card().getByText('You have 10 keys, the most there can be. Revoke one to create another.')).toBeDefined();
  });

  it('says why the keys did not load', async () => {
    await open({ 'GET /api/v1/me/api-keys': { status: 503, body: { detail: 'Down for a minute.' } } });
    expect((await card().findByRole('alert')).textContent).toBe('Down for a minute.');
    expect(card().getByRole('link', { name: 'API guide' })).toBeDefined();
  });
});

const CREATED = {
  id: 'key_new0000001',
  name: 'Model upload script',
  prefix: 'cs_P3vN8kWq',
  scopes: ['read', 'models'],
  created_at: '2026-11-08T12:00:00Z',
  last_used_at: null,
  expires_at: '2027-11-08T12:00:00Z',
  secret: 'cs_P3vN8kWqexample0key0for0the0tests0only',
};

describe('Create key', () => {
  it('asks for a name, the scopes and an expiry, read always on', async () => {
    const { user } = await open(keysAnswer(KEYS));
    await user.click(await card().findByRole('button', { name: 'Create key' }));
    const dialog = within(await screen.findByRole('dialog'));
    const name = dialog.getByLabelText(/^Name/);
    expect(document.activeElement).toBe(name);
    expect(dialog.getByText('0 / 40')).toBeDefined();
    expect(dialog.getByRole('button', { name: 'Create key' })).toHaveProperty('disabled', true);

    const read = dialog.getByRole('checkbox', { name: 'Read' });
    expect(read).toHaveProperty('checked', true);
    expect(read).toHaveProperty('disabled', true);
    expect(dialog.getByRole('checkbox', { name: 'Run backtests' })).toHaveProperty('checked', true);
    expect(dialog.getByRole('checkbox', { name: 'Manage models' })).toHaveProperty('checked', false);
    expect(dialog.getByRole('checkbox', { name: 'Publish' })).toHaveProperty('checked', false);
    expect(dialog.getByRole('radio', { name: '90 days' }).getAttribute('aria-checked')).toBe('true');
    expect(dialog.getByText('On 6 Feb 2027. Create a new key then.')).toBeDefined();
    expect(dialog.getByText('You see the key once, right after this')).toBeDefined();

    await user.type(name, '   ');
    expect(dialog.getByRole('button', { name: 'Create key' })).toHaveProperty('disabled', true);
    await user.click(dialog.getByRole('radio', { name: 'Never' }));
    expect(dialog.getByText('Works until you revoke it.')).toBeDefined();
  });

  it('creates the key, shows it once with Copy and examples, then lists it without its secret', async () => {
    const { fetch, user, queryClient } = await open({
      ...keysAnswer(KEYS),
      'POST /api/v1/me/api-keys': { status: 201, body: CREATED },
    });
    await user.click(await card().findByRole('button', { name: 'Create key' }));
    let dialog = within(await screen.findByRole('dialog'));
    await user.type(dialog.getByLabelText(/^Name/), '  Model upload script ');
    expect(dialog.getByText('22 / 40')).toBeDefined();
    await user.click(dialog.getByRole('checkbox', { name: 'Run backtests' }));
    await user.click(dialog.getByRole('checkbox', { name: 'Manage models' }));
    await user.click(dialog.getByRole('checkbox', { name: 'Read' }));
    await user.click(dialog.getByRole('radio', { name: '1 year' }));
    expect(dialog.getByText('On 8 Nov 2027. Create a new key then.')).toBeDefined();
    await user.click(dialog.getByRole('button', { name: 'Create key' }));

    dialog = within(await screen.findByRole('dialog', { name: 'Copy your new key' }));
    expect(sentBody(fetch, 'POST /api/v1/me/api-keys')).toEqual({
      name: 'Model upload script',
      scopes: ['read', 'models'],
      expires_in_days: 365,
    });
    expect(dialog.getByText('You will not see it again')).toBeDefined();
    expect(dialog.getByText('read · models · 1 year')).toBeDefined();
    expect(dialog.getByText(CREATED.secret)).toBeDefined();

    await user.click(dialog.getByRole('button', { name: 'Copy' }));
    expect(await navigator.clipboard.readText()).toBe(CREATED.secret);
    expect(dialog.getByRole('button', { name: 'Copied' })).toBeDefined();

    expect(dialog.getByText(/curl .*\/api\/v1\/me/)).toBeDefined();
    await user.click(dialog.getByRole('radio', { name: 'Claude Code' }));
    expect(dialog.getByText(/claude mcp add --transport http candlestack/)).toBeDefined();
    await user.click(dialog.getByRole('button', { name: 'Copy the example' }));
    expect(await navigator.clipboard.readText()).toContain(`Bearer ${CREATED.secret}`);
    await user.click(dialog.getByRole('radio', { name: 'Cursor' }));
    expect(dialog.getByText(/"mcpServers"/)).toBeDefined();

    await user.click(dialog.getByRole('button', { name: 'Done' }));
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(row('Model upload script').getByText('Never used')).toBeDefined();
    expect(card().getByText('4 of 10')).toBeDefined();
    const cached = queryClient.getQueryData(apiKeysQueryOptions().queryKey);
    expect(JSON.stringify(cached)).not.toContain(CREATED.secret);
  });

  it('sends no expiry for Never, and opens on an empty form again', async () => {
    const { fetch, user } = await open({
      ...keysAnswer([]),
      'POST /api/v1/me/api-keys': { status: 201, body: { ...CREATED, expires_at: null } },
    });
    await user.click(await card().findByRole('button', { name: 'Create key' }));
    const dialog = within(await screen.findByRole('dialog'));
    await user.type(dialog.getByLabelText(/^Name/), 'Cursor');
    await user.click(dialog.getByRole('radio', { name: 'Never' }));
    await user.click(dialog.getByRole('button', { name: 'Create key' }));
    await screen.findByRole('dialog', { name: 'Copy your new key' });
    expect(sentBody(fetch, 'POST /api/v1/me/api-keys')).toEqual({
      name: 'Cursor',
      scopes: ['read', 'backtests'],
      expires_in_days: null,
    });
    expect(screen.getByText('read · models · no expiry')).toBeDefined();

    await user.keyboard('{Escape}');
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await user.click(card().getByRole('button', { name: 'Create key' }));
    expect(within(await screen.findByRole('dialog')).getByRole('heading', { name: 'Create API key' })).toBeDefined();
    expect(screen.queryByText(CREATED.secret)).toBeNull();
  });

  it('says why the API refused, and asks for the list again at the limit', async () => {
    const { fetch, user } = await open({
      ...keysAnswer(KEYS),
      'POST /api/v1/me/api-keys': problem(
        409,
        'too-many-api-keys',
        'You have 10 API keys, the most you can have. Revoke one you no longer use, then create the new one.',
        { limit: 10 },
      ),
    });
    await user.click(await card().findByRole('button', { name: 'Create key' }));
    const dialog = within(await screen.findByRole('dialog'));
    await user.type(dialog.getByLabelText(/^Name/), 'One more');
    await user.click(dialog.getByRole('button', { name: 'Create key' }));
    expect((await dialog.findByRole('alert')).textContent).toBe(
      'You have 10 API keys, the most you can have. Revoke one you no longer use, then create the new one.',
    );
    const lists = () => fetch.mock.calls.filter(([path, init]) => path === '/api/v1/me/api-keys' && !init?.method);
    await vi.waitFor(() => expect(lists()).toHaveLength(2));
    await user.type(dialog.getByLabelText(/^Name/), '!');
    expect(dialog.queryByRole('alert')).toBeNull();
  });

  it('says the name cannot be used when the API refuses it', async () => {
    const { user } = await open({
      ...keysAnswer(KEYS),
      'POST /api/v1/me/api-keys': problem(422, 'validation', "body 'name': Value error"),
    });
    await user.click(await card().findByRole('button', { name: 'Create key' }));
    const dialog = within(await screen.findByRole('dialog'));
    await user.type(dialog.getByLabelText(/^Name/), 'Bad name');
    await user.click(dialog.getByRole('button', { name: 'Create key' }));
    expect((await dialog.findByRole('alert')).textContent).toBe('This name cannot be used. Try another one.');
  });
});

describe('Revoke and Delete', () => {
  it('revokes a key after asking, and Cancel keeps it', async () => {
    const { fetch, user } = await open({
      ...keysAnswer(KEYS),
      'DELETE /api/v1/me/api-keys/key_cursor0001': { status: 204 },
    });
    await user.click(await card().findByRole('button', { name: 'Revoke Cursor' }));
    let dialog = within(await screen.findByRole('dialog', { name: 'Revoke Cursor?' }));
    expect(dialog.getByText('Anything that uses this key stops working at once. It was never used.')).toBeDefined();
    expect(dialog.getByText('This cannot be undone. You can create a new key any time.')).toBeDefined();
    expect(dialog.getByText('cs_Z2bE6yQa…')).toBeDefined();
    await user.click(dialog.getByRole('button', { name: 'Cancel' }));
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);

    await user.click(card().getByRole('button', { name: 'Revoke Cursor' }));
    dialog = within(await screen.findByRole('dialog'));
    await user.click(dialog.getByRole('button', { name: 'Revoke key' }));
    await vi.waitFor(() => expect(card().queryByText('Cursor')).toBeNull());
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/me/api-keys/key_cursor0001',
      expect.objectContaining({ method: 'DELETE' }),
    );
    expect(card().getByText('2 of 10')).toBeDefined();
  });

  it('says when a key was last used', async () => {
    const { user } = await open(keysAnswer(KEYS));
    await user.click(await card().findByRole('button', { name: 'Revoke Nightly sweep script' }));
    const dialog = within(await screen.findByRole('dialog'));
    expect(
      dialog.getByText('Anything that uses this key stops working at once. It was last used today 02:00.'),
    ).toBeDefined();
  });

  it('deletes an expired key, which no longer works', async () => {
    const { fetch, user } = await open({
      ...keysAnswer(KEYS),
      'DELETE /api/v1/me/api-keys/key_notebook01': { status: 204 },
    });
    await user.click(await card().findByRole('button', { name: 'Delete Old notebook' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Delete Old notebook?' }));
    expect(
      dialog.getByText('It expired on 1 Nov 2026 and no longer works. This removes it from the list.'),
    ).toBeDefined();
    await user.click(dialog.getByRole('button', { name: 'Delete key' }));
    await vi.waitFor(() => expect(card().queryByText('Old notebook')).toBeNull());
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/me/api-keys/key_notebook01',
      expect.objectContaining({ method: 'DELETE' }),
    );
  });

  it('says why revoking failed, and stays open', async () => {
    const { user } = await open({
      ...keysAnswer(KEYS),
      'DELETE /api/v1/me/api-keys/key_cursor0001': { status: 503, body: { detail: 'Down for a minute.' } },
    });
    await user.click(await card().findByRole('button', { name: 'Revoke Cursor' }));
    const dialog = within(await screen.findByRole('dialog'));
    await user.click(dialog.getByRole('button', { name: 'Revoke key' }));
    expect((await dialog.findByRole('alert')).textContent).toBe('Down for a minute.');
    expect(screen.getByRole('dialog')).toBeDefined();
    await user.click(dialog.getByRole('button', { name: 'Cancel' }));
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(row('Cursor').getByText('No expiry')).toBeDefined();
  });
});
