import { QueryClient } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { problem, sentBody, stubApi } from '@/test/api';

import { ApiError } from './client';
import {
  authorizationRequestQueryOptions,
  connectedAppsQueryOptions,
  decideAuthorizationRequest,
  disconnectApp,
} from './oauth';

afterEach(() => {
  vi.unstubAllGlobals();
});

const CLAUDE = { name: 'Claude', logo_uri: null, client_uri: 'https://claude.ai', redirect_host: 'claude.ai' };

const REQUEST = {
  client: CLAUDE,
  scopes: [{ scope: 'read', description: 'See your backtests, models and market data' }],
  expires_at: '2026-11-08T12:10:00Z',
};

const APP = {
  id: 'app_7f3k2m4qx4',
  client: CLAUDE,
  scopes: ['read', 'backtests'],
  connected_at: '2026-11-02T10:00:00Z',
  last_used_at: null,
};

describe('authorizationRequestQueryOptions', () => {
  it('reads the request without a session, and keeps it while the page is open', async () => {
    stubApi({ 'GET /api/v1/oauth/requests/req_abc-123': { status: 200, body: REQUEST } });
    const options = authorizationRequestQueryOptions('req_abc-123');
    expect(options.queryKey).toEqual(['oauth', 'requests', 'req_abc-123']);
    expect(options.staleTime).toBe(Infinity);
    await expect(new QueryClient().fetchQuery(options)).resolves.toEqual(REQUEST);
  });
});

describe('decideAuthorizationRequest', () => {
  it('posts the decision with the scopes and returns where the browser goes', async () => {
    const back = { redirect_to: 'https://claude.ai/api/mcp/auth_callback?code=abc&state=xyz' };
    const fetch = stubApi({ 'POST /api/v1/oauth/requests/req_abc': { status: 200, body: back } });
    const consent = { decision: 'allow' as const, scopes: ['read' as const, 'backtests' as const] };
    await expect(decideAuthorizationRequest('req_abc', consent)).resolves.toEqual(back);
    expect(sentBody(fetch, 'POST /api/v1/oauth/requests/req_abc')).toEqual(consent);
  });

  it('passes a refusal on with its problem', async () => {
    stubApi({ 'POST /api/v1/oauth/requests/req_abc': problem(403, 'not-on-beta-list', 'Not on the beta list.') });
    await expect(decideAuthorizationRequest('req_abc', { decision: 'allow' })).rejects.toMatchObject({
      problem: 'not-on-beta-list',
    });
  });
});

describe('connectedAppsQueryOptions', () => {
  it('lists the apps under the account, so signing out forgets them, and asks again on coming back', async () => {
    stubApi({ 'GET /api/v1/me/connected-apps': { status: 200, body: { items: [APP] } } });
    const options = connectedAppsQueryOptions();
    expect(options.queryKey).toEqual(['account', 'connected-apps']);
    expect(options.refetchOnWindowFocus).toBe(true);
    await expect(new QueryClient().fetchQuery(options)).resolves.toEqual({ items: [APP] });
  });
});

describe('disconnectApp', () => {
  it('deletes the app', async () => {
    const fetch = stubApi({ 'DELETE /api/v1/me/connected-apps/app_7f3k2m4qx4': { status: 204 } });
    await expect(disconnectApp('app_7f3k2m4qx4')).resolves.toBeUndefined();
    expect(fetch).toHaveBeenCalledOnce();
  });

  it('reads 404 as disconnected already, not as an error', async () => {
    stubApi({ 'DELETE /api/v1/me/connected-apps/app_gone': problem(404, 'not-found', 'No such app.') });
    await expect(disconnectApp('app_gone')).resolves.toBeUndefined();
  });

  it('passes any other failure on', async () => {
    stubApi({ 'DELETE /api/v1/me/connected-apps/app_7f3k2m4qx4': { status: 503, body: { detail: 'Down.' } } });
    await expect(disconnectApp('app_7f3k2m4qx4')).rejects.toBeInstanceOf(ApiError);
  });
});
