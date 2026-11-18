import { describe, expect, it } from 'vitest';

import { consentPath, consentRequestOf, isConsentPath, validateConsentSearch } from './consentSearch';

// The router passes the raw search object; SearchSchemaInput is only a type marker.
type RawSearch = Parameters<typeof validateConsentSearch>[0];
const validate = (search: Record<string, unknown>) => validateConsentSearch(search as RawSearch);

describe('validateConsentSearch', () => {
  it('keeps a request ID as the API makes them', () => {
    expect(validate({ request: 'Zx9_-aB3' })).toEqual({ request: 'Zx9_-aB3' });
  });

  it('drops anything else, never putting it into a request path', () => {
    for (const request of ['', '../me', 'a/b', 'a b', 'x'.repeat(129), 42, undefined])
      expect(validate({ request })).toEqual({ request: undefined });
  });
});

describe('consentPath and isConsentPath', () => {
  it('builds the page of a request, and knows it from other paths', () => {
    expect(consentPath('Zx9_-aB3')).toBe('/oauth/consent?request=Zx9_-aB3');
    expect(isConsentPath(consentPath('Zx9_-aB3'))).toBe(true);
    expect(isConsentPath('/oauth/consent')).toBe(true);
    expect(isConsentPath('/oauth/consentx?request=a')).toBe(false);
    expect(isConsentPath('/dashboard')).toBe(false);
  });
});

describe('consentRequestOf', () => {
  it('reads the request of a consent page, and nothing from another page or a broken ID', () => {
    expect(consentRequestOf(consentPath('Zx9_-aB3'))).toBe('Zx9_-aB3');
    expect(consentRequestOf('/oauth/consent?request=..%2Fme')).toBeUndefined();
    expect(consentRequestOf('/oauth/consent')).toBeUndefined();
    expect(consentRequestOf('/dashboard?request=Zx9_-aB3')).toBeUndefined();
    expect(consentRequestOf(undefined)).toBeUndefined();
  });
});
