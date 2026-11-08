import { queryOptions } from '@tanstack/react-query';

import { ApiError, del, get, postJson } from './client';
import type { components } from './schema';

type Schemas = components['schemas'];

export type ApiKey = Schemas['ApiKeyOut'];
export type ApiKeys = Schemas['ApiKeysOut'];
export type ApiKeyCreated = Schemas['ApiKeyCreated'];
export type ApiKeyIn = Schemas['ApiKeyIn'];
export type Scope = Schemas['Scope'];

// GET /api/v1/me/api-keys: the user's keys that are not revoked, oldest first, and how many can
// work at once. Under ['account', ...] with the user, so signing out forgets them. Fresh until the
// app changes them: keys are made and revoked only here, with the session.
export function apiKeysQueryOptions() {
  return queryOptions({
    queryKey: ['account', 'api-keys'],
    queryFn: ({ signal }) => get<ApiKeys>('/v1/me/api-keys', signal),
    staleTime: Infinity,
  });
}

// POST /api/v1/me/api-keys: a new key, with its secret in this answer only. 409
// too-many-api-keys when the user already has as many working keys as the limit.
export const createApiKey = (key: ApiKeyIn) => postJson<ApiKeyCreated>('/v1/me/api-keys', key);

// DELETE /api/v1/me/api-keys/{id}: the key stops working and leaves the list. A 404 says it was
// already revoked (in another tab, say): gone all the same.
export async function revokeApiKey(id: string): Promise<void> {
  try {
    await del(`/v1/me/api-keys/${encodeURIComponent(id)}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return;
    throw error;
  }
}
