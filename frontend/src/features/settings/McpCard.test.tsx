// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ConnectedApp } from '@/api';
import { ARSEN, sessionWithPreferences, stubApi } from '@/test/api';
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

// The design's apps: Claude used minutes ago, ChatGPT yesterday evening, one that never called.
const APPS: ConnectedApp[] = [
  {
    id: 'app_claude0001',
    client: {
      name: 'Claude',
      logo_uri: null,
      client_uri: 'https://claude.ai',
      redirect_host: 'claude.ai',
      document_host: 'claude.ai',
    },
    scopes: ['read', 'backtests', 'models'],
    connected_at: '2026-11-02T10:00:00Z',
    last_used_at: '2026-11-08T11:55:00Z',
  },
  {
    id: 'app_chatgpt001',
    client: { name: 'ChatGPT', logo_uri: null, client_uri: null, redirect_host: 'chatgpt.com', document_host: null },
    scopes: ['read'],
    connected_at: '2026-11-06T10:00:00Z',
    last_used_at: '2026-11-08T02:40:00Z',
  },
  {
    id: 'app_notes00001',
    client: {
      name: 'Notes Agent',
      logo_uri: null,
      client_uri: null,
      redirect_host: 'notes.example',
      document_host: null,
    },
    scopes: ['read'],
    connected_at: '2026-11-07T10:00:00Z',
    last_used_at: null,
  },
];

const appsAnswer = (items: ConnectedApp[]) => ({
  'GET /api/v1/me/connected-apps': { status: 200, body: { items } },
});

const NO_KEYS = { 'GET /api/v1/me/api-keys': { status: 200, body: { items: [], limit: 10 } } };

const card = () => within(screen.getByRole('region', { name: 'MCP server' }));
// The row of an app: the box around its name.
const row = (name: string) => within(card().getByText(name).closest('div.grid') as HTMLElement);

async function open(answers: Parameters<typeof stubApi>[0]) {
  const fetch = stubApi({ ...sessionWithPreferences(ARSEN), ...NO_KEYS, ...answers });
  const app = await renderApp('/settings/api-and-mcp');
  return { fetch, ...app, user: userEvent.setup() };
}

describe('MCP server card', () => {
  it('shows the server address of this host with Copy', async () => {
    const { user } = await open(appsAnswer([]));
    const url = `${window.location.origin}/mcp`;
    expect(card().getByText(url)).toBeDefined();
    expect(card().queryByText('Coming soon')).toBeNull();
    await user.click(card().getByRole('button', { name: 'Copy' }));
    expect(await navigator.clipboard.readText()).toBe(url);
    expect(card().getByRole('button', { name: 'Copied' })).toBeDefined();
  });

  it('lists each client with its mark, how it connects and its guide', async () => {
    await open(appsAnswer([]));
    const clients: [string, string][] = [
      ['Claude', 'Add a custom connector, sign in with CandleStack'],
      ['ChatGPT', 'Add a connector, sign in with CandleStack'],
      ['Codex', 'One command, sign in with CandleStack'],
      ['Claude Code', 'One command, sign in with CandleStack or use an API key'],
      ['Cursor', 'One entry in mcp.json with an API key'],
      ['VS Code', 'One entry in mcp.json with an API key'],
      ['Other clients', 'Any MCP client over Streamable HTTP'],
    ];
    for (const [name, how] of clients) {
      const guide = card().getByRole('link', { name: `Guide for ${name}` });
      const section = name === 'Codex' ? '#codex' : '';
      expect(guide.getAttribute('href')).toBe(`https://candlestack.tech/docs/mcp/install/${section}`);
      expect(guide.getAttribute('target')).toBe('_blank');
      const line = within(guide.parentElement as HTMLElement);
      expect(line.getByText(how)).toBeDefined();
      const tile = (guide.parentElement as HTMLElement).querySelector('span[aria-hidden]');
      if (name === 'Other clients') expect(tile?.textContent).toBe('+');
      else expect(tile?.querySelector('svg')).not.toBeNull();
    }
  });

  it('lists the connected apps with their host, scopes, last use and when they connected', async () => {
    await open(appsAnswer(APPS));
    await card().findByText('claude.ai');
    const claude = row('claude.ai');
    expect(claude.getByText('claude.ai')).toBeDefined();
    expect(claude.getByText('models')).toBeDefined();
    expect(claude.getByText('Last used 5 min ago')).toBeDefined();
    expect(claude.getByText('Connected 2 Nov 2026')).toBeDefined();
    expect(claude.getByRole('button', { name: 'Disconnect Claude' })).toBeDefined();

    const chatgpt = row('chatgpt.com');
    expect(chatgpt.getByText('Last used Yesterday 21:40')).toBeDefined();
    const notes = row('Notes Agent');
    expect(notes.getByText('Never used')).toBeDefined();
    expect(notes.getByText('N')).toBeDefined();

    const head = card().getByRole('heading', { name: /Connected apps/ });
    expect(head.textContent).toBe('Connected apps3');
    expect(card().getByText('Apps that signed in with your account. Keys are in the card above.')).toBeDefined();
  });

  it('says no app is connected yet, and how one does', async () => {
    await open(appsAnswer([]));
    expect(await card().findByText('No apps connected')).toBeDefined();
    expect(
      card().getByText(
        'Add the server URL as a connector in Claude or ChatGPT. You sign in and choose what it can do.',
      ),
    ).toBeDefined();
    expect(card().getByRole('heading', { name: /Connected apps/ }).textContent).toBe('Connected apps0');
  });

  it('says why the apps did not load, and tries again', async () => {
    let calls = 0;
    const { user } = await open({
      'GET /api/v1/me/connected-apps': async () =>
        ++calls === 1
          ? { status: 503, body: { detail: 'Down for a minute.' } }
          : { status: 200, body: { items: APPS } },
    });
    expect((await card().findByRole('alert')).textContent).toBe('Down for a minute.');
    await user.click(card().getByRole('button', { name: 'Try again' }));
    expect(await card().findByRole('button', { name: 'Disconnect Claude' })).toBeDefined();
    expect(card().queryByRole('alert')).toBeNull();
  });
});

describe('Disconnect', () => {
  it('asks first, Cancel keeps the app, Disconnect ends it', async () => {
    const { fetch, user } = await open({
      ...appsAnswer(APPS),
      'DELETE /api/v1/me/connected-apps/app_chatgpt001': { status: 204 },
    });
    await user.click(await card().findByRole('button', { name: 'Disconnect ChatGPT' }));
    let dialog = within(await screen.findByRole('dialog', { name: 'Disconnect ChatGPT?' }));
    expect(
      dialog.getByText('ChatGPT loses access to your account at once. Its chats keep what they already show.'),
    ).toBeDefined();
    expect(dialog.getByText('You can connect it again from ChatGPT any time.')).toBeDefined();
    expect(dialog.getByText('chatgpt.com')).toBeDefined();
    await user.click(dialog.getByRole('button', { name: 'Cancel' }));
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);

    await user.click(card().getByRole('button', { name: 'Disconnect ChatGPT' }));
    dialog = within(await screen.findByRole('dialog'));
    await user.click(dialog.getByRole('button', { name: 'Disconnect' }));
    await vi.waitFor(() => expect(card().queryByText('chatgpt.com')).toBeNull());
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/me/connected-apps/app_chatgpt001',
      expect.objectContaining({ method: 'DELETE' }),
    );
    expect(card().getByRole('heading', { name: /Connected apps/ }).textContent).toBe('Connected apps2');
  });

  it('says why it failed and keeps the app', async () => {
    const { user } = await open({
      ...appsAnswer(APPS),
      'DELETE /api/v1/me/connected-apps/app_claude0001': { status: 503, body: { detail: 'Down for a minute.' } },
    });
    await user.click(await card().findByRole('button', { name: 'Disconnect Claude' }));
    const dialog = within(await screen.findByRole('dialog'));
    await user.click(dialog.getByRole('button', { name: 'Disconnect' }));
    expect((await dialog.findByRole('alert')).textContent).toBe('Down for a minute.');
    // The row behind the dialog, and the app in it.
    expect(screen.getAllByText('claude.ai')).toHaveLength(2);
  });
});
