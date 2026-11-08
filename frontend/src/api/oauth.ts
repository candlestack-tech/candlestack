import { queryOptions } from '@tanstack/react-query';

import { ApiError, del, get, postJson } from './client';
import type { components } from './schema';

type Schemas = components['schemas'];

// The OAuth sign-in of MCP clients such as Claude and ChatGPT (docs/architecture.md, OAuth): the
// consent page decides an app's request, Settings lists the apps the user allowed.
export type AuthorizationRequest = Schemas['AuthorizationRequestOut'];
export type AppClient = Schemas['ClientOut'];
export type Consent = Schemas['ConsentIn'];
export type ConnectedApp = Schemas['ConnectedAppOut'];
export type ConnectedApps = Schemas['ConnectedAppsOut'];

// GET /api/v1/oauth/requests/{id}: the app as it names itself, the host it returns to and the
// scopes it asks for, in plain words. No session needed, so the page names the app before the
// sign-in. 404 authorization-request-not-found once it was decided, expired (10 minutes) or never
// was. It does not change while the page is open. The route's loader asks first; a failed answer
// is not asked again when the page mounts (the page offers Try again).
export function authorizationRequestQueryOptions(id: string) {
  return queryOptions({
    queryKey: ['oauth', 'requests', id],
    queryFn: ({ signal }) => get<AuthorizationRequest>(`/v1/oauth/requests/${encodeURIComponent(id)}`, signal),
    staleTime: Infinity,
    retryOnMount: false,
  });
}

// POST /api/v1/oauth/requests/{id}: the user's decision, once, with the session. Answers where
// the browser goes back to the app: its redirect URI with a code or error=access_denied. 403
// not-on-beta-list when an account off the beta list allows.
export const decideAuthorizationRequest = (id: string, consent: Consent) =>
  postJson<Schemas['ConsentOut']>(`/v1/oauth/requests/${encodeURIComponent(id)}`, consent);

// GET /api/v1/me/connected-apps: the apps the user allowed, the most recently connected first.
// Under ['account', ...], so signing out forgets them. An app's last use moves with its requests
// and a new consent in another tab adds one, so the list is asked again on coming back to the tab.
export function connectedAppsQueryOptions() {
  return queryOptions({
    queryKey: ['account', 'connected-apps'],
    queryFn: ({ signal }) => get<ConnectedApps>('/v1/me/connected-apps', signal),
    refetchOnWindowFocus: true,
  });
}

// DELETE /api/v1/me/connected-apps/{id}: the app's tokens stop working at once. A 404 says it was
// already disconnected (in another tab, say): gone all the same.
export async function disconnectApp(id: string): Promise<void> {
  try {
    await del(`/v1/me/connected-apps/${encodeURIComponent(id)}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return;
    throw error;
  }
}
