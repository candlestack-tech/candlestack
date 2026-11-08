import { QueryClient } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { problem, sentBody, stubApi } from '@/test/api';

import { apiKeysQueryOptions, createApiKey, revokeApiKey } from './apiKeys';
import { ApiError } from './client';

afterEach(() => {
  vi.unstubAllGlobals();
});

const KEY = {
  id: 'key_7f3k2m4qx4',
  name: 'Claude Code on my laptop',
  prefix: 'cs_7f3k2m4q',
  scopes: ['read', 'backtests'],
  created_at: '2026-11-03T10:00:00Z',
  last_used_at: null,
  expires_at: null,
};

describe('apiKeysQueryOptions', () => {
  it('lists the keys under the account, so signing out forgets them', async () => {
    stubApi({ 'GET /api/v1/me/api-keys': { status: 200, body: { items: [KEY], limit: 10 } } });
    const options = apiKeysQueryOptions();
    expect(options.queryKey).toEqual(['account', 'api-keys']);
    await expect(new QueryClient().fetchQuery(options)).resolves.toEqual({ items: [KEY], limit: 10 });
  });

  it('asks again on coming back, since every request with a key moves its last use', () => {
    const options = apiKeysQueryOptions();
    expect(options.staleTime).toBeUndefined();
    expect(options.refetchOnWindowFocus).toBe(true);
  });
});

describe('createApiKey', () => {
  it('posts the name, scopes and expiry and returns the key with its secret', async () => {
    const created = { ...KEY, secret: 'cs_example_secret' };
    const fetch = stubApi({ 'POST /api/v1/me/api-keys': { status: 201, body: created } });
    const key = { name: 'Claude Code on my laptop', scopes: ['read' as const], expires_in_days: 90 };
    await expect(createApiKey(key)).resolves.toEqual(created);
    expect(sentBody(fetch, 'POST /api/v1/me/api-keys')).toEqual(key);
  });
});

describe('revokeApiKey', () => {
  it('deletes the key', async () => {
    const fetch = stubApi({ 'DELETE /api/v1/me/api-keys/key_7f3k2m4qx4': { status: 204 } });
    await expect(revokeApiKey('key_7f3k2m4qx4')).resolves.toBeUndefined();
    expect(fetch).toHaveBeenCalledOnce();
  });

  it('reads 404 as revoked already, not as an error', async () => {
    stubApi({ 'DELETE /api/v1/me/api-keys/key_gone': problem(404, 'not-found', 'No such key.') });
    await expect(revokeApiKey('key_gone')).resolves.toBeUndefined();
  });

  it('passes any other failure on', async () => {
    stubApi({ 'DELETE /api/v1/me/api-keys/key_7f3k2m4qx4': { status: 503, body: { detail: 'Down.' } } });
    await expect(revokeApiKey('key_7f3k2m4qx4')).rejects.toBeInstanceOf(ApiError);
  });
});
