import { afterEach, describe, expect, it, vi } from 'vitest';

import { ADA, NOT_SIGNED_IN, sentBody, session, stubApi } from '@/test/api';

import {
  connectUrl,
  deleteMe,
  disconnectSignInMethod,
  getMe,
  signInUrl,
  signOut,
  signOutEverywhere,
  updateMe,
} from './accounts';
import { ApiError } from './client';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('getMe', () => {
  it('returns the signed-in user', async () => {
    stubApi(session(ADA));
    await expect(getMe()).resolves.toEqual(ADA);
  });

  it('reads 401 not-signed-in as signed out, not as an error', async () => {
    stubApi({ 'GET /api/v1/me': NOT_SIGNED_IN });
    await expect(getMe()).resolves.toBeNull();
  });

  it('passes any other failure on', async () => {
    stubApi({ 'GET /api/v1/me': { status: 503, body: { detail: 'Down for a minute.' } } });
    await expect(getMe()).rejects.toBeInstanceOf(ApiError);
  });
});

describe('signInUrl', () => {
  it('starts at the provider of the API, without next for the default landing', () => {
    expect(signInUrl('github')).toBe('/api/v1/auth/github/start');
    expect(signInUrl('google')).toBe('/api/v1/auth/google/start');
  });

  it('passes next encoded, search included', () => {
    expect(signInUrl('github', '/instruments?q=btc')).toBe('/api/v1/auth/github/start?next=%2Finstruments%3Fq%3Dbtc');
  });
});

describe('signOut', () => {
  it('posts to the sign-out endpoint', async () => {
    const fetch = stubApi({ 'POST /api/v1/auth/sign-out': { status: 204 } });
    await expect(signOut()).resolves.toBeUndefined();
    expect(fetch).toHaveBeenCalledOnce();
  });

  it('reads 401 as signed out already, not as an error', async () => {
    stubApi({ 'POST /api/v1/auth/sign-out': NOT_SIGNED_IN });
    await expect(signOut()).resolves.toBeUndefined();
  });

  it('passes any other failure on', async () => {
    for (const status of [403, 503]) {
      stubApi({ 'POST /api/v1/auth/sign-out': { status, body: { detail: 'No.' } } });
      await expect(signOut()).rejects.toMatchObject({ status });
    }
  });
});

describe('connectUrl', () => {
  it('starts at the provider with intent=connect and where to come back', () => {
    expect(connectUrl('google', '/settings/sign-in')).toBe(
      '/api/v1/auth/google/start?intent=connect&next=%2Fsettings%2Fsign-in',
    );
  });
});

describe('signOutEverywhere', () => {
  it('posts to its endpoint and reads 401 as signed out already', async () => {
    const fetch = stubApi({ 'POST /api/v1/auth/sign-out-everywhere': { status: 204 } });
    await expect(signOutEverywhere()).resolves.toBeUndefined();
    expect(fetch).toHaveBeenCalledOnce();
    stubApi({ 'POST /api/v1/auth/sign-out-everywhere': NOT_SIGNED_IN });
    await expect(signOutEverywhere()).resolves.toBeUndefined();
  });

  it('passes any other failure on', async () => {
    stubApi({ 'POST /api/v1/auth/sign-out-everywhere': { status: 503, body: { detail: 'No.' } } });
    await expect(signOutEverywhere()).rejects.toMatchObject({ status: 503 });
  });
});

describe('the account', () => {
  it('changes the display name and returns the user', async () => {
    const renamed = { ...ADA, display_name: 'Ada L.' };
    const fetch = stubApi({ 'PATCH /api/v1/me': { status: 200, body: renamed } });
    await expect(updateMe({ display_name: 'Ada L.' })).resolves.toEqual(renamed);
    expect(sentBody(fetch, 'PATCH /api/v1/me')).toEqual({ display_name: 'Ada L.' });
  });

  it('deletes the account', async () => {
    const fetch = stubApi({ 'DELETE /api/v1/me': { status: 204 } });
    await expect(deleteMe()).resolves.toBeUndefined();
    expect(fetch).toHaveBeenCalledOnce();
  });

  it('removes a sign-in method, and passes the last one’s 409 on', async () => {
    stubApi({ 'DELETE /api/v1/me/sign-in-methods/github': { status: 204 } });
    await expect(disconnectSignInMethod('github')).resolves.toBeUndefined();
    stubApi({
      'DELETE /api/v1/me/sign-in-methods/google': {
        status: 409,
        body: { type: 'https://candlestack.tech/problems/last-sign-in-method', detail: 'Keep one.' },
      },
    });
    await expect(disconnectSignInMethod('google')).rejects.toMatchObject({
      status: 409,
      problem: 'last-sign-in-method',
    });
  });
});
