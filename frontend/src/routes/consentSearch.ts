import type { SearchSchemaInput } from '@tanstack/react-router';

// /oauth/consent?request=<id>: the consent page of an app's OAuth request, where the API's
// authorize step sends the browser (docs/architecture.md, OAuth).
export const CONSENT_PAGE = '/oauth/consent';

export type ConsentSearch = { request?: string };

// The API's IDs are URL-safe base64 (secrets.token_urlsafe). Anything else names no request, and
// is never put into a request path.
const REQUEST_ID = /^[\w-]{1,128}$/;

// The key is always set, undefined included, as the router keeps search params a route does not
// return (signInSearch.ts).
export function validateConsentSearch(search: { request?: unknown } & SearchSchemaInput): ConsentSearch {
  const { request } = search;
  return { request: typeof request === 'string' && REQUEST_ID.test(request) ? request : undefined };
}

// The consent page of a request, as a path of the app: where signing in from it comes back to.
export const consentPath = (request: string) => `${CONSENT_PAGE}?${new URLSearchParams({ request }).toString()}`;

// Whether `path` (a path of this app, from appPath) is the consent page. The API sends a signed-out
// browser to /signin?next=<the consent page>; the consent page has its own sign-in, which names
// the app, so the sign-in page passes it on.
export const isConsentPath = (path: string) => URL.parse(path, 'https://app.invalid')?.pathname === CONSENT_PAGE;

// The request of a consent page `path` names, or undefined for another page or a broken ID: the
// sign-in page names the app when the beta list turned away a sign-in that started there.
export function consentRequestOf(path: string | undefined): string | undefined {
  const url = path === undefined ? null : URL.parse(path, 'https://app.invalid');
  if (url?.pathname !== CONSENT_PAGE) return undefined;
  const request = url.searchParams.get('request');
  return request !== null && REQUEST_ID.test(request) ? request : undefined;
}
