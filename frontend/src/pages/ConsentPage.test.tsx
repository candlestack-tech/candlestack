// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import type { AuthorizationRequest } from '@/api';
import { ARSEN, problem, sentBody, session, stubApi } from '@/test/api';
import { renderApp } from '@/test/app';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ID = 'Zx9_aB3cD4eF5gH6iJ7kL8mN9oP0qR1s';
const PAGE = `/oauth/consent?request=${ID}`;
const REQUEST_PATH = `/api/v1/oauth/requests/${ID}`;

// Claude asks for everything, as a client does that names no scope.
const CLAUDE: AuthorizationRequest = {
  client: {
    name: 'Claude',
    logo_uri: null,
    client_uri: 'https://claude.ai',
    redirect_host: 'claude.ai',
    document_host: 'claude.ai',
  },
  scopes: [
    { scope: 'read', description: 'See your backtests, models and market data' },
    { scope: 'backtests', description: 'Start, rename and star backtests' },
    { scope: 'models', description: 'Upload, rename and delete your models' },
    { scope: 'publish', description: 'Publish and unpublish backtests in Experiments' },
  ],
  expires_at: '2026-11-08T12:10:00Z',
};

const requestAnswer = (request: AuthorizationRequest = CLAUDE) => ({
  [`GET ${REQUEST_PATH}`]: { status: 200, body: request },
});

const BACK = 'https://claude.ai/api/mcp/auth_callback?code=abc&state=xyz&iss=x';

// The page loads its code on demand: once before the tests, so they time only what they test.
beforeAll(async () => {
  stubApi({ ...session(ARSEN), ...requestAnswer() });
  await renderApp(PAGE);
  await screen.findByRole('heading', { level: 1 }, { timeout: 15_000 });
  cleanup();
  vi.unstubAllGlobals();
}, 30_000);

async function open(answers: Parameters<typeof stubApi>[0], url = PAGE) {
  const fetch = stubApi(answers);
  const app = await renderApp(url);
  return { fetch, ...app, user: userEvent.setup() };
}

// jsdom cannot leave the page, so the stub records where the browser was sent.
function stubLeaving() {
  const assign = vi.fn();
  vi.stubGlobal('location', { assign });
  return assign;
}

const heading = () => screen.findByRole('heading', { level: 1 });
const box = (name: string) => screen.getByRole('checkbox', { name });

describe('Consent page, signed in', () => {
  it('names the app, where it returns to and the account, with the scopes in plain words', async () => {
    await open({ ...session(ARSEN), ...requestAnswer() });
    expect((await heading()).textContent).toBe('Claude wants access to your CandleStack account');
    expect(document.title).toBe('Allow access · CandleStack');
    expect(screen.getByText('From claude.ai. It acts as you with what you allow here.')).toBeDefined();
    expect(screen.getByText('Named by claude.ai')).toBeDefined();
    expect(screen.getByText('arsen@example.com')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Not you?' })).toBeDefined();

    const read = box('See your backtests, models and market data');
    expect(read).toHaveProperty('checked', true);
    expect(read).toHaveProperty('disabled', true);
    expect(screen.getByText('Always')).toBeDefined();
    expect(box('Start, rename and star backtests')).toHaveProperty('checked', true);
    expect(box('Upload, rename and delete your models')).toHaveProperty('checked', true);
    expect(box('Publish and unpublish backtests in Experiments')).toHaveProperty('checked', false);
    expect(
      screen.getByText('Claude can never delete your account, change how you sign in or download your model files.'),
    ).toBeDefined();
    expect(screen.getByText('You go back to claude.ai. Disconnect it any time in Settings.')).toBeDefined();
    expect(screen.queryByRole('link', { name: 'Sign in' })).toBeNull();
  });

  it('allows what is ticked and sends the browser back to the app', async () => {
    const { fetch, user } = await open({
      ...session(ARSEN),
      ...requestAnswer(),
      [`POST ${REQUEST_PATH}`]: { status: 200, body: { redirect_to: BACK } },
    });
    await heading();
    await user.click(box('Upload, rename and delete your models'));
    await user.click(box('Publish and unpublish backtests in Experiments'));
    await user.click(box('See your backtests, models and market data'));
    const assign = stubLeaving();
    await user.click(screen.getByRole('button', { name: 'Allow' }));
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith(BACK));
    expect(sentBody(fetch, `POST ${REQUEST_PATH}`)).toEqual({
      decision: 'allow',
      scopes: ['read', 'backtests', 'publish'],
    });
    expect(screen.getByRole('button', { name: 'Allow' })).toHaveProperty('disabled', true);
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveProperty('disabled', true);
  });

  it('says the name of an app that registered itself is its own', async () => {
    const own = { ...CLAUDE.client, redirect_host: 'claude.ai.example', document_host: null };
    await open({ ...session(ARSEN), ...requestAnswer({ ...CLAUDE, client: own }) });
    await heading();
    expect(screen.getByText('From claude.ai.example. It acts as you with what you allow here.')).toBeDefined();
    expect(screen.getByText('Named by the app itself, not checked')).toBeDefined();
    expect(screen.queryByText(/^Named by claude/)).toBeNull();
  });

  it('holds the boxes and Not you? while the decision is on its way', async () => {
    // The decision's answer, held back until the test lets it come.
    let answer: (value: { status: number; body: unknown }) => void = () => {};
    const { user } = await open({
      ...session(ARSEN),
      ...requestAnswer(),
      [`POST ${REQUEST_PATH}`]: () => new Promise((resolve) => (answer = resolve)),
    });
    await heading();
    await user.click(screen.getByRole('button', { name: 'Allow' }));
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Allow' })).toHaveProperty('disabled', true));
    for (const name of ['Start, rename and star backtests', 'Publish and unpublish backtests in Experiments'])
      expect(box(name)).toHaveProperty('disabled', true);
    expect(screen.getByRole('button', { name: 'Not you?' })).toHaveProperty('disabled', true);

    answer({ status: 503, body: { detail: 'Down for a minute.' } });
    await screen.findByRole('alert');
    expect(box('Start, rename and star backtests')).toHaveProperty('disabled', false);
    expect(screen.getByRole('button', { name: 'Not you?' })).toHaveProperty('disabled', false);
  });

  it('offers the buttons again when Back brings the decided page out of the cache', async () => {
    const { user } = await open({
      ...session(ARSEN),
      ...requestAnswer(),
      [`POST ${REQUEST_PATH}`]: { status: 200, body: { redirect_to: BACK } },
    });
    await heading();
    const assign = stubLeaving();
    await user.click(screen.getByRole('button', { name: 'Allow' }));
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith(BACK));
    const allow = () => screen.getByRole('button', { name: 'Allow' });

    window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: false }));
    expect(allow()).toHaveProperty('disabled', true);
    window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
    await vi.waitFor(() => expect(allow()).toHaveProperty('disabled', false));
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveProperty('disabled', false);
    expect(box('Start, rename and star backtests')).toHaveProperty('disabled', false);
  });

  it('denies on Cancel, and the app hears it', async () => {
    const denied = 'https://claude.ai/api/mcp/auth_callback?error=access_denied&state=xyz';
    const { fetch, user } = await open({
      ...session(ARSEN),
      ...requestAnswer(),
      [`POST ${REQUEST_PATH}`]: { status: 200, body: { redirect_to: denied } },
    });
    await heading();
    const assign = stubLeaving();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith(denied));
    expect(sentBody(fetch, `POST ${REQUEST_PATH}`)).toEqual({ decision: 'deny' });
  });

  it('offers only what the app asked for, and read always', async () => {
    const { fetch, user } = await open({
      ...session(ARSEN),
      ...requestAnswer({ ...CLAUDE, scopes: [{ scope: 'publish', description: 'Publish backtests' }] }),
      [`POST ${REQUEST_PATH}`]: { status: 200, body: { redirect_to: BACK } },
    });
    await heading();
    expect(screen.getAllByRole('checkbox')).toHaveLength(2);
    expect(box('See your backtests, models and market data')).toHaveProperty('checked', true);
    expect(box('Publish backtests')).toHaveProperty('checked', false);
    stubLeaving();
    await user.click(screen.getByRole('button', { name: 'Allow' }));
    await vi.waitFor(() =>
      expect(sentBody(fetch, `POST ${REQUEST_PATH}`)).toEqual({ decision: 'allow', scopes: ['read'] }),
    );
  });

  it('draws the mark of a known app, the logo a documented app gave, else its initial', async () => {
    await open({ ...session(ARSEN), ...requestAnswer() });
    await heading();
    const tile = () =>
      screen.getByRole('heading', { level: 1 }).closest('div.flex-col')?.querySelector('span[aria-hidden]');
    expect(tile()?.querySelector('svg path')?.getAttribute('fill')).toBe('#D97757');
    cleanup();

    const logo = 'https://notes.example/logo.png';
    const notes = {
      name: 'Notes Agent',
      logo_uri: logo,
      client_uri: null,
      redirect_host: 'notes.example',
      document_host: 'notes.example',
    };
    await open({ ...session(ARSEN), ...requestAnswer({ ...CLAUDE, client: notes }) });
    await heading();
    const image = tile()?.querySelector('img');
    expect(image?.getAttribute('src')).toBe(logo);
    expect(image?.getAttribute('referrerpolicy')).toBe('no-referrer');
    image?.dispatchEvent(new Event('error'));
    await vi.waitFor(() => expect(tile()?.textContent).toBe('N'));
  });

  it('says why a decision failed and lets the user try again', async () => {
    const { user } = await open({
      ...session(ARSEN),
      ...requestAnswer(),
      [`POST ${REQUEST_PATH}`]: { status: 503, body: { detail: 'Down for a minute.' } },
    });
    await heading();
    await user.click(screen.getByRole('button', { name: 'Allow' }));
    expect((await screen.findByRole('alert')).textContent).toBe('Down for a minute.');
    expect(screen.getByRole('button', { name: 'Allow' })).toHaveProperty('disabled', false);
  });

  it('signs out on Not you? and offers the sign-in on the same card', async () => {
    const { fetch, user } = await open({
      ...session(ARSEN),
      ...requestAnswer(),
      'POST /api/v1/auth/sign-out': { status: 204 },
    });
    await heading();
    await user.click(screen.getByRole('button', { name: 'Not you?' }));
    expect((await screen.findByRole('heading', { name: 'Sign in to connect Claude' })).textContent).toBeDefined();
    expect(fetch).toHaveBeenCalledWith('/api/v1/auth/sign-out', expect.objectContaining({ method: 'POST' }));
  });

  it('shows the sign-in when the session ended meanwhile', async () => {
    const { user } = await open({
      ...session(ARSEN),
      ...requestAnswer(),
      [`POST ${REQUEST_PATH}`]: problem(401, 'not-signed-in', 'Sign in to use this.'),
    });
    await heading();
    await user.click(screen.getByRole('button', { name: 'Allow' }));
    expect(await screen.findByRole('heading', { name: 'Sign in to connect Claude' })).toBeDefined();
  });
});

describe('Consent page, off the beta list', () => {
  it('says so in the sign-in page words, and Use another account signs out', async () => {
    const { user } = await open({
      ...session(ARSEN),
      ...requestAnswer(),
      [`POST ${REQUEST_PATH}`]: problem(403, 'not-on-beta-list', 'The account is not on the beta list.'),
      'POST /api/v1/auth/sign-out': { status: 204 },
    });
    await heading();
    await user.click(screen.getByRole('button', { name: 'Allow' }));
    expect((await screen.findByRole('heading', { level: 1 })).textContent).toBe('You are not on the beta list yet');
    expect(
      screen.getByText(/cannot sign in during the beta, so Claude cannot connect\. Ask the team for an invite/),
    ).toBeDefined();
    expect(screen.getByRole('link', { name: 'Contact the team' }).getAttribute('href')).toBe(
      'https://candlestack.tech',
    );
    await user.click(screen.getByRole('button', { name: 'Use another account' }));
    expect(await screen.findByRole('heading', { name: 'Sign in to connect Claude' })).toBeDefined();
  });
});

describe('Consent page, signed out', () => {
  it('names the app and signs in on the card, coming back to the request', async () => {
    const { user } = await open({ ...session(null), ...requestAnswer() });
    expect((await heading()).textContent).toBe('Sign in to connect Claude');
    expect(
      screen.getByText('Claude wants access to your CandleStack account. Sign in first, then choose what it can do.'),
    ).toBeDefined();
    expect(screen.getByText('In beta, open to people on the beta list')).toBeDefined();
    expect(screen.getByText('We only get your name and email. No password is stored.')).toBeDefined();
    const assign = stubLeaving();
    await user.click(screen.getByRole('button', { name: 'Continue with GitHub' }));
    expect(assign).toHaveBeenCalledWith(
      `/api/v1/auth/github/start?next=${encodeURIComponent(`/oauth/consent?request=${ID}`)}`,
    );
  });

  it('takes over from the sign-in page the API sends a signed-out browser to', async () => {
    const { router } = await open({ ...session(null), ...requestAnswer() }, `/signin?next=${encodeURIComponent(PAGE)}`);
    expect(router.state.location.href).toBe(PAGE);
    expect((await heading()).textContent).toBe('Sign in to connect Claude');
  });

  it('leaves the sign-in page alone after a denied sign-in', async () => {
    const { router } = await open(
      {
        ...session(null),
        'GET /api/v1/auth/denied': { status: 200, body: { email: 'a@example.com', provider: 'github' } },
      },
      `/signin?next=${encodeURIComponent(PAGE)}&error=denied`,
    );
    expect(router.state.location.pathname).toBe('/signin');
  });
});

describe('Consent page, a request that is gone', () => {
  it('says the request expired when the API no longer knows it', async () => {
    await open({
      ...session(ARSEN),
      [`GET ${REQUEST_PATH}`]: problem(404, 'authorization-request-not-found', 'No such request.'),
    });
    expect((await heading()).textContent).toBe('This request has expired');
    expect(
      screen.getByText('A request to connect an app works once, for 10 minutes. Go back to the app and connect again.'),
    ).toBeDefined();
    expect(screen.getByRole('link', { name: 'Go to Dashboard' }).getAttribute('href')).toBe('/dashboard');
  });

  it('says the same for an address without a request, asking nothing', async () => {
    const { fetch } = await open(session(null), '/oauth/consent?request=..%2Fme');
    expect((await heading()).textContent).toBe('This request has expired');
    expect(fetch.mock.calls.some(([path]) => String(path).startsWith('/api/v1/oauth'))).toBe(false);
  });

  it('says so when the request ended while the page was open', async () => {
    const { user } = await open({
      ...session(ARSEN),
      ...requestAnswer(),
      [`POST ${REQUEST_PATH}`]: problem(404, 'authorization-request-not-found', 'No such request.'),
    });
    await heading();
    await user.click(screen.getByRole('button', { name: 'Allow' }));
    expect((await screen.findByRole('heading', { level: 1 })).textContent).toBe('This request has expired');
  });

  it('says why the request did not load, and tries again', async () => {
    let calls = 0;
    const { user } = await open({
      ...session(ARSEN),
      [`GET ${REQUEST_PATH}`]: async () =>
        ++calls === 1 ? { status: 503, body: { detail: 'Down for a minute.' } } : { status: 200, body: CLAUDE },
    });
    expect((await heading()).textContent).toBe('Could not load this request');
    expect(within(screen.getByRole('region', { name: 'Allow access' })).getByRole('alert').textContent).toBe(
      'Down for a minute.',
    );
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(
      await screen.findByRole('heading', { name: 'Claude wants access to your CandleStack account' }),
    ).toBeDefined();
  });
});
